import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from biotwin import local_monitoring as local
from biotwin.desktop_controller import CREWS, DesktopController
from biotwin.presentation import PresentationController, PresentationError, MAX_BODY
from biotwin.browser_workspace import MAX_IMPORT_BODY, import_text
from biotwin.workflow import Workflow
from tests import test_presentation as presentation_tests


def reaction():
    return {'task_id': 'browser-simple-reaction-v1', 'value': 250, 'unit': 'ms',
            'quality': 'usable', 'trials': [{'outcome': 'response', 'reaction_ms': 250} for _ in range(5)],
            'valid_responses': 5, 'false_starts': 0, 'omissions': 0,
            'configuration': {'trials': 5, 'foreperiod_seconds': [1, 3], 'response_limit_seconds': 2,
                              'minimum_response_ms': 100, 'input_mode': 'keyboard-space'},
            'timing_clock': 'performance.now', 'focus_visibility_guard': True, 'cancelled': False}


class BrowserWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.c = PresentationController()

    def state(self, role='crew', crew=CREWS[0]):
        return self.c.state(role, crew)

    def act(self, action, payload=None, role='crew', crew=CREWS[0], **changes):
        state = self.state(role, crew)
        body = {'role': role, 'crew': crew, 'action': action, 'payload': payload or {},
                'expected_revision': state['revision'], 'expected_session_id': state['session_id'],
                'expected_digest': state['local']['snapshot_digest'] if state['local'] else
                    self.c._controller.sessions[crew].workflow.state(state['at'])[role]['current_snapshot']['digest']
                    if self.c._controller.sessions[crew].workflow.state(state['at'])[role]['current_snapshot'] else None}
        body.update(changes)
        return self.c.action(body)

    def row(self, **changes):
        return {'subject_id': 'LOCAL:crew-001', 'metric': 'heart_rate', 'value': 72, 'unit': 'bpm',
                'observed_at': datetime.now(timezone.utc).isoformat(), 'source_id': 'instrument',
                'method': 'operator device reading', 'quality': 'usable', **changes}

    def test_recorded_capture_consultation_reply_checklist_followup_roundtrip(self):
        state = self.act('start_local')
        self.assertEqual(state['local']['observations'], [])
        self.act('capture_manual', {'row': self.row()})
        self.act('capture_reaction', {'summary': reaction()})
        self.act('send_consultation')
        self.assertFalse(self.state('doctor')['local']['observations'])
        self.act('advance')
        self.assertEqual(len(self.state('doctor')['local']['observations']), 2)
        self.act('author_reply', {'operator': 'le', 'threshold': 80, 'metric': 'heart_rate',
                 'original_reply': 'Verify context.', 'checklist_a': 'Repeat acquisition',
                 'checklist_b': 'Review source'}, role='doctor')
        self.act('advance')
        self.act('complete', {'step': 0, 'note': 'Repeated explicitly.'})
        self.act('followup')
        self.assertFalse(self.state('doctor')['projection']['followups'])
        self.act('advance')
        self.assertEqual(len(self.state('doctor')['projection']['followups']), 1)
        state = self.state()
        exported = self.c.read_bound('crew', CREWS[0], state['session_id'], state['revision'])
        before = json.loads(exported['text'])
        self.act('start_local')
        self.act('import_session', exported)
        after = self.c._controller.document()
        self.assertEqual(before, after)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / exported['filename']
            path.write_text(exported['text'], encoding='utf-8')
            native = DesktopController()
            native.open(path)
            self.assertEqual(native.document(), before)
            captured = before['payload']['session']['payload']['events'][2]['snapshot']['payload']['observations']
            self.assertIn('Browser performance.now', next(r for r in captured if r['metric'] == 'reaction_time')['note'])

    def test_pending_packets_and_eventless_advance_restore_exact_visible_state(self):
        self.act('start_local')
        self.act('capture_manual', {'row': self.row()})
        other = self.state(crew=CREWS[1])

        def roundtrip():
            before = {role: self.state(role) for role in ('crew','doctor')}
            state = before['crew']
            exported = self.c.read_bound('crew', CREWS[0], state['session_id'], state['revision'])
            self.assertEqual(json.loads(exported['text'])['version'], 2)
            self.act('start_local')
            self.act('import_session', exported)
            for role in before:
                after = self.state(role)
                for key in ('at','projection','transport','messages','actions','snapshot_digest'):
                    self.assertEqual(after[key], before[role][key], (role,key))
            after_other = self.state(crew=CREWS[1])
            for key in ('at','projection','session_id'):
                self.assertEqual(after_other[key],other[key])

        self.act('send_consultation'); roundtrip()
        self.assertEqual(self.state()['transport']['seconds_until_arrival'],1200)
        self.assertEqual(self.state('doctor')['local']['observations'],[])
        self.act('advance')
        self.act('author_reply', {'operator':'le','threshold':80,'metric':'heart_rate',
            'original_reply':'Review exact source.','checklist_a':'Repeat acquisition','checklist_b':'Review changed evidence'},role='doctor')
        roundtrip()
        self.assertIsNone(self.state()['projection']['checklist']['branch'])
        self.act('advance')
        self.act('complete',{'step':0,'note':'Operator explicitly repeated acquisition.'})
        self.act('followup'); roundtrip()
        self.assertFalse(self.state('doctor')['projection']['followups'])
        self.act('advance'); self.act('advance')
        at = self.state()['at']; roundtrip()
        self.assertEqual(self.state()['at'],at)

    def test_hr_dependency_projection_and_explicit_historical_followup(self):
        self.act('start_local'); self.act('capture_manual', {'row':self.row()})
        self.act('send_consultation'); self.act('advance')
        self.act('author_reply',{'operator':'le','threshold':80,'metric':'heart_rate',
            'original_reply':'Verify the instrument reading.','checklist_a':'Repeat acquisition',
            'checklist_b':'Confirm changed source\nRecord supported repeat'},role='doctor')
        self.act('advance'); self.act('complete',{'step':0,'note':'Completed against HR72.'})
        self.act('capture_manual',{'row':self.row(value=90)})
        state=self.state(); p=state['projection']; checklist=p['checklist']
        self.assertEqual(p['status'],'Checklist ready')
        self.assertEqual(p['physiological_status'],'Measurement needed')
        self.assertIsNone(p['metric']['value']); self.assertIsNone(p['metric']['reference']['mean'])
        self.assertEqual(checklist['branch'],'B'); self.assertEqual(checklist['completed_steps'],[])
        self.assertEqual(checklist['current_completion_count'],0)
        self.assertEqual(checklist['historical_completion_count'],1)
        dep=p['protocol_evidence']['dependencies'][0]
        self.assertEqual(dep['metric'],'heart_rate'); self.assertEqual(dep['unit'],'bpm')
        self.assertEqual(dep['reviewed']['value'],72); self.assertEqual(dep['current']['value'],90)
        self.assertEqual(dep['current']['method'],'operator device reading')
        self.assertTrue(dep['current']['source_id'].startswith('LOCAL:'))
        self.assertTrue(dep['current']['usable']); self.assertTrue(dep['reviewed']['usable'])
        self.assertTrue(checklist['followup_context']['requires_confirmation'])
        before=self.state()
        with self.assertRaises(PresentationError): self.act('followup')
        self.assertEqual(self.state(),before)
        self.act('followup',{'confirm_historical':True}); self.act('advance')
        received=self.state('doctor')['projection']['followups'][0]
        self.assertEqual(received['completions'][0]['note'],'Completed against HR72.')
        self.assertEqual(self.state()['projection']['checklist']['current_completion_count'],0)

    def test_incompatible_and_missing_dependency_values_remain_explicit_unknown(self):
        for override,reason in (({'source_id':'changed instrument'},'incompatible dependency'),
                                ({'value':None,'quality':'missing'},'unusable dependency')):
            with self.subTest(override=override):
                self.act('start_local'); self.act('capture_manual',{'row':self.row()})
                self.act('send_consultation'); self.act('advance')
                self.act('author_reply',{'operator':'le','threshold':80,'metric':'heart_rate',
                    'original_reply':'Check exact source.','checklist_a':'Repeat','checklist_b':'Review'},role='doctor')
                self.act('capture_manual',{'row':self.row(**override)}); self.act('advance')
                p=self.state()['projection']; dep=p['protocol_evidence']['dependencies'][0]
                self.assertEqual(p['checklist']['branch'],'UNKNOWN')
                self.assertEqual(p['status'],'Measurement needed')
                self.assertFalse(dep['current']['usable']); self.assertEqual(dep['current']['reason'],reason)
                self.assertEqual(dep['reviewed']['value'],72)

    def test_followup_confirmation_forecasts_protocol_expiry_at_send_time(self):
        self.act('start_local'); self.act('capture_manual',{'row':self.row()})
        self.act('send_consultation'); self.act('advance')
        self.act('author_reply',{'operator':'le','threshold':80,'metric':'heart_rate',
            'original_reply':'Verify acquisition.','checklist_a':'Repeat','checklist_b':'Review'},role='doctor')
        self.act('advance'); self.act('complete',{'step':0,'note':'Explicit completion while protocol valid.'})
        controller=self.c._controller; events=copy.deepcopy(controller.current.workflow.events)
        protocol=next(e['packet']['protocol'] for e in events if e['kind']=='packet' and e['packet']['type']=='protocol')
        protocol['valid_until']=(datetime.fromisoformat(controller.current.at)+timedelta(seconds=30)).isoformat()
        controller.current.workflow=Workflow(events)
        before=self.state(); checklist=before['projection']['checklist']
        self.assertEqual(checklist['current_completion_count'],1)
        self.assertEqual(checklist['followup_context']['basis'],'historical')
        self.assertTrue(checklist['followup_context']['requires_confirmation'])
        self.assertIn('send time',checklist['followup_context']['message'])
        with self.assertRaises(PresentationError) as raised: self.act('followup')
        self.assertIn('historical',str(raised.exception)); self.assertEqual(raised.exception.status,409)
        self.assertEqual(self.state(),before)
        with self.assertRaisesRegex(ValueError,'historical'): controller.followup()
        self.assertEqual(self.state(),before)
        after=self.act('followup',{'confirm_historical':True})
        self.assertEqual(after['projection']['checklist']['current_completion_count'],0)
        self.assertEqual(after['projection']['checklist']['historical_completion_count'],1)

    def test_actual_capacity_boundary_retains_export_and_reports_warning(self):
        self.act('start_local')
        rows=[self.row(id=f'bounded-{i:03}') for i in range(480)]
        self.act('import_observations',{'filename':'bounded.json','text':json.dumps(rows)})
        caught=False
        for i in range(21):
            before=self.state()
            try:
                self.act('capture_manual',{'row':self.row(id=f'append-{i}')})
            except local.StorageCapacityError as exc:
                self.assertIn('Export',str(exc)); caught=True
                self.assertEqual(self.state(),before)
                self.assertTrue(before['local']['storage']['warning'])
                self.assertLess(len(before['local']['observations']),500)
                exported=self.c.read_bound('crew',CREWS[0],before['session_id'],before['revision'])
                self.assertLessEqual(len(exported['text'].encode('utf-8')),local.MAX_BYTES)
                break
        self.assertTrue(caught)

    def test_import_csv_json_and_failure_atomicity_and_secret_names(self):
        self.act('start_local')
        self.act('import_observations', {'filename': 'data.json', 'text': json.dumps([self.row()])})
        row = self.row(metric='spo2', value=97, unit='%')
        csv = ','.join(row) + '\n' + ','.join(str(v) for v in row.values()) + '\n'
        self.act('import_observations', {'filename': 'data.csv', 'text': csv})
        before = self.state()
        for name, text in [('.env', 'forbidden'), ('.ENV.keys.json', 'forbidden'),
                           ('C:\\data.json', '[]'), ('../data.json', '[]'),
                           ('data.json', json.dumps([self.row(unit='Hz')])),
                           ('data.json', 'x' * (local.MAX_BYTES + 1)),
                           ('data.json', 'é' * (local.MAX_BYTES // 2 + 1))]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.act('import_observations', {'filename': name, 'text': text})
            self.assertEqual(self.state(), before)
        with patch('biotwin.browser_workspace.tempfile.TemporaryDirectory') as temporary:
            with self.assertRaises(ValueError):
                import_text(self.c._controller, 'import_session', '.env.json', None)
            temporary.assert_not_called()

    def test_signal_inspection_review_repeat_and_doctor_isolation(self):
        self.act('start_local')
        signal = {'subject_id': 'LOCAL:crew-001', 'channel': 'ECG', 'samples': [1, None] * 500,
                  'sample_rate_hz': 100, 'unit': 'mV', 'method': 'external recorder',
                  'source_id': 'independent', 'quality': 'review',
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        state = self.act('import_signal', {'filename': 'source.json', 'text': json.dumps(signal)})
        sid = state['local']['signals'][0]['id']
        window = self.c.read_bound('crew', CREWS[0], state['session_id'], state['revision'], signal_id=sid)
        self.assertEqual(len(window['points']), 900)
        self.assertIsNone(window['points'][1]['value'])
        self.act('mark_segment', {'signal_id': sid, 'start': 0, 'end': 100, 'note': 'Missing intervals.'})
        state = self.act('request_repeat', {'signal_id': sid, 'reason': 'Acquire another segment.'})
        request_id = state['local']['repeat_requests'][0]['id']
        self.act('resolve_repeat', {'request_id': request_id, 'note': 'Operator recorded resolution.'})
        doctor = self.state('doctor')
        self.assertFalse(doctor['local']['observations'])
        self.assertNotIn(sid, json.dumps(doctor))
        self.act('send_consultation')
        self.act('advance')
        doctor = self.state('doctor')
        self.assertTrue(doctor['local']['observations'])
        self.assertFalse(doctor['local']['signals'])
        self.assertFalse(doctor['local']['segments'])
        self.assertFalse(doctor['local']['repeat_requests'])
        for role in ('doctor',):
            with self.assertRaises(PresentationError):
                self.c.read_bound(role, CREWS[0], doctor['session_id'], doctor['revision'], signal_id=sid)

    def test_assistant_preview_save_action_and_delivered_handoff(self):
        self.act('start_local')
        state = self.state()
        path = state['local']['assistant']['catalog']['protocols'][0]
        answers = {q['id']: 'no' for q in path['questions']}
        payload = {'protocol_id': path['id'], 'answers': answers, 'note': 'Private intake text.',
                   'context': state['local']['assistant']['context']}
        unknown = self.act('assistant_preview', {**payload, 'answers': {}})
        self.assertEqual(unknown['preview']['status'], 'clarify')
        urgent = next(q['id'] for q in path['questions'] if q['kind'] == 'urgent')
        urgent_preview = self.act('assistant_preview', {**payload, 'answers': {**answers, urgent: 'yes'}})
        self.assertEqual(urgent_preview['preview']['status'], 'urgent')
        preview = self.act('assistant_preview', payload)
        self.assertEqual(preview['state']['revision'], state['revision'])
        state = self.act('assistant_assess', payload)
        plan = state['local']['assistant']['records'][0]
        self.assertNotIn('Private intake text.', json.dumps(self.state('doctor')))
        state = self.act('assistant_complete', {'assessment_id': plan['id'], 'step_id': plan['steps'][0]['id'],
                         'note': 'Recorded task explicitly.', 'context': state['local']['assistant']['context']})
        self.assertEqual(len(state['local']['assistant']['records']), 2)
        self.act('send_consultation')
        self.assertFalse(self.state('doctor')['local']['assistant']['records'])
        self.act('advance')
        doctor = self.state('doctor')['local']['assistant']
        self.assertEqual(len(doctor['records']), 2)
        self.assertTrue(doctor['handoff']['received_view_only'])
        self.assertIn('Private intake text.', doctor['handoff']['text'])
        self.assertIsNone(doctor['context'])
        with self.assertRaises(ValueError):
            self.act('assistant_preview', payload)

    def test_wrong_crew_and_tampered_session_imports_are_atomic(self):
        self.act('start_local')
        self.act('capture_manual', {'row': self.row()})
        state = self.state()
        exported = self.c.read_bound('crew', CREWS[0], state['session_id'], state['revision'])
        self.act('start_local', crew=CREWS[1])
        before = self.state(crew=CREWS[1])
        with self.assertRaises(ValueError):
            self.act('import_session', exported, crew=CREWS[1])
        self.assertEqual(before, self.state(crew=CREWS[1]))
        bad = json.loads(exported['text'])
        bad['sha256'] = '0' * 64
        before = self.state()
        with self.assertRaises(ValueError):
            self.act('import_session', {'filename': exported['filename'], 'text': json.dumps(bad)})
        self.assertEqual(before, self.state())

    def test_stale_bindings_role_guards_and_candidate_rollback(self):
        self.act('start_local')
        state = self.state()
        for changes in ({'expected_session_id': 'other'}, {'expected_digest': 'other'}, {'expected_revision': 0}):
            with self.assertRaises(PresentationError) as raised:
                self.act('capture_manual', {'row': self.row()}, **changes)
            self.assertEqual(raised.exception.status, 409)
        for action in ('start_local', 'capture_manual', 'assistant_preview', 'import_session'):
            with self.assertRaises(PresentationError) as raised:
                self.act(action, role='doctor')
            self.assertEqual(raised.exception.status, 403)
        with patch('biotwin.presentation.local_state', side_effect=ValueError('projection failure')):
            with self.assertRaises(ValueError):
                self.c.action({'role': 'crew', 'crew': CREWS[0], 'action': 'capture_manual',
                              'payload': {'row': self.row()}, 'expected_revision': state['revision'],
                              'expected_session_id': state['session_id'], 'expected_digest': state['local']['snapshot_digest']})
        self.assertEqual(state, self.state())
        self.act('start_local', crew=CREWS[1])
        self.act('start_interactive')
        self.assertEqual(self.state(crew=CREWS[1])['mode'], 'local')
        self.act('reset_guided')
        self.act('seek', {'seconds': 1800})
        self.assertEqual(self.state()['projection']['metric']['value'], 585)
        self.assertEqual(self.state(crew=CREWS[1])['mode'], 'local')

    def test_browser_reaction_method_metadata_and_import_validation(self):
        self.act('start_local')
        before = self.state()
        for changes in ({'task_id': 'laptop-simple-reaction-v1'}, {'timing_clock': 'Date.now'},
                        {'focus_visibility_guard': False}, {'cancelled': True}, {'value': 999}):
            summary = {**reaction(), **changes}
            with self.assertRaises(ValueError):
                self.act('capture_reaction', {'summary': summary})
            self.assertEqual(before, self.state())
        self.act('capture_reaction', {'summary': reaction()})
        row = self.state()['local']['observations'][0]
        for changes in ({'method': 'laptop-simple-reaction-v1'}, {'source_id': 'LOCAL:any:browser-simple-reaction-v1'}):
            with self.assertRaises(ValueError):
                DesktopController._validate_reaction({**row, **changes})
        self.assertEqual(row['method'], 'browser-simple-reaction-v1')
        self.assertNotEqual(row['method'], 'laptop-simple-reaction-v1')


class BrowserWorkspaceHTTPTests(presentation_tests.PresentationHTTPTests):
    def envelope(self, action, payload=None, role='crew'):
        state = self.host.controller.state(role, CREWS[0])
        snapshot = self.host.controller._controller.sessions[CREWS[0]].workflow.state(state['at'])[role]['current_snapshot']
        return {'role': role, 'crew': CREWS[0], 'action': action, 'payload': payload or {},
                'expected_revision': state['revision'], 'expected_session_id': state['session_id'],
                'expected_digest': snapshot['digest'] if snapshot else None}

    def test_http_import_route_limits_and_sensitive_ancillary_routes(self):
        status, raw, _ = self.request('/api/action', 'POST', json.dumps(self.envelope('start_local')))
        self.assertEqual(status, 200)
        state = json.loads(raw)
        query = {'role': 'crew', 'crew': CREWS[0], 'session_id': state['session_id'], 'revision': state['revision']}
        self.assertEqual(self.request('/api/export?' + urlencode(query))[0], 200)
        query['role'] = 'doctor'
        self.assertEqual(self.request('/api/export?' + urlencode(query))[0], 403)
        query['role'] = 'crew'; query['revision'] = 0
        self.assertEqual(self.request('/api/export?' + urlencode(query))[0], 409)
        envelope = self.envelope('import_observations', {'filename': 'rows.json', 'text': '[]'})
        self.assertEqual(self.request('/api/action', 'POST', json.dumps(envelope))[0], 400)
        self.assertEqual(self.request('/api/import', 'POST', json.dumps(self.envelope('advance')))[0], 400)
        envelope['payload']['text'] = ' ' * (MAX_BODY + 1)
        self.assertEqual(self.request('/api/import', 'POST', json.dumps(envelope))[0], 400)
        row = {'subject_id': 'LOCAL:crew-001', 'metric': 'sleep_quality', 'value': 5, 'unit': 'score/10',
               'observed_at': datetime.now(timezone.utc).isoformat(), 'source_id': 'diary',
               'method': 'self-report', 'quality': 'review', 'note': 'x' * 1000}
        envelope = self.envelope('import_observations', {'filename': 'diary.json', 'text': json.dumps([row] * 20)})
        encoded = json.dumps(envelope)
        self.assertGreater(len(encoded), MAX_BODY)
        self.assertEqual(self.request('/api/import', 'POST', encoded)[0], 200)
        self.assertGreater(MAX_IMPORT_BODY, 6 * local.MAX_BYTES)
        self.assertEqual(self.request(headers={'Origin': 'https://evil.example'})[0], 403)

    def test_http_capacity_error_is_actionable_and_session_remains_exportable(self):
        status,_,_=self.request('/api/action','POST',json.dumps(self.envelope('start_local')))
        self.assertEqual(status,200)
        before=self.host.controller.state('crew',CREWS[0])
        row={'subject_id':'LOCAL:crew-001','metric':'heart_rate','value':72,'unit':'bpm',
            'observed_at':datetime.now(timezone.utc).isoformat(),'source_id':'instrument',
            'method':'operator reading','quality':'usable'}
        with patch('biotwin.local_monitoring.MAX_BYTES',before['local']['storage']['bytes']):
            status,raw,_=self.request('/api/action','POST',json.dumps(self.envelope('capture_manual',{'row':row})))
        self.assertEqual(status,507)
        error=json.loads(raw)
        self.assertEqual(error['code'],'storage_capacity'); self.assertIn('Export',error['error'])
        self.assertTrue(error['draft_retained'])
        self.assertEqual(self.host.controller.state('crew',CREWS[0]),before)
        query={'role':'crew','crew':CREWS[0],'session_id':before['session_id'],'revision':before['revision']}
        self.assertEqual(self.request('/api/export?'+urlencode(query))[0],200)


if __name__ == '__main__':
    unittest.main()
