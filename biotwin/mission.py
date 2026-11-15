"""Pure astronaut-console projections; no clinical decisions or cross-role evidence.

Public contract: ``mission_projection(view_model)`` or ``project_role(role_view)``.
All values are JSON-compatible and copied; these functions never mutate replay.
Status is one of Monitoring / Change detected / Checklist ready / Measurement needed.
``metric.change`` is the existing detect_change result with persistence=1: a single
descriptive deviation, explicitly distinct from persistent change or a diagnosis.
"""
from __future__ import annotations

import copy
from .data import CONTEXT, finite, timestamp
from .statistics import detect_change, reference_summary
from .protocol import _aware


def _latest(rows):
    if not rows: return None
    latest_time=max(timestamp(row['observed_at'])[0] for row in rows)
    latest=[row for row in rows if timestamp(row['observed_at'])[0]==latest_time]
    return latest[0] if len(latest)==1 else None


def sensor_projection(rows):
    """Report operator records and their provenance; never infer healthy status."""
    mapping={'HR':'heart_rate','BP':'systolic_bp','SpO2':'spo2','temperature':'temperature','sleep':'sleep_duration'}
    result={}
    for label,metric in mapping.items():
        records=[r for r in rows if r['metric']==metric]
        row=_latest(records)
        usable=row and row['quality']=='usable' and finite(row.get('value'))
        result[label]={'value':row['value'] if usable else None,'unit':row['unit'] if row else None,
            'status':'operator recorded' if usable else 'unavailable',
            'reason':'Self-entered measurement; not independently verified' if usable else 'Missing, conflicting or review-quality measurement',
            'observed_at':row['observed_at'] if row else None,'method':row['method'] if row else None,
            'source_id':row['source_id'] if row else None,'quality':row['quality'] if row else 'missing'}
        if label=='BP' and usable:
            diastolic=_latest([r for r in rows if r['metric']=='diastolic_bp'])
            if (diastolic and diastolic['quality']=='usable' and finite(diastolic['value'])
                and diastolic['observed_at']==row['observed_at'] and diastolic['source_id']==row['source_id'] and diastolic['method']==row['method']):
                result[label]['value']=f'{row["value"]:g}/{diastolic["value"]:g}'
            else:
                result[label]['value']=None; result[label]['status']='unavailable'; result[label]['reason']='A same-time/source/method systolic and diastolic pair is required'
    return result


def _dependency_record(snapshot, dependency, now, freshness, *, reviewed=False):
    rows = [r for r in snapshot['payload']['observations'] if r['metric']==dependency['metric']] if snapshot else []
    row = _latest(rows)
    reason = 'conflicting equal-time evidence' if rows and row is None else 'missing dependency'
    if row:
        compatible = all(row.get(k)==dependency.get(k) for k in CONTEXT)
        boundary = snapshot['payload']['created_at'] if reviewed else now
        age = (_aware(boundary)-_aware(row['observed_at'])).total_seconds()
        reason = ('incompatible dependency' if not compatible else
            'unusable dependency' if row['quality']!='usable' or not finite(row['value']) else
            'future dependency evidence' if age<0 else 'stale dependency' if age>freshness else None)
    else:
        compatible = False
    return {**{k: copy.deepcopy(row.get(k)) if row else None for k in
        ('id','value','unit','source_id','method','phase','observed_at','quality','provenance')},
        'compatible': compatible, 'usable': reason is None, 'reason': reason}


def protocol_evidence(packet, snapshot, now):
    if not packet: return None
    protocol = packet['protocol']; reviewed = packet['reviewed_snapshot']
    return {'protocol_id': protocol['id'], 'reviewed_snapshot_at': reviewed['payload']['created_at'],
        'reviewed_digest': reviewed['digest'], 'dependencies': [
            {'metric': dep['metric'], 'unit': dep['unit'], 'context': copy.deepcopy(dep),
             'current': _dependency_record(snapshot,dep,now,protocol['freshness_seconds']),
             'reviewed': _dependency_record(reviewed,dep,now,protocol['freshness_seconds'],reviewed=True)}
            for dep in protocol['dependencies']]}


