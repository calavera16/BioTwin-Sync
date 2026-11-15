import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from biotwin.desktop_assets import load_examples, preview
from biotwin.desktop_controller import CREWS
from biotwin.presentation import (PresentationController, PresentationError, PresentationHost,
    PUBLIC_FILES, MAX_BODY, load_assets)


class AssetsFixture:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        entries = []
        for name in PUBLIC_FILES:
            raw = b'<!doctype html><html></html>' if name == 'index.html' else b'/* bundled test asset */'
            (root/name).write_bytes(raw)
            entries.append({'file': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        (root/'manifest.json').write_text(json.dumps({'format': 'biotwin-presentation-assets', 'version': 1, 'files': entries}))
        return root

    def __exit__(self, *_args):
        self.temp.cleanup()


class PresentationStateTests(unittest.TestCase):
    def setUp(self):
        self.controller = PresentationController()

    def act(self, action, payload=None, role='crew', crew=CREWS[0], revision=None):
        return self.controller.action({'role': role, 'crew': crew, 'action': action,
            'expected_revision': self.controller.revision if revision is None else revision, 'payload': payload or {}})

    def test_four_scene_role_and_roster_isolation(self):
        earth = self.controller.state('doctor', CREWS[0])
        self.assertIsNone(earth['projection']['metric']['value'])
        self.assertFalse(earth['projection']['metric']['history'])
        self.assertFalse(earth['messages'])
        self.assertTrue(all(row['value'] is None for row in earth['crews']))
        self.act('seek', {'seconds': 1200})
        earth = self.controller.state('doctor', CREWS[0])
        self.assertEqual(earth['projection']['metric']['value'], 420)
        self.assertTrue(any(message['kind'] == 'protocol' for message in earth['messages']))
        crew = self.act('seek', {'seconds': 1800})
        earth = self.controller.state('doctor', CREWS[0])
        self.assertEqual(crew['projection']['metric']['value'], 585)
        self.assertEqual(earth['projection']['metric']['value'], 420)
        self.assertEqual([row['value'] for row in crew['crews']], [585, 600])
        self.assertEqual([row['value'] for row in earth['crews']], [420, 435])
        self.assertNotIn('585', json.dumps(earth))
        self.assertFalse(any(m['kind'] == 'protocol' for m in crew['messages']))
        self.assertFalse(crew['projection']['checklist']['steps'])
        crew = self.act('seek', {'seconds': 2400})
        self.assertEqual(crew['projection']['checklist']['branch'], 'B')
        self.assertEqual(len(crew['projection']['checklist']['steps']), 3)
        self.assertEqual(crew['projection']['checklist']['completion_count'], 0)
        self.assertEqual(self.controller.state('crew', CREWS[1])['at'], crew['at'])

    def test_interactive_workflow_is_delivered_only(self):
        self.act('start_interactive')
        self.act('capture', {'value': 600})
        self.act('send_consultation')
        self.assertIsNone(self.controller.state('doctor', CREWS[0])['projection']['metric']['value'])
        self.act('advance')
        self.assertEqual(self.controller.state('doctor', CREWS[0])['projection']['metric']['value'], 600)
        reply = {'operator': 'le', 'threshold': 500, 'original_reply': 'Review the repeated measurement.',
            'checklist_a': 'Record context', 'checklist_b': 'Check input\nRepeat measurement'}
        self.act('author_reply', reply, role='doctor')
        self.assertFalse(any(m['kind'] == 'protocol' for m in self.controller.state('crew', CREWS[0])['messages']))
        self.act('advance')
        self.act('complete', {'step': 0, 'note': 'Explicitly checked input.'})
        self.act('followup')
        self.assertFalse(self.controller.state('doctor', CREWS[0])['projection']['followups'])
        self.act('advance')
        doctor = self.controller.state('doctor', CREWS[0])
        self.assertEqual(len(doctor['projection']['followups']), 1)
        self.assertIn('Explicitly checked input.', doctor['messages'][-1]['text'])
        self.assertFalse(self.controller.state('doctor', CREWS[1])['projection']['followups'])

    def test_stale_role_and_bad_commands_never_mutate(self):
        self.act('start_interactive')
        original = self.controller.state('crew', CREWS[0])
        for action, payload, role in [('capture', {'value': 42}, 'doctor'),
                ('author_reply', {}, 'crew'), ('capture', {'value': True}, 'crew'),
                ('capture', {'value': float('nan')}, 'crew'), ('capture', {'value': 10001}, 'crew'),
                ('complete', {'step': 0, 'note': 'not yet'}, 'crew'),
                ('seek', {'seconds': 1}, 'crew'), ('advance', {'extra': 1}, 'crew'),
                ('unknown', {}, 'crew')]:
            with self.subTest(action=action, payload=payload), self.assertRaises(ValueError):
                self.act(action, payload, role=role)
            self.assertEqual(original, self.controller.state('crew', CREWS[0]))
        self.act('capture', {'value': 500})
        before = self.controller.state('crew', CREWS[0])
        with self.assertRaises(PresentationError) as raised:
            self.act('capture', {'value': 900}, revision=original['revision'])
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(before, self.controller.state('crew', CREWS[0]))

    def test_request_selection_is_serialized(self):
        self.act('seek', {'seconds': 1800})
        errors = []
        def read(role, crew, value):
            for _ in range(8):
                result = self.controller.state(role, crew)
                if result['projection']['metric']['value'] != value:
                    errors.append(result)
        threads = [threading.Thread(target=read, args=args) for args in
            [('crew', CREWS[0], 585), ('doctor', CREWS[0], 420), ('crew', CREWS[1], 600), ('doctor', CREWS[1], 435)]]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertFalse(errors)
        self.assertFalse(any(thread.is_alive() for thread in threads))

    def test_competing_commands_have_one_revision_winner(self):
        self.act('start_interactive')
        revision = self.controller.revision
        outcomes = []
        def capture(value):
            try:
                self.act('capture', {'value': value}, revision=revision)
                outcomes.append(200)
            except PresentationError as exc:
                outcomes.append(exc.status)
        threads = [threading.Thread(target=capture, args=(value,)) for value in (500, 600)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertEqual(sorted(outcomes), [200, 409])
        self.assertEqual(self.controller.revision, revision + 1)


class PresentationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AssetsFixture()
        self.assets = self.fixture.__enter__()
        self.host = PresentationHost(directory=self.assets).start()
        self.port = self.host.server.server_address[1]

    def tearDown(self):
        self.host.close()
        self.fixture.__exit__()

    def request(self, path='/api/state?role=crew&crew=Crew%20001', method='GET', body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.port, timeout=5)
        values = {'X-BioTwin-Token': self.host.token, 'Origin': self.host.origin, 'Content-Type': 'application/json'}
        values.update(headers or {})
        for key in list(values):
            if values[key] is None: del values[key]
        conn.request(method, path, body, values)
        response = conn.getresponse()
        raw = response.read()
        result = response.status, raw, dict(response.getheaders())
        conn.close()
        return result

    def command(self, action='seek', payload=None, revision=0, role='crew', **kwargs):
        body = json.dumps({'role': role, 'crew': CREWS[0], 'action': action,
            'payload': payload if payload is not None else {'seconds': 1800}, 'expected_revision': revision})
        return self.request('/api/action', 'POST', body, **kwargs)

    def test_real_transport_isolation_and_stale_rejection(self):
        status, raw, _ = self.command()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['projection']['metric']['value'], 585)
        status, raw, _ = self.request('/api/state?role=doctor&crew=Crew%20001')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['crews'][0]['value'], 420)
        self.assertNotIn(b'585', raw)
        before = self.host.controller.state('crew', CREWS[0])
        self.assertEqual(self.command()[0], 409)
        self.assertEqual(before, self.host.controller.state('crew', CREWS[0]))

    def test_auth_host_origin_and_json_boundaries(self):
        self.assertEqual(self.request(headers={'X-BioTwin-Token': None})[0], 403)
        self.assertEqual(self.request(headers={'X-BioTwin-Token': 'wrong'})[0], 403)
        for host in ['localhost:'+str(self.port), 'evil.example', '127.0.0.1:'+str(self.port)+'.evil']:
            self.assertEqual(self.request(headers={'Host': host})[0], 403)
        for origin in [None, 'null', 'https://evil.example', self.host.origin+'/']:
            self.assertEqual(self.command(headers={'Origin': origin})[0], 403)
        self.assertEqual(self.command(headers={'Content-Type': 'text/plain'})[0], 415)
        for raw in ['{', '[]', '{"role":"crew","role":"doctor"}', '{"a":NaN}', '['*3000]:
            status, response, _ = self.request('/api/action', 'POST', raw)
            self.assertEqual(status, 400)
            self.assertEqual(set(json.loads(response)), {'error'})
        self.assertEqual(self.request('/api/action', 'POST', 'x'*(MAX_BODY+1))[0], 413)
        self.assertEqual(self.host.controller.revision, 0)

    def test_static_allowlist_manifest_and_path_bounds(self):
        for name in PUBLIC_FILES:
            status, raw, headers = self.request('/'+name)
            self.assertEqual(status, 200)
            self.assertEqual(raw, (self.assets/name).read_bytes())
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path in ['/manifest.json', '/.env', '/../README.md', '/%2e%2e/README.md', '//evil.example/x', '/app.js?x=1', '/app.js/extra']:
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request('/'+'a'*2050)[0], 414)
        (self.assets/'app.js').write_bytes(b'tampered')
        with self.assertRaises(ValueError): load_assets(self.assets)

    def test_real_source_identity_and_bounds(self):
        for example in load_examples():
            status, raw, _ = self.request('/api/source?example='+example['file']+'&start=0')
            source = json.loads(raw)
            window = preview(example, example['times'][0], duration=10, max_points=900)
            self.assertEqual(status, 200)
            self.assertEqual(source['source_key'], example['source_key'])
            self.assertEqual(source['doi'], example['doi'])
            self.assertEqual(source['points'], [{'t': p['source_seconds'], 'value': p['value']} for p in window['points']])
            self.assertLessEqual(len(source['points']), 900)
            self.assertIn('another participant', source['context_label'])
        for query in ['example=../secret', 'example=crew001_ecg.json&start=NaN',
                'example=crew001_ecg.json&start=-1', 'example=crew001_ecg.json&start=999999',
                'example=crew001_ecg.json&extra=1', 'example=crew001_ecg.json&example=crew002_ecg.json']:
            self.assertEqual(self.request('/api/source?'+query)[0], 400)

    def test_shutdown_repeated_start_and_new_token(self):
        self.assertIs(self.host.start(), self.host)
        token, thread = self.host.token, self.host.thread
        self.host.close()
        self.host.close()
        self.assertFalse(thread.is_alive())
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', self.port), timeout=1)
        self.host.start()
        self.assertNotEqual(token, self.host.token)


if __name__ == '__main__':
    unittest.main()
