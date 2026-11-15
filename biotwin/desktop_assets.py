"""Explicitly allowlisted, integrity-checked offline desktop examples."""
from __future__ import annotations

from bisect import bisect_left
import hashlib
import math
from pathlib import Path
import re
import sys

from .waveforms import Waveform, read_json, replay_window

EXAMPLE_FILES = ('crew001_ecg.json', 'crew002_ecg.json', 'crew001_bvp.json')
EXAMPLE_CONTEXTS = {
    'crew001_ecg.json': ('Crew 001', 'ECG', 256.0, 'Crew_01/Processing/abm_leftseat_scenario2.csv'),
    'crew002_ecg.json': ('Crew 002', 'ECG', 256.0, 'Crew_02/Processing/abm_leftseat_scenario2.csv'),
    'crew001_bvp.json': ('Crew 001', 'Bvp', 64.0, 'Crew_01/Processing/emp_bvp_leftseat_scenario2.csv'),
}
NOTICE_FILES = ('ATTRIBUTION.txt', 'QUICKSTART-EN.txt', 'QUICKSTART-KO.txt', 'RUNTIME-NOTICES.txt',
                'PYTHON-LICENSE.txt', 'TCL-LICENSE.txt', 'TK-LICENSE.txt', 'PYINSTALLER-LICENSE.txt')
ASSET_FILES = ('manifest.json', *EXAMPLE_FILES, *NOTICE_FILES, 'winscat-reference-manifest.json', 'signal-review-v2.json', 'assistant-protocols-v1.json', 'crew-visuals-v1.json')


def asset_directory():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return base / 'assets' / 'desktop'


def load_examples(directory=None):
    try:
        return _load_examples(directory)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError('Malformed bundled excerpt schema.') from exc