def project_role(role_model):
    """Project only evidence already present in this one role's view model."""
    role = copy.deepcopy(role_model)
    snapshot = role.get('current_snapshot')
    rows = snapshot['payload']['observations'] if snapshot else []
    history = [o for o in rows if o['metric'] == 'reaction_time']
    current = _latest(history)
    ambiguous=bool(history) and current is None
    # Plot one exact task/source context. An incompatible newer record does not
    # borrow a previous method's baseline, and equal-time ambiguity abstains.
    if current:
        history=[o for o in history if all(o.get(k)==current.get(k) for k in (*CONTEXT,'subject_id'))]
    elif ambiguous:
        history=[]
    reference = reference_summary(history, current) if current else {
        'status': 'unavailable', 'reason': 'conflicting latest-time evidence' if ambiguous else 'no received measurement', 'count': 0,
        'mean': None, 'sd': None, 'zscore': None, 'reference_interval': None}
    change = detect_change(history, current, persistence=1) if current else {
        'status': 'unavailable', 'flagged': None, 'reason': 'no received measurement', 'reference': reference}
    usable = current is not None and current.get('quality') == 'usable' and finite(current.get('value'))
    evaluation = role.get('evaluation')
    unknown = evaluation is not None and evaluation['outcome'] == 'UNKNOWN'
    available = reference['status'] == 'available'
    physiological_status = ('Measurement needed' if not usable else
              'Change detected' if change['flagged'] else 'Monitoring' if available else 'Collecting baseline')
    workflow_status = ('Measurement needed' if unknown else 'Checklist ready' if evaluation else 'Awaiting checklist')
    status = workflow_status if evaluation else physiological_status
    metric = {'name': 'reaction_time', 'value': current['value'] if usable else None,
        'unit': current['unit'] if current else 'ms', 'observed_at': current['observed_at'] if current else None,
        'quality': current.get('quality') if current else 'missing',
        'source_id':current.get('source_id') if current else None,
        'method':current.get('method') if current else None,
        'history': sorted(history, key=lambda o: (timestamp(o['observed_at'])[0], o['id'])),
        'reference': reference, 'change': change,
        'band_1sd': [reference['mean']-reference['sd'], reference['mean']+reference['sd']] if available else None,
        'band_2sd': reference.get('reference_interval') if available else None}
    completions = role.get('completions', [])
    valid = sorted({c['step_index'] for c in completions if not c['stale_context']})
    steps = evaluation['checklist'] if evaluation else []
    packet = role.get('protocol') or next(iter(reversed(role.get('authored_protocols', []))), None)
    reviewed_snapshot = packet['reviewed_snapshot'] if packet else None
    reviewed_current = _latest([o for o in reviewed_snapshot['payload']['observations']
                                if o['metric'] == 'reaction_time']) if reviewed_snapshot else None
    reviewed = {'value': reviewed_current['value'] if reviewed_current else None,
        'unit': reviewed_current['unit'] if reviewed_current else None,
        'observed_at': reviewed_current['observed_at'] if reviewed_current else None,
        'snapshot_at': reviewed_snapshot['payload']['created_at'] if reviewed_snapshot else None,
        'digest': reviewed_snapshot['digest'] if reviewed_snapshot else None}
    historical = [c for c in completions if c['stale_context']]
    followup_context = {'allowed': bool(completions), 'basis': 'current' if valid else 'historical' if historical else 'none',
        'requires_confirmation': bool(historical) and not bool(valid),
        'message': ('Follow-up includes current completion records and retained historical records.' if valid and historical else
            'Follow-up includes completion records matching the current evidence.' if valid else
            'Only historical completion records remain. Follow-up sends historical work and changed evidence; no current checklist step is completed.' if historical else
            'Explicitly complete a checklist step before sending follow-up.')}
    return {'role': role.get('role'), 'at': role.get('at'), 'status': status,
        'physiological_status': physiological_status, 'workflow_status': workflow_status,
        'attention': change['flagged'] is True or unknown,
        'unknown': unknown if evaluation else not usable or not available,
        'physiological_unknown': not usable or not available,
        'snapshot_at': snapshot['payload']['created_at'] if snapshot else None,
        'digest': snapshot['digest'] if snapshot else None, 'metric': metric, 'reviewed': reviewed,
        'protocol_evidence': protocol_evidence(packet,snapshot,role['at']),
        'checklist': {'branch': evaluation['outcome'] if evaluation else None, 'steps': steps,
            'completed_steps': valid, 'completion_count': len(valid), 'total_steps': len(steps),
            'current_completion_count': len(valid), 'historical_completion_count': len(historical),
            'followup_context': followup_context, 'stale_completions': historical,
            'completions': completions, 'reasons': evaluation['reasons'] if evaluation else [],
            'original_reply': evaluation['original_reply'] if evaluation else None},
        'sensors': sensor_projection(rows),
        'fixed_reference_cusum': role.get('fixed_reference_cusum', []),
        'inbox': role.get('inbox', []), 'followups': role.get('followups', [])}


def mission_projection(model):
    """Local paired-console projection. Narrator metadata never grants Earth data."""
    packets = [copy.deepcopy(e) for e in model['narrator_timeline'] if e['kind'] == 'packet']
    pending = sorted((p for p in packets if p['status'] == 'in transit'),
                     key=lambda p: (timestamp(p['arrives_at'])[0], p['event_id']))
    next_arrival = pending[0] if pending else None
    seconds = ((timestamp(next_arrival['arrives_at'])[0]-timestamp(model['at'])[0]).total_seconds()
               if next_arrival else None)
    return {'at': model['at'], 'crew': project_role(model['crew']), 'doctor': project_role(model['doctor']),
        'communication': {'packets': packets, 'next_arrival': next_arrival,
                          'seconds_until_arrival': seconds}}


def project_events(events, at):
    """Convenience pure entry point for validated replay events plus cutoff."""
    from .viewmodel import view_model
    return mission_projection(view_model(events, at))
