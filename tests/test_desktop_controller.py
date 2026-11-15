import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from biotwin.desktop_controller import CREWS, STOPS, SUBJECTS, DesktopController, local_nlsp_report
from biotwin.data import WINSCAT_METRICS
from biotwin.protocol import make_snapshot
from biotwin.session import make_session, save_session


class DesktopControllerTests(unittest.TestCase):
    def test_guided_replay_is_role_isolated_and_rewindable(self):
        c = DesktopController()
        self.assertIsNone(c.model()['doctor']['current_snapshot'])
        c.seek(2)
        m = c.model()
        self.assertEqual(max(m['crew']['current_snapshot']['payload']['observations'], key=lambda o: o['observed_at'])['value'], 585)
        self.assertEqual(max(m['doctor']['current_snapshot']['payload']['observations'], key=lambda o: o['observed_at'])['value'], 420)
        self.assertIsNone(m['crew']['protocol'])
        c.seek(4)
        self.assertEqual(len(c.model()['crew']['completions']), 3)
        c.seek(5)
        self.assertTrue(all(row['stale_context'] for row in c.model()['crew']['completions']))
        c.seek(6)
        self.assertFalse(c.model()['doctor']['followups'])
        c.step()
        self.assertEqual(len(c.model()['doctor']['followups']), 1)
        c.seek(0)
        self.assertFalse(c.model()['crew']['completions'])
        self.assertIsNone(c.model()['doctor']['current_snapshot'])

    def test_independent_crews(self):
        c = DesktopController()
        c.seek(7)
        c.select(CREWS[1])
        self.assertEqual(c.current.stop, 0)
        self.assertEqual(c.model()['crew']['current_snapshot']['payload']['subject_id'], SUBJECTS[CREWS[1]])
        self.assertEqual(c.model()['crew']['current_snapshot']['payload']['observations'][-1]['value'], 435)
        c.select(CREWS[0])
        self.assertEqual(c.current.stop, 7)

    def test_complete_operator_workflow(self):
        c = DesktopController()
        c.reset(interactive=True)
        with self.assertRaises(ValueError):
            c.author_reply('le', 450, 'Review', 'Acquire', 'Repeat')
        c.capture(585)
        c.send_consultation()
        self.assertIsNone(c.model()['doctor']['current_snapshot'])
        c.advance()
        c.author_reply('le', 450, 'Confirm acquisition conditions.', 'Confirm task', 'Repeat the same measurement\nRecord context')
        self.assertIsNone(c.model()['crew']['evaluation'])
        c.advance()
        self.assertEqual(c.model()['crew']['evaluation']['outcome'], 'B')
        self.assertFalse(c.model()['crew']['completions'])
        c.complete(0, 'Operator repeated the task explicitly.')
        with self.assertRaises(ValueError):
            c.complete(0, 'Duplicate')
        c.followup()
        self.assertFalse(c.model()['doctor']['followups'])
        c.advance()
        self.assertEqual(len(c.model()['doctor']['followups']), 1)

    def test_failed_action_preserves_state_and_clock(self):
        c = DesktopController()
        c.reset(interactive=True)
        before = copy.deepcopy(c.current)
        for value in ('nan', '-1', '10001'):
            with self.assertRaises(ValueError):
                c.capture(value)
            self.assertEqual(c.current.at, before.at)
            self.assertEqual(c.current.workflow.events, before.workflow.events)

    def test_validated_roundtrip_and_other_crew_import_rejection(self):
        c = DesktopController()
        c.seek(len(STOPS)-1)
        expected = c.document()
        with tempfile.TemporaryDirectory() as directory:
            path = c.save(directory)
            c.open(path)
            self.assertEqual(expected, c.document())
            c.select(CREWS[1])
            old = c.document()
            with self.assertRaises(ValueError):
                c.open(path)
            self.assertEqual(c.document(), old)
            broken = Path(directory)/'broken.json'
            broken.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                c.open(broken)
            self.assertEqual(c.document(), old)

    def test_synthetic_v2_guided_pending_and_eventless_advance_keep_exact_clock(self):
        c=DesktopController()
        def roundtrip():
            at=c.current.at; before=c.model(); doc=c.document()
            self.assertEqual(doc['version'],2)
            with tempfile.TemporaryDirectory() as directory: c.open(c.save(directory))
            self.assertEqual(c.current.at,at); self.assertEqual(c.model(),before)
            self.assertEqual(c.document(),doc); self.assertEqual(c.current.clock_basis,'saved')
        roundtrip()  # Future guided events must remain invisible at 09:00.
        c.reset(interactive=True); c.send_consultation(); roundtrip()
        self.assertIsNone(c.model()['doctor']['current_snapshot'])
        c.advance(); c.advance(); roundtrip()  # No event at either advanced cutoff.

    def test_saved_early_cutoff_does_not_hide_invalid_future_synthetic_context(self):
        c=DesktopController(); before=c.document(); events=copy.deepcopy(c.current.workflow.events)
        changed=events[3]['snapshot']['payload']
        events[3]['snapshot']=make_snapshot(changed['subject_id'],
            [{**o,'source_id':'SYNTHETIC:unsupported'} for o in changed['observations']],changed['created_at'])
        # Truncate dependent completions, which would otherwise bind the old digest.
        doc=make_session('future-context',events[:4],replay_at='2026-11-14T09:00:00+00:00')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): c.open(save_session(doc,directory))
        self.assertEqual(c.document(),before)

    def test_offset_timestamps_choose_actual_latest_instant(self):
        c = DesktopController()
        # 18:00+09 is earlier than 09:45+00 although its string sorts later.
        events = copy.deepcopy(c.current.workflow.events)
        events[0]['at'] = '2026-11-14T18:00:00+09:00'
        doc = make_session('offset-session', events, label='Offset test', provenance='synthetic')
        with tempfile.TemporaryDirectory() as directory:
            c.open(save_session(doc, directory))
        self.assertEqual(c.current.at, '2026-11-14T09:45:00+00:00')
        self.assertEqual(c.current.clock_basis, 'inferred')
        self.assertEqual(len(c.model()['doctor']['followups']), 0)

    def test_unsupported_desktop_import_preserves_current_state(self):
        c = DesktopController()
        original = c.document()
        base = c.current.workflow.events[0]
        for observations in ([], [{**base['snapshot']['payload']['observations'][0], 'value': None}],
                             [{**base['snapshot']['payload']['observations'][0], 'metric': 'foreign', 'unit': 'unknown'}]):
            event = {**base, 'snapshot': make_snapshot(SUBJECTS[CREWS[0]], observations, base['at'])}
            document = make_session('unsupported-desktop', [event], provenance='synthetic')
            with tempfile.TemporaryDirectory() as directory:
                path = save_session(document, directory)
                with self.assertRaises(ValueError):
                    c.open(path)
            self.assertEqual(c.document(), original)

    def test_winscat_report_does_not_require_or_open_bedrest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root/'winscat_campaign3'
            folder.mkdir()
            fields = ['ID', 'Campaign', 'Subject', 'Test', 'Test_Phase', 'BR_Day', 'Date', 'Time', *WINSCAT_METRICS]
            with (folder/'MR085G_Campaign_3_WinSCAT_C3F_0001.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for i in range(6):
                    writer.writerow({'ID': str(i), 'Campaign': 'C3F', 'Subject': '1', 'Test': 'Nasa1',
                        'Test_Phase': 'IN_TEST', 'BR_Day': str(i), 'Date': f'01/0{i+1}/2026', 'Time': '09:00:00',
                        **{metric: 400+i for metric in WINSCAT_METRICS}})
            report = local_nlsp_report(root)
            self.assertEqual(report['baseline_coverage']['eligible_assessment_visits'], 1)
            bedrest = root/'bedrest0543_campaign3'
            bedrest.mkdir()
            (bedrest/'malformed.csv').write_bytes(b'not a valid source')
            self.assertEqual(local_nlsp_report(root), report)
            with (folder/'oversized.csv').open('wb') as stream:
                stream.truncate(256001)
            with self.assertRaises(ValueError):
                local_nlsp_report(root)


if __name__ == '__main__':
    unittest.main()
