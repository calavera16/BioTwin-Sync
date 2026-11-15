"""Headless application commands. Explicit human completion is an event."""
import copy
from .data import CONTEXT, finite
from .protocol import _aware, canonical_bytes, evaluate_protocol, make_snapshot, verify_snapshot, OPERATORS

PROTOCOL_FIELDS = {'id', 'version', 'subject_id', 'reviewed_digest', 'valid_from', 'valid_until', 'dependencies', 'freshness_seconds', 'conditions', 'original_reply', 'checklists'}


def validate_protocol(protocol, reviewed):
    canonical_bytes(protocol)
    payload = verify_snapshot(reviewed)
    if type(protocol) is not dict or set(protocol) != PROTOCOL_FIELDS:
        raise ValueError('invalid protocol schema')
    if type(protocol['version']) is not int or protocol['version'] != 1:
        raise ValueError('unsupported protocol version')
    if any(type(protocol[k]) is not str or not protocol[k].strip() for k in ('id', 'subject_id', 'original_reply')):
        raise ValueError('invalid protocol text')
    if protocol['subject_id'] != payload['subject_id'] or protocol['reviewed_digest'] != reviewed['digest']:
        raise ValueError('reviewed identity mismatch')
    if _aware(protocol['valid_until']) < _aware(protocol['valid_from']):
        raise ValueError('invalid validity interval')
    if not finite(protocol['freshness_seconds']) or protocol['freshness_seconds'] <= 0:
        raise ValueError('invalid freshness limit')
    deps = protocol['dependencies']
    if type(deps) is not list or not deps or any(type(d) is not dict or set(d) != set(CONTEXT) or any(type(v) is not str or not v.strip() for v in d.values()) for d in deps):
        raise ValueError('invalid dependencies')
    names = {d['metric'] for d in deps}
    if len(names) != len(deps):
        raise ValueError('duplicate dependencies')
    conditions = protocol['conditions']
    if type(conditions) is not dict or set(conditions) != {'A', 'B'}:
        raise ValueError('invalid conditions')
    for rules in conditions.values():
        if type(rules) is not list or not rules:
            raise ValueError('empty conditions')
        for rule in rules:
            if type(rule) is not dict or set(rule) != {'metric', 'op', 'value'} or type(rule['metric']) is not str or rule['metric'] not in names or type(rule['op']) is not str or rule['op'] not in OPERATORS or not finite(rule['value']):
                raise ValueError('invalid condition')
        if {r['metric'] for r in rules} != names:
            raise ValueError('branch must cover all dependencies')
    lists = protocol['checklists']
    if type(lists) is not dict or set(lists) != {'A', 'B', 'UNKNOWN'} or any(type(items) is not list or not items or any(type(s) is not str or not s.strip() for s in items) for items in lists.values()):
        raise ValueError('invalid authored checklists')
    return copy.deepcopy(protocol)


def completion_record(role, step_index, completed_at, note):
    packet, current = role['protocol'], role['current_snapshot']
    if packet is None or current is None:
        raise ValueError('received protocol and current evidence required')
    evaluation = evaluate_protocol(packet['protocol'], packet['reviewed_snapshot'], current, completed_at)
    if type(step_index) is not int or not 0 <= step_index < len(evaluation['checklist']):
        raise ValueError('invalid checklist step')
    if type(note) is not str or not note.strip() or len(note) > 4000:
        raise ValueError('enter a completion note (1..4000 characters)')
    if _aware(completed_at) < _aware(current['payload']['created_at']):
        raise ValueError('completion precedes context')
    p = packet['protocol']
    result = {'protocol_id': p['id'], 'protocol_version': p['version'], 'branch': evaluation['outcome'],
        'reviewed_digest': packet['reviewed_snapshot']['digest'], 'current_digest': current['digest'],
        'step_index': step_index, 'step_text': evaluation['checklist'][step_index], 'completed_at': completed_at, 'note': note}
    key = ('protocol_id', 'protocol_version', 'branch', 'reviewed_digest', 'current_digest', 'step_index')
    if any(all(c[k] == result[k] for k in key) for c in role['completions']):
        raise ValueError('duplicate checklist completion in this context')
    return result


