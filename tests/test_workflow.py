import copy
import unittest
from biotwin.protocol import make_snapshot
from biotwin.simulation import replay, synthetic_fixture
from biotwin.transport import make_delta, full_packet, apply_delta, apply_resynchronization
from biotwin.workflow import Workflow, complete_fixture
from biotwin.viewmodel import role_view, capability


def clock(minutes):
    return f'2026-11-14T09:{minutes:02}:00+00:00'


class EnvelopeReplay(unittest.TestCase):
    def setUp(self):
        self.old = synthetic_fixture()['events'][0]['snapshot']
        payload = self.old['payload']
        self.middle = make_snapshot(payload['subject_id'], payload['observations'], clock(1))
        self.target = make_snapshot(payload['subject_id'], payload['observations'], clock(2))
        self.events = [{'event_id': f'local-{i}', 'kind': 'local', 'actor': 'crew', 'at': s['payload']['created_at'], 'snapshot': s} for i, s in enumerate((self.old, self.middle, self.target))]

    def packet(self, identity, sent, arrived, packet):
        return {'event_id': identity, 'kind': 'packet', 'packet_id': identity, 'sender': 'crew', 'recipient': 'doctor', 'sent_at': clock(sent), 'arrives_at': clock(arrived), 'packet': packet}

    def test_actual_delta_delivery_and_role_isolation(self):
        self.events += [self.packet('initial', 0, 3, {'type': 'snapshot', 'snapshot': self.old}), self.packet('update', 4, 5, {'type': 'envelope', 'envelope': make_delta(self.old, self.middle)})]
        self.assertEqual(replay(self.events, clock(4))['doctor']['current_snapshot'], self.old)
        self.assertEqual(replay(self.events, clock(5))['doctor']['current_snapshot'], self.middle)
        self.assertEqual(make_delta(self.old, self.middle)['kind'], 'delta')

    def test_missing_base_explicit_recovery_then_delta(self):
        self.events += [self.packet('missing', 2, 3, {'type': 'envelope', 'envelope': make_delta(self.old, self.middle)}),
            self.packet('resync', 4, 5, {'type': 'resync', 'envelope': full_packet(self.middle)}),
            self.packet('delta', 6, 7, {'type': 'envelope', 'envelope': make_delta(self.middle, self.target)})]
        missing = replay(self.events, clock(3))
        self.assertTrue(missing['doctor']['resynchronization_needed'])
        self.assertIsNone(missing['doctor']['current_snapshot'])
        self.assertIn('full-resynchronization-needed', missing['rejected'][0]['reason'])
        final = replay(self.events, clock(7))
        self.assertFalse(final['doctor']['resynchronization_needed'])
        self.assertEqual(final['doctor']['current_snapshot'], self.target)
        with self.assertRaisesRegex(ValueError, 'wrong base'):
            apply_delta(self.target, make_delta(self.old, self.middle))

    def test_wrong_base_duplicate_and_out_of_order_never_roll_back(self):
        update = self.packet('update', 4, 5, {'type': 'envelope', 'envelope': make_delta(self.old, self.middle)})
        self.events += [self.packet('initial', 0, 3, {'type': 'snapshot', 'snapshot': self.old}), update,
            {**update, 'event_id': 'duplicate', 'arrives_at': clock(6)},
            self.packet('stale', 7, 8, {'type': 'envelope', 'envelope': make_delta(self.old, self.target)})]
        state = replay(self.events, clock(9))
        self.assertEqual(state['doctor']['current_snapshot'], self.middle)
        self.assertEqual(len(state['rejected']), 2)
        self.assertTrue(state['doctor']['resynchronization_needed'])

    def test_corruption_or_missing_sender_target_rejected(self):
        bad = make_delta(self.old, self.middle)
        bad['target_digest'] = '0' * 64
        self.events.append(self.packet('corrupt', 3, 4, {'type': 'envelope', 'envelope': bad}))
        with self.assertRaises(ValueError):
            replay(self.events, clock(5))
        self.events.pop()
        self.events = [e for e in self.events if e['snapshot'] != self.middle]
        self.events.append(self.packet('no-target', 3, 4, {'type': 'envelope', 'envelope': make_delta(self.old, self.middle)}))
        with self.assertRaisesRegex(ValueError, 'does not possess'):
            replay(self.events, clock(5))

    def test_unsolicited_resync_is_not_wrong_base_bypass(self):
        self.events += [self.packet('initial', 0, 3, {'type': 'snapshot', 'snapshot': self.old}),
            self.packet('resync', 4, 5, {'type': 'resync', 'envelope': full_packet(self.target)})]
        state = replay(self.events, clock(6))
        self.assertEqual(state['doctor']['current_snapshot'], self.old)
        self.assertIn('unsolicited', state['rejected'][0]['reason'])

    def test_selected_full_envelope_is_actually_delivered(self):
        fixture = synthetic_fixture()
        old, new = fixture['events'][0]['snapshot'], fixture['events'][-1]['snapshot']
        events = fixture['events'] + [self.packet('full-update', 30, 35, {'type': 'envelope', 'envelope': make_delta(old, new)})]
        self.assertEqual(make_delta(old, new)['kind'], 'full')
        self.assertEqual(replay(events, clock(35))['doctor']['current_snapshot'], new)

    def test_equivalent_instant_cannot_be_disguised_as_new_snapshot(self):
        payload = self.old['payload']
        target = make_snapshot(payload['subject_id'], payload['observations'], '2026-11-14T18:00:00+09:00')
        self.assertNotEqual(target['digest'], self.old['digest'])
        with self.assertRaisesRegex(ValueError, 'same-instant'):
            make_delta(self.old, target)
        with self.assertRaisesRegex(ValueError, 'same-instant'):
            apply_delta(self.old, full_packet(target, self.old['digest']))
        with self.assertRaisesRegex(ValueError, 'same-instant'):
            apply_resynchronization(self.old, full_packet(target))
        self.assertEqual(apply_resynchronization(self.old, full_packet(self.old)), self.old)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = synthetic_fixture()
        self.app = Workflow(self.fixture['events'])

    def test_no_automatic_completion_and_invalid_future_duplicate_steps(self):
        self.assertEqual(self.app.state(clock(40))['crew']['completions'], [])
        before = copy.deepcopy(self.app.events)
        for index, completed, now in ((-1, clock(41), clock(41)), (3, clock(41), clock(41)), (True, clock(41), clock(41)), (0, clock(42), clock(41))):
            with self.assertRaises(ValueError):
                self.app.complete('bad', index, completed, 'operator note', now=now)
            self.assertEqual(before, self.app.events)
        record = self.app.complete('done', 0, clock(41), 'Acquisition conditions recorded.', now=clock(41))
        self.assertEqual(record['branch'], 'B')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.app.complete('duplicate', 0, clock(42), 'Same context.', now=clock(42))
        with self.assertRaises(ValueError):
            self.app.complete('before-receipt', 0, clock(39), 'No reply yet.', now=clock(40))

    def test_delayed_followup_context_and_stale_completions(self):
        fixture = complete_fixture()
        before = role_view(fixture['events'], '2026-11-14T10:04:00+00:00', 'doctor')
        after = role_view(fixture['events'], '2026-11-14T10:05:00+00:00', 'doctor')
        self.assertEqual(before['followups'], [])
        self.assertEqual(before['completions'], [])
        self.assertEqual(len(after['completions']), 3)
        self.assertTrue(all(c['stale_context'] for c in after['completions']))
        followup = after['followups'][0]
        self.assertEqual(followup['sent_at'], clock(45))
        self.assertEqual(followup['snapshot']['payload']['created_at'], clock(44))
        self.assertEqual(len(followup['context_snapshots']), 2)
        self.assertEqual(after['current_snapshot'], followup['snapshot'])
        crew = role_view(fixture['events'], clock(45), 'crew')
        self.assertEqual(crew['protocol']['protocol']['original_reply'], self.fixture['events'][2]['packet']['protocol']['original_reply'])
        self.assertEqual(role_view(fixture['events'], clock(30), 'crew')['completions'], [])

    def test_tampered_completion_and_followup_are_rejected(self):
        fixture = complete_fixture()
        for mutation in ('completion', 'followup'):
            events = copy.deepcopy(fixture['events'])
            if mutation == 'completion':
                events[4]['record']['step_text'] = 'fabricated completion'
            else:
                events[-1]['packet']['completions'][0]['note'] = 'invented note'
            with self.assertRaises(ValueError):
                Workflow(events)

    def test_structured_authoring_validates_all_conditions(self):
        protocol = self.fixture['events'][2]['packet']['protocol']
        reviewed = self.fixture['events'][0]['snapshot']
        self.assertEqual(self.app.author_protocol(reviewed, **protocol), protocol)
        for changes in ({'version': True}, {'conditions': {'A': [], 'B': []}}, {'checklists': {'A': ['a']}}):
            with self.assertRaises(ValueError):
                self.app.author_protocol(reviewed, **{**protocol, **changes})

    def test_capabilities_and_measurement_selector_are_explicit(self):
        for name in ('SpO2', 'sleep_stages', 'live_sensors', 'clinical_decisions'):
            self.assertEqual(capability(name)['status'], 'unavailable')
        doctor = role_view(self.fixture['events'], clock(30), 'doctor')
        self.assertFalse(any(p['id'] == 'changed' for chart in doctor['chart_series'] for p in chart['points']))
        self.assertEqual(doctor['body_regions']['head']['measurement_selectors'], ['reaction_time'])
        self.assertIn('no organ health', doctor['body_regions']['heart']['meaning'])

    def test_authoring_helpers_use_only_role_received_evidence(self):
        early = self.app.suggest_dependencies('reaction time and SpO2', clock(10))
        later = self.app.suggest_dependencies('reaction time and SpO2', clock(30))
        self.assertTrue(early['requires_confirmation'])
        self.assertTrue(later['requires_confirmation'])
        self.assertTrue(any(s['contexts'] for s in later['suggestions']))
        self.assertFalse(any(s['contexts'] for s in early['suggestions']))
        view = role_view(self.fixture['events'], clock(40), 'crew')
        self.assertEqual(view['fixed_reference_cusum'][0]['result']['status'], 'available')
        self.assertEqual(len(view['fixed_reference_cusum'][0]['result']['reference']['reference_ids']), 6)

    def test_authored_content_cannot_reuse_protocol_identity(self):
        events = copy.deepcopy(self.fixture['events'])
        packet = copy.deepcopy(events[2]['packet'])
        packet['protocol']['checklists']['B'][0] = 'Different authored step.'
        events.append({**events[2], 'event_id': 'different-content', 'packet_id': 'different-reply', 'sent_at': clock(21), 'arrives_at': clock(41), 'packet': packet})
        with self.assertRaisesRegex(ValueError, 'ID/version'):
            Workflow(events)

    def test_chart_anchor_uses_instant_order_across_offsets(self):
        payload = self.fixture['events'][0]['snapshot']['payload']
        records = copy.deepcopy(payload['observations'])
        latest = next(o for o in records if o['id'] == 'initial')
        # 08:50 UTC sorts lexically after 09:00 UTC when displayed in +09:00.
        records.append({**latest, 'id': 'offset-row', 'observed_at': '2026-11-14T17:50:00+09:00', 'value': 415})
        app = Workflow()
        app.capture('offset-capture', payload['subject_id'], records, clock(0))
        chart = role_view(app.events, clock(0), 'crew')['chart_series'][0]
        self.assertEqual(chart['points'][-1]['id'], 'initial')
        self.assertEqual(chart['reference']['count'], 7)

    def test_late_older_reply_preserved_without_active_rollback(self):
        events = copy.deepcopy(self.fixture['events'])
        events[2]['arrives_at'] = clock(42)
        newer = copy.deepcopy(events[2])
        newer.update(event_id='newer-reply', packet_id='newer-reply', sent_at=clock(21), arrives_at=clock(41))
        newer['packet']['protocol']['id'] = 'newer-protocol'
        events.append(newer)
        state = replay(events, clock(43))['crew']
        self.assertEqual(state['protocol']['protocol']['id'], 'newer-protocol')
        self.assertEqual(len(state['protocol_history']), 2)
        self.assertIn('out-of-order protocol', state['inbox'][-1]['status'])
