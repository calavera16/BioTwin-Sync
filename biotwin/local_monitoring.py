"""Bounded, explicit local evidence. No sensor discovery or network activity."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import uuid

from .data import finite
from .protocol import _aware, canonical_bytes
from .session import _bounded, _pairs, validate_session

MAX_BYTES=2*1024*1024
MAX_ROWS=500
MAX_SAMPLES=30000
METRICS={'reaction_time':('ms',100,5000), 'heart_rate':('bpm',20,250),
    'systolic_bp':('mmHg',40,300),'diastolic_bp':('mmHg',20,200),
    'spo2':('%',0,100),'temperature':('C',25,45),
    'sleep_duration':('h',0,24),'sleep_quality':('score/10',0,10),'signal_review':('status',0,1)}

CAPACITY_MESSAGE = ('This local session has reached its storage capacity. Your existing work is retained. '
    'Export the session, then start a new recorded session to continue; keep this draft to enter there.')


class StorageCapacityError(ValueError):
    """Safe domain error; never includes records, paths or input text."""
    def __init__(self):
        super().__init__(CAPACITY_MESSAGE)


def storage_status(document):
    size = len(canonical_bytes(document)) + 1
    return {'bytes': size, 'limit_bytes': MAX_BYTES, 'remaining_bytes': max(0, MAX_BYTES-size),
        'warning': size >= MAX_BYTES * .8,
        'recovery': 'Export this session before starting a new recorded session. Full historical snapshots consume space; the byte limit can be reached before 500 observations.'}


def require_capacity(document):
    if storage_status(document)['bytes'] > MAX_BYTES:
        raise StorageCapacityError()


def actual_event_time(events):
    """Infer only occurrence times. A scheduled arrival is never a saved clock."""
    return max((event.get('sent_at', event.get('at')) for event in events), key=_aware)


def safe_path(path, *, suffixes=None):
    p=Path(path).absolute()
    for node in (p,*p.parents):
        if node.name.casefold()=='.env' or node.name.casefold().startswith('.env.') or node.is_symlink() or node.is_junction():
            raise ValueError('Secret or linked paths are unsupported.')
    if suffixes is not None and p.suffix.casefold() not in suffixes:
        raise ValueError('Choose a supported file type.')
    return p


def _read(path, suffixes, limit=MAX_BYTES):
    p=safe_path(path,suffixes=suffixes)
    if not p.is_file() or p.stat().st_size>limit: raise ValueError('Regular file required; import byte limit exceeded.')
    with p.open('rb') as stream: raw=stream.read(limit+1)
    if len(raw)>limit: raise ValueError('Import byte limit exceeded.')
    try: return p,raw.decode('utf-8-sig')
    except UnicodeError as exc: raise ValueError('UTF-8 input required.') from exc


def _json(text):
    try:
        result=json.loads(text,object_pairs_hook=_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON.')))
        _bounded(result); canonical_bytes(result); return result
    except (json.JSONDecodeError,RecursionError) as exc: raise ValueError('Invalid bounded JSON.') from exc


def _text(value,name,limit=160):
    if type(value) is not str or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):
        raise ValueError(f'{name} requires 1..{limit} plain characters.')
    return value.strip()


def observation(row, *, subject_id, session_id, now, generated_id=None):
    """Normalize supported units; never convert unknown units into healthy data."""
    if type(row) is not dict: raise ValueError('Observation must be an object.')
    if 'assistant_record' in row or row.get('metric')=='assistant_review':
        raise ValueError('Assistant outputs can only be created by the bounded onboard assistant.')
    if row.get('provenance','recorded')!='recorded': raise ValueError('Synthetic examples cannot become recorded personal observations.')
    if row.get('subject_id')!=subject_id: raise ValueError(f'Observation belongs to another local subject (expected {subject_id}).')
    metric=row.get('metric')
    if metric not in METRICS: raise ValueError('Unsupported local metric. Supported: heart_rate, systolic_bp, diastolic_bp, spo2, temperature, sleep_duration, sleep_quality.')
    unit,lo,hi=METRICS[metric]
    if row.get('unit')!=unit: raise ValueError(f'{metric} requires explicit unit {unit}.')
    at=_text(row.get('observed_at'),'observed_at'); when=_aware(at)
    if when>_aware(now): raise ValueError('Observation timestamp is in the future of this session.')
    source=_text(row.get('source_id'),'source_id'); method=_text(row.get('method'),'method')
    if source.startswith(('NASA:','NLSP:','SYNTHETIC:')): raise ValueError('Local evidence must use an independent local source.')
    quality=row.get('quality')
    if quality not in ('usable','review','missing'): raise ValueError('Explicit usable/review/missing quality required.')
    value=row.get('value')
    if isinstance(value,str):
        try: value=float(value) if value.strip() else None
        except ValueError as exc: raise ValueError('Measurement must be numeric or missing.') from exc
    if value is not None and (not finite(value) or not lo<=value<=hi): raise ValueError(f'{metric} outside supported input range {lo}..{hi} {unit}.')
    if value is None: quality='missing'
    if quality=='missing' and value is not None: raise ValueError('Missing-quality records must have a null value.')
    if metric=='reaction_time' and method not in ('laptop-simple-reaction-v1','browser-simple-reaction-v1'):
        raise ValueError('Local reaction time must come from the timed laptop check.')
    note=row.get('note','')
    if type(note) is not str or len(note)>1000: raise ValueError('Note limit is 1,000 characters.')
    prefix=f'LOCAL:{session_id}:'
    scoped=source if source.startswith(prefix) else prefix+source
    if source.startswith('LOCAL:') and not source.startswith(prefix): raise ValueError('Source belongs to another session.')
    if len(scoped)>160: raise ValueError('Scoped source identifier exceeds 160 characters; choose a shorter source name.')
    identifier=generated_id or row.get('id') or 'local-'+uuid.uuid4().hex
    _text(identifier,'observation id')
    return {'id':identifier,
        'subject_id':subject_id,'source_id':scoped,'metric':metric,'unit':unit,'value':value,
        'observed_at':at,'phase':'laptop-local','method':method,'quality':quality,
        'provenance':'recorded','note':note,'session_id':session_id,
        'measurement_kind':'sleep diary (not sleep staging)' if metric.startswith('sleep_') else 'operator-recorded measurement'}


def import_observations(path, *, subject_id, session_id, now):
    p,text=_read(path,{'.csv','.json'})
    if p.suffix.casefold()=='.json': rows=_json(text)
    else:
        reader=csv.DictReader(io.StringIO(text)); fields=reader.fieldnames or []
        if len(fields)!=len(set(fields)): raise ValueError('Duplicate CSV columns.')
        required_cols=('subject_id','metric','value','unit','observed_at','source_id','method','quality')
        missing_cols=[c for c in required_cols if c not in fields]
        if missing_cols: raise ValueError(f"CSV is missing required column(s): {', '.join(missing_cols)}. Required: {', '.join(required_cols)}.")
        rows=[]
        for row in reader:
            if len(rows)>=MAX_ROWS: raise ValueError('Observation limit: 500.')
            if None in row: raise ValueError('Unexpected CSV cells.')
            rows.append(row)
    if type(rows) is not list or not 1<=len(rows)<=MAX_ROWS: raise ValueError('Import requires 1..500 observation objects.')
    if any(r.get('metric')=='reaction_time' for r in rows if isinstance(r,dict)):
        raise ValueError('Imported reaction values cannot impersonate timed laptop checks.')
    result=[observation(row,subject_id=subject_id,session_id=session_id,now=now) for row in rows]
    if any(type(r['id']) is not str or not r['id'] or len(r['id'])>160 for r in result) or len({r['id'] for r in result})!=len(result):
        raise ValueError('Observation IDs must be bounded and unique.')
    return result


def validate_signal(signal, *, subject_id, session_id):
    if type(signal) is not dict or signal.get('subject_id')!=subject_id: raise ValueError('Signal belongs to another local subject.')
    if signal.get('channel') not in ('ECG','BVP'): raise ValueError('Channel must be ECG or BVP.')
    _aware(signal.get('observed_at'))
    unit=_text(signal.get('unit'),'signal unit',40); method=_text(signal.get('method'),'signal method')
    source=_text(signal.get('source_id'),'signal source')
    prefix=f'LOCAL:{session_id}:'
    if source.startswith(('NASA:','NLSP:','SYNTHETIC:')) or (source.startswith('LOCAL:') and not source.startswith(prefix)):
        raise ValueError('Signal source must be isolated to this local session.')
    samples=signal.get('samples')
    if type(samples) is not list or not 16<=len(samples)<=MAX_SAMPLES or any(v is not None and (not finite(v) or abs(v)>1e9) for v in samples):
        raise ValueError('Signal requires 16..30,000 finite bounded or null missing samples.')
    rate=signal.get('sample_rate_hz'); times=signal.get('sample_times_seconds')
    if rate is not None and (not finite(rate) or not 1<=rate<=2000): raise ValueError('Explicit sample rate must be 1..2,000 Hz.')
    if times is not None:
        if (type(times) is not list or len(times)!=len(samples) or any(not finite(v) for v in times)
            or times[0]<0 or times[-1]-times[0]>3600 or any(b<=a for a,b in zip(times,times[1:]))):
            raise ValueError('Sample times must be finite, strictly increasing and span at most one hour.')
        if rate is not None and any(abs((b-a)-1/rate)>max(1e-6,.01/rate) for a,b in zip(times,times[1:])):
            raise ValueError('Sample rate conflicts with explicit times. Omit rate for nonuniform input.')
    elif rate is None: raise ValueError('Explicit sample rate or exact sample times required.')
    if rate is not None and (len(samples)-1)/rate>3600: raise ValueError('Signal duration limit: one hour.')
    quality=signal.get('quality')
    if quality not in ('review','usable'): raise ValueError('Signal quality must be explicitly review or usable.')
    provenance=signal.get('provenance','recorded')
    if provenance not in ('recorded','synthetic'): raise ValueError('Signal provenance must be recorded or synthetic.')
    if provenance=='synthetic' and quality!='review': raise ValueError('Synthetic signal examples must remain review quality.')
    return {'id':_text(signal.get('id') or 'signal-'+uuid.uuid4().hex,'signal id'),
        'subject_id':subject_id,'session_id':session_id,'source_id':source if source.startswith(prefix) else prefix+source,
        'channel':signal['channel'],'unit':unit,'method':method,'quality':quality,
        'observed_at':signal['observed_at'],'sample_rate_hz':rate,'sample_times_seconds':copy.deepcopy(times),
        'samples':list(samples),'time_basis':'independent source clock','provenance':provenance}


def import_signal(path, *, subject_id, session_id):
    _,text=_read(path,{'.json'})
    return validate_signal(_json(text),subject_id=subject_id,session_id=session_id)


def empty_supplement(): return {'signals':[],'segments':[],'repeat_requests':[]}


def validate_supplement(value, *, subject_id, session_id):
    _bounded(value)
    if type(value) is not dict or set(value)!={'signals','segments','repeat_requests'}: raise ValueError('Invalid local supplement.')
    if type(value['signals']) is not list or len(value['signals'])>4: raise ValueError('Maximum four signal records.')
    signals=[validate_signal(s,subject_id=subject_id,session_id=session_id) for s in value['signals']]
    ids={s['id'] for s in signals}
    if len(ids)!=len(signals): raise ValueError('Duplicate signal IDs.')
    if type(value['segments']) is not list or len(value['segments'])>100: raise ValueError('Maximum 100 review segments.')
    for row in value['segments']:
        if type(row) is not dict or set(row)!={'signal_id','start_sample','end_sample','note','reviewed_at'} or row['signal_id'] not in ids:
            raise ValueError('Unknown segment signal.')
        signal=next(s for s in signals if s['id']==row['signal_id'])
        if type(row['start_sample']) is not int or type(row['end_sample']) is not int or not 0<=row['start_sample']<row['end_sample']<=len(signal['samples']):
            raise ValueError('Segment requires a valid half-open sample range.')
        _text(row['note'],'review note',1000); _aware(row['reviewed_at'])
    if type(value['repeat_requests']) is not list or len(value['repeat_requests'])>100: raise ValueError('Maximum 100 repeat requests.')
    seen=set()
    for row in value['repeat_requests']:
        if type(row) is not dict or set(row)!={'id','signal_id','reason','requested_at','resolved_at','resolution'} or row['signal_id'] not in ids:
            raise ValueError('Invalid repeat request.')
        _text(row['id'],'request id'); _text(row['reason'],'repeat reason',1000); _aware(row['requested_at'])
        if row['id'] in seen: raise ValueError('Duplicate repeat request.')
        seen.add(row['id'])
        if row['resolved_at'] is not None:
            if _aware(row['resolved_at'])<_aware(row['requested_at']): raise ValueError('Resolution predates request.')
            _text(row['resolution'],'resolution',1000)
        elif row['resolution'] is not None: raise ValueError('Open repeat request cannot have a resolution.')
    return {'signals':signals,'segments':copy.deepcopy(value['segments']),'repeat_requests':copy.deepcopy(value['repeat_requests'])}


def local_document(session, supplement, *, replay_at=None):
    session=validate_session(session); payload=session['payload']
    subject_ids={e['snapshot']['payload']['subject_id'] for e in payload['events'] if e['kind']=='local'}
    if len(subject_ids)!=1: raise ValueError('Exactly one local subject required.')
    body={'session':session,'local':validate_supplement(supplement,subject_id=next(iter(subject_ids)),session_id=payload['session_id'])}
    if replay_at is not None:
        if type(replay_at) is not str or len(replay_at)>80 or _aware(replay_at)<_aware(actual_event_time(payload['events'])):
            raise ValueError('Saved replay time must include every recorded event.')
        body['replay_at']=replay_at
    return {'format':'biotwin-local-session','version':2 if replay_at is not None else 1,'payload':body,'sha256':hashlib.sha256(canonical_bytes(body)).hexdigest()}


def read_local_document(path):
    _,text=_read(path,{'.json'}); document=_json(text)
    if type(document) is not dict or set(document)!={'format','version','payload','sha256'} or document['format']!='biotwin-local-session' or type(document['version']) is not int or document['version'] not in (1,2):
        raise ValueError('Unsupported local session.')
    body=document['payload']
    fields={'session','local'} | ({'replay_at'} if document['version']==2 else set())
    if type(body) is not dict or set(body)!=fields or hashlib.sha256(canonical_bytes(body)).hexdigest()!=document['sha256']:
        raise ValueError('Local session integrity mismatch.')
    checked=local_document(body['session'],body['local'],replay_at=body.get('replay_at'))
    if checked!=document: raise ValueError('Local session is not normalized.')
    return checked


def save_local_document(document,directory):
    # Revalidate in memory using the same normalized envelope contract.
    checked=local_document(document['payload']['session'],document['payload']['local'],replay_at=document['payload'].get('replay_at'))
    if checked!=document: raise ValueError('Invalid local session envelope.')
    raw=canonical_bytes(document)+b'\n'
    require_capacity(checked)
    root=safe_path(directory); root.mkdir(parents=True,exist_ok=True)
    target=safe_path(root/(checked['payload']['session']['payload']['session_id']+'.biotwin-local.json'))
    if target.exists():
        previous=read_local_document(target)
        if previous['payload']['session']['payload']['session_id']!=checked['payload']['session']['payload']['session_id']:
            raise ValueError('Refusing to overwrite unrelated local data.')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=root,prefix='.biotwin-local-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,target); temporary=None
    finally:
        if temporary: temporary.unlink(missing_ok=True)
    return str(target)


def export_import_examples(directory, *, subject_id):
    """Explicit user export: null diary template and labelled synthetic signal."""
    from datetime import datetime,timezone
    root=safe_path(directory); root.mkdir(parents=True,exist_ok=True)
    at=datetime.now(timezone.utc).isoformat(); tag=uuid.uuid4().hex[:8]
    rows=[{'subject_id':subject_id,'metric':metric,'value':None,'unit':unit,
        'observed_at':at,'source_id':'template-user-fill','method':'replace with actual acquisition method',
        'quality':'missing','note':'Empty template. Replace fields only with actual measured/diary data.'}
        for metric,unit in (('heart_rate','bpm'),('sleep_duration','h'))]
    signal={'id':'synthetic-example-'+tag,'subject_id':subject_id,'channel':'ECG','sample_rate_hz':256,
        'samples':[.1*math.sin(i/256*2*math.pi)+(.8 if i%256<4 else 0) for i in range(2560)],
        'unit':'synthetic amplitude','observed_at':at,'source_id':'EXAMPLE:synthetic-ECG-v1',
        'method':'synthetic example waveform; not a person measurement','quality':'review','provenance':'synthetic'}
    documents={f'observation-template-{tag}.json':json.dumps(rows,indent=2),
        f'synthetic-signal-example-{tag}.json':json.dumps(signal),
        f'IMPORT-SCHEMA-{tag}.txt':
        'BioTwin local imports\nObservation JSON is an array; CSV uses subject_id,metric,value,unit,observed_at,source_id,method,quality headers (optional id,note).\n'
        'Use the current LOCAL crew subject. Timestamps require an explicit timezone. Replace null values only with actual observations.\n'
        'Units: heart_rate bpm; systolic_bp/diastolic_bp mmHg; spo2 %; temperature C; sleep_duration h; sleep_quality score/10.\n'
        'Quality: review (default), usable (operator attested), missing (null only). Reaction time requires the timed keyboard task.\n'
        'Limit:500 observation rows and 2MiB per import. CSV missing values use an empty value cell.\n'
        'Signal JSON: subject_id,channel ECG/BVP,samples,unit,observed_at,source_id,method,quality, plus sample_rate_hz or sample_times_seconds.\n'
        'Null samples preserve gaps; times are source-relative seconds. Limit:4 records,30000 samples each. Never pair independent clocks.\n'
        'The signal example is SYNTHETIC and review quality, not a measured person, sensor capability, model validation, or clinical evidence.\n'
        'It stays separate from personal vital observations. Its arbitrary waveform is outside the trained NASA distribution. Scores may be unavailable/uncertain.\n'}
    paths=[]
    for filename,content in documents.items():
        target=safe_path(root/filename)
        with target.open('x',encoding='utf-8',newline='\n') as stream: stream.write(content+'\n')
        paths.append(str(target))
    return paths