class Workflow:
    """Mutations validate a candidate event log before replacing user state."""
    def __init__(self, events=None):
        if events is not None and (type(events) is not list or any(type(e) is not dict for e in events)):
            raise ValueError('events must be a list of event objects')
        self.events = copy.deepcopy(events if events is not None else [])
        if self.events:
            self._validate(self.events)

    @staticmethod
    def _validate(events):
        from .simulation import replay
        if type(events) is not list or any(type(e) is not dict for e in events):
            raise ValueError('events must be a list of event objects')
        last = max((_aware(e.get('arrives_at', e.get('at'))) for e in events), default=None)
        if last is not None:
            replay(events, last.isoformat())
        # Existing snapshots permit typed narrative metadata. Validate its full
        # immutable chain before committing raw captures, not just on file save.
        rows = [o for e in events if e['kind']=='local'
                for o in e['snapshot']['payload']['observations'] if o['metric']=='assistant_review']
        if rows:
            from .health_assistant import validate_session_records
            subjects = {e['snapshot']['payload']['subject_id'] for e in events if e['kind']=='local'}
            sessions = {o['assistant_record']['context']['session_id'] for o in rows}
            if len(subjects)!=1 or len(sessions)!=1:
                raise ValueError('Assistant workflow must have one isolated subject and session.')
            validate_session_records(events,session_id=next(iter(sessions)),subject_id=next(iter(subjects)))

    def state(self, at):
        from .simulation import replay
        return replay(self.events, at)

    def _append(self, event):
        candidate = self.events + [copy.deepcopy(event)]
        self._validate(candidate)
        self.events = candidate
        return copy.deepcopy(event)

    def capture(self, event_id, subject_id, observations, at, actor='crew'):
        snapshot = make_snapshot(subject_id, observations, at)
        self._append({'event_id': event_id, 'kind': 'local', 'actor': actor, 'at': at, 'snapshot': snapshot})
        return snapshot

    def send(self, event_id, packet_id, sender, recipient, sent_at, arrives_at, packet):
        return self._append({'event_id': event_id, 'kind': 'packet', 'packet_id': packet_id, 'sender': sender,
            'recipient': recipient, 'sent_at': sent_at, 'arrives_at': arrives_at, 'packet': packet})

    def suggest_dependencies(self, text, at, actor='doctor'):
        from .metric_tags import suggest_metric_tags
        if actor not in ('crew', 'doctor'):
            raise ValueError('unknown authoring role')
        snapshot = self.state(at)[actor]['current_snapshot']
        return suggest_metric_tags(text, snapshot['payload']['observations'] if snapshot else [])

    def author_protocol(self, reviewed_snapshot, **fields):
        return validate_protocol(fields, reviewed_snapshot)

    def complete(self, event_id, step_index, completed_at, note, *, now, actor='crew'):
        if _aware(completed_at) > _aware(now):
            raise ValueError('future completion')
        role = self.state(completed_at)[actor]
        record = completion_record(role, step_index, completed_at, note)
        self._append({'event_id': event_id, 'kind': 'completion', 'actor': actor, 'at': completed_at, 'record': record})
        return record

    def send_followup(self, event_id, packet_id, sent_at, arrives_at, actor='crew'):
        role = self.state(sent_at)[actor]
        if not role['completions'] or role['current_snapshot'] is None:
            raise ValueError('follow-up requires actual completed steps and evidence')
        packet = {'type': 'followup', 'snapshot': role['current_snapshot'], 'completions': role['completions'],
            'context_snapshots': [role['known_snapshots'][d] for d in sorted({c[k] for c in role['completions'] for k in ('reviewed_digest', 'current_digest')})]}
        return self.send(event_id, packet_id, actor, 'doctor' if actor == 'crew' else 'crew', sent_at, arrives_at, packet)


def complete_fixture():
    from .simulation import synthetic_fixture
    fixture = synthetic_fixture()
    app = Workflow(fixture['events'])
    for index in range(3):
        at = f'2026-11-14T09:{41 + index}:00+00:00'
        app.complete(f'05-complete-{index}', index, at, f'Synthetic operator recorded checklist step {index + 1}.', now=at)
    source = app.state('2026-11-14T09:44:00+00:00')['crew']['current_snapshot']['payload']
    previous = next(o for o in source['observations'] if o['id'] == 'changed')
    at = '2026-11-14T09:44:00+00:00'
    app.capture('06-repeat', source['subject_id'], source['observations'] + [{**previous, 'id': 'followup-repeat', 'value': 560, 'observed_at': at}], at)
    app.send_followup('07-followup', 'followup-1', '2026-11-14T09:45:00+00:00', '2026-11-14T10:05:00+00:00')
    fixture['events'] = app.events
    fixture['cutoffs'] += ['2026-11-14T09:43:00+00:00', '2026-11-14T09:45:00+00:00', '2026-11-14T10:05:00+00:00']
    return fixture
