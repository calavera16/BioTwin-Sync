"""Workflow cues abstain on uncertainty; colours never create medical rules."""
import copy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from biotwin.crew_status import project_crew_status
from biotwin.desktop_controller import DesktopController
from biotwin.mission import mission_projection
from biotwin.protocol import make_snapshot


class CrewStatusTests(unittest.TestCase):
    def setUp(self):
        self.controller=DesktopController()

    def inputs(self, role='crew'):
        c=self.controller; model=c.model()
        return (mission_projection(model)[role], c.assistant_handoff(role),
            dict(crew=c.selected,session_id=c.current.session_id,snapshot=model[role]['current_snapshot']))

    def cue(self, role='crew'):
        projection,handoff,options=self.inputs(role)
        return project_crew_status(projection,handoff,**options)

    def test_usable_available_observations_have_explicitly_limited_green(self):
        cue=self.cue()
        self.assertEqual(cue['tone'],'green')
        self.assertEqual(cue['title'],'No change in recorded reaction check')
        self.assertIn('Reaction-time history only',cue['detail'])
        self.assertIn('not a clinical',cue['detail'])
        self.assertNotIn('safe',cue['title'].lower())

    def test_empty_baseline_missing_and_review_quality_never_reassure(self):
        self.controller.start_local(); self.assertEqual(self.cue()['tone'],'neutral')
        self.controller=DesktopController()
        for edit in ('missing','review','baseline'):
            role,handoff,options=self.inputs()
            if edit=='baseline': role['metric']['reference']['status']='unavailable'
            else: role['metric'].update(quality=edit,value=None if edit=='missing' else 420)
            with self.subTest(edit=edit):
                self.assertEqual(project_crew_status(role,handoff,**options)['tone'],'neutral')

    def test_change_and_unknown_checklist_request_review(self):
        self.controller.seek(2); self.assertEqual(self.cue()['tone'],'amber')
        role,handoff,options=self.inputs(); role['unknown']=True
        role['checklist']['branch']='UNKNOWN'
        self.assertEqual(project_crew_status(role,handoff,**options)['tone'],'amber')

    def test_unknown_safety_answers_and_stale_latest_intake_are_amber(self):
        c=self.controller; c.start_local(); path=c.assistant_catalog()['protocols'][0]
        c.assess_assistant(path['id'],{},expected_context=c.assistant_context())
        self.assertEqual(self.cue()['tone'],'amber')
        self.assertEqual(self.cue()['target'],'assistant')
        answers={q['id']:'no' for q in path['questions']}
        c.assess_assistant(path['id'],answers,expected_context=c.assistant_context())
        later=datetime.now(timezone.utc)+timedelta(minutes=31)
        with patch('biotwin.desktop_controller.datetime',wraps=datetime) as mocked:
            mocked.now.return_value=later
            cue=self.cue()
        self.assertEqual(cue['tone'],'amber'); self.assertIn('stale',cue['detail'])

    def test_prior_urgent_cannot_be_cleared_by_negative_answers_or_completion(self):
        c=self.controller; c.start_local(); path=c.assistant_catalog()['protocols'][0]
        answers={q['id']:'no' for q in path['questions']}
        urgent=dict(answers); urgent[next(q['id'] for q in path['questions'] if q['kind']=='urgent')]='yes'
        plan=c.assess_assistant(path['id'],urgent,expected_context=c.assistant_context())
        self.assertEqual(self.cue()['tone'],'red')
        c.complete_assistant(plan['id'],plan['steps'][0]['id'],'QA recorded contact, not resolution',expected_context=c.assistant_context())
        c.assess_assistant(path['id'],answers,expected_context=c.assistant_context())
        self.assertEqual(self.cue()['tone'],'red'); self.assertEqual(self.cue()['target'],'urgent')
        self.assertIn('history remains',self.cue()['detail'])

    def test_other_crew_and_undelivered_earth_cannot_borrow_urgent(self):
        c=self.controller; c.start_local(); path=c.assistant_catalog()['protocols'][0]
        q=next(q['id'] for q in path['questions'] if q['kind']=='urgent')
        c.assess_assistant(path['id'],{q:'yes'},expected_context=c.assistant_context())
        self.assertEqual(self.cue()['tone'],'red')
        self.assertEqual(self.cue('doctor')['tone'],'neutral')
        role,report,options=self.inputs()
        options['crew']='Crew 002'
        self.assertEqual(project_crew_status(role,report,**options)['tone'],'neutral')
        c.select('Crew 002'); self.assertNotEqual(self.cue()['tone'],'red')

    def test_role_or_digest_mismatch_and_missing_quality_context_abstain(self):
        for mismatch in ('role','snapshot_digest'):
            role,report,options=self.inputs(); report[mismatch]='other'
            self.assertEqual(project_crew_status(role,report,**options)['tone'],'neutral')
        role,report,options=self.inputs(); options['snapshot']=None
        self.assertEqual(project_crew_status(role,report,**options)['tone'],'neutral')

    def test_latest_intake_order_uses_actual_time_not_offset_text(self):
        role,report,options=self.inputs()
        common=dict(kind='assessment',status='collect',next_step='Collect context')
        context=dict(crew=options['crew'],session_id=options['session_id'])
        report['records']=[dict(common,id='old',stale=True,context=dict(context,origin_created_at='2026-11-14T10:00:00+09:00')),
            dict(common,id='new',stale=False,context=dict(context,origin_created_at='2026-11-14T02:00:00+00:00'))]
        self.assertEqual(project_crew_status(role,report,**options)['tone'],'green')

    def test_recorded_review_and_conflicting_sensor_rows_block_green(self):
        for metric in ('heart_rate','sleep_quality'):
            role,report,options=self.inputs()
            rows=copy.deepcopy(options['snapshot']['payload']['observations'])
            row=copy.deepcopy(rows[-1]); row.update(id='review-other',metric=metric,quality='review',value=72)
            rows.append(row)
            snapshot=make_snapshot(options['snapshot']['payload']['subject_id'],rows,options['snapshot']['payload']['created_at'])
            role['digest']=report['snapshot_digest']=snapshot['digest']; options['snapshot']=snapshot
            self.assertEqual(project_crew_status(role,report,**options)['tone'],'neutral')
        role,report,options=self.inputs()
        rows=copy.deepcopy(options['snapshot']['payload']['observations'])
        for index in range(2):
            row=copy.deepcopy(rows[-1]); row.update(id=f'conflict-{index}',metric='heart_rate',value=72+index)
            rows.append(row)
        snapshot=make_snapshot(options['snapshot']['payload']['subject_id'],rows,options['snapshot']['payload']['created_at'])
        role['digest']=report['snapshot_digest']=snapshot['digest']; options['snapshot']=snapshot
        self.assertEqual(project_crew_status(role,report,**options)['tone'],'neutral')

    def test_projection_is_nonmutating_and_does_not_select_crew(self):
        inputs=self.inputs(); before=copy.deepcopy(inputs); document=self.controller.document()
        project_crew_status(inputs[0],inputs[1],**inputs[2])
        self.assertEqual(inputs,before); self.assertEqual(self.controller.document(),document)

    def test_strictly_newer_usable_repeat_recovers_without_erasing_review_history(self):
        from biotwin.mission import project_role
        c=self.controller; model=c.model()['crew']
        original=model['current_snapshot']; rows=copy.deepcopy(original['payload']['observations'])
        last=datetime.fromisoformat(rows[-1]['observed_at'])
        old=copy.deepcopy(rows[-1]); old.update(id='old-HR-review',metric='heart_rate',value=72,quality='review',
            observed_at=(last-timedelta(minutes=1)).isoformat())
        rows.append(old)
        report=c.assistant_handoff('crew')
        def project(values):
            snapshot=make_snapshot(original['payload']['subject_id'],values,original['payload']['created_at'])
            role_model=copy.deepcopy(model); role_model['current_snapshot']=snapshot
            projection=project_role(role_model); bound=copy.deepcopy(report); bound['snapshot_digest']=snapshot['digest']
            return project_crew_status(projection,bound,crew=c.selected,session_id=c.current.session_id,snapshot=snapshot)
        self.assertEqual(project(rows)['tone'],'neutral')
        repeat=copy.deepcopy(old); repeat.update(id='new-HR-usable',quality='usable',observed_at=last.isoformat())
        rows.append(repeat); before=copy.deepcopy(rows)
        self.assertEqual(project(rows)['tone'],'green')
        self.assertEqual(rows,before); self.assertEqual(rows[-2]['quality'],'review')


if __name__=='__main__': unittest.main()
