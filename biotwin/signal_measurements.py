"""Bounded, dependency-free engineering beat/pulse estimates, never clinical HRV."""
from __future__ import annotations

from datetime import datetime
import math
import statistics

WARNING = ('Unvalidated engineering estimate for review. Not astronaut/clinical validation; '
           'ECG beat intervals and BVP pulse intervals are different. No SpO2 or sleep inference.')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e12


def validate_signal(signal):
    if not isinstance(signal, dict) or signal.get('channel', '').upper() not in ('ECG', 'BVP'):
        raise ValueError('signal channel must be ECG or BVP')
    values = signal.get('samples')
    rate = signal.get('sample_rate_hz')
    if not isinstance(values, list) or not 2 <= len(values) <= 30_000:
        raise ValueError('signal needs 2..30000 samples')
    if any(v is not None and not finite(v) for v in values):
        raise ValueError('samples must be bounded finite numbers or null')
    if rate is not None and (not finite(rate) or not 16 <= rate <= 1000):
        raise ValueError('sample_rate_hz must be 16..1000')
    for key in ('source_id', 'subject_id', 'unit', 'method', 'time_basis'):
        if not isinstance(signal.get(key), str) or not 1 <= len(signal[key]) <= 500:
            raise ValueError('missing/bounded provenance: '+key)
    try:
        observed = datetime.fromisoformat(signal['observed_at'])
        if observed.utcoffset() is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('observed_at requires timezone-aware ISO time') from None
    times = signal.get('sample_times_seconds')
    if times is None:
        if rate is None:
            raise ValueError('explicit times or nominal rate required')
        times = [i / rate for i in range(len(values))]
        basis = 'derived uniform offsets from declared sample_rate_hz; not original timestamps'
    else:
        if (not isinstance(times, list) or len(times) != len(values) or
                any(not finite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:]))):
            raise ValueError('sample_times_seconds must be finite, aligned and strictly increasing')
        basis = signal['time_basis']
    return values, times, float(rate) if rate is not None else None, basis


def _mean_filter(values, width):
    prefix = [0.]
    for value in values:
        prefix.append(prefix[-1] + value)
    return [(prefix[min(len(values), i+width+1)] - prefix[max(0, i-width)]) /
            (min(len(values), i+width+1)-max(0, i-width)) for i in range(len(values))]


def analyze_signal(signal):
    values, times, rate, basis = validate_signal(signal)
    channel = signal['channel'].upper()
    result = {'status': 'unavailable', 'rate_per_minute': None,
              'rate_kind': 'ECG beat rate' if channel == 'ECG' else 'BVP pulse rate',
              'interval_rmssd_ms': None, 'variability_status': 'unavailable',
              'beat_count': 0, 'duration_seconds': times[-1]-times[0],
              'source_id': signal['source_id'], 'subject_id': signal['subject_id'],
              'observed_at': signal['observed_at'], 'time_basis': basis,
              'method': 'BioTwin engineering peaks v1', 'warning': WARNING, 'reasons': []}
    reasons = result['reasons']
    duration = result['duration_seconds']
    if rate is None:
        reasons.append('nominal sample rate unavailable; source timestamps retained')
        return result
    if duration < 8:
        reasons.append('at least 8 seconds required')
    if any(v is None for v in values):
        reasons.append('missing samples; no interpolation used')
    steps = [b-a for a, b in zip(times, times[1:])]
    if (abs((len(values)-1)/max(duration, 1e-9)/rate-1) > .05 or
            sum(abs(s*rate-1) > .25 for s in steps)/len(steps) > .05 or max(steps)*rate > 2):
        reasons.append('nonuniform or inconsistent sampling; rate/variability withheld')
    if signal.get('quality') != 'usable':
        reasons.append('source quality is missing, review-required or unusable; estimate withheld')
    if reasons:
        return result
    span = max(values)-min(values)
    if span <= max(1e-12, max(abs(v) for v in values)*1e-10):
        reasons.append('flat signal')
        return result
    slow = _mean_filter(values, max(1, round(rate*.35)))
    centered = [v-b for v, b in zip(values, slow)]
    if channel == 'ECG':
        envelope = _mean_filter([v*v for v in centered], max(1, round(rate*.025)))
    else:
        envelope = _mean_filter(centered, max(1, round(rate*.04)))
    middle = statistics.median(envelope)
    threshold = middle + .4*statistics.pstdev(envelope)
    candidates = [i for i in range(1, len(values)-1)
                  if envelope[i] > threshold and envelope[i] > envelope[i-1] and envelope[i] >= envelope[i+1]]
    distance = round(rate*(.28 if channel == 'ECG' else .32))
    selected = []
    for i in sorted(candidates, key=lambda i: envelope[i], reverse=True):
        if all(abs(i-j) >= distance for j in selected):
            selected.append(i)
    selected.sort()
    # Refine ECG event position on the original high-passed absolute deflection.
    if channel == 'ECG':
        width = max(1, round(rate*.06))
        selected = sorted(set(max(range(max(0, i-width), min(len(values), i+width+1)),
                                  key=lambda j: abs(centered[j])) for i in selected))
    intervals = [times[b]-times[a] for a, b in zip(selected, selected[1:])]
    result['beat_count'] = len(selected)
    if len(intervals) < 5:
        reasons.append('fewer than six supported peaks')
        return result
    median = statistics.median(intervals)
    bpm = 60/median
    cv = statistics.pstdev(intervals)/statistics.mean(intervals)
    lag = round(median*rate)
    energy = sum(v*v for v in centered)
    periodicity = sum(a*b for a, b in zip(centered[:-lag], centered[lag:]))/max(energy, 1e-20)
    if not 35 <= bpm <= 210 or cv > .20 or periodicity < .35:
        reasons.append('peak support inconsistent; irregular/noisy signals require inspection')
        return result
    result.update(status='experimental_estimate', rate_per_minute=round(bpm, 2),
                  interval_cv=round(cv, 4), periodicity=round(periodicity, 4))
    if duration >= 30 and len(intervals) >= 20 and cv <= .15:
        result['interval_rmssd_ms'] = round(math.sqrt(statistics.mean(
            ((b-a)*1000)**2 for a, b in zip(intervals, intervals[1:]))), 2)
        result['variability_status'] = 'experimental_short_window_interval_variability'
    else:
        reasons.append('variability requires >=30 seconds and >=21 regular peaks; not clinical HRV')
    return result
