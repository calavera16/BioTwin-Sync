import copy
import unittest

from biotwin.desktop_controller import DesktopController, instant
from biotwin.mission import mission_projection, project_events, project_role
from biotwin.protocol import make_snapshot, evaluate_protocol
from biotwin.simulation import synthetic_fixture
from biotwin.statistics import reference_summary


class MissionTests(unittest.TestCase):
    def test_baseline_change_and_arrival_are_causal(self):
        controller = DesktopController()
        baseline = mission_projection(controller.model())
        self.assertEqual(baseline['crew']['status'], 'Monitoring')
        self.assertEqual(baseline['crew']['metric']['value'], 420)
        self.assertEqual(baseline['crew']['metric']['reference']['mean'], 420)
        self.assertIsNone(baseline['doctor']['metric']['value'])
        self.assertEqual(baseline['doctor']['status'], 'Measurement needed')
        self.assertEqual(baseline['communication']['seconds_until_arrival'], 1200)
        controller.seek(2)
        changed = mission_projection(controller.model())
        self.assertEqual(changed['crew']['status'], 'Change detected')
        self.assertTrue(changed['crew']['metric']['change']['flagged'])
        self.assertEqual(changed['crew']['metric']['value'], 585)
        self.assertEqual(changed['doctor']['metric']['value'], 420)
        self.assertIsNone(changed['crew']['reviewed']['value'])
        self.assertEqual(changed['communication']['seconds_until_arrival'], 600)
        controller.seek(3)
        arrived = mission_projection(controller.model())
        self.assertEqual(arrived['crew']['status'], 'Checklist ready')
        self.assertEqual(arrived['crew']['checklist']['branch'], 'B')
        self.assertEqual(arrived['crew']['reviewed']['value'], 420)
        self.assertEqual(arrived['crew']['metric']['value'], 585)
        self.assertEqual(arrived['crew']['checklist']['completion_count'], 0)

    def test_bands_are_computed_from_past_actual_history(self):
        controller = DesktopController()
        controller.seek(2)
        metric = mission_projection(controller.model())['crew']['metric']
        current = metric['history'][-1]
        ref = reference_summary(metric['history'], current)
        self.assertEqual(metric['reference'], ref)
        self.assertEqual(metric['band_2sd'], ref['reference_interval'])
        self.assertEqual(metric['band_1sd'], [ref['mean']-ref['sd'], ref['mean']+ref['sd']])
        self.assertNotIn(current['id'], ref['reference_ids'])
        self.assertGreater(ref['zscore'], 12)

    def test_unknown_evidence_does_not_invent_a_reading(self):
        fixture = synthetic_fixture()
        event = fixture['events'][-1]
        original = event['snapshot']['payload']
        rows = [{**o, 'value': None, 'quality': 'missing'} if o['id'] == 'changed' else o
                for o in original['observations']]
        event['snapshot'] = make_snapshot(original['subject_id'], rows, original['created_at'])
        result = project_events(fixture['events'], instant('09:40'))['crew']
        self.assertEqual(result['status'], 'Measurement needed')
        self.assertTrue(result['unknown'])
        self.assertIsNone(result['metric']['value'])
        self.assertEqual(result['checklist']['branch'], 'UNKNOWN')
        self.assertIsNone(result['metric']['band_1sd'])
        self.assertTrue(all(s['value'] is None for s in result['sensors'].values()))

    def test_completion_and_followup_retain_context_and_rewind(self):
        controller = DesktopController()
        controller.seek(4)
        result = mission_projection(controller.model())
        self.assertEqual(result['crew']['checklist']['completed_steps'], [0, 1, 2])
        self.assertEqual(result['doctor']['checklist']['completion_count'], 0)
        controller.seek(5)
        result = mission_projection(controller.model())
        self.assertEqual(result['crew']['checklist']['completion_count'], 0)
        self.assertEqual(len(result['crew']['checklist']['stale_completions']), 3)
        controller.seek(6)
        self.assertFalse(mission_projection(controller.model())['doctor']['followups'])
        controller.seek(7)
        result = mission_projection(controller.model())
        self.assertEqual(result['doctor']['metric']['value'], 560)
        self.assertEqual(len(result['doctor']['followups']), 1)
        controller.seek(0)
        self.assertFalse(mission_projection(controller.model())['crew']['checklist']['steps'])

    def test_pure_projection_and_independent_crew_values(self):
        controller = DesktopController()
        model = controller.model()
        before = copy.deepcopy(model)
        projected = mission_projection(model)
        projected['crew']['metric']['history'].clear()
        self.assertEqual(model, before)
        controller.select('Crew 002')
        self.assertEqual(project_role(controller.model()['crew'])['metric']['value'], 435)
        controller.select('Crew 001')
        self.assertEqual(project_role(controller.model()['crew'])['metric']['value'], 420)

    def test_insufficient_reference_and_equal_time_conflict_abstain(self):
        controller=DesktopController(); role=controller.model()['crew']
        row=role['current_snapshot']['payload']['observations'][-1]
        role['current_snapshot']=make_snapshot(row['subject_id'],[row],row['observed_at'])
        result=project_role(role)
        self.assertEqual(result['status'],'Collecting baseline'); self.assertTrue(result['unknown'])
        role['current_snapshot']=make_snapshot(row['subject_id'],[row,{**row,'id':'conflict','value':999}],row['observed_at'])
        result=project_role(role)
        self.assertEqual(result['status'],'Measurement needed'); self.assertIsNone(result['metric']['value'])

    def test_multiple_dependencies_are_projected_individually_without_cross_metric_reference(self):
        controller=DesktopController(); controller.seek(3)
        role=controller.model()['crew']; packet=role['protocol']; protocol=packet['protocol']
        old=packet['reviewed_snapshot']['payload']; current=role['current_snapshot']['payload']
        # Independent scalar fixture: source and unit remain explicit for each dependency.
        oxygen={**old['observations'][-1],'id':'oxygen-old','metric':'spo2','unit':'%',
            'source_id':'SYNTHETIC:oxygen-fixture','method':'synthetic oxygen instrument','value':97}
        reviewed=make_snapshot(old['subject_id'],old['observations']+[oxygen],old['created_at'])
        new_oxygen={**oxygen,'id':'oxygen-new','value':96,'observed_at':instant('09:30')}
        role['current_snapshot']=make_snapshot(current['subject_id'],current['observations']+[oxygen,new_oxygen],current['created_at'])
        packet['reviewed_snapshot']=reviewed; protocol['reviewed_digest']=reviewed['digest']
        protocol['dependencies'].append({k:oxygen[k] for k in ('source_id','metric','unit','phase','method')})
        protocol['conditions']['A'].append({'metric':'spo2','op':'ge','value':95})
        protocol['conditions']['B'].append({'metric':'spo2','op':'ge','value':95})
        role['evaluation']=evaluate_protocol(protocol,reviewed,role['current_snapshot'],role['at'])
        p=project_role(role); dependencies=p['protocol_evidence']['dependencies']
        self.assertEqual([d['metric'] for d in dependencies],['reaction_time','spo2'])
        self.assertEqual(dependencies[1]['reviewed']['value'],97)
        self.assertEqual(dependencies[1]['current']['value'],96)
        self.assertEqual(dependencies[1]['current']['unit'],'%')
        self.assertTrue(dependencies[1]['current']['usable'])
        self.assertEqual(p['checklist']['branch'],'B')
        self.assertEqual(p['metric']['name'],'reaction_time')
        self.assertTrue(all(r['metric']=='reaction_time' for r in p['metric']['history']))


if __name__ == '__main__':
    unittest.main()
