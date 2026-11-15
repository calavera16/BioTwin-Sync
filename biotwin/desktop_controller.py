"""Standard-library desktop commands. Tk widgets never own workflow state."""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy
import math
import uuid

from .data import CONTEXT, finite
from .session import import_session, make_session, save_session
from .protocol import _aware
from .simulation import synthetic_fixture
from .viewmodel import view_model
from .workflow import Workflow
from . import local_monitoring as local

CREWS = ('Crew 001', 'Crew 002')
SUBJECTS = dict(zip(CREWS, ('SYNTHETIC:crew-001', 'SYNTHETIC:crew-002')))
STOPS = (
    ('09:00', 'Consultation sent'), ('09:20', 'Earth received; reply in transit'),
    ('09:30', 'Local measurement changes'), ('09:40', 'Conditional checklist received'),
    ('09:43', 'Operator explicitly completes 3 steps'), ('09:44', 'Repeat changes the evidence context'),
    ('09:45', 'Follow-up in transit'), ('10:05', 'Earth receives follow-up'),
)


def instant(clock):
    return f'2026-11-14T{clock}:00+00:00'


def plus(at, minutes=1):
    return (datetime.fromisoformat(at) + timedelta(minutes=minutes)).isoformat()


def guided_events(crew):
    """Re-author the fixture for an isolated anonymous synthetic identity."""
    subject = SUBJECTS[crew]
    template = synthetic_fixture()
    shift = 15 if crew == CREWS[1] else 0
    observations = [{**o, 'subject_id': subject, 'value': o['value'] + shift}
                    for o in template['events'][0]['snapshot']['payload']['observations']]
    flow = Workflow()
    old = flow.capture('01-local', subject, observations, instant('09:00'))
    flow.send('02-consultation', 'consultation-1', 'crew', 'doctor', instant('09:00'), instant('09:20'),
              {'type': 'snapshot', 'snapshot': old})
    fields = copy.deepcopy(template['events'][2]['packet']['protocol'])
    fields.update(subject_id=subject, reviewed_digest=old['digest'])
    for rules in fields['conditions'].values():
        for rule in rules:
            rule['value'] += shift
    protocol = flow.author_protocol(old, **fields)
    flow.send('03-reply', 'reply-1', 'doctor', 'crew', instant('09:20'), instant('09:40'),
              {'type': 'protocol', 'protocol': protocol, 'reviewed_snapshot': old})
    changed = {**observations[-1], 'id': 'changed', 'value': 585 + shift, 'observed_at': instant('09:30')}
    flow.capture('04-change', subject, observations + [changed], instant('09:30'))
    for index in range(3):
        at = instant(f'09:{41+index}')
        flow.complete(f'05-complete-{index}', index, at,
                      f'Synthetic operator explicitly recorded step {index+1}.', now=at)
    at = instant('09:44')
    flow.capture('06-repeat', subject, observations + [changed, {**changed, 'id': 'repeat',
                 'value': 560 + shift, 'observed_at': at}], at)
    flow.send_followup('07-followup', 'followup-1', instant('09:45'), instant('10:05'))
    return flow.events


@dataclass
class CrewSession:
    workflow: Workflow
    at: str
    mode: str
    stop: int
    session_id: str
    label: str | None = None
    local_data: dict | None = None
    incarnation: str = dataclass_field(default_factory=lambda: uuid.uuid4().hex)
    clock_basis: str = 'saved'


