"""Custom SOTERIA replay, independent of NASA's processing scripts.

Source timestamps and units are retained; no cross-device synchronization claim.
Only manifest-listed ECG and BVP CSVs may be read. No network or model imports.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def sha256_file(path):
    path = Path(path)
    if path.name == '.env' or path.name.startswith('.env.'):
        raise ValueError('credential files are forbidden')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path, limit=16_000_000):
    path = Path(path)
    if path.name == '.env' or path.name.startswith('.env.'):
        raise ValueError('credential files are forbidden')
    if path.stat().st_size > limit:
        raise ValueError('JSON exceeds size bound')
    def bad_constant(value):
        raise ValueError('nonfinite JSON constant')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON object key')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=bad_constant, object_pairs_hook=unique_object)


def manifest_entry(data_dir, source_key, verify=True):
    """Resolve exactly one manifest entry under data/raw/soteria, then hash it."""
    root = Path(data_dir).resolve()
    if not isinstance(source_key, str):
        raise ValueError('source key must be a string')
    key = PurePosixPath(source_key)
    if key.is_absolute() or '..' in key.parts or '\\' in source_key or ':' in source_key:
        raise ValueError('unsafe source key')
    entries = read_json(root / 'download-manifest.json')
    if not isinstance(entries, list) or len(entries) > 10_000 or any(not isinstance(e, dict) for e in entries):
        raise ValueError('invalid acquisition manifest')
    found = [e for e in entries if e.get('source_key') == source_key]
    if len(found) != 1:
        raise ValueError('source must occur exactly once in acquisition manifest')
    entry = found[0]
    if (not isinstance(entry.get('local_path'), str) or type(entry.get('bytes')) is not int
            or not 0 < entry['bytes'] <= 2_000_000_000 or not isinstance(entry.get('sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
        raise ValueError('invalid acquisition entry')
    local = PurePosixPath(entry['local_path'])
    # Acquisition paths are project-relative data/... . A moved data directory is supported.
    if local.parts[:3] != ('data', 'raw', 'soteria') or '..' in local.parts or '\\' in str(local):
        raise ValueError('manifest path outside raw data')
    path = (root / Path(*local.parts[1:])).resolve()
    raw_root = (root / 'raw' / 'soteria').resolve()
    if not path.is_relative_to(raw_root) or path.relative_to(raw_root).as_posix() != source_key:
        raise ValueError('manifest/source path mismatch')
    if path.name == '.env' or path.name.startswith('.env.'):
        raise ValueError('credential files are forbidden')
    if verify and (path.stat().st_size != entry['bytes'] or sha256_file(path) != entry['sha256']):
        raise ValueError('source integrity mismatch')
    return path, entry


@dataclass(frozen=True)
class Waveform:
    source_key: str
    source_sha256: str
    channel: str
    nominal_rate_hz: float
    times: tuple[float, ...]
    values: tuple[float | None, ...]
    nonfinite_count: int

    def audit(self):
        span = self.times[-1] - self.times[0]
        gaps = [b-a for a, b in zip(self.times, self.times[1:])]
        reasons = []
        if self.nonfinite_count:
            reasons.append('nonfinite/missing channel samples')
        if any(g > 3 / self.nominal_rate_hz for g in gaps):
            reasons.append('timestamp gaps exceed three nominal periods; cause unverified')
        if abs((len(self.times)-1)/span/self.nominal_rate_hz-1) > .05:
            reasons.append('count/span differs from nominal rate by more than 5%')
        return {'source_key': self.source_key, 'source_sha256': self.source_sha256,
                'channel': self.channel, 'sample_count': len(self.times),
                'nominal_rate_hz': self.nominal_rate_hz,
                'observed_count_per_span_hz': (len(self.times)-1)/span,
                'source_time_start': self.times[0], 'source_time_end': self.times[-1],
                'span_seconds': span, 'max_time_step_seconds': max(gaps),
                'nonfinite_count': self.nonfinite_count, 'quality_reasons': reasons,
                'time_basis': 'source-relative seconds; synchronization unverified',
                'unit': 'source amplitude unit unverified'}


def load_waveform(data_dir, source_key):
    """Stream selected columns; reject malformed/nonmonotonic time, retain missing signal."""
    name = PurePosixPath(source_key).name
    if name.startswith('abm_') and name.endswith('.csv'):
        channel, rate = 'ECG', 256.0
    elif name.startswith('emp_bvp_') and name.endswith('.csv'):
        channel, rate = 'Bvp', 64.0
    else:
        raise ValueError('only ECG/BVP replay sources supported')
    path, entry = manifest_entry(data_dir, source_key)
    times, values, missing = [], [], 0
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if header.count('UserTimeStamp') != 1 or header.count(channel) != 1:
            raise ValueError('required unique timestamp/channel columns missing')
        ti, vi = header.index('UserTimeStamp'), header.index(channel)
        for line, row in enumerate(reader, 2):
            if len(times) >= 5_000_000:
                raise ValueError('waveform sample bound exceeded')
            try:
                when = float(row[ti])
            except (IndexError, ValueError):
                raise ValueError(f'invalid timestamp at line {line}') from None
            if not math.isfinite(when) or times and when <= times[-1]:
                raise ValueError(f'nonfinite or nonincreasing timestamp at line {line}')
            try:
                value = float(row[vi])
                if not math.isfinite(value):
                    value = None
            except (IndexError, ValueError):
                value = None
            missing += value is None
            times.append(when)
            values.append(value)
    if len(times) < 2:
        raise ValueError('waveform requires at least two samples')
    return Waveform(source_key, entry['sha256'], channel, rate, tuple(times), tuple(values), missing)


def replay_window(waveform, start_seconds, duration_seconds=10, max_points=1000):
    """Bounded source-relative [start,end) replay, min/max buckets retain local peaks."""
    if (not isinstance(start_seconds, (int, float)) or not math.isfinite(start_seconds)
            or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds)
            or duration_seconds <= 0 or duration_seconds > 300 or start_seconds < waveform.times[0]
            or type(max_points) is not int or not 4 <= max_points <= 20_000):
        raise ValueError('invalid replay bounds')
    lo = bisect_left(waveform.times, start_seconds)
    hi = bisect_left(waveform.times, start_seconds + duration_seconds)
    width = max(1, math.ceil((hi-lo) / (max_points//3)))
    selected = []
    for left in range(lo, hi, width):
        indices = list(range(left, min(hi, left+width)))
        finite = [i for i in indices if waveform.values[i] is not None]
        picks = ({min(finite, key=lambda i: waveform.values[i]), max(finite, key=lambda i: waveform.values[i])}
                 if finite else {left})
        # Keep missingness visible even when another bucket sample is finite.
        if len(finite) != len(indices):
            picks.add(next(i for i in indices if waveform.values[i] is None))
        selected.extend(sorted(picks))
    reasons = []
    if any(v is None for v in waveform.values[lo:hi]):
        reasons.append('nonfinite/missing signal samples')
    if any(b-a > 3/waveform.nominal_rate_hz for a, b in zip(waveform.times[lo:hi], waveform.times[lo+1:hi])):
        reasons.append('timestamp gaps exceed three nominal periods; cause unverified')
    finite_values = [v for v in waveform.values[lo:hi] if v is not None]
    if finite_values and max(finite_values)-min(finite_values) <= 1e-6:
        reasons.append('flatline source amplitude')
    if hi-lo < duration_seconds * waveform.nominal_rate_hz * .95:
        reasons.append('less than 95% nominal sample coverage; reason unverified')
    return {'source_key': waveform.source_key, 'source_sha256': waveform.source_sha256,
            'channel': waveform.channel, 'source_start_seconds': start_seconds,
            'source_end_seconds': start_seconds+duration_seconds, 'sample_start': lo, 'sample_end': hi,
            'sample_count': hi-lo, 'nominal_rate_hz': waveform.nominal_rate_hz,
            'points': [{'source_seconds': waveform.times[i], 'value': waveform.values[i]} for i in selected],
            'preview_method': 'min/max buckets', 'quality_reasons': reasons,
            'synchronization': 'unverified', 'unit': 'source amplitude unit unverified'}
