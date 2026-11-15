import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from biotwin.session import make_session, save_session, import_session, load_into, validate_session, MAX_BYTES
from biotwin.protocol import canonical_bytes
from biotwin.workflow import Workflow, complete_fixture


class Sessions(unittest.TestCase):
    def setUp(self):
        self.events = complete_fixture()['events']
        self.document = make_session('roundtrip', self.events)

    def test_atomic_roundtrip_and_deterministic_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = save_session(self.document, directory)
            self.assertEqual(import_session(path), self.document)
            initial = Path(path).read_bytes()
            save_session(self.document, directory)
            self.assertEqual(Path(path).read_bytes(), initial)
            app = Workflow()
            load_into(app, path)
            late = app.state('2026-11-14T10:05:00+00:00')
            self.assertEqual(len(late['doctor']['followups']), 1)
            self.assertEqual(app.state('2026-11-14T09:00:00+00:00')['doctor']['inbox'], [])

    def test_optional_v2_clock_roundtrip_and_unchanged_v1_default(self):
        self.assertEqual(self.document['version'],1)
        self.assertNotIn('replay_at',self.document['payload'])
        for at in ('2026-11-14T09:00:00+00:00','2026-11-14T10:45:00+00:00'):
            doc=make_session('clock-session',self.events,replay_at=at)
            self.assertEqual(doc['version'],2)
            self.assertEqual(doc['payload']['replay_at'],at)
            self.assertEqual(doc['payload']['metadata'],self.document['payload']['metadata'])
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(import_session(save_session(doc,directory)),doc)
            bad=copy.deepcopy(doc); bad['payload']['replay_at']='2026-11-14T10:46:00+00:00'
            with self.assertRaisesRegex(ValueError,'integrity'): validate_session(bad)
            for invalid in (None,True,5,'2026-11-14','x'*81):
                bad=copy.deepcopy(doc); bad['payload']['replay_at']=invalid
                bad['sha256']=hashlib.sha256(canonical_bytes(bad['payload'])).hexdigest()
                with self.assertRaises(ValueError): validate_session(bad)

    def test_tamper_preserves_live_state_and_existing_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(save_session(self.document, directory))
            original = path.read_bytes()
            app = Workflow(self.events[:4])
            before = copy.deepcopy(app.events)
            bad = copy.deepcopy(self.document)
            bad['payload']['events'][0]['snapshot']['payload']['observations'][0]['value'] += 1
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                load_into(app, path)
            self.assertEqual(app.events, before)
            with self.assertRaises(ValueError):
                save_session(self.document, directory)
            path.write_bytes(original)
            with patch('biotwin.session.os.replace', side_effect=OSError('simulated disk failure')):
                with self.assertRaises(OSError):
                    save_session(self.document, directory)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_safe_identifier_and_unrelated_collision(self):
        for identity in ('../escape', 'CON.txt', 'CON', 'COM1', 'LPT9', 'a/b', '', '.env', 'x' * 65):
            with self.assertRaises(ValueError):
                make_session(identity, self.events)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, 'roundtrip.biotwin-session.json')
            target.write_text('{"unrelated":"retain"}', encoding='utf-8')
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                save_session(self.document, directory)
            self.assertEqual(before, target.read_bytes())

    def test_size_duplicate_keys_unknown_schema_and_deep_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'bad.json')
            examples = [b'{"format":"a","format":"b"}', b'{"x":NaN}', b'[' * 30 + b'0' + b']' * 30, b'x' * (MAX_BYTES + 1)]
            for raw in examples:
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    import_session(path)
            bad = copy.deepcopy(self.document)
            bad['version'] = 3
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError):
                import_session(path)

    def test_schema_valid_digest_does_not_allow_forged_event(self):
        events = copy.deepcopy(self.events)
        events[-1]['packet']['completions'][0]['step_index'] = 1000
        with self.assertRaises(ValueError):
            make_session('forged', events)

    def test_junction_or_symlink_paths_reject_before_file_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'candidate.json')
            with patch('pathlib.Path.is_junction', return_value=True), patch('pathlib.Path.open') as opened:
                with self.assertRaises(ValueError):
                    import_session(path)
                with self.assertRaises(ValueError):
                    save_session(self.document, directory)
                opened.assert_not_called()

    def test_malformed_event_objects_rejected_cleanly(self):
        for events in ([None], [{'kind': 'local', 'actor': [], 'at': '2026-11-14T09:00:00+00:00', 'event_id': 'bad', 'snapshot': {}}]):
            with self.assertRaises(ValueError):
                make_session('bad', events)

    def test_secret_filename_case_alias_rejected_without_opening(self):
        with patch('pathlib.Path.open') as opened:
            with self.assertRaises(ValueError):
                import_session('.ENV.json')
            opened.assert_not_called()