class DesktopController:
    def __init__(self):
        self.selected = CREWS[0]
        self._assistant_selection_generation = 0
        self.sessions = {}
        for crew in CREWS:
            self.reset(crew)

    @property
    def current(self):
        return self.sessions[self.selected]

    def select(self, crew):
        if crew not in CREWS:
            raise ValueError('Select one of the two anonymous crews.')
        if crew != self.selected:
            self._assistant_selection_generation += 1
        self.selected = crew

    def reset(self, crew=None, *, interactive=False):
        crew = crew or self.selected
        events = guided_events(crew)
        self.sessions[crew] = CrewSession(Workflow(events[:1] if interactive else events),
            instant('09:00'), 'operator' if interactive else 'guided', 0,
            f"crew-{CREWS.index(crew)+1:03}-{uuid.uuid4().hex[:10]}")

    def start_local(self):
        """Start empty recorded evidence, never inherit a synthetic baseline."""
        sid=f'local-{CREWS.index(self.selected)+1:03}-{uuid.uuid4().hex[:10]}'
        at=datetime.now(timezone.utc).isoformat()
        workflow=Workflow(); workflow.capture('0001-local-start',self.local_subject,[],at)
        self.sessions[self.selected]=CrewSession(workflow,at,'local',0,sid,None,local.empty_supplement())

    @property
    def local_subject(self):
        return f'LOCAL:crew-{CREWS.index(self.selected)+1:03}'

    @property
    def subject(self):
        return self.local_subject if self.current.mode=='local' else SUBJECTS[self.selected]

    def seek_seconds(self, seconds):
        """Rebuild guided state at a bounded continuous synthetic instant."""
        if self.current.mode != 'guided':
            raise ValueError('Seeking is available in guided replay.')
        seconds = float(seconds)
        if not math.isfinite(seconds):
            raise ValueError('Synthetic replay time must be finite.')
        start = datetime.fromisoformat(instant('09:00'))
        end = (datetime.fromisoformat(instant(STOPS[-1][0]))-start).total_seconds()
        seconds = max(0.0, min(end, seconds))
        self.current.at = (start+timedelta(seconds=seconds)).isoformat()
        self.current.stop = max(i for i, (at, _) in enumerate(STOPS)
            if datetime.fromisoformat(instant(at)) <= datetime.fromisoformat(self.current.at))

    def seek(self, index):
        index = max(0, min(int(index), len(STOPS)-1))
        seconds = (datetime.fromisoformat(instant(STOPS[index][0]))-
                   datetime.fromisoformat(instant('09:00'))).total_seconds()
        self.seek_seconds(seconds)

    def step(self):
        self.seek(self.current.stop+1)

    def model(self):
        return view_model(self.current.workflow.events, self.current.at)

    def prerequisites(self):
        model = self.model()
        enabled = self.current.mode in ('operator','local')
        crew, doctor = model['crew'], model['doctor']
        return {
            'capture': (enabled, 'Start an operator or laptop-local session to capture evidence.'),
            'send': (enabled and crew['current_snapshot'] is not None and bool(crew['current_snapshot']['payload']['observations']), 'Capture a measurement first.'),
            'author': (enabled and doctor['current_snapshot'] is not None, 'Advance delivery until Earth has received the consultation.'),
            'complete': (enabled and crew['evaluation'] is not None, 'Receive the reviewer checklist before recording completion.'),
            'followup': (enabled and bool(crew['completions']), 'Explicitly complete a checklist step first.'),
            'advance': (enabled, 'Start an operator session to advance its synthetic clock.'),
        }

    def _require(self, action):
        okay, reason = self.prerequisites()[action]
        if not okay:
            raise ValueError(reason)

    def _id(self, prefix):
        return f'{len(self.current.workflow.events)+1:04}-{prefix}'

    def _mutate(self, action):
        """Clock is committed only after a successful validated mutation."""
        at = plus(self.current.at)
        previous_workflow, previous_at = self.current.workflow, self.current.at
        self.current.workflow = Workflow(previous_workflow.events)
        try:
            result = action(at)
            self.current.at = at
            if self.current.mode == 'local': local.require_capacity(self.document())
            return result
        except Exception:
            self.current.workflow, self.current.at = previous_workflow, previous_at
            raise

    def capture(self, value):
        self._require('capture')
        if self.current.mode=='local': raise ValueError('Use the timed reaction check in Laptop tools. Typed reaction values are demo only.')
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 10000:
            raise ValueError('Synthetic reaction time must be finite and between 0 and 10,000 ms.')
        state = self.current.workflow.state(self.current.at)['crew']['current_snapshot']['payload']
        previous = max(state['observations'], key=lambda o: _aware(o['observed_at']))
        return self._mutate(lambda at: self.current.workflow.capture(self._id('measurement'), SUBJECTS[self.selected],
            state['observations'] + [{**previous, 'id': self._id('observation'), 'value': value, 'observed_at': at}], at))

    def send_consultation(self):
        self._require('send')
        snapshot = self.current.workflow.state(self.current.at)['crew']['current_snapshot']
        return self._mutate(lambda at: self.current.workflow.send(self._id('consultation'), self._id('packet'),
            'crew', 'doctor', at, plus(at, 20), {'type': 'snapshot', 'snapshot': snapshot}))

    def author_reply(self, operator, threshold, original_reply, checklist_a, checklist_b, metric='reaction_time'):
        self._require('author')
        if metric=='assistant_review':
            raise ValueError('Assistant records are narrative review metadata, never a scalar protocol dependency.')
        complements = {'le': 'gt', 'lt': 'ge', 'gt': 'le', 'ge': 'lt'}
        if operator not in complements:
            raise ValueError('Choose a supported comparison operator.')
        threshold = float(threshold)
        if not math.isfinite(threshold) or not 0 <= threshold <= 10000:
            raise ValueError('Reviewer threshold must be finite and 0..10,000 ms.')
        def steps(value):
            result = [line.strip() for line in value.splitlines() if line.strip()]
            if not 1 <= len(result) <= 6 or any(len(s) > 500 for s in result):
                raise ValueError('Enter 1..6 measurement-checklist lines per branch; at most 500 characters per line.')
            return result
        a, b = steps(checklist_a), steps(checklist_b)
        if not original_reply.strip() or len(original_reply) > 2000:
            raise ValueError('Enter a reviewer reply of 1..2,000 characters.')
        reviewed = self.current.workflow.state(self.current.at)['doctor']['current_snapshot']
        eligible=[o for o in reviewed['payload']['observations'] if o['metric']==metric]
        if not eligible: raise ValueError('Choose a metric in the delivered snapshot.')
        latest = max(eligible, key=lambda o: _aware(o['observed_at']))
        def action(at):
            protocol = self.current.workflow.author_protocol(reviewed, id=self._id('reviewer-checklist'), version=1,
                subject_id=self.subject, reviewed_digest=reviewed['digest'], valid_from=at,
                valid_until=plus(at, 240), dependencies=[{k: latest[k] for k in CONTEXT}], freshness_seconds=14400,
                conditions={'A': [{'metric': metric, 'op': operator, 'value': threshold}],
                            'B': [{'metric': metric, 'op': complements[operator], 'value': threshold}]},
                original_reply=original_reply.strip(), checklists={'A': a, 'B': b,
                'UNKNOWN': ['Record unavailable or incompatible evidence and acquire a supported repeat for review.']})
            return self.current.workflow.send(self._id('reply'), self._id('reply-packet'), 'doctor', 'crew', at,
                plus(at, 20), {'type': 'protocol', 'protocol': protocol, 'reviewed_snapshot': reviewed})
        return self._mutate(action)

    def complete(self, step, note):
        self._require('complete')
        return self._mutate(lambda at: self.current.workflow.complete(self._id('completion'), step, at, note, now=at))

    def followup(self, *, confirm_historical=False):
        self._require('followup')
        context = self.followup_context()
        if context['requires_confirmation'] and confirm_historical is not True:
            raise ValueError(context['message'])
        return self._mutate(lambda at: self.current.workflow.send_followup(self._id('followup'), self._id('followup-packet'), at, plus(at, 20)))

    def followup_context(self):
        """Classify history at the actual planned send time, including expiry."""
        from .mission import project_role
        from .viewmodel import role_view
        at=plus(self.current.at)
        context=project_role(role_view(self.current.workflow.events,at,'crew'))['checklist']['followup_context']
        context['send_at']=at
        if context['requires_confirmation']:
            context['message']='At the follow-up send time: '+context['message']
        return context

    def advance(self):
        self._require('advance')
        self.current.at = plus(self.current.at, 20)

    def document(self):
        if self.current.mode=='local':
            session=make_session(self.current.session_id,self.current.workflow.events,
                label=self.current.label or f'{self.selected} / laptop-local / recorded',provenance='recorded')
            return local.local_document(session,self.current.local_data,replay_at=self.current.at)
        return make_session(self.current.session_id, self.current.workflow.events,
            label=self.current.label or f'{self.selected} / {self.current.mode} / explicitly synthetic', provenance='synthetic',replay_at=self.current.at)

    def save(self, directory):
        if self.current.mode=='local': return local.save_local_document(self.document(),directory)
        return save_session(self.document(), directory)

    def open(self, path):
        # Select the envelope without reading arbitrary/secret input paths.
        _,raw=local._read(path,{'.json'},8*1024*1024)
        envelope=local._json(raw)
        if isinstance(envelope,dict) and envelope.get('format')=='biotwin-local-session':
            return self._open_local(path)
        document = import_session(path)
        events = document['payload']['events']
        candidate = Workflow(events)
        if not events:
            raise ValueError('An imported desktop session must contain synthetic evidence.')
        at = document['payload'].get('replay_at') or local.actual_event_time(events)
        # A saved guided cutoff may precede later authored events. Validate the
        # complete source context independently of what is visible at that cutoff.
        horizon = max((e.get('arrives_at',e.get('at')) for e in events),key=_aware)
        state = candidate.state(horizon)
        subjects = {s['payload']['subject_id'] for role in ('crew', 'doctor')
                    for s in state[role]['known_snapshots'].values()}
        if subjects != {SUBJECTS[self.selected]} or document['payload']['metadata']['provenance'] != 'synthetic':
            raise ValueError(f'This file belongs to another identity or source. Select the matching crew; {self.selected} accepts its own synthetic sessions only.')
        expected_context = {'source_id': 'SYNTHETIC:reaction-demo', 'metric': 'reaction_time', 'unit': 'ms',
                            'phase': 'demo', 'method': 'synthetic fixed reaction task'}
        for role in ('crew', 'doctor'):
            for snapshot in state[role]['known_snapshots'].values():
                observations = snapshot['payload']['observations']
                if not observations or any(not finite(o['value']) or not 0 <= o['value'] <= 10000
                    or o['quality'] != 'usable' or o['provenance'] != 'synthetic'
                    or any(o[k] != v for k, v in expected_context.items()) for o in observations):
                    raise ValueError('Desktop sessions require nonempty, finite synthetic reaction-time measurements in the supported ms/task context.')
        # Validated in full before replacing the selected crew only.
        self.sessions[self.selected] = CrewSession(candidate, at, 'operator', 0, document['payload']['session_id'], document['payload']['metadata']['label'], clock_basis='saved' if document['version']==2 else 'inferred')
        return document

    def _open_local(self,path):
        document=local.read_local_document(path); session=document['payload']['session']
        events=session['payload']['events']; candidate=Workflow(events)
        if not events or session['payload']['metadata']['provenance']!='recorded': raise ValueError('Recorded local events required.')
        at=document['payload'].get('replay_at') or local.actual_event_time(events)
        state=candidate.state(at); sid=session['payload']['session_id']
        subjects=set()
        for role in ('crew','doctor'):
            for snapshot in state[role]['known_snapshots'].values():
                payload=snapshot['payload']; subjects.add(payload['subject_id'])
                if len(payload['observations'])>local.MAX_ROWS: raise ValueError('Local observation limit: 500.')
                for row in payload['observations']:
                    if row.get('metric')=='assistant_review':
                        from .health_assistant import validate_observation
                        validate_observation(row)
                        continue
                    checked=local.observation(row,subject_id=self.local_subject,session_id=sid,now=payload['created_at'])
                    if any(row.get(k)!=v for k,v in checked.items()): raise ValueError('Unsupported local observation context.')
                    if row['metric']=='reaction_time': self._validate_reaction(row)
        if subjects!={self.local_subject}: raise ValueError('Local session belongs to another crew. Select its crew before opening.')
        self.sessions[self.selected]=CrewSession(candidate,at,'local',0,sid,session['payload']['metadata']['label'],document['payload']['local'],clock_basis='saved' if document['version']==2 else 'inferred')
        return document

    def _require_local(self):
        if self.current.mode!='local': raise ValueError('Start a laptop-local session first. Demo evidence remains separate.')

    def assistant_catalog(self):
        from .health_assistant import catalog
        return catalog()

    def assistant_context(self):
        """Opaque nonmodal draft binding, including same-file replacement identity."""
        snapshot=self.model()['crew']['current_snapshot']
        return {'crew':self.selected,'session_id':self.current.session_id,
                'incarnation':self.current.incarnation,'mode':self.current.mode,
                'selection_generation':self._assistant_selection_generation,
                'snapshot_digest':snapshot['digest'] if snapshot else None}

    def _assistant_binding(self,expected_context):
        self._require_local()
        if type(expected_context) is not dict or expected_context!=self.assistant_context():
            raise ValueError('Assistant draft context changed; reopen or reassess for the current crew/session/evidence.')

    def preview_assistant(self,protocol_id,answers,note=''):
        from .health_assistant import preview
        return preview(protocol_id,answers,note)

    def assess_assistant(self,protocol_id,answers,note='',*,expected_context):
        from . import health_assistant as assistant
        self._assistant_binding(expected_context)
        snapshot=self.model()['crew']['current_snapshot']
        context=assistant.evidence_context(snapshot,self.selected,self.current.session_id)
        record=assistant.assessment(protocol_id,answers,note,context,datetime.now(timezone.utc).isoformat())
        self._capture_local([assistant.observation(record)])
        return copy.deepcopy(record)

    def complete_assistant(self,assessment_id,step_id,note,*,expected_context):
        from . import health_assistant as assistant
        self._assistant_binding(expected_context)
        snapshot=self.model()['crew']['current_snapshot']
        records=assistant.records(snapshot)
        plans=[r for r in records if r['id']==assessment_id and r['kind']=='assessment']
        if len(plans)!=1: raise ValueError('Choose a saved assessment in this crew/session.')
        context=assistant.evidence_context(snapshot,self.selected,self.current.session_id)
        record=assistant.action(plans[0],step_id,note,context,datetime.now(timezone.utc).isoformat(),records)
        self._capture_local([assistant.observation(record)])
        return copy.deepcopy(record)

    def assistant_handoff(self,role='crew'):
        from . import health_assistant as assistant
        if role not in ('crew','doctor'): raise ValueError('Unknown assistant view role.')
        snapshot=self.model()[role]['current_snapshot']
        report=assistant.handoff(snapshot,crew=self.selected,session_id=self.current.session_id,
            now=datetime.now(timezone.utc).isoformat(),historical=role=='doctor')
        report['role']=role
        report['snapshot_digest']=snapshot['digest'] if snapshot else None
        report['snapshot_created_at']=snapshot['payload']['created_at'] if snapshot else None
        report['received_view_only']=role=='doctor'
        if role=='doctor':
            report['text']='DELIVERED SNAPSHOT ONLY — historical intake; current crew symptoms are unknown.\n'+report['text']
        return report

    def assistant_records(self,role='crew'):
        return self.assistant_handoff(role)['records']

    def export_assistant_report(self,directory):
        report=self.assistant_handoff('crew')
        root=local.safe_path(directory); root.mkdir(parents=True,exist_ok=True)
        target=local.safe_path(root/f'assistant-{self.current.session_id}-{uuid.uuid4().hex[:10]}.txt')
        with target.open('x',encoding='utf-8',newline='\n') as stream:
            stream.write(report['text']+'\n')
        return str(target)

    def _capture_local(self,rows,local_data=None):
        self._require_local()
        previous=self.model()['crew']['current_snapshot']['payload']['observations']
        if len(previous)+len(rows)>local.MAX_ROWS: raise local.StorageCapacityError()
        if len({o['id'] for o in previous+rows})!=len(previous)+len(rows): raise ValueError('Duplicate observation ID.')
        # A simulated delivery jump can put the replay clock ahead of wall time.
        # Keep snapshots strictly ordered without changing acquisition times.
        after_current=(_aware(self.current.at)+timedelta(microseconds=1)).isoformat()
        at=max([after_current,datetime.now(timezone.utc).isoformat(),*[r['observed_at'] for r in rows]],key=_aware)
        candidate=Workflow(self.current.workflow.events)
        result=candidate.capture(self._id('local-measurement'),self.subject,previous+rows,at)
        session=make_session(self.current.session_id,candidate.events,label=self.current.label or f'{self.selected} / laptop-local / recorded',provenance='recorded')
        document=local.local_document(session,local_data if local_data is not None else self.current.local_data,replay_at=at)
        local.require_capacity(document)
        self.current.workflow=candidate; self.current.at=at; return result

    def capture_manual(self,row):
        self._require_local()
        if row.get('metric')=='reaction_time': raise ValueError('Use the actual timed reaction check.')
        checked=local.observation(row,subject_id=self.subject,session_id=self.current.session_id,now=datetime.now(timezone.utc).isoformat())
        return self._capture_local([checked])

    def import_observations(self,path):
        self._require_local()
        rows=local.import_observations(path,subject_id=self.subject,session_id=self.current.session_id,now=datetime.now(timezone.utc).isoformat())
        return self._capture_local(rows)

    @staticmethod
    def _validate_reaction(row):
        from .reaction_check import TASK
        import statistics
        detail=row.get('reaction_check')
        browser_task='browser-simple-reaction-v1'
        if type(detail) is not dict or detail.get('task_id') not in (TASK,browser_task) or type(detail.get('trials')) is not list or not 3<=len(detail['trials'])<=10:
            raise ValueError('Actual timed reaction trial results required.')
        task=detail['task_id']
        if row.get('method')!=task or row.get('source_id')!=f"LOCAL:{row.get('session_id')}:{task}":
            raise ValueError('Reaction method/source must match its acquisition task.')
        if task==browser_task and (detail.get('timing_clock')!='performance.now'
                or detail.get('focus_visibility_guard') is not True or detail.get('cancelled') is not False):
            raise ValueError('Browser acquisition must retain its clock, focus/visibility cancellation and completed-task metadata.')
        if task==browser_task:
            fields={'task_id','value','unit','quality','trials','valid_responses','false_starts','omissions',
                    'configuration','limitations','timing_clock','focus_visibility_guard','cancelled'}
            if set(detail)!=fields or detail['unit']!='ms' or any(type(detail[k]) is not int for k in ('valid_responses','false_starts','omissions')):
                raise ValueError('Unsupported browser reaction summary fields or units.')
        values=[]
        expected={'trials':5,'foreperiod_seconds':[1.0,3.0],'response_limit_seconds':2.0,'minimum_response_ms':100,'input_mode':'keyboard-space'}
        if detail.get('configuration')!=expected or len(detail['trials'])!=5:
            raise ValueError('Recorded laptop checks require the fixed five-trial acquisition protocol.')
        for trial in detail['trials']:
            if type(trial) is not dict or set(trial)!={'outcome','reaction_ms'} or trial['outcome'] not in ('response','false_start','omission'):
                raise ValueError('Invalid reaction trial.')
            value=trial['reaction_ms']
            if trial['outcome']=='response':
                if not finite(value) or not 100<=value<=5000: raise ValueError('Invalid response time.')
                if task==browser_task and value>2000: raise ValueError('Browser response exceeds the fixed acquisition deadline.')
                values.append(value)
            elif value is not None and (trial['outcome']!='false_start' or not finite(value) or not 0<=value<100): raise ValueError('Invalid non-response trial.')
        quality='usable' if len(values)>=3 and len(values)==len(detail['trials']) else ('review' if values else 'missing')
        median=statistics.median(values) if values else None
        if (detail.get('valid_responses')!=len(values)
            or detail.get('false_starts')!=sum(r['outcome']=='false_start' for r in detail['trials'])
            or detail.get('omissions')!=sum(r['outcome']=='omission' for r in detail['trials'])):
            raise ValueError('Reaction trial counters are inconsistent.')
        if row['value']!=median or row['quality']!=quality or detail.get('value')!=median or detail.get('quality')!=quality:
            raise ValueError('Reaction aggregate does not match actual trials.')

    def capture_reaction(self,summary):
        self._require_local()
        from .reaction_check import TASK
        task=summary.get('task_id')
        if task not in (TASK,'browser-simple-reaction-v1'): raise ValueError('Unsupported reaction acquisition task.')
        summary=copy.deepcopy(summary)
        if task=='browser-simple-reaction-v1':
            summary['limitations']='Browser performance.now/display/keyboard/device timing is uncalibrated. Focus or visibility loss cancels acquisition. Not native laptop acquisition, WinSCAT, diagnosis or validated readiness.'
        row=local.observation({'subject_id':self.subject,'source_id':task,'metric':'reaction_time',
            'value':summary.get('value'),'unit':'ms','observed_at':datetime.now(timezone.utc).isoformat(),
            'method':task,'quality':summary.get('quality'),'note':summary.get('limitations','')},
            subject_id=self.subject,session_id=self.current.session_id,now=datetime.now(timezone.utc).isoformat())
        row['reaction_check']=copy.deepcopy(summary); self._validate_reaction(row)
        return self._capture_local([row])

    def add_signal(self,path):
        self._require_local()
        signal=local.import_signal(path,subject_id=self.subject,session_id=self.current.session_id)
        if _aware(signal['observed_at'])>datetime.now(timezone.utc): raise ValueError('Signal acquisition time cannot be in the future.')
        candidate=copy.deepcopy(self.current.local_data); candidate['signals'].append(signal)
        candidate=local.validate_supplement(candidate,subject_id=self.subject,session_id=self.current.session_id)
        self._record_signal_review(candidate,signal['id'],'Signal imported for technical review.')
        return signal

    def _record_signal_review(self,candidate,signal_id,note):
        signal=next(s for s in candidate['signals'] if s['id']==signal_id)
        row=local.observation({'subject_id':self.subject,'source_id':signal['source_id'],
            'metric':'signal_review','unit':'status','value':None,'quality':'missing',
            'observed_at':datetime.now(timezone.utc).isoformat(),'method':'operator signal review v1','note':note},
            subject_id=self.subject,session_id=self.current.session_id,now=datetime.now(timezone.utc).isoformat())
        row['signal_review']={'signal_id':signal_id,'channel':signal['channel'],
            'segments':[r for r in candidate['segments'] if r['signal_id']==signal_id],
            'repeat_requests':[r for r in candidate['repeat_requests'] if r['signal_id']==signal_id],
            'limitation':'Technical review metadata; raw samples stay in the local session supplement.'}
        self._capture_local([row],candidate); self.current.local_data=candidate

    def mark_segment(self,signal_id,start,end,note):
        self._require_local(); candidate=copy.deepcopy(self.current.local_data)
        candidate['segments'].append({'signal_id':signal_id,'start_sample':int(start),'end_sample':int(end),
            'note':note,'reviewed_at':datetime.now(timezone.utc).isoformat()})
        candidate=local.validate_supplement(candidate,subject_id=self.subject,session_id=self.current.session_id)
        self._record_signal_review(candidate,signal_id,'Operator marked a review segment: '+note)

    def request_repeat(self,signal_id,reason):
        self._require_local(); candidate=copy.deepcopy(self.current.local_data)
        row={'id':'repeat-'+uuid.uuid4().hex,'signal_id':signal_id,'reason':reason,
            'requested_at':datetime.now(timezone.utc).isoformat(),'resolved_at':None,'resolution':None}
        candidate['repeat_requests'].append(row)
        candidate=local.validate_supplement(candidate,subject_id=self.subject,session_id=self.current.session_id)
        self._record_signal_review(candidate,signal_id,'Operator requested a repeat: '+reason)
        return row

    def resolve_repeat(self,request_id,note):
        self._require_local(); candidate=copy.deepcopy(self.current.local_data)
        rows=[r for r in candidate['repeat_requests'] if r['id']==request_id]
        if not rows or rows[0]['resolved_at'] is not None: raise ValueError('Choose an open repeat request.')
        rows[0].update(resolved_at=datetime.now(timezone.utc).isoformat(),resolution=note)
        candidate=local.validate_supplement(candidate,subject_id=self.subject,session_id=self.current.session_id)
        self._record_signal_review(candidate,rows[0]['signal_id'],'Operator resolved repeat request: '+note)


