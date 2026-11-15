"""Pure, conservative presentation of an existing role's workflow evidence.

This introduces no medical rules or thresholds. The mission projection owns
measurement change flags; the validated assistant handoff owns intake routing.
The caller supplies one crew/session and one role, never merged crew/Earth data.
"""
from __future__ import annotations
from datetime import datetime


def project_crew_status(role, handoff, *, crew, session_id, snapshot=None):
    """Return words and a redundant symbol as well as a colour category.

    Only the latest active intake affects routine routing. Every prior urgent
    assessment remains urgent history, including after later negative answers.
    A mismatched handoff abstains rather than borrowing another role's report.
    Inputs must already be the existing validated projections/handoff.
    """
    def cue(tone, title, action, detail, symbol, target='assistant'):
        return dict(tone=tone, title=title, action=action, detail=detail, symbol=symbol, target=target)

    neutral = lambda detail: cue('neutral', 'More observations needed',
        'Take a health check', detail, '—')
    if (handoff.get('role') != role.get('role') or
            handoff.get('snapshot_digest') != role.get('digest')):
        return neutral('Evidence context unavailable. Open measurements to review the source.')
    records = handoff.get('records', [])
    if any(r.get('context', {}).get('crew') != crew or
           r.get('context', {}).get('session_id') != session_id for r in records):
        return neutral('This report does not belong to this crew/session.')
    assessments = [r for r in records if r.get('kind') == 'assessment']
    if handoff.get('urgent_history') or any(r.get('status') == 'urgent' for r in assessments):
        return cue('red', 'Reported urgent concern', 'Get onboard help now',
            'Urgent history remains recorded. Contact the onboard medical officer / crew; do not wait for Earth.', '!', 'urgent')
    # Match the existing handoff's definition of the active assessment. Earlier
    # superseded routine intakes may be stale without making the new one stale.
    latest = max(assessments, key=lambda r: (datetime.fromisoformat(r['context']['origin_created_at']), r['id']), default=None)
    if latest and latest.get('stale'):
        return cue('amber', 'Reassessment needed', 'Open onboard assistant',
            'The latest intake is stale. Record a fresh reassessment or contact onboard help.', '!')
    if latest and latest.get('status') in ('clarify', 'review'):
        return cue('amber', 'Clarify your concern' if latest['status'] == 'clarify' else 'Onboard review needed',
            'Open onboard assistant', latest['next_step'], '?')
    metric = role.get('metric', {})
    change = metric.get('change', {})
    if role.get('attention') or role.get('checklist', {}).get('stale_completions'):
        return cue('amber', 'An observation needs review', 'Review the next step',
            'A change or uncertain checklist needs review against the current available evidence.', '!', 'checklist')
    if any((sensor.get('source_id') or sensor.get('observed_at')) and
           (sensor.get('quality') != 'usable' or sensor.get('value') is None)
           for sensor in role.get('sensors', {}).values()):
        return neutral('A recorded measurement is incomplete or needs quality review. Open measurements for its source.')
    if metric.get('quality') not in ('usable', 'missing'):
        return neutral('The available measurement needs quality review or a supported repeat.')
    if metric.get('value') is None or metric.get('quality') != 'usable':
        return neutral('Available evidence is incomplete. Missing measurements are not inferred.')
    if metric.get('reference', {}).get('status') != 'available' or role.get('unknown'):
        return neutral('There is not enough comparable earlier history for a change summary.')
    if change.get('flagged') is not False:
        return neutral('A change summary is unavailable for these observations.')
    # The role projection deliberately collapses missing/conflicting sensors.
    # Check the SAME role's validated snapshot before offering a green cue;
    # absent devices stay absent, while actual review-quality/conflicting rows
    # cannot disappear behind an otherwise stable cognitive observation.
    if snapshot is None or snapshot.get('digest') != role.get('digest'):
        return neutral('A complete source-quality summary is unavailable.')
    from .protocol import verify_snapshot
    from .health_assistant import METRIC
    from .data import timestamp
    try:
        rows = verify_snapshot(snapshot)['observations']
    except (ValueError, TypeError, KeyError):
        return neutral('The source-quality context could not be verified.')
    recorded = [r for r in rows if r['metric'] != METRIC]
    groups = {}
    for row in recorded:
        groups.setdefault(row['metric'], []).append(row)
    for group in groups.values():
        latest_time = max(timestamp(r['observed_at'])[0] for r in group)
        latest_rows = [r for r in group if timestamp(r['observed_at'])[0] == latest_time]
        if len(latest_rows) > 1:
            return neutral('Recorded observations conflict at the latest time. Review their sources.')
        row = latest_rows[0]
        if row['metric'] == 'signal_review':
            return neutral('Technical signal review is recorded separately; it has no scalar condition assessment. Open signal review for the latest notes.')
        if row['quality'] != 'usable' or row.get('value') is None:
            return neutral('A latest recorded observation is missing or needs quality review. Review its source or record a supported repeat.')
    # Green describes only this usable, comparable observation set. An intake
    # never supplies an all-clear, nor do recorded actions imply resolution.
    return cue('green', 'No change in recorded reaction check', 'Continue your health check',
        'Reaction-time history only. Other measurements have no change assessment here. This is not a clinical readiness decision.', '✓')
