"""Independently extract exact source values, with original index provenance."""
from __future__ import annotations
import argparse
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biotwin.quality import verify_annotations
from biotwin.waveforms import load_waveform


def prepare(data_dir, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    annotations, hashes = verify_annotations(data_dir)
    entries = []
    sources = [('crew001_ecg.json', 'Crew 001', 'Crew_01/Processing/abm_leftseat_scenario2.csv'),
               ('crew002_ecg.json', 'Crew 002', 'Crew_02/Processing/abm_leftseat_scenario2.csv'),
               ('crew001_bvp.json', 'Crew 001', 'Crew_01/Processing/emp_bvp_leftseat_scenario2.csv')]
    for name, crew, key in sources:
        wave = load_waveform(data_dir, key)
        annotation = next((a for a in annotations if a['source_key'] == key), None)
        bounds = [(math.floor(a/100*len(wave.times)), math.floor(b/100*len(wave.times)))
                  for a, b in annotation['percentage_intervals']] if annotation else []
        # Source choice and extraction anchored to annotation positions, never model predictions.
        # Include the first author interval boundary with context when possible.
        anchor = wave.times[bounds[0][0]] if bounds and bounds[0][0] < len(wave.times) else wave.times[0]
        start = max(wave.times[0], anchor - 15)
        lo, hi = bisect_left(wave.times, start), bisect_left(wave.times, start+60)
        document = {'format': 'biotwin-source-excerpt', 'version': 1, 'crew': crew,
            'source_key': key, 'source_sha256': wave.source_sha256, 'channel': wave.channel,
            'nominal_rate_hz': wave.nominal_rate_hz, 'original_sample_count': len(wave.times),
            'sample_start': lo, 'sample_end_exclusive': hi,
            'times': list(wave.times[lo:hi]), 'values': list(wave.values[lo:hi]),
            'author_exclusion_sample_bounds': bounds,
            'annotation': annotation, 'annotation_metadata_hashes': hashes if annotation else None,
            'transformation': 'Independent selected-column parsing; exact source float values and timestamps; contiguous half-open <=60-second excerpt; no filtering, interpolation or normalization. Display uses separate min/max decimation.',
            'time_basis': 'source-relative seconds; timestamp jitter and cross-device synchronization unverified',
            'unit': 'source amplitude unit unverified',
            'author_mark_meaning': 'Heartbeat-detector exclusion; not disease or complete artifact truth. Unmarked does not mean clean. BVP has no supervised annotations.',
            'attribution': 'NASA SOTERIA Simulation Testbed Data, NASA Langley Research Center / NASA System-Wide Safety Project; Fettrow et al. (2025), Scientific Data 12, 1247.',
            'doi': 'https://doi.org/10.1038/s41597-025-05336-7',
            'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/'}
        raw = (json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')
        (output/name).write_bytes(raw)
        entries.append({'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    (output/'manifest.json').write_text(json.dumps({'format': 'biotwin-desktop-excerpts', 'version': 1,
        'examples': entries}, indent=2)+'\n', encoding='utf-8')
    return entries


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--output', type=Path, default=ROOT/'assets'/'desktop')
    args = parser.parse_args()
    print(json.dumps(prepare(args.data_dir, args.output), indent=2))