def default_sessions_directory():
    import sys
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    return base / 'sessions'


def local_nlsp_report(folder):
    """Read a bounded WinSCAT cohort only; never open adjacent BEDREST or other files."""
    from .data import load_winscat
    from .statistics import reference_summary
    from .diagnostics import select_cohort_example
    root = Path(folder).absolute()/'winscat_campaign3'
    if not root.is_dir() or any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)):
        raise ValueError('Select a regular NLSP root containing winscat_campaign3; linked paths are unsupported.')
    paths = list(root.glob('*.csv'))
    if (not 1 <= len(paths) <= 50 or any(not p.is_file() or p.is_symlink() or p.is_junction() or p.stat().st_size > 256_000 for p in paths)
            or sum(p.stat().st_size for p in paths) > 1_000_000):
        raise ValueError('WinSCAT load bound: 1..50 regular CSV files, each <=256 KB and <=1 MB total.')
    observations, audit = load_winscat(root)
    if len(observations) > 5000:
        raise ValueError('WinSCAT observation bound exceeded (5,000).')
    available = [o for o in observations if reference_summary(observations, o)['status'] == 'available']
    visits = {(o['subject_id'], o['observed_at'], o['source_file'], o['source_row']) for o in available}
    return {'scope': 'Bounded local WinSCAT only; adjacent BEDREST and all unrelated files are not loaded.',
        'source_audit': audit,
        'baseline_coverage': {'minimum_earlier_comparable_tests': 5, 'total_metric_observations': len(observations),
            'eligible_metric_observations': len(available), 'eligible_assessment_visits': len(visits)},
        'cohort_example': select_cohort_example(observations),
        'limitations': ['Sparse historical assessments do not establish validated longitudinal persistence.',
            'Unverified source units and source-local unspecified timezone retained.',
            'Short trajectory similarity is not clinical similarity; identities remain separate from synthetic crews and SOTERIA.',
            'Local public data; redistribution terms unresolved. No raw records are bundled.']}
