"""Standard-library ECG count estimator and experimental error-rejection evidence.

No diagnosis, clinical clearance, interpolation, evidence deletion or vital-sign update.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
import stat
import sys

FEATURES = ('flat_fraction', 'edge_fraction', 'derivative_ratio', 'crest_factor',
            'kurtosis', 'block_std_ratio', 'detector_agreement', 'rate_disagreement',
            'interval_cv', 'short_interval_fraction', 'long_interval_fraction', 'peak_strength')
DEFAULT_MODEL = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'assets' / 'desktop' / 'ecg-reliability-v1.json'
WARNING = ('Experimental source-local ECG beat-count estimate and error-rejection score, '
           'not a calibrated probability, diagnosis, healthy status or medical clearance. '
           'Irregular rhythms are not automatically noise. Keep original evidence and source clocks; '
           'never populate crew clinical vitals from these estimates.')
PARAMETERS = {'window_seconds': 30, 'context_seconds': 2, 'refractory_seconds': .24,
              'baseline_halfwidth_seconds': .35, 'energy_halfwidth_seconds': .025,
              'derivative_halfwidth_seconds': .05, 'threshold_sd': .5,
              'refine_seconds': .075, 'match_tolerance_seconds': .15,
              'bad_error_bpm': 5, 'seed': 20261114}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _mean_filter(values, width):
    prefix = [0.]
    for value in values:
        prefix.append(prefix[-1]+value)
    return [(prefix[min(len(values), i+width+1)]-prefix[max(0, i-width)]) /
            (min(len(values), i+width+1)-max(0, i-width)) for i in range(len(values))]


def match_beats(detected, reference, tolerance):
    """Ordered one-to-one interval matching; no reference reuse."""
    i = j = matched = 0
    while i < len(detected) and j < len(reference):
        if abs(detected[i]-reference[j]) <= tolerance:
            matched += 1
            i += 1
            j += 1
        elif detected[i] < reference[j]:
            i += 1
        else:
            j += 1
    return {'tp': matched, 'fp': len(detected)-matched, 'fn': len(reference)-matched}


def _peaks(envelope, centered, rate):
    threshold = statistics.median(envelope)+PARAMETERS['threshold_sd']*statistics.pstdev(envelope)
    candidates = [i for i in range(1, len(envelope)-1)
                  if envelope[i] > threshold and envelope[i] > envelope[i-1] and envelope[i] >= envelope[i+1]]
    # Linear-size occupancy mask bounds refractory selection even for noisy windows.
    occupied = bytearray(len(envelope))
    distance = max(1, round(rate*PARAMETERS['refractory_seconds']))
    selected = []
    for i in sorted(candidates, key=lambda j: (-envelope[j], j)):
        if not occupied[i]:
            selected.append(i)
            occupied[max(0, i-distance+1):min(len(envelope), i+distance)] = b'\1'*(min(len(envelope), i+distance)-max(0, i-distance+1))
    width = max(1, round(rate*PARAMETERS['refine_seconds']))
    refined = sorted(set(max(range(max(0, i-width), min(len(envelope), i+width+1)),
                             key=lambda j: (abs(centered[j]), -j)) for i in selected))
    # Refinement can move neighbors closer together; preserve one refractory event.
    output = []
    for i in refined:
        if output and i-output[-1] < distance:
            if abs(centered[i]) > abs(centered[output[-1]]):
                output[-1] = i
        else:
            output.append(i)
    return output


def estimate_window(values, rate, score_start=0, score_end=None):
    """Detect with supplied context; count only exact [score_start, score_end)."""
    if (not isinstance(values, (list, tuple)) or not 32 <= len(values) <= 60_000 or
            any(not _finite(v) or abs(v) > 1e12 for v in values) or
            not _finite(rate) or not 16 <= rate <= 1000):
        raise ValueError('bounded finite ECG samples and 16..1000 Hz required')
    score_end = len(values) if score_end is None else score_end
    if type(score_start) is not int or type(score_end) is not int or not 0 <= score_start < score_end <= len(values):
        raise ValueError('invalid scored interval')
    slow = _mean_filter(values, max(1, round(rate*PARAMETERS['baseline_halfwidth_seconds'])))
    centered = [a-b for a, b in zip(values, slow)]
    energy = _mean_filter([v*v for v in centered], max(1, round(rate*PARAMETERS['energy_halfwidth_seconds'])))
    difference = [0.] + [b-a for a, b in zip(centered, centered[1:])]
    derivative = _mean_filter([v*v for v in difference], max(1, round(rate*PARAMETERS['derivative_halfwidth_seconds'])))
    primary = [i for i in _peaks(energy, centered, rate) if score_start <= i < score_end]
    secondary = [i for i in _peaks(derivative, centered, rate) if score_start <= i < score_end]
    x = values[score_start:score_end]
    std = max(statistics.pstdev(x), 1e-12)
    mean = statistics.mean(x)
    minimum, maximum = min(x), max(x)
    span = maximum-minimum
    epsilon = max(span*1e-10, 1e-12)
    diff = [b-a for a, b in zip(x, x[1:])]
    normalized = [(v-mean)/std for v in x]
    block = max(2, len(x)//10)
    deviations = [statistics.pstdev(x[i:i+block])/std for i in range(0, len(x)-block+1, block)]
    intervals = [(b-a)/rate for a, b in zip(primary, primary[1:])]
    agreement = match_beats(primary, secondary, round(rate*.15))['tp']*2/max(1, len(primary)+len(secondary))
    features = dict(zip(FEATURES, [
        sum(abs(d) <= epsilon for d in diff)/max(1, len(diff)),
        sum(abs(v-minimum) <= epsilon or abs(v-maximum) <= epsilon for v in x)/len(x),
        math.sqrt(statistics.mean((d/std)**2 for d in diff)),
        max(abs(v) for v in normalized), statistics.mean(v**4 for v in normalized),
        max(deviations)/(min(deviations)+.1), agreement,
        abs(len(primary)-len(secondary))*60*rate/(score_end-score_start),
        statistics.pstdev(intervals)/statistics.mean(intervals) if len(intervals) >= 2 else 0.,
        sum(v < .30 for v in intervals)/max(1, len(intervals)),
        sum(v > 2 for v in intervals)/max(1, len(intervals)),
        statistics.mean(abs(centered[i])/std for i in primary) if primary else 0.]))
    if any(not _finite(v) or abs(v) > 1e6 for v in features.values()):
        raise ValueError('feature arithmetic out of bounds')
    reasons = []
    if features['flat_fraction'] > .05:
        reasons.append('sample holds or flat trace')
    if features['edge_fraction'] > .15:
        reasons.append('many samples at amplitude extrema')
    if agreement < .70:
        reasons.append('peak estimators disagree')
    if not primary:
        reasons.append('no supported detected peaks; physiology unknown')
    return {'estimate_bpm': len(primary)*60*rate/(score_end-score_start), 'beat_count': len(primary),
            'detected_beats_sample_indices': primary, 'secondary_beat_count': len(secondary),
            'features': features, 'rules_accept': not reasons, 'reasons': reasons}


def model_digest(model):
    return hashlib.sha256(json.dumps({k: v for k, v in model.items() if k != 'sha256'},
                                    sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_model(model):
    keys = {'schema_version', 'id', 'kind', 'features', 'mean', 'scale', 'coefficients',
            'intercept', 'threshold', 'deployment', 'provenance', 'sha256'}
    if not isinstance(model, dict) or set(model) != keys:
        raise ValueError('reliability model schema mismatch')
    if (type(model['schema_version']) is not int or model['schema_version'] != 1 or
            model['id'] != 'ecg-reliability-v1' or model['kind'] != 'logistic-error-rejection' or
            model['features'] != list(FEATURES) or model['deployment'] not in ('experimental_review', 'benchmark_gate_passed')):
        raise ValueError('unsupported reliability model')
    for key in ('mean', 'scale', 'coefficients'):
        vector = model[key]
        if not isinstance(vector, list) or len(vector) != len(FEATURES) or any(not _finite(v) or abs(v) > 1e9 for v in vector):
            raise ValueError('invalid model vector')
    if any(v < 1e-12 for v in model['scale']) or not _finite(model['intercept']) or abs(model['intercept']) > 1e9:
        raise ValueError('invalid model scale/intercept')
    if model['threshold'] is not None and (not _finite(model['threshold']) or not 0 <= model['threshold'] <= 1):
        raise ValueError('invalid rejection threshold')
    provenance = model['provenance']
    if (not isinstance(provenance, dict) or not 1 <= len(provenance) <= 20 or
            any(not isinstance(k, str) or len(k) > 200 or not isinstance(v, str) or
                len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for k, v in provenance.items())):
        raise ValueError('invalid provenance')
    if model['sha256'] != model_digest(model):
        raise ValueError('model integrity mismatch')
    return model


def safe_path(path):
    """Inspect metadata only before resolving; reject secret components/reparse aliases."""
    path = Path(path).absolute()
    if any(part.lower() == '.env' or part.lower().startswith('.env.') for part in path.parts):
        raise ValueError('credential path forbidden')
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise ValueError('symlink/junction paths forbidden')
    resolved = path.resolve()
    if any(part.lower() == '.env' or part.lower().startswith('.env.') for part in resolved.parts):
        raise ValueError('resolved credential path forbidden')
    return resolved


def read_finite_json(path, limit=50_000):
    path = safe_path(path)
    if path.suffix.lower() != '.json' or not path.is_file() or not 1 <= path.stat().st_size <= limit:
        raise ValueError('bounded regular JSON file required')
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate model JSON key')
            obj[key] = value
        return obj
    def bad_constant(value):
        raise ValueError('nonfinite JSON constant')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique, parse_constant=bad_constant)


def load_model(path=DEFAULT_MODEL):
    return validate_model(read_finite_json(path))


def predict_error(model, features):
    validate_model(model)
    if (not isinstance(features, dict) or set(features) != set(FEATURES) or
            any(not _finite(v) or abs(v) > 1e6 for v in features.values())):
        raise ValueError('exact bounded finite feature dictionary required')
    terms = [(features[name]-model['mean'][i])/model['scale'][i]*model['coefficients'][i]
             for i, name in enumerate(FEATURES)]
    logit = model['intercept']+sum(terms)
    if any(not math.isfinite(value) for value in terms) or not math.isfinite(logit):
        raise ValueError('nonfinite inference arithmetic')
    return 1/(1+math.exp(-max(-700, min(700, logit))))


def analyze_reliability(signal, model_path=None):
    from .signal_measurements import validate_signal
    values, times, rate, basis = validate_signal(signal)
    result = {'schema_version': 1, 'status': 'unavailable', 'source_id': signal['source_id'],
              'subject_id': signal['subject_id'], 'observed_at': signal['observed_at'],
              'time_basis': basis, 'warning': WARNING, 'windows': [],
              'model': {'id': None, 'status': 'unavailable'}, 'reasons': [],
              'provenance': {'method': 'BioTwin ECG count reliability v1', 'source_method': signal['method'],
                             'source_unit': signal['unit'], 'source_quality': signal.get('quality', 'unknown')}}
    if signal['channel'].upper() != 'ECG' or rate is None:
        result['reasons'].append('ECG with declared sampling rate required')
        return result
    try:
        model = load_model(model_path or DEFAULT_MODEL)
        result['model'] = {'id': model['id'], 'status': model['deployment'], 'sha256': model['sha256']}
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        model = None
        result['model']['reason'] = 'Reliability model absent or invalid; no learned acceptance'
    width = round(rate*30)
    padding = round(rate*2)
    for lo in range(0, len(values), width):
        hi = min(len(values), lo+width)
        window = {'sample_start': lo, 'sample_end_exclusive': hi, 'start_seconds': times[lo],
                  'end_seconds': times[hi-1]+1/rate, 'estimate_bpm': None, 'beat_count': None,
                  'detected_beats_sample_indices': [], 'features': None, 'rules_accept': False,
                  'error_score': None, 'learned_accept': None, 'status': 'unavailable', 'reasons': [],
                  'provenance': {'source_id': signal['source_id'], 'subject_id': signal['subject_id'],
                                 'sample_rate_hz': rate, 'score_interval': 'half-open',
                                 'context_sample_start': max(0, lo-padding),
                                 'context_sample_end_exclusive': min(len(values), hi+padding)}}
        context_lo, context_hi = max(0, lo-padding), min(len(values), hi+padding)
        window['availability_seconds'] = times[context_hi-1]+1/rate
        if hi-lo != width:
            window['reasons'].append('partial 30-second window')
        elif any(v is None for v in values[context_lo:context_hi]):
            window['reasons'].append('missing samples; no interpolation')
        elif any(abs((times[i+1]-times[i])*rate-1) > .01 for i in range(context_lo, context_hi-1)):
            window['reasons'].append('nonuniform timing; estimate withheld')
        else:
            estimate = estimate_window(values[context_lo:context_hi], rate, lo-context_lo, hi-context_lo)
            window.update(estimate)
            window['detected_beats_sample_indices'] = [i+context_lo for i in estimate['detected_beats_sample_indices']]
            window['status'] = 'experimental_review'
            if signal.get('quality') != 'usable':
                window['reasons'].append('source quality requires review; acceptance withheld')
            if model:
                score = predict_error(model, estimate['features'])
                window['error_score'] = score
                if model['threshold'] is not None:
                    accepted = score <= model['threshold']
                    in_scope = (rate == 360 and signal.get('reliability_domain') == 'mitdb-1.0.0')
                    full_right_context = context_hi >= hi+padding
                    window['learned_accept'] = bool(accepted and model['deployment'] == 'benchmark_gate_passed'
                                                    and signal.get('quality') == 'usable' and in_scope and full_right_context)
                    if not in_scope:
                        window['reasons'].append('external source domain; benchmark acceptance does not transfer')
                    if signal.get('reliability_domain') == 'nstdb-1.0.0':
                        window['reasons'].append('stress database is report-only; learned acceptance withheld')
                    if not full_right_context:
                        window['reasons'].append('right context truncated by supplied crop; acceptance withheld')
                if model['deployment'] != 'benchmark_gate_passed':
                    window['reasons'].append('held-out benchmark gate not passed; no learned acceptance')
            window['reasons'].append('review source-local estimate; clinical meaning remains unknown')
        result['windows'].append(window)
    if any(w['estimate_bpm'] is not None for w in result['windows']):
        result['status'] = 'experimental_review'
    return result
