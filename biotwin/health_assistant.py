"""Strict offline educational intake and immutable evidence-bound action records.

No clinical classifier, device, network, treatment or safety clearance lives here.
Hashes provide integrity checks, not authentication of the operator.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
import uuid

from .protocol import _aware, canonical_bytes

METRIC = 'assistant_review'
LIBRARY_VERSION = '2026-11-14-v1'
CATALOG_SHA256 = 'c4f11c636cea8846da802bd253469583c0c25c4fcfa78eb65e15be9e8e43640d'
MAX_RECORDS = 100
# An engineering draft-expiry limit, never a medically safe waiting interval.
REVIEW_AGE_SECONDS = 1800
AGE_NOTICE = 'Intake expires after 30 minutes of real elapsed time for software review only; never a clinical safe-wait interval. New, worsening or uncertain symptoms require fresh reassessment/contact immediately regardless of age.'
ANSWERS = ('yes', 'no', 'unsure')
CONTEXT_KEYS = {'crew', 'session_id', 'subject_id', 'origin_snapshot_digest',
                'origin_created_at', 'evidence_digest', 'evidence'}


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _text(value, name, limit=1000, *, empty=False):
    if (type(value) is not str or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 and c not in '\n\t' for c in value)):
        raise ValueError(f'{name} must be bounded plain text (maximum {limit} characters).')
    return value


def catalog(directory=None):
    """Read only the pinned shipped asset; no caller receives shared references."""
    from .desktop_assets import asset_directory
    from .local_monitoring import _json, _read
    root = Path(directory) if directory is not None else asset_directory()
    _, raw = _read(root / 'assistant-protocols-v1.json', {'.json'}, 100_000)
    # Hash normalized UTF-8 text: BOM is not part of the shipped format.
    if hashlib.sha256(raw.encode('utf-8')).hexdigest() != CATALOG_SHA256:
        raise ValueError('Unsupported or changed assistant library; reinstall the reviewed prototype assets.')
    result = _json(raw)
    if (result.get('format') != 'biotwin-assistant-protocols' or type(result.get('version')) is not int
            or result['version'] != 1 or result.get('library_version') != LIBRARY_VERSION):
        raise ValueError('Unsupported assistant library version.')
    result['review_age_seconds'] = REVIEW_AGE_SECONDS
    result['review_age_notice'] = AGE_NOTICE
    return copy.deepcopy(result)


def _protocol(protocol_id):
    if type(protocol_id) is not str:
        raise ValueError('Choose a supported assistant path.')
    library = catalog()
    rows = [p for p in library['protocols'] if p['id'] == protocol_id]
    if len(rows) != 1:
        raise ValueError('Choose a supported assistant path.')
    return library, rows[0]


def preview(protocol_id, answers, note=''):
    """Default unknown, urgent-first assessment without a state mutation."""
    library, path = _protocol(protocol_id)
    _text(note, 'Narrative note', empty=True)
    keys = {q['id'] for q in path['questions']}
    if type(answers) is not dict or not set(answers).issubset(keys):
        raise ValueError('Unknown assistant question or invalid answer object.')
    if any(type(v) is not str or v not in ANSWERS for v in answers.values()):
        raise ValueError('Answers must be yes, no or unsure.')
    normalized = {q['id']: answers.get(q['id'], 'unsure') for q in path['questions']}
    urgent = [q['id'] for q in path['questions'] if q['kind'] == 'urgent' and normalized[q['id']] == 'yes']
    unknown = [q['id'] for q in path['questions'] if q['kind'] in ('urgent', 'contact') and normalized[q['id']] == 'unsure']
    contact = [q['id'] for q in path['questions'] if q['kind'] == 'contact' and normalized[q['id']] == 'yes']
    if urgent:
        status, reasons, next_step = 'urgent', urgent, library['urgent_help']
    elif unknown:
        status, reasons = 'clarify', unknown
        next_step = 'Safety answers are uncertain. Clarify them and contact the onboard medical officer / crew for guidance. Do not interpret missing answers as reassurance.'
    elif contact:
        status, reasons = 'review', contact
        next_step = 'Contact the onboard medical officer / crew for medical review; record symptoms and available evidence. This prototype cannot determine the cause.'
    else:
        status, reasons = 'collect', []
        next_step = 'Collect the suggested context and record a fresh reassessment or contact the onboard medical officer / crew. This is not an all-clear.'
    source_ids = sorted({s for q in path['questions'] for s in q['source_ids']} |
                        {s for step in path['steps'] for s in step['source_ids']})
    # Urgent escalation cannot be obscured by a long routine checklist.
    if status == 'urgent':
        steps = [{'id': 'urgent-contact', 'text': library['urgent_help'],
                  'source_ids': ['nhs-breathing'] if 'shared_urgent' in urgent else ['nhs-headache'], 'tool': None}]
    elif status in ('clarify', 'review'):
        steps = [{'id': 'contact-crew', 'text': next_step, 'source_ids': source_ids, 'tool': None}]
    else:
        steps = copy.deepcopy(path['steps'])
    return {'protocol_id': protocol_id, 'library_version': LIBRARY_VERSION,
            'clinical_review_status': 'not_clinician_reviewed', 'status': status,
            'next_step': next_step, 'reasons': reasons,
            'missing_answers': [k for k, v in normalized.items() if v == 'unsure'],
            'answers': normalized, 'note': note, 'steps': steps, 'source_ids': source_ids,
            'review_age_notice': AGE_NOTICE}


def evidence_context(snapshot, crew, session_id):
    from .protocol import verify_snapshot
    payload = verify_snapshot(snapshot)
    rows = [o for o in payload['observations'] if o['metric'] != METRIC]
    evidence = [{'id': o['id'], 'source_id': o['source_id'], 'metric': o['metric'],
                 'observed_at': o['observed_at'], 'value': o['value'], 'unit': o['unit'],
                 'quality': o['quality'], 'method': o['method'], 'provenance': o['provenance'],
                 'note': o.get('note', ''), 'signal_review': copy.deepcopy(o.get('signal_review')),
                 'sha256': _digest(o)} for o in rows]
    return {'crew': crew, 'session_id': session_id, 'subject_id': payload['subject_id'],
            'origin_snapshot_digest': snapshot['digest'], 'origin_created_at': payload['created_at'],
            'evidence_digest': _digest(rows), 'evidence': evidence}


def assessment(protocol_id, answers, note, context, at, *, identifier=None):
    _aware(at)
    if any(_aware(at) < _aware(e['observed_at']) for e in context['evidence']):
        raise ValueError('Assessment cannot predate evidence acquisition.')
    result = preview(protocol_id, answers, note)
    result.update(id=identifier or 'assist-' + uuid.uuid4().hex, kind='assessment',
                  record_version=1, assessed_at=at, context=copy.deepcopy(context))
    return result


def stale(record, context, now):
    """A newer negative answer never resolves a historical urgent report."""
    old = record['context']
    if any(old[k] != context[k] for k in ('crew', 'subject_id', 'session_id', 'evidence_digest')):
        return True
    at = record.get('assessed_at', record.get('completed_at'))
    age = (_aware(now) - _aware(at)).total_seconds()
    return age < 0 or age > REVIEW_AGE_SECONDS


def action(plan, step_id, note, context, at, records, *, identifier=None):
    _text(note, 'Completion note')
    if plan.get('kind') != 'assessment' or stale(plan, context, at):
        raise ValueError('Plan is stale; record a fresh reassessment before another action.')
    assessments = [r for r in records if r['kind'] == 'assessment']
    latest = max(assessments, key=lambda r: (_aware(r['context']['origin_created_at']), r['id']), default=None)
    if latest is None or latest['id'] != plan['id']:
        raise ValueError('A newer intake supersedes this plan; use its actions. Earlier urgent history remains recorded.')
    steps = [s for s in plan['steps'] if s['id'] == step_id]
    if len(steps) != 1:
        raise ValueError('Choose a step from this assessment.')
    if any(r['kind'] == 'action' and r['assessment_id'] == plan['id'] and r['step_id'] == step_id for r in records):
        raise ValueError('This step was already explicitly completed for this assessment.')
    if _aware(at) < _aware(plan['assessed_at']):
        raise ValueError('Action cannot predate the assessment or current evidence.')
    return {'id': identifier or 'assist-action-' + uuid.uuid4().hex, 'kind': 'action',
            'record_version': 1, 'library_version': LIBRARY_VERSION,
            'assessment_id': plan['id'], 'protocol_id': plan['protocol_id'],
            'step_id': step_id, 'step_text': steps[0]['text'],
            'source_ids': copy.deepcopy(steps[0]['source_ids']), 'completed_at': at,
            'note': note, 'context': copy.deepcopy(context),
            'meaning': 'Operator recorded an action; this does not establish symptom resolution or clinical clearance.'}


def observation(record):
    context = record['context']
    return {'id': record['id'], 'subject_id': context['subject_id'],
            'source_id': f"LOCAL:{context['session_id']}:assistant-{LIBRARY_VERSION}",
            'metric': METRIC, 'unit': 'record', 'value': None, 'quality': 'review',
            'observed_at': record.get('assessed_at', record.get('completed_at')),
            'phase': 'laptop-local', 'method': 'offline-assistant-v1', 'provenance': 'recorded',
            'session_id': context['session_id'], 'assistant_record': copy.deepcopy(record)}


def records(snapshot):
    if snapshot is None:
        return []
    return [copy.deepcopy(o['assistant_record']) for o in snapshot['payload']['observations'] if o['metric'] == METRIC]


def validate_observation(row):
    try:
        return _validate_observation(row)
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('Invalid bounded assistant observation schema.') from exc


def _validate_observation(row):
    """Shape validation independent of replay, including misplaced payloads."""
    if type(row) is not dict:
        raise ValueError('Observation must be an object.')
    if row.get('metric') != METRIC:
        if 'assistant_record' in row:
            raise ValueError('Assistant payload cannot be attached to an ordinary observation.')
        if 'signal_review' in row:
            _validate_signal_summary(row)
        return
    record = row.get('assistant_record')
    if type(record) is not dict or record.get('kind') not in ('assessment', 'action'):
        raise ValueError('Typed assistant record required.')
    _text(record.get('id'), 'Assistant record ID', 100)
    if not re.fullmatch(r'assist(?:-action)?-[0-9a-f]{32}', record['id']):
        raise ValueError('Invalid assistant record identifier.')
    context = record.get('context')
    if type(context) is not dict or set(context) != CONTEXT_KEYS:
        raise ValueError('Invalid assistant evidence context.')
    identities = {'Crew 001': 'LOCAL:crew-001', 'Crew 002': 'LOCAL:crew-002'}
    if (type(context['crew']) is not str or context['crew'] not in identities
            or context['subject_id'] != identities[context['crew']]
            or type(context['session_id']) is not str
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', context['session_id'])):
        raise ValueError('Invalid assistant crew/session identity.')
    if any(type(context[k]) is not str or not re.fullmatch(r'[0-9a-f]{64}', context[k])
           for k in ('origin_snapshot_digest', 'evidence_digest')):
        raise ValueError('Invalid assistant evidence digest.')
    _aware(context['origin_created_at'])
    if type(context['evidence']) is not list or len(context['evidence']) > 500:
        raise ValueError('Invalid bounded assistant evidence list.')
    if row != observation(record):
        raise ValueError('Invalid assistant observation transport.')
    if type(record.get('record_version')) is not int or record['record_version'] != 1 or record.get('library_version') != LIBRARY_VERSION:
        raise ValueError('Unsupported assistant record/library version.')


def _validate_signal_summary(row):
    """Only bounded historical operator metadata may enter a readable handoff."""
    detail = row['signal_review']
    if (row.get('metric') != 'signal_review' or row.get('value') is not None
            or row.get('quality') != 'missing' or row.get('unit') != 'status'
            or row.get('method') != 'operator signal review v1'
            or type(detail) is not dict
            or set(detail) != {'signal_id', 'channel', 'segments', 'repeat_requests', 'limitation'}):
        raise ValueError('Invalid or misplaced technical signal-review summary.')
    _text(detail['signal_id'], 'Technical signal ID', 160)
    _text(detail['limitation'], 'Technical summary limitation')
    if detail['channel'] not in ('ECG', 'BVP'):
        raise ValueError('Invalid technical signal channel.')
    segments, repeats = detail['segments'], detail['repeat_requests']
    if type(segments) is not list or len(segments) > 100 or type(repeats) is not list or len(repeats) > 100:
        raise ValueError('Technical summary record limit: 100 segments and 100 repeat requests.')
    for segment in segments:
        if (type(segment) is not dict or set(segment) != {'signal_id', 'start_sample', 'end_sample', 'note', 'reviewed_at'}
                or segment['signal_id'] != detail['signal_id']
                or type(segment['start_sample']) is not int or type(segment['end_sample']) is not int
                or not 0 <= segment['start_sample'] < segment['end_sample'] <= 30000):
            raise ValueError('Invalid technical review segment.')
        _text(segment['note'], 'Technical segment note')
        if _aware(segment['reviewed_at']) > _aware(row['observed_at']):
            raise ValueError('Technical segment review postdates its carrying observation.')
    ids = set()
    for repeat in repeats:
        if (type(repeat) is not dict or set(repeat) != {'id', 'signal_id', 'reason', 'requested_at', 'resolved_at', 'resolution'}
                or repeat['signal_id'] != detail['signal_id']):
            raise ValueError('Invalid technical repeat history.')
        _text(repeat['id'], 'Technical repeat ID', 160)
        if repeat['id'] in ids:
            raise ValueError('Duplicate technical repeat ID.')
        ids.add(repeat['id'])
        _text(repeat['reason'], 'Technical repeat reason')
        if _aware(repeat['requested_at']) > _aware(row['observed_at']):
            raise ValueError('Technical repeat request postdates its carrying observation.')
        if repeat['resolved_at'] is not None:
            if _aware(repeat['resolved_at']) < _aware(repeat['requested_at']):
                raise ValueError('Technical repeat resolution predates request.')
            if _aware(repeat['resolved_at']) > _aware(row['observed_at']):
                raise ValueError('Technical repeat resolution postdates its carrying observation.')
            _text(repeat['resolution'], 'Technical repeat resolution')
        elif repeat['resolution'] is not None:
            raise ValueError('Open technical repeat cannot claim a resolution.')


def validate_session_records(events, *, session_id, subject_id):
    try:
        return _validate_session_records(events, session_id=session_id, subject_id=subject_id)
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('Invalid bounded assistant record schema.') from exc


def _validate_session_records(events, *, session_id, subject_id):
    """Replay all local captures; regenerate decisions, sources and action text.

    A forged result cannot be made valid by updating its surrounding checksums.
    One initial measurement snapshot must precede an assistant record.
    """
    previous = None
    prior_rows = {}
    by_id = {}
    crew = {'LOCAL:crew-001': 'Crew 001', 'LOCAL:crew-002': 'Crew 002'}.get(subject_id)
    local_events = sorted((e for e in events if e['kind'] == 'local'),
                          key=lambda e: (_aware(e['at']), e['event_id']))
    assistant_bearing = any(o['metric'] == METRIC for e in local_events
                            for o in e['snapshot']['payload']['observations'])
    if assistant_bearing and any(e['actor'] != 'crew' for e in local_events):
        # Even an empty doctor-local snapshot would replace its delivered view
        # and conceal earlier reported urgent history. Doctor evidence must come
        # exclusively through the validated transport in assistant sessions.
        raise ValueError('Assistant sessions permit crew local captures only; doctor views are received-only.')
    for event in local_events:
        snapshot = event['snapshot']
        if snapshot['payload']['subject_id'] != subject_id:
            raise ValueError('Assistant session identity mismatch.')
        current = {}
        for row in snapshot['payload']['observations']:
            validate_observation(row)
            if row.get('metric') == METRIC:
                if _aware(row['observed_at']) > _aware(snapshot['payload']['created_at']):
                    raise ValueError('Assistant record postdates carried snapshot.')
                current[row['id']] = row
        if event['actor'] != 'crew':
            if current:
                raise ValueError('Assistant records may only originate in crew captures.')
            continue
        if len(current) > MAX_RECORDS:
            raise ValueError('Assistant record limit: 100.')
        if any(current.get(k) != v for k, v in prior_rows.items()):
            raise ValueError('Assistant history cannot be changed or removed.')
        added = [row for k, row in current.items() if k not in prior_rows]
        if len(added) > 1:
            raise ValueError('Each assistant capture must add exactly one bounded record.')
        if added:
            if previous is None or crew is None or event['actor'] != 'crew':
                raise ValueError('Assistant requires originating local crew evidence.')
            row = added[0]
            record = row['assistant_record']
            context = evidence_context(previous, crew, session_id)
            if record['context'] != context:
                raise ValueError('Assistant record does not match its exact originating evidence.')
            # Ordinary measurement and assistant creation are separate commands.
            if [o for o in previous['payload']['observations'] if o['metric'] != METRIC] != [o for o in snapshot['payload']['observations'] if o['metric'] != METRIC]:
                raise ValueError('Assistant record cannot silently add or alter measurements.')
            at = record.get('assessed_at', record.get('completed_at'))
            if _aware(at) > _aware(snapshot['payload']['created_at']):
                raise ValueError('Assistant record postdates its capture.')
            if record['kind'] == 'assessment':
                expected = assessment(record.get('protocol_id'), record.get('answers'), record.get('note'), context, at, identifier=record['id'])
            else:
                plan = by_id.get(record.get('assessment_id'))
                if plan is None:
                    raise ValueError('Unknown action assessment.')
                expected = action(plan, record.get('step_id'), record.get('note'), context, at, list(by_id.values()), identifier=record['id'])
            if record != expected:
                raise ValueError('Assistant decision, sources or action content is inconsistent with the pinned library.')
            if record['id'] in by_id:
                raise ValueError('Duplicate assistant record.')
            by_id[record['id']] = copy.deepcopy(record)
        previous, prior_rows = snapshot, current


def handoff(snapshot, *, crew, session_id, now, historical=False):
    library = catalog()
    result = records(snapshot)
    context = evidence_context(snapshot, crew, session_id) if snapshot else None
    assessments = {r['id']: r for r in result if r['kind'] == 'assessment'}
    latest = max(assessments.values(), key=lambda r: (_aware(r['context']['origin_created_at']), r['id']), default=None)
    # Cumulative snapshot ordering uses stable IDs; reports use record times.
    result.sort(key=lambda r: (_aware(r.get('assessed_at', r.get('completed_at'))), r['id']))
    for r in result:
        basis = assessments.get(r.get('assessment_id'), r)
        r['stale'] = stale(basis, context, now) or (latest is not None and basis['id'] != latest['id'])
    urgent = [r['id'] for r in result if r['kind'] == 'assessment' and r['status'] == 'urgent']
    lines = ['ONBOARD ASSISTANT — educational consultation summary', library['disclaimer'],
             'Mission routing is a project adaptation, not a NASA-approved procedure.', AGE_NOTICE,
             'Recorded actions never mean symptom resolution or clearance. Narrative notes are not medically interpreted.']
    if not result:
        lines.append('No assistant records are available in this role snapshot (doctor sees delivered records only).')
    if urgent:
        lines.extend(['REPORTED URGENT HISTORY REMAINS: ' + ', '.join(urgent), library['urgent_help']])
    sources = {s['id']: s for s in library['sources']}
    for r in result:
        if r['kind'] == 'assessment':
            context_label = 'STALE' if r['stale'] else ('historical received evidence context' if historical else 'current software context')
            lines.extend(['', f"Assessment {r['id']} / {r['protocol_id']} / {r['status'].upper()} / {context_label}",
                          'Assessment time: ' + r['assessed_at'], 'Library: ' + r['library_version'], r['next_step'],
                          'Answers: ' + '; '.join(f'{k}={v}' for k, v in r['answers'].items()),
                          'Reasons: ' + (', '.join(r['reasons']) or 'No explicit routing answer; evidence collection only'),
                          'Unknown: ' + (', '.join(r['missing_answers']) or 'none reported'),
                          'Uninterpreted note: ' + (r['note'] or '(none)'),
                          'Origin snapshot: ' + r['context']['origin_snapshot_digest'] + ' acquired/captured ' + r['context']['origin_created_at']])
            if not r['context']['evidence']:
                lines.append('Originating measurements: unavailable (empty session).')
            for e in r['context']['evidence']:
                value = 'unavailable' if e['value'] is None else str(e['value'])
                lines.append(f"Evidence {e['id']} / {e['metric']} = {value} {e['unit']} / quality {e['quality']} / {e['method']} / {e['provenance']} / {e['source_id']} / acquired {e['observed_at']} / {e['sha256']}")
                if e['note']:
                    lines.append('Evidence note: ' + e['note'])
                if e['signal_review'] is not None:
                    detail = e['signal_review']
                    lines.append(f"Technical signal review {detail['signal_id']} / {detail['channel']}; raw waveform not transmitted here.")
                    for repeat in detail['repeat_requests']:
                        lines.append(f"Repeat request {repeat['id']} at {repeat['requested_at']}: {repeat['reason']}; resolved {repeat['resolved_at'] or 'not recorded'}; note {repeat['resolution'] or '(none)'}")
                    for segment in detail['segments']:
                        lines.append(f"Technical segment {segment['start_sample']}:{segment['end_sample']} at {segment['reviewed_at']}: {segment['note']}")
        else:
            lines.extend(['', f"Action {r['id']} / assessment {r['assessment_id']} / {r['step_id']} at {r['completed_at']}",
                          r['step_text'], r['note'], r['meaning'], 'STALE basis' if r['stale'] else 'Same software evidence basis'])
    used = sorted({s for r in result for s in r['source_ids']} | {'nasa-context'})
    lines.append('\nSources (not clinician reviewed; terrestrial guidance is not space validation):')
    for key in used:
        s = sources[key]
        lines.append(f"{s['title']} — {s['url']} — accessed {s['accessed_at']} — {s['role']} — {s['clinical_review_status']}")
        if s['review_due']:
            lines.append(f"Source reviewed {s['reviewed_at']}; due {s['review_due']}" + (' (OVERDUE on access)' if s['review_overdue_on_access'] else ''))
    return {'text': '\n'.join(lines), 'records': result, 'urgent_history': urgent,
            'stale': any(r['stale'] for r in result), 'disclaimer': library['disclaimer']}