def _load_examples(directory=None):
    root = Path(directory) if directory else asset_directory()
    manifest = read_json(root / 'manifest.json', 100_000)
    if type(manifest) is not dict or set(manifest) != {'format', 'version', 'examples'} or manifest['format'] != 'biotwin-desktop-excerpts' or type(manifest['version']) is not int or manifest['version'] != 1:
        raise ValueError('Unsupported bundled excerpt manifest.')
    entries = manifest['examples']
    if type(entries) is not list or len(entries) != len(EXAMPLE_FILES) or any(type(e) is not dict for e in entries) or {e.get('file') for e in entries} != set(EXAMPLE_FILES):
        raise ValueError('Bundled excerpt allowlist mismatch.')
    result = []
    for entry in entries:
        if set(entry) != {'file', 'sha256', 'bytes'} or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= 3_000_000 or type(entry['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('Malformed bundled manifest entry.')
        path = root / entry['file']
        if path.is_symlink() or path.stat().st_size > 3_000_000:
            raise ValueError('Invalid bundled excerpt file.')
        if path.stat().st_size != entry['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Bundled excerpt integrity mismatch: ' + entry['file'])
        document = read_json(path, 3_000_000)
        required = {'format', 'version', 'crew', 'source_key', 'source_sha256', 'channel', 'nominal_rate_hz',
            'original_sample_count', 'sample_start', 'sample_end_exclusive', 'times', 'values',
            'author_exclusion_sample_bounds', 'annotation', 'annotation_metadata_hashes', 'transformation',
            'time_basis', 'unit', 'author_mark_meaning', 'attribution', 'doi', 'license', 'license_url'}
        if type(document) is not dict or set(document) != required or document['format'] != 'biotwin-source-excerpt' or type(document['version']) is not int or document['version'] != 1:
            raise ValueError('Unsupported bundled excerpt schema.')
        context = EXAMPLE_CONTEXTS[entry['file']]
        if ((document['crew'], document['channel'], document['nominal_rate_hz'], document['source_key']) != context
                or type(document['nominal_rate_hz']) not in (int, float)
                or type(document['source_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', document['source_sha256'])):
            raise ValueError('Bundled source identity or rate mismatch.')
        if (any(type(document[k]) is not str or not document[k].strip() for k in ('transformation', 'time_basis', 'unit', 'author_mark_meaning', 'attribution'))
                or document['unit'] != 'source amplitude unit unverified'
                or document['license'] != 'CC BY 4.0' or document['license_url'] != 'https://creativecommons.org/licenses/by/4.0/'
                or document['doi'] != 'https://doi.org/10.1038/s41597-025-05336-7'):
            raise ValueError('Malformed bundled provenance or attribution.')
        times, values = document['times'], document['values']
        if (type(times) is not list or type(values) is not list or not 2 <= len(times) <= 25000 or len(times) != len(values)
                or any(type(t) not in (int, float) or not math.isfinite(t) for t in times)
                or any(b <= a for a, b in zip(times, times[1:])) or times[-1]-times[0] > 60
                or any(v is not None and (type(v) not in (int, float) or not math.isfinite(v)) for v in values)):
            raise ValueError('Malformed bundled excerpt samples.')
        offset, total = document['sample_start'], document['original_sample_count']
        if type(offset) is not int or type(total) is not int or not 0 <= offset < offset+len(times) <= total <= 5_000_000 or type(document['sample_end_exclusive']) is not int or document['sample_end_exclusive'] != offset+len(times):
            raise ValueError('Malformed original sample mapping.')
        if document['channel'] not in ('ECG', 'Bvp') or document['crew'] not in ('Crew 001', 'Crew 002'):
            raise ValueError('Unsupported bundled example context.')
        if type(document['author_exclusion_sample_bounds']) is not list:
            raise ValueError('Malformed author exclusion mapping.')
        for bounds in document['author_exclusion_sample_bounds']:
            if len(bounds) != 2 or any(type(x) is not int for x in bounds) or not 0 <= bounds[0] <= bounds[1] <= total:
                raise ValueError('Malformed author exclusion mapping.')
        annotation = document['annotation']
        if document['channel'] == 'ECG':
            if (type(annotation) is not dict or annotation['source_key'] != document['source_key']
                    or annotation['sha256'] != document['source_sha256'] or type(annotation['percentage_intervals']) is not list
                    or type(document['annotation_metadata_hashes']) is not dict
                    or set(document['annotation_metadata_hashes']) != {'workbook_sha256', 'split_manifest_sha256', 'download_manifest_sha256', 'validation_sha256'}
                    or any(type(v) is not str or not re.fullmatch('[0-9a-f]{64}', v) for v in document['annotation_metadata_hashes'].values())):
                raise ValueError('Malformed ECG annotation provenance.')
            intervals = annotation['percentage_intervals']
            if any(type(pair) is not list or len(pair) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in pair) or not 0 <= pair[0] < pair[1] <= 100 for pair in intervals):
                raise ValueError('Malformed original annotation percentages.')
            if [[math.floor(a/100*total), math.floor(b/100*total)] for a, b in intervals] != document['author_exclusion_sample_bounds']:
                raise ValueError('Original percentage-to-sample mapping mismatch.')
        elif annotation is not None or document['annotation_metadata_hashes'] is not None or document['author_exclusion_sample_bounds']:
            raise ValueError('BVP cannot carry supervised ECG marks.')
        document['file'] = entry['file']
        result.append(document)
    return result


def as_waveform(example):
    return Waveform(example['source_key'], example['source_sha256'], example['channel'],
        example['nominal_rate_hz'], tuple(example['times']), tuple(example['values']),
        sum(v is None for v in example['values']))


def preview(example, start, duration=10, max_points=1200):
    result = replay_window(as_waveform(example), start, duration, max_points)
    # Translate original sample positions to original source timestamps, not percentages of this excerpt.
    times, offset = example['times'], example['sample_start']
    overlays = []
    for left, right in example['author_exclusion_sample_bounds']:
        # BioTwin does not assign endpoint-contact windows a positive/negative label.
        # Display the strict interior separately from the retained source bounds.
        lo, hi = max(0, left+1-offset), min(len(times)-1, right-1-offset)
        if lo <= hi:
            overlays.append({'start': times[lo], 'end': times[hi], 'label': 'author-excluded interior; source annotation endpoints excluded from label', 'source_sample_bounds': [left, right]})
    result['author_exclusions'] = overlays
    result['original_sample_start'] = offset + result['sample_start']
    result['original_sample_end'] = offset + result['sample_end']
    return result
