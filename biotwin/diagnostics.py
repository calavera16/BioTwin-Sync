"""Offline development diagnostics: software checks and source limits are separate."""
import copy
from collections import Counter
from pathlib import Path
from .cohort import find_similar
from .data import CONTEXT, audit_directory, load_winscat, timestamp
from .protocol import evaluate_protocol, make_snapshot
from .simulation import synthetic_fixture
from .statistics import reference_summary, detect_change


def select_cohort_example(observations):
    """Predeclared coverage ranking, never outcome/clinical quality selection."""
    candidates = []
    anchors = {(o['subject_id'], o['observed_at'], tuple(o[k] for k in CONTEXT)): o for o in observations if o['metric'] == 'CPTRT' and o['quality'] == 'usable'}
    for anchor in anchors.values():
        result = find_similar(observations, anchor['subject_id'], 'CPTRT', anchor['observed_at'], context={k: anchor[k] for k in CONTEXT})
        multi = sum(min(result.get('query_count', 0), m['count']) >= 2 for m in result['matches'])
        coverage = sum(min(result.get('query_count', 0), m['count']) for m in result['matches'])
        rank = (multi, len(result['matches']), coverage, result.get('query_count', 0), timestamp(anchor['observed_at'])[0], anchor['id'])
        candidates.append((rank, anchor, result))
    if not candidates:
        return {'status': 'unavailable', 'reason': 'No usable CPTRT query cutoff', 'matches': []}
    rank, anchor, result = max(candidates, key=lambda c: c[0])
    return {**result, 'anchor': {k: anchor[k] for k in ('id', 'subject_id', 'observed_at', *CONTEXT)},
        'selection': {'policy': 'maximize returned multi-point matches, distinct returned other people (up to three), matched point coverage, query length; latest source date then ID ties',
            'cutoffs_evaluated': len(candidates), 'cutoffs_with_multi_point_match': sum(c[0][0] > 0 for c in candidates), 'multi_point_matches': rank[0], 'matched_point_coverage': rank[2]},
        'limitations': ['Historical source-local chronology; not a consultation event.', 'Unverified source units; short trajectory similarity is not clinical similarity.']}


def synthetic_case_matrix():
    fixture = synthetic_fixture()
    reviewed, changed = fixture['events'][0]['snapshot'], fixture['events'][-1]['snapshot']
    protocol = fixture['events'][2]['packet']['protocol']
    now = fixture['cutoffs'][-1]
    def modified(**change):
        payload = changed['payload']
        return make_snapshot(payload['subject_id'], [{**o, **change} if o['id'] == 'changed' else o for o in payload['observations']], payload['created_at'])
    cases = [('stable', reviewed, protocol, 'A'), ('changed', changed, protocol, 'B'),
        ('missing', modified(value=None, quality='missing'), protocol, 'UNKNOWN'),
        ('quality-review', modified(quality='review'), protocol, 'UNKNOWN'),
        ('incompatible-unit', modified(unit='seconds'), protocol, 'UNKNOWN'),
        ('stale', changed, {**protocol, 'freshness_seconds': 60}, 'UNKNOWN'),
        ('expired', changed, {**protocol, 'valid_until': '2026-11-14T09:35:00+00:00'}, 'UNKNOWN'),
        ('ambiguous-branches', changed, {**protocol, 'conditions': {'A': [{'metric': 'reaction_time', 'op': 'gt', 'value': 0}], 'B': [{'metric': 'reaction_time', 'op': 'gt', 'value': 0}]}}, 'UNKNOWN')]
    rows = []
    for name, current, rules, expected in cases:
        evaluated = evaluate_protocol(rules, reviewed, current, now)
        rows.append({'case': name, 'provenance': 'synthetic', 'expected_software_outcome': expected,
            'context_aware_outcome': evaluated['outcome'], 'reasons': evaluated['reasons'],
            'static_reply_outcome': 'A', 'static_reply_policy': 'Always display the original stable-context A checklist; no current-evidence evaluation',
            'context_aware_matches_specification': evaluated['outcome'] == expected, 'static_matches_specification': expected == 'A'})
    return {'label': 'Synthetic software-behavior comparison, NOT clinical accuracy or efficacy', 'cases': rows,
        'context_aware_specification_matches': sum(r['context_aware_matches_specification'] for r in rows),
        'static_specification_matches': sum(r['static_matches_specification'] for r in rows), 'case_count': len(rows)}


