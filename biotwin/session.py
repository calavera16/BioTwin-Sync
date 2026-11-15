"""Versioned bounded plain-JSON sessions with verified atomic replacement."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from .protocol import canonical_bytes, _aware
from .workflow import Workflow

MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 2000
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > 24 or budget[0] > 200000:
        raise ValueError('session nesting/item limit exceeded')
    if type(value) is str and len(value) > 100000:
        raise ValueError('session string limit exceeded')
    if type(value) is dict:
        for k, v in value.items():
            _bounded(k, depth + 1, budget)
            _bounded(v, depth + 1, budget)
    elif type(value) is list:
        for v in value:
            _bounded(v, depth + 1, budget)


def validate_session(document):
    _bounded(document)
    encoded = canonical_bytes(document)
    if len(encoded) > MAX_BYTES:
        raise ValueError('session exceeds byte limit')
    if type(document) is not dict or set(document) != {'format', 'version', 'payload', 'sha256'} or document['format'] != 'biotwin-session' or type(document['version']) is not int or document['version'] not in (1,2):
        raise ValueError('unsupported session schema')
    payload = document['payload']
    fields = {'session_id', 'events', 'metadata'} | ({'replay_at'} if document['version']==2 else set())
    if type(payload) is not dict or set(payload) != fields or type(payload['session_id']) is not str or not ID.fullmatch(payload['session_id']) or payload['session_id'].upper() in RESERVED:
        raise ValueError('invalid session identifier/payload')
    if document['version']==2:
        if type(payload['replay_at']) is not str or len(payload['replay_at'])>80:
            raise ValueError('invalid saved replay time')
        _aware(payload['replay_at'])
    if type(payload['metadata']) is not dict or set(payload['metadata']) != {'label', 'provenance'} or type(payload['metadata']['label']) is not str or not payload['metadata']['label'].strip() or payload['metadata']['provenance'] not in ('synthetic', 'recorded', 'mixed'):
        raise ValueError('invalid session metadata')
    if type(payload['events']) is not list or len(payload['events']) > MAX_EVENTS:
        raise ValueError('session event limit exceeded')
    if hashlib.sha256(canonical_bytes(payload)).hexdigest() != document['sha256']:
        raise ValueError('session integrity mismatch')
    Workflow(payload['events'])  # Validate every send, arrival and completion, including future replay.
    local_rows = [o for event in payload['events'] if event['kind']=='local'
                  for o in event['snapshot']['payload']['observations']]
    if any(o['metric']=='assistant_review' or 'assistant_record' in o for o in local_rows):
        from .health_assistant import validate_session_records
        subjects = {e['snapshot']['payload']['subject_id'] for e in payload['events'] if e['kind']=='local'}
        if len(subjects)!=1:
            raise ValueError('Assistant sessions require exactly one isolated local subject.')
        validate_session_records(payload['events'],session_id=payload['session_id'],subject_id=next(iter(subjects)))
    return copy.deepcopy(document)


def make_session(session_id, events, *, label='Local development session', provenance='synthetic', replay_at=None):
    payload = {'session_id': session_id, 'events': copy.deepcopy(events), 'metadata': {'label': label, 'provenance': provenance}}
    if replay_at is not None: payload['replay_at']=replay_at
    return validate_session({'format': 'biotwin-session', 'version': 2 if replay_at is not None else 1, 'payload': payload, 'sha256': hashlib.sha256(canonical_bytes(payload)).hexdigest()})


def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('duplicate JSON key')
        result[k] = v
    return result


def import_session(path):
    source = Path(path)
    if source.suffix != '.json' or source.name.casefold() == '.env' or source.name.casefold().startswith('.env.') or any(p.is_symlink() or p.is_junction() for p in [source, *source.absolute().parents]):
        raise ValueError('session import requires a regular JSON file')
    with source.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('session exceeds byte limit')
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid session JSON') from exc
    return validate_session(document)


def save_session(document, directory='outputs/sessions'):
    document = validate_session(document)
    root = Path(directory).absolute()
    if any(p.is_symlink() or p.is_junction() for p in [root, *root.parents]):
        raise ValueError('symlink/junction storage paths are unsupported')
    root.mkdir(parents=True, exist_ok=True)
    target = root / (document['payload']['session_id'] + '.biotwin-session.json')
    if target.exists() or target.is_symlink() or target.is_junction():
        previous = import_session(target)
        if previous['payload']['session_id'] != document['payload']['session_id']:
            raise ValueError('refusing to overwrite an unrelated session')
    data = canonical_bytes(document) + b'\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.biotwin-', suffix='.tmp', dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return str(target)


def load_into(workflow, path):
    """Read and validate first; failed imports leave existing state untouched."""
    document = import_session(path)
    candidate = Workflow(document['payload']['events'])
    workflow.events = candidate.events
    return document


# Export uses the same integrity/schema and collision protections as local save.
export_session = save_session
