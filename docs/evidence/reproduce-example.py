"""Offline frozen-model inference; no downloads, fitting, or clinical acceptance.

Run from any directory: python docs/evidence/reproduce-example.py [--plots]
The JSON path defaults to this directory; --output-dir supports a scratch folder.
Inference uses only Python's standard library. --plots additionally needs Matplotlib.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from biotwin.signal_review import (analyze_local_signal, create_injected_demo,
                                  extract_features, load_default_model, predict)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def run(output, plots=False):
    asset = ROOT / 'assets/desktop/crew001_ecg.json'
    excerpt = json.loads(asset.read_text(encoding='utf-8'))
    # First complete 2560-sample block, not selected for its model score.
    values, times = excerpt['values'][:2560], excerpt['times'][:2560]
    signal = {
        'subject_id': 'NASA:SOTERIA:' + excerpt['annotation']['participant'],
        'source_id': 'NASA:SOTERIA:' + excerpt['source_key'], 'channel': 'ECG',
        'samples': values, 'sample_times_seconds': times,
        'sample_rate_hz': excerpt['nominal_rate_hz'], 'unit': excerpt['unit'],
        'quality': 'review', 'time_basis': excerpt['time_basis'],
        # API-required fixed fixture marker, explicitly not an acquisition date.
        'observed_at': '2000-01-01T00:00:00+00:00',
        'method': 'Exact bundled NASA original samples; inference fixture',
    }
    corrupted = create_injected_demo(signal, 'hold')
    model = load_default_model()
    cases = []
    for label, current in [('original', signal), ('deliberate_hold_demo', corrupted)]:
        features = extract_features(current['samples'])
        result = analyze_local_signal(current)
        cases.append({
            'case': label, 'features': features,
            'technical_corruption_score_full_precision': predict(model, features),
            'author_exclusion_score_full_precision': predict(model['natural_head'], features),
            'runtime_status': result['status'], 'runtime_windows': result['windows'],
            'engineering_measurement_status': result['measurements']['status'],
            'engineering_measurement_reasons': result['measurements']['reasons'],
            'reliability_status': result['reliability']['status'],
            'injection': current.get('controlled_injection_demo'),
        })
    report = {
        'schema_version': 1, 'scope': 'Traceable inference illustration, not held-out evaluation',
        'warning': 'Training person Crew01; original does not mean clean. Scores are uncalibrated. No health diagnosis or clinical validity.',
        'observed_at_meaning': 'Fixed API fixture marker only; no calendar acquisition date is known or inferred',
        'input_asset': asset.relative_to(ROOT).as_posix(),
        'asset_sha256': hashlib.sha256(asset.read_bytes()).hexdigest(),
        'original_source_key': excerpt['source_key'],
        'original_source_sha256': excerpt['source_sha256'],
        'original_sample_start': excerpt['sample_start'],
        'original_sample_end_exclusive': excerpt['sample_start'] + len(values),
        'sample_count': len(values), 'nominal_rate_hz': 256,
        'source_seconds_first': times[0], 'source_seconds_last': times[-1],
        'time_basis': excerpt['time_basis'], 'amplitude_unit': excerpt['unit'],
        'transformation': 'First complete 2560 original samples; no filtering, interpolation, normalization, or timestamp replacement. Paired demo deliberately injects a sample hold.',
        'model_id': model['id'], 'model_sha256': model['sha256'],
        'natural_head_sha256': model['natural_head']['sha256'], 'cases': cases,
        'attribution': excerpt['attribution'], 'doi': excerpt['doi'],
        'license': excerpt['license'], 'license_url': excerpt['license_url'],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'example-inference.json', report)
    if plots:
        plot(output, times, values, corrupted['samples'], report)
    print('Wrote example-inference.json; original and deliberately injected hold scored offline.')


def plot(output, times, values, corrupted, report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout='constrained')
    for ax, samples, case, color in zip(axes, [values, corrupted], report['cases'], ['#216D91', '#A54A30']):
        ax.plot(times, samples, lw=.8, color=color)
        ax.set_title(f"{case['case'].replace('_', ' ')} | injection score {case['technical_corruption_score_full_precision']:.6f} | author-exclusion score {case['author_exclusion_score_full_precision']:.6f}", loc='left', fontsize=11)
        ax.set_ylabel('Source amplitude\n(unit unverified)')
        ax.grid(alpha=.18)
    axes[1].set_xlabel('Original source-relative seconds (timestamp jitter retained)')
    fig.suptitle('NASA SOTERIA pilot-simulator ECG: actual frozen-model inference\nCrew01 is a training person; this is a traceable example, not held-out evidence.', fontsize=13)
    fig.savefig(output / 'nasa-signal-example.png', dpi=160)
    plt.close(fig)
    summary = json.loads((Path(__file__).parent / 'results-summary.json').read_text(encoding='utf-8'))
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), layout='constrained')
    for ax, key, title in zip(axes, ['controlled_injection', 'natural_author_exclusion'], ['Controlled injected corruption', 'Natural author-exclusion proxy']):
        item = summary['signal_review'][key]
        vals = [100 * item['model']['balanced_accuracy'], 100 * item['rules']['balanced_accuracy']]
        bars = ax.bar(['Frozen forest', 'Fixed rules'], vals, color=['#216D91', '#9AA6AD'], width=.55)
        ax.bar_label(bars, fmt='%.2f%%', padding=5)
        ax.set_ylim(0, 110)
        ax.set_ylabel('Balanced accuracy (%)')
        ax.set_title(title + '\n' + item['figure_denominator'], fontsize=11)
        ax.axhline(50, color='#777777', lw=.8, ls=':')
        ax.text(.5, -.15, item['figure_conclusion'], transform=ax.transAxes, ha='center', va='top', fontsize=10, color='#853D2D' if key.startswith('natural') else '#216D91')
    fig.suptitle('Two different targets, two different outcomes\nHeld-out Crew08/09; small selected sample and scenario/person confounding', fontsize=14)
    fig.savefig(output / 'model-results.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent)
    parser.add_argument('--plots', action='store_true')
    args = parser.parse_args()
    run(args.output_dir.resolve(), args.plots)
