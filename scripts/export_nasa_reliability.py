"""Descriptive reliability export for the parent's six explicitly frozen SOTERIA sources.

No reference beats, NASA accuracy evaluation, new source selection or clock merging.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
import hashlib
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biotwin.ecg_reliability import estimate_window, load_model, predict_error, read_finite_json, safe_path, WARNING
from scripts.benchmark_ecg_reliability import write_new

SOURCE_KEYS = {f'Crew_{crew}/Processing/abm_leftseat_scenario{scenario}.csv'
               for crew, scenario in (('01', 2), ('02', 2), ('03', 2), ('05', 2), ('06', 1), ('10', 2))}


def sha256_file(path):
    hasher = hashlib.sha256()
    with safe_path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_selected_trace(path, duration=600):
    times, values = [], []
    with safe_path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if header.count('UserTimeStamp') != 1 or header.count('ECG') != 1:
            raise ValueError('unique NASA timestamp and ECG columns required')
        ti, vi = header.index('UserTimeStamp'), header.index('ECG')
        for row in reader:
            when = float(row[ti])
            if not math.isfinite(when) or times and when <= times[-1]:
                raise ValueError('source timestamps nonfinite or nonincreasing')
            if times and when >= times[0]+duration:
                break
            try:
                value = float(row[vi])
                if not math.isfinite(value) or abs(value) > 1e12:
                    value = None
            except (ValueError, IndexError):
                value = None
            times.append(when)
            values.append(value)
            if len(times) > 256*600*2:
                raise ValueError('NASA selected sample bound exceeded')
    if len(times) < 2:
        raise ValueError('NASA selected source lacks samples')
    return times, values


def source_timing(times):
    """Declared coverage stays inside the frozen crop; retain actual endpoint evidence."""
    selected_end = times[0]+600
    nominal_endpoint = times[-1]+1/256
    return {'source_start_seconds': times[0], 'source_end_seconds': min(selected_end, nominal_endpoint),
            'source_last_timestamp_seconds': times[-1],
            'source_nominal_endpoint_seconds': nominal_endpoint,
            'selected_crop_end_seconds': selected_end}


def score_trace(times, values, source, subject, model=None):
    fs = 256
    start = times[0]
    timing = source_timing(times)
    available_end = timing['source_end_seconds']
    windows = []
    for offset in range(0, 600, 30):
        begin, end = start+offset, min(start+offset+30, available_end)
        if begin >= available_end:
            break
        lo, hi = bisect_left(times, begin), bisect_left(times, end)
        context_lo, context_hi = bisect_left(times, max(start, begin-2)), bisect_left(times, min(available_end, end+2))
        row = {'source_id': source, 'subject_id': subject, 'sample_start': lo, 'sample_end_exclusive': hi,
               'start_seconds': begin, 'end_seconds': end,
               'availability_seconds': min(available_end, times[context_hi-1]+1/fs),
               'estimate_bpm': None, 'beat_count': None, 'features': None, 'rules_accept': False,
               'error_score': None, 'learned_accept': False, 'status': 'unavailable', 'reasons': [],
               'reference_bpm': None, 'reference_status': 'unavailable: no independent NASA beat annotations',
               'provenance': {'nominal_rate_hz': fs, 'source_unit': 'source amplitude unit unverified',
                              'context_sample_start': context_lo, 'context_sample_end_exclusive': context_hi,
                              'source_last_timestamp_seconds': timing['source_last_timestamp_seconds'],
                              'source_nominal_endpoint_seconds': timing['source_nominal_endpoint_seconds'],
                              'selected_crop_end_seconds': timing['selected_crop_end_seconds'],
                              'time_basis': 'original source-relative seconds; synchronization unverified'}}
        if hi-lo != fs*30 or end-begin < 30-1e-8:
            row['reasons'].append('partial 30-second source window; no interpolation')
        elif any(v is None for v in values[context_lo:context_hi]):
            row['reasons'].append('missing source samples; no interpolation')
        elif any(abs((times[i+1]-times[i])*fs-1) > .01 for i in range(context_lo, context_hi-1)):
            row['reasons'].append('source timing inconsistent with declared nominal rate')
        else:
            estimate = estimate_window(values[context_lo:context_hi], fs, lo-context_lo, hi-context_lo)
            row.update(estimate)
            row['detected_beats_sample_indices'] = [i+context_lo for i in estimate['detected_beats_sample_indices']]
            row['status'] = 'experimental_review'
            row['reasons'].extend(['external NASA source domain; learned acceptance withheld',
                                   'previously inspected descriptive source; no NASA accuracy or health inference'])
            if model:
                row['error_score'] = predict_error(model, estimate['features'])
            if context_hi == len(times):
                row['reasons'].append('right context truncated at frozen selected crop; diagnostic estimate only')
        windows.append(row)
    return windows


def export(selection_path, model_path, output):
    selection_path = safe_path(selection_path)
    selection = read_finite_json(selection_path, limit=100_000)
    if (selection.get('id') != 'soteria-recorded-first600-v1' or selection.get('duration_seconds') != 600 or
            len(selection.get('sources', [])) != 6 or {s.get('source_key') for s in selection['sources']} != SOURCE_KEYS):
        raise ValueError('exact parent-frozen six-source receipt required')
    model = load_model(model_path) if model_path else None
    series = []
    for entry in selection['sources']:
        relative = 'data/raw/soteria/'+entry['source_key']
        if entry.get('local_path') != relative:
            raise ValueError('NASA source path differs from explicit receipt')
        path = safe_path(ROOT/relative)
        if path.stat().st_size != entry['bytes'] or sha256_file(path) != entry['sha256']:
            raise ValueError('NASA source identity mismatch')
        times, values = read_selected_trace(path)
        source = 'nasa-soteria:'+entry['source_key']
        subject = entry['source_key'].split('/')[0]
        windows = score_trace(times, values, source, subject, model)
        series.append({'source': source, 'subject': subject, 'split': 'previously_seen_external_descriptive',
                       'time_basis': 'source-relative-seconds', 'source_start_seconds': times[0],
                       'source_end_seconds': source_timing(times)['source_end_seconds'],
                       'url': entry['url'], 'sha256': entry['sha256'],
                       'windows': windows})
    result = {'schema_version': 1, 'selection_sha256': sha256_file(selection_path),
              'model_sha256': model['sha256'] if model else None,
              'exporter_sha256': sha256_file(__file__), 'warning': WARNING,
              'interpretation': 'Previously inspected NASA pilot data; no reference labels or NASA accuracy claim',
              'series': series}
    write_new(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True)
    parser.add_argument('--model')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = export(args.selection, args.model, args.output)
    print(f'Exported {len(result["series"])} independent previously seen NASA source series')


if __name__ == '__main__':
    main()
