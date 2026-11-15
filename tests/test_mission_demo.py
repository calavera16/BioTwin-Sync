import unittest
import copy
from biotwin.mission_demo import mission_proof
from biotwin.protocol import evaluate_protocol, _aware
from biotwin.desktop_controller import DesktopController

class MissionDemoTests(unittest.TestCase):
    def test_examples_use_one_actual_protocol_and_fail_closed_on_age(self):
        examples = mission_proof()['examples']
        before = copy.deepcopy(examples)
        protocol = examples['protocol']
        self.assertEqual([c['evaluation']['outcome'] for c in examples['cases']], ['A', 'B', 'UNKNOWN'])
        for case in examples['cases']:
            actual = evaluate_protocol(protocol, examples['reviewed_snapshot'], case['current_snapshot'], examples['evaluated_at'])
            self.assertEqual(case['evaluation'], actual)
            self.assertEqual(actual['checklist'], protocol['checklists'][actual['outcome']])
            row = case['current_snapshot']['payload']['observations'][0]
            self.assertEqual(case['measurement_age_seconds'], (_aware(examples['evaluated_at'])-_aware(row['observed_at'])).total_seconds())
        fresh, stale = examples['cases'][1:]
        self.assertEqual(fresh['evidence']['dependencies'][0]['current']['value'], stale['evidence']['dependencies'][0]['current']['value'])
        self.assertTrue(stale['evidence']['dependencies'][0]['current']['compatible'])
        self.assertFalse(stale['evidence']['dependencies'][0]['current']['usable'])
        self.assertGreater(stale['measurement_age_seconds'], protocol['freshness_seconds'])
        self.assertIn('current: stale dependency reaction_time', stale['evaluation']['reasons'])
        self.assertEqual(examples, before)

    def test_examples_do_not_modify_an_active_session_or_share_mutable_cases(self):
        controller = DesktopController()
        controller.start_local()
        before = copy.deepcopy(controller.current.workflow.events)
        proof = mission_proof()
        self.assertEqual(controller.current.workflow.events, before)
        proof['examples']['cases'][0]['current_snapshot']['payload']['observations'][0]['value'] = 999
        self.assertEqual(proof['examples']['cases'][1]['current_snapshot']['payload']['observations'][0]['value'], 585)
        self.assertEqual(mission_proof()['examples']['cases'][0]['current_snapshot']['payload']['observations'][0]['value'], 420)

    def test_delivered_role_scope_and_actual_branch(self):
        proof = mission_proof()
        self.assertEqual(proof, mission_proof())
        phases = proof['phases']
        self.assertIsNone(phases[0]['doctor']['metric']['value'])
        self.assertEqual([p['crew']['metric']['value'] for p in phases], [420,420,585,585])
        self.assertEqual([p['doctor']['metric']['value'] for p in phases], [None,420,420,420])
        self.assertIsNone(phases[2]['crew']['checklist']['branch'])
        final = phases[-1]['crew']
        self.assertEqual(final['checklist']['branch'], 'B')
        self.assertEqual(len(final['checklist']['steps']),3)
        dep = final['protocol_evidence']['dependencies'][0]
        self.assertEqual(dep['reviewed']['value'],420)
        self.assertEqual(dep['current']['value'],585)
        self.assertTrue(dep['current']['compatible'])
        self.assertTrue(dep['current']['usable'])
