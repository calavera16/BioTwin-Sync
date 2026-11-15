import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from biotwin import health_assistant as assistant
from biotwin.desktop_controller import DesktopController, CREWS
from biotwin.local_monitoring import local_document
from biotwin.protocol import canonical_bytes, make_snapshot
from biotwin.session import make_session


class HealthAssistantTests(unittest.TestCase):
    def setUp(self):
        self.c = DesktopController()
        self.c.start_local()

    def assess(self, path='headache', answers=None, note=''):
        return self.c.assess_assistant(path, answers or {}, note, expected_context=self.c.assistant_context())

    def negatives(self, path):
        protocol = next(p for p in self.c.assistant_catalog()['protocols'] if p['id'] == path)
        return {q['id']: 'no' for q in protocol['questions']}

    def row(self):
        return dict(subject_id=self.c.subject, source_id='actual-external-device', metric='heart_rate',
                    value=72, unit='bpm', quality='review', observed_at=datetime.now(timezone.utc).isoformat(),
                    method='operator entered external reading')

    def complete(self, plan, index=0):
        return self.c.complete_assistant(plan['id'], plan['steps'][index]['id'],
            'Operator explicitly recorded this action; symptoms are not resolved by this note.',
            expected_context=self.c.assistant_context())

    def test_three_empty_paths_default_unknown_without_borrowed_measurements(self):
        for path in ('headache', 'tiredness', 'measurement'):
            with self.subTest(path=path):
                self.c.start_local()
                plan = self.assess(path)
                self.assertEqual(plan['status'], 'clarify')
                self.assertTrue(all(v == 'unsure' for v in plan['answers'].values()))
                self.assertEqual(plan['context']['evidence'], [])
                self.assertFalse(any(r['stale'] for r in self.c.assistant_records()))
                self.assertTrue(self.c.prerequisites()['send'][0])

    def test_urgent_yes_overrides_unknown_contact_and_normal_vitals(self):
        self.c.capture_manual(self.row())
        for path in ('headache', 'tiredness', 'measurement'):
            plan = self.c.preview_assistant(path, {'shared_urgent': 'yes'})
            self.assertEqual(plan['status'], 'urgent')
            self.assertIn('Do not wait', plan['next_step'])
            self.assertEqual([s['id'] for s in plan['steps']], ['urgent-contact'])
        plan = self.c.preview_assistant('headache', {'headache_urgent': 'yes'})
        self.assertEqual(plan['status'], 'urgent')
        self.assertIn('nhs-headache', plan['source_ids'])

    def test_precedence_unknown_safety_before_contact_and_no_all_clear(self):
        self.assertEqual(self.c.preview_assistant('headache', {'headache_contact': 'yes'})['status'], 'clarify')
        answers = self.negatives('headache'); answers['headache_contact'] = 'yes'
        self.assertEqual(self.c.preview_assistant('headache', answers)['status'], 'review')
        for path in ('headache', 'tiredness', 'measurement'):
            plan = self.c.preview_assistant(path, self.negatives(path))
            self.assertEqual(plan['status'], 'collect')
            self.assertIn('not an all-clear', plan['next_step'])

    def test_narrative_is_verbatim_and_never_changes_answers(self):
        note = 'I think I am fine. YES severe pain?\nThis text was not medically interpreted.'
        plan = self.assess(note=note)
        self.assertEqual(plan['note'], note)
        self.assertEqual(plan['status'], 'clarify')
        self.assertIn(note, self.c.assistant_handoff()['text'])

    def test_unknown_questions_types_text_and_versions_reject_atomically(self):
        before = self.c.document()
        for args in [('unknown', {}), ('headache', {'unknown': 'yes'}),
                     ('headache', {'shared_urgent': True}), ('headache', {'shared_urgent': 'Yes'}),
                     ('headache', None)]:
            with self.assertRaises(ValueError):
                self.c.assess_assistant(*args, expected_context=self.c.assistant_context())
        with self.assertRaises(ValueError): self.assess(note='x' * 1001)
        with self.assertRaises(ValueError): self.assess(note='bad\x00note')
        self.assertEqual(before, self.c.document())

    def test_catalog_is_pinned_and_no_shared_mutable_references(self):
        first = self.c.assistant_catalog(); first['protocols'][0]['questions'][0]['text'] = 'changed'
        self.assertNotEqual(first, self.c.assistant_catalog())
        with tempfile.TemporaryDirectory() as directory:
            source = Path('assets/desktop/assistant-protocols-v1.json')
            (Path(directory) / source.name).write_bytes(source.read_bytes() + b' ')
            with self.assertRaises(ValueError): assistant.catalog(directory)
        fatigue = next(s for s in self.c.assistant_catalog()['sources'] if s['id'] == 'nhs-fatigue')
        self.assertTrue(fatigue['review_overdue_on_access'])
        self.assertEqual(fatigue['review_due'], '2026-06-02')

    def test_explicit_multiple_actions_immutable_and_duplicate_rejected(self):
        plan = self.assess('headache', self.negatives('headache'))
        one = self.complete(plan); two = self.complete(plan, 1)
        self.assertEqual(one['assessment_id'], plan['id'])
        self.assertEqual(two['kind'], 'action')
        self.assertIn('does not establish symptom resolution', one['meaning'])
        before = self.c.document()
        with self.assertRaises(ValueError): self.complete(plan)
        self.assertEqual(before, self.c.document())
        plan['steps'][0]['text'] = 'bad mutation'
        self.assertNotEqual(plan['steps'], self.c.assistant_records()[0]['steps'])

    def test_new_measurement_invalidates_action_and_old_draft(self):
        plan = self.assess('headache', self.negatives('headache'))
        binding = self.c.assistant_context()
        self.c.capture_manual(self.row())
        before = self.c.document()
        self.assertTrue(self.c.assistant_records()[0]['stale'])
        with self.assertRaises(ValueError): self.complete(plan)
        with self.assertRaises(ValueError):
            self.c.assess_assistant('headache', {}, expected_context=binding)
        self.assertEqual(before, self.c.document())

    def test_reassessment_keeps_past_urgent_history(self):
        urgent = self.assess(answers={'shared_urgent': 'yes'})
        self.complete(urgent)
        self.assess('headache', self.negatives('headache'))
        report = self.c.assistant_handoff()
        self.assertEqual(report['urgent_history'], [urgent['id']])
        self.assertIn('URGENT HISTORY REMAINS', report['text'])
        self.assertEqual(len(report['records']), 3)
        self.assertTrue(next(r for r in report['records'] if r['id'] == urgent['id'])['stale'])

    def test_urgent_reassessment_supersedes_previous_routine_plan_actions(self):
        routine = self.assess('headache', self.negatives('headache'))
        self.assess('measurement', {'shared_urgent': 'yes'})
        before = self.c.document()
        with self.assertRaises(ValueError): self.complete(routine)
        self.assertEqual(before, self.c.document())

    def test_switch_reset_and_same_file_reopen_invalidate_nonmodal_draft(self):
        binding = self.c.assistant_context()
        self.c.select(CREWS[1]); self.c.start_local()
        with self.assertRaises(ValueError): self.c.assess_assistant('headache', {}, expected_context=binding)
        self.c.select(CREWS[0]); self.c.start_local()
        with self.assertRaises(ValueError): self.c.assess_assistant('headache', {}, expected_context=binding)
        self.assess()
        with tempfile.TemporaryDirectory() as directory:
            path = self.c.save(directory)
            binding = self.c.assistant_context()
            self.c.open(path)
            with self.assertRaises(ValueError): self.c.assess_assistant('headache', {}, expected_context=binding)

    def test_away_and_back_crew_selection_never_revives_old_draft_token(self):
        binding = self.c.assistant_context()
        before = self.c.document()
        self.c.select(CREWS[1]); self.c.select(CREWS[0])
        with self.assertRaises(ValueError):
            self.c.assess_assistant('headache', {}, expected_context=binding)
        self.assertEqual(before, self.c.document())

    def test_wall_age_expires_unchanged_plan_and_link_advance_does_not_refresh_time(self):
        plan = self.assess('headache', self.negatives('headache'))
        at = datetime.fromisoformat(plan['assessed_at'])
        self.c.send_consultation(); self.c.advance()
        self.assertEqual(self.c.assistant_records()[0]['assessed_at'], plan['assessed_at'])
        # Advancing the simulation cannot move actual intake acquisition time.
        another = self.assess('headache', self.negatives('headache'))
        self.assertLess(datetime.fromisoformat(another['assessed_at']), datetime.fromisoformat(self.c.current.at))
        class Later(datetime):
            @classmethod
            def now(cls, tz=None): return at + timedelta(minutes=31)
        with patch('biotwin.desktop_controller.datetime', Later):
            self.assertTrue(self.c.assistant_records()[0]['stale'])
            with self.assertRaises(ValueError): self.complete(plan)

    def test_doctor_only_received_snapshot_not_new_local_assessment_actions_or_evidence(self):
        plan = self.assess()
        self.assertEqual(self.c.assistant_records('doctor'), [])
        self.c.send_consultation()
        self.complete(plan)
        self.assess('measurement', {'shared_urgent': 'yes'})
        self.assertEqual(self.c.assistant_records('doctor'), [])
        self.c.advance()
        received = self.c.assistant_records('doctor')
        self.assertEqual([r['id'] for r in received], [plan['id']])
        self.assertEqual(self.c.assistant_handoff('doctor')['urgent_history'], [])
        self.assertIn('current crew symptoms are unknown', self.c.assistant_handoff('doctor')['text'])

    def test_save_open_export_round_trip_and_prior_empty_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            old = self.c.save(directory); self.c.open(old)
            plan = self.assess(); self.complete(plan)
            document = self.c.document(); path = self.c.save(directory)
            self.c.start_local(); self.c.open(path)
            self.assertEqual(self.c.document(), document)
            text_path = self.c.export_assistant_report(directory)
            report = Path(text_path).read_text(encoding='utf-8')
            self.assertIn(plan['id'], report)
            self.assertIn('https://www.nhs.uk/symptoms/headaches/', report)
            self.assertIn('unavailable (empty session)', report)

    def test_ordinary_import_and_manual_cannot_forge_assistant_payload(self):
        plan = self.assess()
        payload = assistant.observation(plan)
        for row in (payload, {**self.row(), 'assistant_record': plan}):
            with self.assertRaises(ValueError): self.c.capture_manual(row)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'fake.json'
                path.write_text(json.dumps([row]), encoding='utf-8')
                with self.assertRaises(ValueError): self.c.import_observations(path)

    def test_metadata_is_excluded_from_scalar_protocols_and_physiological_charts(self):
        self.assess()
        self.assertEqual(self.c.model()['crew']['chart_series'], [])
        self.c.send_consultation(); self.c.advance()
        with self.assertRaises(ValueError):
            self.c.author_reply('le', 1, 'Review', 'A', 'B', metric=assistant.METRIC)

    def rehashed_document(self, events):
        # Deliberately update every outer integrity checksum: semantic validation
        # must still detect forged decisions/context, not merely checksum errors.
        for e in events:
            if e['kind'] == 'local':
                e['snapshot']['digest'] = hashlib.sha256(canonical_bytes(e['snapshot']['payload'])).hexdigest()
        session = make_session(self.c.current.session_id, events, provenance='recorded')
        return local_document(session, self.c.current.local_data)

    def test_rehashed_tampering_of_decision_answers_sources_versions_and_context_rejects(self):
        self.assess()
        original = self.c.current.workflow.events
        changes = [lambda r: r.update(status='collect'),
                   lambda r: r['answers'].update(shared_urgent='yes'),
                   lambda r: r.update(source_ids=['nasa-context']),
                   lambda r: r.update(library_version='old'),
                   lambda r: r.update(record_version=True),
                   lambda r: r['context'].update(session_id='other'),
                   lambda r: r['context'].update(evidence_digest='a' * 64),
                   lambda r: r.update(steps=[{'id': 'meds', 'text': 'forged'}]),
                   lambda r: r.update(note='x' * 1001)]
        before = self.c.document()
        for mutate in changes:
            with self.subTest(mutation=changes.index(mutate)):
                events = copy.deepcopy(original)
                mutate(events[-1]['snapshot']['payload']['observations'][0]['assistant_record'])
                with self.assertRaises(ValueError): self.rehashed_document(events)
        self.assertEqual(before, self.c.document())

    def test_actions_recompute_text_context_and_age_on_import(self):
        plan = self.assess('headache', self.negatives('headache')); self.complete(plan)
        original = self.c.current.workflow.events
        for change in ('step_text', 'assessment_id', 'completed_at'):
            events = copy.deepcopy(original)
            row = next(o for o in events[-1]['snapshot']['payload']['observations'] if o['assistant_record']['kind'] == 'action')
            if change == 'completed_at':
                row['assistant_record'][change] = (datetime.fromisoformat(plan['assessed_at']) + timedelta(minutes=31)).isoformat()
                row['observed_at'] = row['assistant_record'][change]
                events[-1]['at'] = row['observed_at']
                events[-1]['snapshot']['payload']['created_at'] = row['observed_at']
            else: row['assistant_record'][change] = 'forged'
            with self.assertRaises(ValueError): self.rehashed_document(events)

    def test_history_cannot_be_removed_or_rewritten_on_a_later_capture(self):
        self.assess(); self.c.capture_manual(self.row())
        for remove in (True, False):
            events = copy.deepcopy(self.c.current.workflow.events)
            rows = events[-1]['snapshot']['payload']['observations']
            if remove: rows[:] = [r for r in rows if r['metric'] != assistant.METRIC]
            else: next(r for r in rows if r['metric'] == assistant.METRIC)['assistant_record']['note'] = 'Changed history'
            with self.assertRaises(ValueError): self.rehashed_document(events)

    def test_reordered_event_array_uses_chronological_replay_and_cannot_rebind_context(self):
        self.assess(); self.assess('tiredness')
        events = copy.deepcopy(self.c.current.workflow.events)
        events.reverse()
        document = self.rehashed_document(events)
        self.assertEqual(len(document['payload']['session']['payload']['events']), 3)

    def test_snapshot_rejects_misplaced_or_unknown_version_typed_payload(self):
        plan = self.assess()
        for version in (2, True, '1'):
            row = assistant.observation(plan)
            row['assistant_record']['record_version'] = version
            with self.assertRaises(ValueError): make_snapshot(self.c.subject, [row], self.c.current.at)
        row = self.row(); row.update(id='fake', phase='laptop-local', provenance='recorded', assistant_record=plan)
        with self.assertRaises(ValueError): make_snapshot(self.c.subject, [row], self.c.current.at)

    def test_forged_nested_transmission_snapshot_rejects_despite_rehashed_integrity(self):
        self.assess(); self.c.send_consultation()
        events = copy.deepcopy(self.c.current.workflow.events)
        forged = events[-1]['packet']['snapshot']
        forged['payload']['observations'][0]['assistant_record']['status'] = 'collect'
        forged['digest'] = hashlib.sha256(canonical_bytes(forged['payload'])).hexdigest()
        with self.assertRaises(ValueError):
            make_session(self.c.current.session_id, events, provenance='recorded')

    def test_bad_rehashed_open_and_cross_session_reference_preserve_current_session(self):
        self.assess()
        before = self.c.document()
        document = copy.deepcopy(before)
        body = document['payload']
        session = body['session']; payload = session['payload']
        row = payload['events'][-1]['snapshot']['payload']['observations'][0]
        row['assistant_record']['status'] = 'collect'
        snap = payload['events'][-1]['snapshot']
        snap['digest'] = hashlib.sha256(canonical_bytes(snap['payload'])).hexdigest()
        session['sha256'] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        document['sha256'] = hashlib.sha256(canonical_bytes(body)).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tampered.json'; path.write_text(json.dumps(document), encoding='utf-8')
            with self.assertRaises(ValueError): self.c.open(path)
        self.assertEqual(before, self.c.document())
        session = copy.deepcopy(before['payload']['session'])
        session['payload']['session_id'] = 'another-session'
        session['sha256'] = hashlib.sha256(canonical_bytes(session['payload'])).hexdigest()
        with self.assertRaises(ValueError): local_document(session, self.c.current.local_data)

    def test_signal_source_review_change_invalidates_existing_plan(self):
        plan = self.assess('measurement', self.negatives('measurement'))
        signal = {'subject_id': self.c.subject, 'source_id': 'actual-device-file', 'channel': 'ECG',
                  'samples': [0.0] * 32, 'sample_rate_hz': 32, 'unit': 'mV',
                  'method': 'external waveform', 'quality': 'review',
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'signal.json'; path.write_text(json.dumps(signal), encoding='utf-8')
            self.c.add_signal(path)
        self.assertTrue(self.c.assistant_records()[0]['stale'])
        with self.assertRaises(ValueError): self.complete(plan)
        second = self.assess('measurement', self.negatives('measurement'))
        self.c.request_repeat(self.c.current.local_data['signals'][0]['id'], 'Repeat requested for technical source review.')
        with self.assertRaises(ValueError): self.complete(second)
        self.assess('measurement', self.negatives('measurement'))
        report = self.c.assistant_handoff()['text']
        self.assertIn('Repeat requested for technical source review.', report)
        self.assertIn('raw waveform not transmitted', report)
        summary = copy.deepcopy(next(o for o in self.c.model()['crew']['current_snapshot']['payload']['observations']
                                     if o['metric'] == 'signal_review' and o['signal_review']['repeat_requests']))
        summary['signal_review']['repeat_requests'][0]['requested_at'] = (
            datetime.fromisoformat(summary['observed_at']) + timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            make_snapshot(self.c.subject, [summary], self.c.current.at)

    def test_record_budget_failure_is_atomic(self):
        self.assess()
        before = self.c.document()
        with patch('biotwin.health_assistant.MAX_RECORDS', 1):
            with self.assertRaises(ValueError): self.assess('tiredness')
        self.assertEqual(before, self.c.document())

    def test_raw_workflow_capture_rejects_forged_semantic_result_before_commit(self):
        snapshot = self.c.model()['crew']['current_snapshot']
        context = assistant.evidence_context(snapshot, self.c.selected, self.c.current.session_id)
        plan = assistant.assessment('headache', {}, '', context, datetime.now(timezone.utc).isoformat())
        plan['status'] = 'collect'
        row = assistant.observation(plan)
        before = copy.deepcopy(self.c.current.workflow.events)
        with self.assertRaises(ValueError):
            self.c.current.workflow.capture('raw-forged', self.c.subject, [row],
                (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat())
        self.assertEqual(before, self.c.current.workflow.events)

    def test_malformed_or_misplaced_technical_metadata_rejects_atomic_import(self):
        self.c.capture_manual(self.row())
        before = self.c.document()
        for detail in ({'repeat_requests': []}, {'signal_id': 'missing-structure'}, None):
            document = copy.deepcopy(before)
            session = document['payload']['session']
            event = session['payload']['events'][-1]
            event['snapshot']['payload']['observations'][0]['signal_review'] = detail
            event['snapshot']['digest'] = hashlib.sha256(canonical_bytes(event['snapshot']['payload'])).hexdigest()
            session['sha256'] = hashlib.sha256(canonical_bytes(session['payload'])).hexdigest()
            document['sha256'] = hashlib.sha256(canonical_bytes(document['payload'])).hexdigest()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'malformed-technical.json'; path.write_text(json.dumps(document), encoding='utf-8')
                with self.assertRaises(ValueError): self.c.open(path)
            self.assertEqual(before, self.c.document())
        self.assess()
        self.assertIn('heart_rate = 72 bpm', self.c.assistant_handoff()['text'])

    def test_doctor_local_empty_or_ordinary_capture_cannot_hide_delivered_urgent_history(self):
        plan = self.assess(answers={'shared_urgent': 'yes'})
        self.c.send_consultation(); self.c.advance()
        before = copy.deepcopy(self.c.current.workflow.events)
        self.assertEqual(self.c.assistant_handoff('doctor')['urgent_history'], [plan['id']])
        at = (datetime.fromisoformat(self.c.current.at) + timedelta(seconds=1)).isoformat()
        ordinary = self.row()
        ordinary.update(id='doctor-local-forgery', phase='laptop-local', provenance='recorded')
        for rows in ([], [ordinary]):
            with self.assertRaises(ValueError):
                self.c.current.workflow.capture('forged-doctor-reset', self.c.subject, rows, at, actor='doctor')
            self.assertEqual(before, self.c.current.workflow.events)
            self.assertEqual(self.c.assistant_handoff('doctor')['urgent_history'], [plan['id']])
            events = copy.deepcopy(before)
            events.append({'event_id': 'forged-doctor-reset', 'kind': 'local', 'actor': 'doctor', 'at': at,
                           'snapshot': make_snapshot(self.c.subject, rows, at)})
            with self.assertRaises(ValueError): make_session(self.c.current.session_id, events, provenance='recorded')

    def test_nonassistant_sessions_keep_existing_doctor_local_capture_compatibility(self):
        at = (datetime.fromisoformat(self.c.current.at) + timedelta(seconds=1)).isoformat()
        self.c.current.workflow.capture('old-doctor-empty', self.c.subject, [], at, actor='doctor')
        make_session(self.c.current.session_id, self.c.current.workflow.events, provenance='recorded')


if __name__ == '__main__': unittest.main()