def dataset_report(data_dir='data/raw/nlsp', quality_dir='outputs/quality/results'):
    result = {'software_failures': [], 'data_limitations': [], 'deferred_capabilities': [
        'Second-device and Windows 10 delivery verification', 'Automatic SpO2 acquisition/inference, sleep-stage inference and external live sensors',
        'Clinical decisions, organ-health inference and EVA clearance', 'Network authentication/encryption'],
        'source_licenses': {'SOTERIA': 'CC BY 4.0; retain source attribution', 'NLSP': 'Public download; license unspecified, raw redistribution terms unresolved'},
        'synthetic_case_matrix': synthetic_case_matrix()}
    try:
        observations, _ = load_winscat(Path(data_dir) / 'winscat_campaign3')
        result['source_audit'] = audit_directory(data_dir)
        available = [o for o in observations if reference_summary(observations, o)['status'] == 'available']
        visits = {(o['subject_id'], o['observed_at'], o['source_file'], o['source_row']) for o in available}
        persistence = [detect_change(observations, o, persistence=2) for o in available]
        result['baseline_coverage'] = {'minimum_earlier_comparable_tests': 5, 'total_metric_observations': len(observations),
            'eligible_metric_observations': len(available), 'eligible_assessment_visits': len(visits),
            'visits': [{'subject_id': v[0], 'observed_at': v[1], 'source_file': v[2], 'source_row': v[3]} for v in sorted(visits)],
            'persistent_flags': sum(r['flagged'] is True for r in persistence),
            'meaning': 'Descriptive reference availability only; these isolated late visits do not establish validated longitudinal persistence.'}
        result['cohort_example'] = select_cohort_example(observations)
        result['data_limitations'] += ['WinSCAT has only sparse repeated assessments; no validated longitudinal persistence support.',
            'Only earlier same-person, same-phase, same-method references count; minimum five was not lowered.',
            'WinSCAT response-time units unverified; original labels and source-local unspecified timezone retained.',
            'BEDREST drug/dose/position conditions and padded identities unresolved; not a resting-vitals baseline.',
            'NASA waveform, WinSCAT and BEDREST participants are separate identities; never merged as one patient.',
            'Short trajectory matches demonstrate retrieval, not clinical validity.']
    except (OSError, ValueError) as exc:
        result['data_limitations'].append('Local source audit unavailable: ' + str(exc))
        result['source_audit'] = {'status': 'unavailable', 'reason': str(exc)}
    try:
        from .quality import quality_status
        result['signal_experiment'] = quality_status(quality_dir)
    except ImportError as exc:
        result['signal_experiment'] = {'status': 'unavailable', 'reason': 'Optional signal module/dependencies unavailable: ' + str(exc)}
    except (OSError, ValueError) as exc:
        result['signal_experiment'] = {'status': 'unavailable', 'reason': str(exc)}
        result['software_failures'].append('Signal status could not be validated: ' + str(exc))
    try:
        from .signal_review import load_default_model
        model = load_default_model()
        result['workflow_signal_models'] = {
            'status': 'experimental',
            'id': model['id'], 'content_sha256': model['sha256'],
            'heads': [{'id': head['id'], 'target': head['target'],
                       'deployment_on_real_recordings': head['deployment']}
                      for head in (model, model['natural_head'])],
            'meaning': 'Actual trained models; controlled-injection success does not establish natural-artifact detection. Real recordings require manual review.'}
    except (OSError, ValueError) as exc:
        result['workflow_signal_models'] = {'status': 'unavailable', 'reason': str(exc)}
    result['status'] = 'software-check-failed' if result['software_failures'] else 'available-with-documented-limitations'
    return result


def self_check():
    from .workflow import complete_fixture
    from .simulation import replay
    from .session import make_session, validate_session
    from .transport import make_delta, full_packet
    fixture = complete_fixture()
    before = replay(fixture['events'], '2026-11-14T10:04:00+00:00')
    after = replay(fixture['events'], fixture['cutoffs'][-1])
    old = fixture['events'][0]['snapshot']
    middle = make_snapshot(old['payload']['subject_id'], old['payload']['observations'], '2026-11-14T09:01:00+00:00')
    later = make_snapshot(old['payload']['subject_id'], old['payload']['observations'], '2026-11-14T09:02:00+00:00')
    events = [fixture['events'][0], {'event_id': 't1', 'kind': 'local', 'actor': 'crew', 'at': middle['payload']['created_at'], 'snapshot': middle},
        {'event_id': 't2', 'kind': 'local', 'actor': 'crew', 'at': later['payload']['created_at'], 'snapshot': later}]
    def packet(identity, sent, arrival, content):
        return {'event_id': identity, 'kind': 'packet', 'packet_id': identity, 'sender': 'crew', 'recipient': 'doctor',
            'sent_at': f'2026-11-14T09:{sent}:00+00:00', 'arrives_at': f'2026-11-14T09:{arrival}:00+00:00', 'packet': content}
    events += [packet('missing-base', '02', '03', {'type': 'envelope', 'envelope': make_delta(old, middle)}),
        packet('recover', '04', '05', {'type': 'resync', 'envelope': full_packet(middle)}),
        packet('delta', '06', '07', {'type': 'envelope', 'envelope': make_delta(middle, later)})]
    transport = replay(events, '2026-11-14T09:08:00+00:00')
    session = make_session('self-check', fixture['events'])
    matrix = synthetic_case_matrix()
    checks = {'followup_delayed': not before['doctor']['followups'], 'followup_arrived': len(after['doctor']['followups']) == 1,
        'explicit_completion_count': len(after['crew']['completions']) == 3, 'session_roundtrip': validate_session(session) == session,
        'missing_base_explicit': transport['rejected'][0]['reason'].startswith('full-resynchronization-needed'),
        'resync_then_actual_delta': transport['doctor']['current_snapshot'] == later and not transport['doctor']['resynchronization_needed'],
        'synthetic_specifications': matrix['context_aware_specification_matches'] == matrix['case_count']}
    return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks, 'transport_delivery': {'selected_update_kind': make_delta(middle, later)['kind'], 'received_statuses': [entry['status'] for entry in transport['doctor']['inbox']], 'verified_final_digest': transport['doctor']['current_snapshot']['digest']}, 'scope': 'Offline synthetic software self-check; not medical validation'}
