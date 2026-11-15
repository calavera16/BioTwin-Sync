"""Experimental injected-corruption review aid. Original evidence is never suppressed."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import random

from .signal_measurements import analyze_signal, validate_signal, finite
from .waveforms import read_json
from .ecg_reliability import analyze_reliability

FEATURES = ('flat_fraction', 'longest_hold_fraction', 'edge_fraction',
            'derivative_rms_over_std', 'difference_p95_over_std', 'difference_max_over_std',
            'crest_factor', 'kurtosis', 'range_over_iqr', 'slope_turn_fraction',
            'lag1_correlation', 'block_std_ratio')
WARNING = ('Detects patterns learned from deliberately injected technical corruption of NASA pilot ECG. '
           'Score is uncalibrated, not artifact probability. Original does not mean clean. '
           'No astronaut, clinical, disease or natural-artifact validation. Preserve every observation.')
DEFAULT_MODEL = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'assets' / 'desktop' / 'signal-review-v2.json'


def _quantile(ordered, fraction):
    pos = (len(ordered)-1)*fraction
    lo = int(pos)
    return ordered[lo] + (ordered[min(lo+1, len(ordered)-1)]-ordered[lo])*(pos-lo)


def extract_features(values):
    if not isinstance(values, (list, tuple)) or not 32 <= len(values) <= 30_000 or any(not finite(v) for v in values):
        raise ValueError('feature input requires 32..30000 bounded finite samples')
    ordered = sorted(values)
    span = ordered[-1]-ordered[0]
    mean = statistics.mean(values)
    std = max(statistics.pstdev(values), 1e-12)
    centered = [(v-mean)/std for v in values]
    diffs = [b-a for a, b in zip(values, values[1:])]
    absolute = sorted(abs(d)/std for d in diffs)
    epsilon = max(span*1e-10, 1e-12)
    flat = [abs(d) <= epsilon for d in diffs]
    run = longest = 0
    for item in flat:
        run = run+1 if item else 0
        longest = max(longest, run)
    edge = sum(abs(v-ordered[0]) <= epsilon or abs(v-ordered[-1]) <= epsilon for v in values)/len(values)
    block = max(2, len(values)//10)
    deviations = [statistics.pstdev(values[i:i+block])/std for i in range(0, len(values)-block+1, block)]
    iqr = _quantile(ordered, .75)-_quantile(ordered, .25)
    features = [sum(flat)/len(flat), longest/len(values), edge,
                math.sqrt(statistics.mean((d/std)**2 for d in diffs)),
                _quantile(absolute, .95), absolute[-1], max(abs(v) for v in centered),
                statistics.mean(v**4 for v in centered), span/max(iqr, std*.01, 1e-12),
                sum(a*b < 0 for a, b in zip(diffs, diffs[1:]))/max(1, len(diffs)-1),
                sum(a*b for a, b in zip(centered, centered[1:]))/len(diffs),
                max(deviations)/(min(deviations)+.1)]
    if any(not math.isfinite(v) or abs(v) > 1e6 for v in features):
        raise ValueError('feature arithmetic out of bounds')
    return dict(zip(FEATURES, features))


def engineering_flag(features):
    return (features['flat_fraction'] > .05 or features['edge_fraction'] > .1 or
            features['derivative_rms_over_std'] > .7 or features['block_std_ratio'] > 5)


def model_digest(model):
    content = {k: v for k, v in model.items() if k != 'sha256'}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_model(model):
    required = {'schema_version', 'id', 'kind', 'features', 'trees', 'threshold', 'target',
                'warning', 'provenance', 'deployment', 'sha256'}
    if not isinstance(model, dict) or set(model) not in (required, required | {'natural_head'}):
        raise ValueError('model schema mismatch')
    if model['schema_version'] != 1 or type(model['schema_version']) is not int or model['kind'] not in ('injected-corruption-random-forest', 'author-exclusion-random-forest'):
        raise ValueError('unsupported model')
    if model['features'] != list(FEATURES) or model['threshold'] != .5:
        raise ValueError('model feature/threshold mismatch')
    if model['deployment'] not in ('experimental_review', 'experimental_scorer_only'):
        raise ValueError('invalid deployment')
    for key in ('id', 'target', 'warning'):
        if not isinstance(model[key], str) or not 1 <= len(model[key]) <= 1500:
            raise ValueError('invalid model metadata')
    provenance = model['provenance']
    if not isinstance(provenance, dict) or not 1 <= len(provenance) <= 30 or any(
            not isinstance(k, str) or len(k) > 500 or not isinstance(v, str) or
            len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for k, v in provenance.items()):
        raise ValueError('invalid provenance hashes')
    if not isinstance(model['trees'], list) or not 1 <= len(model['trees']) <= 32:
        raise ValueError('invalid tree count')
    for tree in model['trees']:
        if not isinstance(tree, list) or not 1 <= len(tree) <= 127:
            raise ValueError('invalid tree size')
        def walk(index, depth, visited):
            if type(index) is not int or not 0 <= index < len(tree) or index in visited or depth > 6:
                raise ValueError('invalid/cyclic tree')
            visited.add(index)
            node = tree[index]
            if not isinstance(node, list) or len(node) != 5:
                raise ValueError('invalid tree node')
            feature, cutoff, left, right, score = node
            if type(feature) is not int or not finite(cutoff) or not finite(score) or not 0 <= score <= 1:
                raise ValueError('invalid tree numbers')
            if feature == -1:
                if left != -1 or right != -1:
                    raise ValueError('invalid leaf')
            else:
                if not 0 <= feature < len(FEATURES):
                    raise ValueError('invalid feature index')
                walk(left, depth+1, visited)
                walk(right, depth+1, visited)
        visited = set()
        walk(0, 0, visited)
        if len(visited) != len(tree):
            raise ValueError('unreachable model node')
    if 'natural_head' in model:
        if not isinstance(model['natural_head'], dict) or 'natural_head' in model['natural_head']:
            raise ValueError('invalid nested natural head')
        validate_model(model['natural_head'])
    if model['sha256'] != model_digest(model):
        raise ValueError('model integrity mismatch')
    return model


def load_model(path=DEFAULT_MODEL):
    path = Path(path)
    if path.name.lower() == '.env' or path.name.lower().startswith('.env.'):
        raise ValueError('credential files forbidden')
    return validate_model(read_json(path, limit=500_000))


def predict(model, features):
    validate_model(model)
    if not isinstance(features, dict) or set(features) != set(FEATURES) or any(
            not finite(v) or abs(v) > 1e6 for v in features.values()):
        raise ValueError('inference needs exact bounded finite features')
    vector = [features[k] for k in FEATURES]
    scores = []
    for tree in model['trees']:
        index = 0
        while tree[index][0] >= 0:
            feature, cutoff, left, right, _ = tree[index]
            # sklearn evaluates decision-tree inputs as float32, mirrored explicitly.
            import struct
            value = struct.unpack('f', struct.pack('f', vector[feature]))[0]
            index = left if value <= cutoff else right
        scores.append(tree[index][4])
    return sum(scores)/len(scores)


def analyze_local_signal(signal, model_path=None, reliability_model_path=None):
    values, times, rate, basis = validate_signal(signal)
    measurements = analyze_signal(signal)
    result = {'status': 'unavailable', 'source_id': signal['source_id'],
              'subject_id': signal['subject_id'], 'observed_at': signal['observed_at'],
              'channel': signal['channel'].upper(), 'method': 'BioTwin local signal review v2',
              'time_basis': basis, 'warning': WARNING, 'measurements': measurements,
              'windows': [], 'model': {'id': None, 'status': 'unavailable', 'target': 'injected technical corruption'},
              'reliability': analyze_reliability(signal, reliability_model_path)}
    if signal['channel'].upper() != 'ECG':
        result['model']['reason'] = 'No trained BVP model; engineering estimates only'
        return result
    try:
        model = load_model(model_path or DEFAULT_MODEL)
    except (OSError, ValueError):
        result['model']['reason'] = 'Trained model missing or invalid'
        return result
    result['model'].update(id=model['id'], status=model['deployment'], sha256=model['sha256'])
    result['status'] = model.get('natural_head', model)['deployment']
    if rate is None:
        result['model']['reason'] = 'Nominal sample rate unavailable; original timing retained'
        result['model']['status'] = 'unavailable'
        result['status'] = 'unavailable'
        return result
    width = round(rate*10)
    for lo in range(0, len(values), width):
        hi = min(len(values), lo+width)
        window = {'start_seconds': times[lo], 'end_seconds': times[hi-1]+1/rate,
                  'sample_start': lo, 'sample_end_exclusive': hi,
                  'technical_corruption_score': None, 'status': 'unavailable',
                  'action': 'Inspect original trace; retain all observations', 'reasons': []}
        if hi-lo != width:
            window['reasons'].append('partial 10-second window')
        elif any(v is None for v in values[lo:hi]):
            window['reasons'].append('missing samples; no interpolation')
        elif abs(times[hi-1]-times[lo]-(width-1)/rate) > .5 or any(
                (times[i+1]-times[i])*rate > 3 for i in range(lo, hi-1)):
            window['reasons'].append('timing support inconsistent')
        elif rate != 256:
            window['reasons'].append('model trained only on nominal 256 Hz ECG')
        else:
            features = extract_features(values[lo:hi])
            score = predict(model, features)
            window['technical_corruption_score'] = round(score, 6)
            natural = model.get('natural_head')
            if natural:
                window['author_exclusion_score'] = round(predict(natural, features), 6)
                window['author_exclusion_status'] = natural['deployment']
            # Real input never inherits the controlled-injection demonstration gate.
            window['injection_scope'] = 'experimental_scorer_only_on_real_inputs'
            flagged = engineering_flag(features)
            window['engineering_flag'] = flagged
            if flagged:
                window['reasons'].append('separate deterministic engineering flag')
            if not natural or natural['deployment'] == 'experimental_scorer_only':
                window['status'] = 'uncertain'
            else:
                window['status'] = 'review_recommended' if window['author_exclusion_score'] >= .5 or flagged else (
                    'uncertain' if .35 <= window['author_exclusion_score'] < .5 else 'no_author_exclusion_pattern_detected')
            if flagged or (natural and natural['deployment'] == 'experimental_review' and window['author_exclusion_score'] >= .5):
                window['action'] = 'Inspect this segment; consider repeating measurement; retain health observations'
            if .35 <= score <= .65:
                window['reasons'].append('injected-corruption score near fixed decision boundary; uncalibrated')
            if natural and .35 <= window['author_exclusion_score'] <= .65:
                window['reasons'].append('author-exclusion score near fixed decision boundary; uncalibrated')
            demo = signal.get('controlled_injection_demo')
            if (isinstance(demo, dict) and demo.get('kind') == 'biotwin-controlled-injection-v2'
                    and signal['source_id'].endswith(':synthetic-injection:'+str(demo.get('family')))):
                window['scope'] = 'deliberately injected demonstration; not a NASA-observed artifact'
                window['status'] = 'review_recommended' if score >= .5 else 'injected_demo_pattern_missed'
                window['action'] = 'Inspect original and deliberately corrupted traces side by side; retain both'
                window['reasons'].append('Known injected example; score is not natural-artifact validation')
        result['windows'].append(window)
    return result


def load_default_model():
    return load_model(DEFAULT_MODEL)


review_signal = analyze_local_signal


def create_injected_demo(signal, family='hold'):
    """Return a separately labelled copy; retain subject/source lineage and original input."""
    values, times, rate, _ = validate_signal(signal)
    if signal['channel'].upper() != 'ECG' or rate != 256 or family not in ('hold', 'clip', 'burst'):
        raise ValueError('controlled demo requires 256 Hz ECG and hold/clip/burst family')
    if len(values) < 2560 or any(v is None for v in values):
        raise ValueError('controlled demo requires at least one complete finite 10-second window')
    result = dict(signal, samples=list(values), quality='review',
                  source_id=signal['source_id']+':synthetic-injection:'+family,
                  method='BioTwin controlled injection demo v2; source recording plus deliberate synthetic corruption')
    if 'sample_times_seconds' in result:
        result['sample_times_seconds'] = list(times)
    intervals = []
    for lo in range(0, len(values)-2559, 2560):
        original = values[lo:lo+2560]
        seed = int.from_bytes(hashlib.sha256(f'biotwin-demo-v2|{signal["source_id"]}|{lo}|{family}'.encode()).digest()[:8], 'big')
        rng = random.Random(seed)
        std = max(statistics.pstdev(original), 1e-12)
        if family == 'clip':
            middle, width = statistics.mean(original), rng.uniform(.35, .65)*std
            result['samples'][lo:lo+2560] = [max(middle-width, min(middle+width, v)) for v in original]
            start, end = lo, lo+2560
        else:
            length = round(rng.uniform(.5, 2)*256)
            start = lo+rng.randrange(2560-length+1)
            end = start+length
            if family == 'hold':
                result['samples'][start:end] = [values[start]]*length
            else:
                noise = rng.uniform(2, 5)*std
                result['samples'][start:end] = [v+rng.gauss(0, noise) for v in values[start:end]]
        intervals.append({'sample_start': start, 'sample_end_exclusive': end,
                          'start_seconds': times[start], 'end_seconds': times[end-1]+1/rate})
    result['controlled_injection_demo'] = {'kind': 'biotwin-controlled-injection-v2',
        'family': family, 'original_source_id': signal['source_id'], 'original_method': signal['method'], 'intervals': intervals,
        'warning': 'Deliberately injected technical corruption, not a NASA-observed natural artifact. Original input retained separately.'}
    return result
