"""Read-only wording for the desktop. All scientific results come from projections."""
from __future__ import annotations
from datetime import datetime, timezone

GUIDED_END_SECONDS = 40 * 60

def time_label(value):
    if not value:
        return 'unavailable'
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.strftime('%H:%M') + ' (timezone unverified)'
        return parsed.astimezone(timezone.utc).strftime('%H:%M UTC')
    except (ValueError, TypeError):
        return str(value)

def measurement_label(metric):
    value = metric.get('value')
    return f'{value:g} {metric.get("unit") or "unit unavailable"}' if value is not None else 'Unavailable'

def reference_label(metric):
    ref = metric.get('reference') or {}
    if ref.get('status') != 'available':
        return ('Reference / SD unavailable: ' + str(ref.get('reason') or 'insufficient comparable evidence') +
                '; earlier comparable sample count: ' + str(ref.get('count') if ref.get('count') is not None else 'unknown') + '. Descriptive history, not a health range.')
    return (f'{ref["zscore"]:+.2f} SD from earlier comparable history; '
            f'mean {ref["mean"]:.1f}, sample SD {ref["sd"]:.1f} {metric.get("unit") or ""}; '
            f'n={ref["count"]}. Descriptive spread, not a health range.')

def single_observation_label(metric):
    """Describe the existing single-observation projection without re-evaluating it."""
    change = metric.get('change') or {}
    if change.get('status') == 'available' and change.get('flagged') is True:
        return 'Single-observation deviation flagged for review.'
    if change.get('status') == 'available' and change.get('flagged') is False:
        return 'No single-observation deviation flagged.'
    return 'Single-observation comparison unavailable: ' + str(change.get('reason') or 'no result supplied') + '.'


def demo_facts(initial_metric, changed_metric):
    """Use already-computed fixture results, never calculate on illustrative data."""
    return {'initial': measurement_label(initial_metric), 'changed': measurement_label(changed_metric),
            'reference': reference_label(changed_metric),
            'observed': time_label(changed_metric.get('observed_at')),
            'metric': changed_metric}

def received_time(role):
    """Time of the delivered packet carrying the current snapshot, if known."""
    digest = (role.get('current_snapshot') or {}).get('digest')
    if not digest:
        return None
    for item in reversed(role.get('inbox') or []):
        if item.get('status') != 'received':
            continue
        packet = item.get('packet') or {}
        snapshot = packet.get('snapshot') or {}
        if snapshot.get('digest') == digest:
            return item.get('arrives_at') or item.get('received_at')
        if packet.get('type') in ('envelope','resync') and (packet.get('envelope') or {}).get('target_digest') == digest:
            return item.get('arrives_at') or item.get('received_at')
        # Follow-ups can carry their own snapshot.
        snapshot = (packet.get('followup') or {}).get('snapshot') or {}
        if snapshot.get('digest') == digest:
            return item.get('arrives_at') or item.get('received_at')
    return None

def evidence_summary(role, projection):
    """Only one role enters here: no aggregate model or narrator timeline."""
    metric = projection['metric']
    snapshot = role.get('current_snapshot')
    scope = 'Earth last received' if role.get('role') == 'doctor' else 'Crew onboard'
    if not snapshot:
        return f'{scope}\nNo snapshot available to this role. Value, source, SD and reference sample count are unknown.'
    received = received_time(role)
    lines = [scope, f'Reaction time: {measurement_label(metric)}',
             f'Observed: {time_label(metric.get("observed_at"))}',
             f'Snapshot created: {time_label(projection.get("snapshot_at"))}']
    if role.get('role') == 'doctor':
        lines.append(f'Received: {time_label(received)}')
    lines += [f'Source: {metric.get("source_id") or "unavailable"}',
              f'Method: {metric.get("method") or "unavailable"}',
              f'Quality: {metric.get("quality") or "unknown"}', reference_label(metric)]
    lines += [single_observation_label(metric),
              'This comparison describes one observation, not repeated change. Only compatible earlier observations form this reference. Missing or conflicting evidence stays unknown. A deviation is descriptive, not a diagnosis or mission-clearance decision.']
    others = [row for row in snapshot['payload'].get('observations', []) if row.get('metric') != 'reaction_time']
    if others:
        lines.append(f'Other recorded evidence: {len(others)} snapshot entries. Showing the last {min(12,len(others))} entries in snapshot order; technical data retains all entries.')
        for row in others[-12:]:
            value = str(row['value']) if row.get('value') is not None else 'unavailable'
            lines.append(f'{row.get("metric") or "Category unavailable"}: {value} {row.get("unit") or "unit unavailable"}\n'
                         f'Observed: {time_label(row.get("observed_at"))} · quality: {row.get("quality") or "unknown"}\n'
                         f'Source: {row.get("source_id") or "unavailable"} · method: {row.get("method") or "unavailable"}')
    return '\n\n'.join(lines)

def evidence_age(observed_at, at):
    if not observed_at or not at:
        return 'Age unavailable'
    try:
        now, observed = datetime.fromisoformat(at), datetime.fromisoformat(observed_at)
        if now.tzinfo is None or observed.tzinfo is None:
            return 'Age unavailable'
        seconds = (now - observed).total_seconds()
        if seconds < 0:
            return 'Age unavailable'
        return f'Evidence age {int(seconds // 60)} min'
    except (ValueError, TypeError):
        return 'Age unavailable'
