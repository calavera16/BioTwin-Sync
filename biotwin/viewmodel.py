"""Plain JSON view models; each role receives only its own arrived evidence."""
import copy
from .protocol import canonical_bytes
from .simulation import replay
from .statistics import reference_summary
from .data import timestamp

CAPABILITIES = {
    'graphical_ui': {'status': 'available', 'reason': 'Native astronaut health-monitoring interface is provided; requires the desktop Tk runtime.'},
    'SpO2': {'status': 'unavailable', 'reason': 'No automatic oxygen-saturation acquisition or inference; supplied measurements can be recorded manually or imported.'},
    'sleep_stages': {'status': 'unavailable', 'reason': 'No sleep-stage source or validated inference.'},
    'live_sensors': {'status': 'unavailable', 'reason': 'Laptop-only local capture, imports and recorded replay; no external sensor connection.'},
    'local_measurements': {'status': 'available', 'reason': 'Timestamped manual measurements, sleep diary and bounded observation/signal imports with separate local histories.'},
    'reaction_check': {'status': 'available', 'reason': 'Timed laptop reaction check; a distinct unvalidated task, not WinSCAT or mission readiness.'},
    'signal_model': {'status': 'experimental', 'reason': 'Trained NASA-backed technical-corruption and author-exclusion scores; real signals require manual review, not automatic quality acceptance.'},
    'onboard_assistant': {'status': 'prototype', 'reason': 'Offline source-linked intake, explicit recheck actions and delayed handoff; not clinician reviewed and no diagnostic model.'},
    'clinical_decisions': {'status': 'unavailable', 'reason': 'No treatment, disease, organ-health or EVA clearance decisions.'},
    'checklist_workflow': {'status': 'available', 'reason': 'Reviewer-authored conditional measurement steps with explicit operator completion.'},
}
BODY_METRICS = {'head': ['reaction_time', 'CPTRT', 'MTHRT', 'M2SRT'], 'heart': ['ECG', 'BVP', 'PPG']}


def capability(name):
    return copy.deepcopy(CAPABILITIES.get(name, {'status': 'unavailable', 'reason': 'Unsupported capability.'}))


def role_view(events, at, role):
    if role not in ('crew', 'doctor'):
        raise ValueError('role must be crew or doctor')
    state = replay(events, at)[role]
    snapshot = state['current_snapshot']
    observations = snapshot['payload']['observations'] if snapshot else []
    charts = {}
    for row in sorted(observations, key=lambda o: (timestamp(o['observed_at'])[0], o['id'])):
        if row['metric'] == 'assistant_review':
            continue  # Structured intake/action history is not a physiological series.
        # Keep incompatible sources/units/methods in separate series.
        key = canonical_bytes({k: row[k] for k in ('source_id', 'metric', 'unit', 'phase', 'method')}).decode()
        charts.setdefault(key, {'context': {k: row[k] for k in ('source_id', 'metric', 'unit', 'phase', 'method')}, 'points': []})['points'].append(copy.deepcopy(row))
    for chart in charts.values():
        chart['reference'] = reference_summary(chart['points'], chart['points'][-1])
    from .cusum import cusum_series
    from .metric_tags import suggest_metric_tags
    reviewed_packet = state['protocol'] or (state['authored_protocols'][-1] if state['authored_protocols'] else None)
    fixed_cusum = []
    if reviewed_packet:
        reviewed_payload = reviewed_packet['reviewed_snapshot']['payload']
        boundary, basis = timestamp(reviewed_payload['created_at'])
        for dependency in reviewed_packet['protocol']['dependencies']:
            refs = [o for o in reviewed_payload['observations'] if o['metric'] == dependency['metric'] and timestamp(o['observed_at'])[1] == basis and timestamp(o['observed_at'])[0] < boundary]
            monitoring = [o for o in observations if o['metric'] == dependency['metric'] and timestamp(o['observed_at'])[1] == basis and timestamp(o['observed_at'])[0] >= boundary]
            fixed_cusum.append({'metric': dependency['metric'], 'reference_policy': 'Explicit reviewed snapshot observations strictly before its creation; frozen before monitoring',
                'result': cusum_series(refs, monitoring, at), 'decision_use': 'Separate engineering diagnostic; never combined as independent confirmation or treatment rule'})
    suggestions = suggest_metric_tags(reviewed_packet['protocol']['original_reply'] if reviewed_packet else '', observations)
    current_digest = snapshot['digest'] if snapshot else None
    protocol = state['protocol']
    completions = []
    # Doctor sees completion only inside received follow-ups, never crew event log.
    records = state['completions'] if role == 'crew' else [c for f in state['followups'] for c in f['completions']]
    seen = set()
    for record in records:
        key = canonical_bytes(record)
        if key in seen:
            continue
        seen.add(key)
        stale = record['current_digest'] != current_digest
        if protocol:
            stale = stale or record['reviewed_digest'] != protocol['reviewed_snapshot']['digest'] or record['protocol_id'] != protocol['protocol']['id']
            if state['evaluation']:
                stale = stale or record['branch'] != state['evaluation']['outcome']
        completions.append({**copy.deepcopy(record), 'stale_context': stale,
            'context_status': 'prior evidence retained; review changed context' if stale else 'matches current evidence'})
    return {'role': role, 'at': at, 'current_snapshot': copy.deepcopy(snapshot), 'chart_series': list(charts.values()),
        'protocol': copy.deepcopy(protocol), 'fixed_reference_cusum': fixed_cusum, 'authoring_suggestions': suggestions, 'original_replies': copy.deepcopy(state['protocol_history']), 'authored_protocols': copy.deepcopy(state['authored_protocols']),
        'evaluation': copy.deepcopy(state['evaluation']), 'completions': completions, 'inbox': copy.deepcopy(state['inbox']),
        'followups': copy.deepcopy(state['followups']), 'resynchronization_needed': state['resynchronization_needed'],
        'body_regions': {region: {'measurement_selectors': [m for m in metrics if any(o['metric'] == m for o in observations)], 'meaning': 'measurement selector only; no organ health inference'} for region, metrics in BODY_METRICS.items()},
        'capabilities': copy.deepcopy(CAPABILITIES)}


def view_model(events, at):
    """Narrator timeline is separate; do not pass this aggregate to a remote role."""
    from .protocol import _aware
    visible = []
    for event in events:
        clock = event.get('sent_at', event.get('at'))
        if _aware(clock) <= _aware(at):
            item = {'event_id': event['event_id'], 'kind': event['kind'], 'at': clock}
            if event['kind'] == 'packet':
                item.update(sender=event['sender'], recipient=event['recipient'], arrives_at=event['arrives_at'],
                    status='arrived' if _aware(event['arrives_at']) <= _aware(at) else 'in transit', packet_bytes=len(canonical_bytes(event['packet'])))
            visible.append(item)
    return {'schema_version': 1, 'at': at, 'narrator_timeline': sorted(visible, key=lambda e: (timestamp(e['at'])[0], e['event_id'])),
        'crew': role_view(events, at, 'crew'), 'doctor': role_view(events, at, 'doctor')}
