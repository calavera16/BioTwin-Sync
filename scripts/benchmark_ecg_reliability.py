"""Frozen, subject-grouped ECG count-error rejection experiment.

Plan reads headers and hashes only. Run requires that exact plan digest; never retune
from test outcomes. WFDB/sklearn are development-only; runtime remains stdlib.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biotwin.ecg_reliability import (FEATURES, PARAMETERS, WARNING, estimate_window,
                                     match_beats, model_digest, predict_error, read_finite_json)
from scripts.acquire_ecg_reliability import MITDB, NSTDB, allowed_files, safe_root, source_url, paired_mirror_url

BEAT_SYMBOLS = tuple('N L R B A a J S V r F e j n E / f Q'.split())
NONBEAT_SYMBOLS = tuple('+ ~ | s T * D = " p t u ( ) x @ ` \' ^'.split())
UNSUPPORTED_SYMBOLS = ('[', ']', '!', '?')
SOURCE_FILES = ('biotwin/ecg_reliability.py', 'scripts/benchmark_ecg_reliability.py',
                'scripts/acquire_ecg_reliability.py', 'docs/ecg-reliability-experiment.md',
                'requirements-reliability.lock.txt', 'biotwin/signal_review.py',
                'docs/ecg-reliability-api.md', 'scripts/export_nasa_reliability.py')
TRAINING = {'estimator': 'StandardScaler+LogisticRegression', 'C': 1., 'solver': 'lbfgs',
            'max_iter': 1000, 'class_weight': 'balanced', 'random_state': 20261114,
            'minimum_class_examples': 10, 'minimum_feature_scale': 1e-12, 'threshold_grid': [i/100 for i in range(101)],
            'calibration_min_coverage': .40, 'calibration_max_bad_fraction': .05,
            'calibration_max_mae_bpm': 2., 'test_min_coverage': .40,
            'test_max_bad_fraction': .05, 'test_max_mae_bpm': 2.,
            'test_min_mae_improvement_fraction': .20,
            'rules_curve': 'agreement cutoffs 0.00..1.00 with holds<=.05, extrema<=.15, beats>0',
            'stress_required': False}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    path = safe_root(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    path = safe_root(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def subject_id(record):
    base = record[:3]
    return '201+202' if base in ('201', '202') else base


def fixed_splits():
    groups = sorted({subject_id(r) for r in MITDB if r not in ('118', '119')},
                    key=lambda s: hashlib.sha256(f'20261114|{s}'.encode()).hexdigest())
    assigned = {group: split for split, values in (('train', groups[:27]),
                ('calibration', groups[27:36]), ('test', groups[36:])) for group in values}
    return {split: [r for r in MITDB if r not in ('118', '119') and assigned[subject_id(r)] == split]
            for split in ('train', 'calibration', 'test')} | {'stress': ['118', '119']+list(NSTDB)}


def source_fingerprints():
    return {name: file_hash(ROOT/name) for name in SOURCE_FILES}


def make_plan(data, output):
    import wfdb
    root = safe_root(data)
    acquisition = read_finite_json(root/'source-manifest.json', limit=1_000_000)
    acquired = {item['path']: item for item in acquisition['files']}
    files = []
    for dataset, record, suffix in allowed_files():
        path = root/dataset/f'{record}.{suffix}'
        if path.is_symlink() or path.resolve().parent != (root/dataset).resolve():
            raise ValueError('source outside expected directory')
        files.append({'dataset': dataset, 'record': record, 'suffix': suffix,
                      'path': f'{dataset}/{record}.{suffix}', 'url': source_url(dataset, record, suffix),
                      'resolved_url': acquired[f'{dataset}/{record}.{suffix}']['resolved_url'],
                      'bytes': path.stat().st_size, 'sha256': file_hash(path)})
    records = []
    splits = fixed_splits()
    for dataset, ids in (('mitdb', MITDB), ('nstdb', NSTDB)):
        for record in ids:
            header = wfdb.rdheader(str(root/dataset/record))
            if not math.isfinite(header.fs) or header.fs != 360 or header.sig_len < 600*header.fs:
                raise ValueError('unexpected source sampling rate/length')
            lead = header.sig_name.index('MLII') if 'MLII' in header.sig_name else 0
            # Header-selected first channel is explicitly declared; no waveform selection.
            if any(Path(name).name != f'{record}.dat' for name in header.file_name):
                raise ValueError('header references unexpected waveform path')
            split = 'stress' if dataset == 'nstdb' else next(s for s, ids in splits.items() if record in ids)
            records.append({'dataset': dataset, 'record': record, 'subject': subject_id(record),
                            'split': split, 'fs': header.fs, 'total_samples': header.sig_len,
                            'scored_samples': round(header.fs*600), 'lead_index': lead,
                            'lead_name': header.sig_name[lead], 'lead_unit': header.units[lead],
                            'lead_selection': 'MLII if present else first declared ECG channel',
                            'all_leads': header.sig_name})
    plan = {'schema_version': 1, 'experiment': 'ecg-reliability-v1', 'state': 'prefit_frozen',
            'data_root': str(root), 'splits': splits, 'subject_grouping': '201/202 same person; NSTDB all variants join118/119 stress-only',
            'parameters': PARAMETERS, 'features': list(FEATURES), 'training': TRAINING,
            'beat_symbols': BEAT_SYMBOLS, 'nonbeat_symbols': NONBEAT_SYMBOLS,
            'unsupported_symbols': UNSUPPORTED_SYMBOLS, 'source_files': files, 'records': records,
            'code_fingerprints': source_fingerprints(), 'dependencies': {
                name: importlib.metadata.version(name) for name in ('wfdb', 'numpy', 'scipy', 'scikit-learn')},
            'python': platform.python_version(), 'warning': WARNING,
            'exclusions': 'Only full finite uniformly sampled30s windows; missing/unsupported reference spans reported; no rhythm regularity exclusion',
            'selection': 'All48 MITDB records plus12 NSTDB records, first600s only, metadata-only subject split'}
    plan['sha256'] = digest(plan)
    write_new(output, plan)
    return plan


def verify_plan(plan, approval):
    if plan.get('sha256') != approval or digest({k: v for k, v in plan.items() if k != 'sha256'}) != approval:
        raise ValueError('exact frozen plan approval digest required')
    if plan['code_fingerprints'] != source_fingerprints():
        raise ValueError('source changed after freeze; create and review a new plan before execution')
    if plan['parameters'] != PARAMETERS or plan['features'] != list(FEATURES) or plan['training'] != TRAINING:
        raise ValueError('runtime/training design differs from frozen plan')
    root = safe_root(plan['data_root'])
    if len(plan['source_files']) != len(allowed_files()):
        raise ValueError('incomplete frozen source identities')
    for item, allowed in zip(plan['source_files'], allowed_files()):
        dataset, record, suffix = allowed
        relative = f'{dataset}/{record}.{suffix}'
        if (item['path'] != relative or item['url'] != source_url(*allowed) or
                item['resolved_url'] not in (source_url(*allowed), paired_mirror_url(*allowed)) or
                file_hash(root/relative) != item['sha256']):
            raise ValueError('source identity changed after freeze')
    return root


def reference_windows(samples, symbols, total_samples, width, subtypes=None, aux_notes=None, lead_index=0):
    """VF/learning/unknown codes cause explicit unavailable targets, never silent zeros."""
    beats = [int(s) for s, symbol in zip(samples, symbols) if symbol in BEAT_SYMBOLS]
    subtypes = [0]*len(samples) if subtypes is None else subtypes
    aux_notes = ['']*len(samples) if aux_notes is None else aux_notes
    if not len(samples) == len(symbols) == len(subtypes) == len(aux_notes):
        raise ValueError('annotation metadata lengths differ')
    unsupported = []
    active = None
    unexpected = set(symbols)-set(BEAT_SYMBOLS)-set(NONBEAT_SYMBOLS)-set(UNSUPPORTED_SYMBOLS)
    for sample, symbol in zip(samples, symbols):
        sample = int(sample)
        if symbol == '[':
            active = sample if active is None else active
        elif symbol == ']':
            unsupported.append((0 if active is None else active, sample+1, 'ventricular flutter/fibrillation reference span'))
            active = None
        elif symbol in ('!', '?') or symbol in unexpected:
            unsupported.append((sample, sample+1, 'unsupported reference annotation '+symbol))
    if active is not None:
        unsupported.append((active, total_samples, 'open ventricular flutter/fibrillation reference span'))
    rhythm_events = [(int(s), str(aux).rstrip('\0')) for s, symbol, aux in zip(samples, symbols, aux_notes) if symbol == '+']
    quality_events = [(int(s), int(subtype) & 255) for s, symbol, subtype in zip(samples, symbols, subtypes) if symbol == '~']
    def states(events, lo, hi, unknown):
        state = unknown
        for point, value in events:
            if point <= lo:
                state = value
            else:
                break
        return [state]+[value for point, value in events if lo < point < hi]
    output = []
    for lo in range(0, total_samples, width):
        hi = min(total_samples, lo+width)
        reasons = sorted({reason for start, end, reason in unsupported if start < hi and end > lo})
        rhythms = sorted(set(states(rhythm_events, lo, hi, 'unknown')))
        quality_codes = states(quality_events, lo, hi, None)
        quality = sorted(set('unknown' if code is None else 'unreadable' if code & (1 << (lead_index+4)) else
                             'noisy' if code & (1 << lead_index) else 'annotator_clean' for code in quality_codes))
        if 'unreadable' in quality:
            reasons.append('source lead marked unreadable; discrete reference target conservatively unsupported')
        symbol_counts = dict(Counter(symbol for s, symbol in zip(samples, symbols) if lo <= s < hi))
        strata = []
        if symbol_counts.get('/', 0) or symbol_counts.get('f', 0) or '(P' in rhythms:
            strata.append('paced_present')
        if any(r not in ('unknown', '(N', '(P') for r in rhythms):
            strata.append('non_sinus_rhythm_annotation_present')
        if any(symbol_counts.get(symbol, 0) for symbol in BEAT_SYMBOLS if symbol not in ('N', '/', 'f')):
            strata.append('other_non_normal_beat_annotation_present')
        if 'unknown' in rhythms:
            strata.append('rhythm_annotation_unknown')
        strata.extend('source_quality_'+state for state in quality)
        output.append({'sample_start': lo, 'sample_end_exclusive': hi,
                       'beats': [s for s in beats if lo <= s < hi], 'reasons': reasons,
                       'reference_supported': not reasons, 'annotation_symbols': symbol_counts,
                       'rhythm_annotations': rhythms, 'source_quality_annotations': quality,
                       'annotation_events': [{'sample': int(s), 'symbol': symbol, 'subtype': int(subtype), 'aux': str(aux)}
                                             for s, symbol, subtype, aux in zip(samples, symbols, subtypes, aux_notes)
                                             if lo <= s < hi and symbol not in BEAT_SYMBOLS],
                       'strata': strata})
    return output


def prepare_record(root, record):
    import wfdb
    fs, length = record['fs'], record['scored_samples']
    base = root/record['dataset']/record['record']
    # Exactly first600s; fixed right context can extend to602s, never scored past600s.
    read_length = min(record['total_samples'], length+round(fs*2))
    signal = wfdb.rdrecord(str(base), sampto=read_length, channels=[record['lead_index']]).p_signal[:, 0].tolist()
    annotation = wfdb.rdann(str(base), 'atr', sampto=length)
    references = reference_windows(annotation.sample.tolist(), annotation.symbol, length, round(fs*30),
                                   annotation.subtype.tolist(), annotation.aux_note, record['lead_index'])
    windows = []
    for reference in references:
        lo, hi = reference['sample_start'], reference['sample_end_exclusive']
        context_lo, context_hi = max(0, lo-round(fs*2)), min(len(signal), hi+round(fs*2))
        row = {k: v for k, v in reference.items() if k != 'beats'}
        row.update(source_id=f'physionet:{record["dataset"]}:1.0.0:{record["record"]}',
                   subject_id='mitdb-person:'+record['subject'], dataset=record['dataset'], record=record['record'],
                   split=record['split'], start_seconds=lo/fs, end_seconds=hi/fs,
                   availability_seconds=context_hi/fs, time_basis='source-relative-seconds',
                   source_quality='reference annotated research ECG; not clinical clearance',
                   lead_name=record['lead_name'], lead_index=record['lead_index'],
                   estimate_bpm=None, reference_bpm=None, absolute_error_bpm=None,
                   bad_error=None, error_score=None, learned_accept=None,
                   rules_accept=False, features=None, status='unavailable',
                   beat_matching={'tp': 0, 'fp': 0, 'fn': 0})
        if record['dataset'] == 'nstdb':
            snr_text = record['record'][4:]
            row['stress_snr_db'] = -6 if snr_text == '_6' else int(snr_text)
            row['stress_noise_condition'] = ('initial_no_added_noise' if lo/fs < 300 else
                                              'added_electrode_motion' if int((lo/fs-300)//120) % 2 == 0 else 'interleaved_no_added_noise')
        elif record['split'] == 'stress':
            row['stress_snr_db'] = None
            row['stress_noise_condition'] = 'original_no_added_noise'
        if not all(math.isfinite(v) and abs(v) <= 1e12 for v in signal[context_lo:context_hi]):
            row['reasons'].append('nonfinite waveform; no interpolation')
        else:
            estimate = estimate_window(signal[context_lo:context_hi], fs, lo-context_lo, hi-context_lo)
            row.update(estimate)
            row['reasons'] = reference['reasons']+estimate['reasons']
            detected = [i+context_lo for i in estimate['detected_beats_sample_indices']]
            row['detected_beats_sample_indices'] = detected
            row['status'] = 'experimental_review'
            if reference['reference_supported']:
                reference_bpm = len(reference['beats'])*60*fs/(hi-lo)
                error = abs(row['estimate_bpm']-reference_bpm)
                row.update(reference_bpm=reference_bpm, absolute_error_bpm=error, bad_error=error > PARAMETERS['bad_error_bpm'],
                           beat_matching=match_beats(detected, reference['beats'], round(fs*.15)))
        windows.append(row)
    return windows


def summarize(rows, accepted=None):
    eligible = [r for r in rows if r.get('absolute_error_bpm') is not None]
    kept = eligible if accepted is None else [r for r in eligible if accepted(r)]
    errors = sorted(r['absolute_error_bpm'] for r in kept)
    bad = sum(r['bad_error'] for r in kept)
    all_bad = sum(r['bad_error'] for r in eligible)
    tp = sum(r['beat_matching']['tp'] for r in kept)
    fp = sum(r['beat_matching']['fp'] for r in kept)
    fn = sum(r['beat_matching']['fn'] for r in kept)
    return {'total_windows': len(rows), 'eligible_windows': len(eligible), 'retained_windows': len(kept),
            'coverage': len(kept)/len(eligible) if eligible else 0.,
            'all_window_coverage': len(kept)/len(rows) if rows else 0.,
            'mae_bpm': statistics.mean(errors) if errors else None,
            'median_absolute_error_bpm': statistics.median(errors) if errors else None,
            'p95_absolute_error_bpm': errors[min(len(errors)-1, math.ceil(len(errors)*.95)-1)] if errors else None,
            'bad_error_windows': bad, 'bad_fraction_among_retained': bad/len(kept) if kept else None,
            'false_acceptance_among_all_bad': bad/all_bad if all_bad else None,
            'all_bad_error_windows': all_bad, 'beat_tp': tp, 'beat_fp': fp, 'beat_fn': fn,
            'beat_precision': tp/(tp+fp) if tp+fp else None,
            'beat_recall': tp/(tp+fn) if tp+fn else None}


def curve(rows, kind='learned'):
    output = []
    for cutoff in TRAINING['threshold_grid']:
        if kind == 'learned':
            keep = lambda r: r['error_score'] is not None and r['error_score'] <= cutoff
        else:
            keep = lambda r: (r['features'] is not None and r['features']['flat_fraction'] <= .05 and
                              r['features']['edge_fraction'] <= .15 and r['beat_count'] > 0 and
                              r['features']['detector_agreement'] >= cutoff)
        output.append({'threshold': cutoff, **summarize(rows, keep)})
    return output


def select_threshold(calibration):
    candidates = [point for point in curve(calibration) if point['coverage'] >= .4 and
                  point['bad_fraction_among_retained'] is not None and point['bad_fraction_among_retained'] <= .05 and
                  point['mae_bpm'] <= 2.]
    if not candidates:
        return None
    return min(candidates, key=lambda p: (-p['coverage'], p['bad_fraction_among_retained'], p['mae_bpm'], p['threshold']))['threshold']


def fit(train, calibration, plan):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    available = lambda rows: [r for r in rows if r['absolute_error_bpm'] is not None and r['features'] is not None]
    training, calibrating = available(train), available(calibration)
    counts = {split: dict(Counter(int(r['bad_error']) for r in rows)) for split, rows in
              (('train', training), ('calibration', calibrating))}
    if any(counts[split].get(label, 0) < 10 for split in counts for label in (0, 1)):
        return None, {'status': 'unavailable', 'reason': 'fewer than10 error/nonerror examples in train or calibration', 'counts': counts}
    x = [[r['features'][name] for name in FEATURES] for r in training]
    y = [int(r['bad_error']) for r in training]
    scaler = StandardScaler().fit(x)
    scaler.scale_ = scaler.scale_.clip(min=1e-12)
    classifier = LogisticRegression(C=1., solver='lbfgs', max_iter=1000, class_weight='balanced', random_state=20261114).fit(scaler.transform(x), y)
    if max(classifier.n_iter_) >= 1000:
        raise RuntimeError('fixed logistic fit did not converge; retain failed run, do not retune')
    model = {'schema_version': 1, 'id': 'ecg-reliability-v1', 'kind': 'logistic-error-rejection',
             'features': list(FEATURES), 'mean': scaler.mean_.tolist(), 'scale': scaler.scale_.tolist(),
             'coefficients': classifier.coef_[0].tolist(), 'intercept': float(classifier.intercept_[0]),
             'threshold': None, 'deployment': 'experimental_review',
             'provenance': {'plan': plan['sha256'], **plan['code_fingerprints']}}
    model['sha256'] = model_digest(model)
    for row in training+calibrating:
        row['error_score'] = predict_error(model, row['features'])
    threshold = select_threshold(calibrating)
    model['threshold'] = threshold
    model['sha256'] = model_digest(model)
    parity_rows = training+calibrating
    sklearn_scores = classifier.predict_proba(scaler.transform([[r['features'][name] for name in FEATURES] for r in parity_rows]))[:, 1]
    parity = max(abs(float(expected)-row['error_score']) for expected, row in zip(sklearn_scores, parity_rows))
    if parity > 1e-12:
        raise RuntimeError('exported inference parity failed')
    return model, {'status': 'fit', 'counts': counts, 'threshold': threshold,
                   'threshold_status': 'selected' if threshold is not None else 'calibration_constraints_failed',
                   'export_max_absolute_difference_train_calibration': parity}


def evaluate(rows, model):
    if model:
        for row in rows:
            if row['features'] is not None:
                row['error_score'] = predict_error(model, row['features'])
                row['learned_accept'] = model['threshold'] is not None and row['error_score'] <= model['threshold']
    reports = {}
    for split in ('train', 'calibration', 'test', 'stress'):
        selected = [r for r in rows if r['split'] == split]
        reports[split] = {'baseline': summarize(selected), 'rules': summarize(selected, lambda r: r['rules_accept']),
                          'learned': summarize(selected, lambda r: bool(r['learned_accept'])),
                          'learned_curve': curve(selected), 'rules_curve': curve(selected, 'rules'),
                          'per_person': {person: {
                              'baseline': summarize([r for r in selected if r['subject_id'] == person]),
                              'rules': summarize([r for r in selected if r['subject_id'] == person], lambda r: r['rules_accept']),
                              'learned': summarize([r for r in selected if r['subject_id'] == person], lambda r: bool(r['learned_accept']))}
                              for person in sorted({r['subject_id'] for r in selected})},
                          'per_record': {record: {
                              'baseline': summarize([r for r in selected if r['record'] == record]),
                              'learned': summarize([r for r in selected if r['record'] == record], lambda r: bool(r['learned_accept']))}
                              for record in sorted({r['record'] for r in selected})},
                          'unsupported_reference_windows': sum(not r['reference_supported'] for r in selected),
                          'unavailable_detector_windows': sum(r['features'] is None for r in selected)}
        strata = sorted({stratum for row in selected for stratum in row['strata']})
        reports[split]['annotation_strata_overlapping'] = {stratum: {
            'baseline': summarize([r for r in selected if stratum in r['strata']]),
            'rules': summarize([r for r in selected if stratum in r['strata']], lambda r: r['rules_accept']),
            'learned': summarize([r for r in selected if stratum in r['strata']], lambda r: bool(r['learned_accept']))}
            for stratum in strata}
        if split == 'stress':
            combinations = sorted({(str(r.get('stress_snr_db')), r['stress_noise_condition']) for r in selected})
            reports[split]['by_snr_and_noise_condition'] = {f'SNR={snr};{condition}': {
                'baseline': summarize([r for r in selected if str(r.get('stress_snr_db')) == snr and r['stress_noise_condition'] == condition]),
                'rules': summarize([r for r in selected if str(r.get('stress_snr_db')) == snr and r['stress_noise_condition'] == condition], lambda r: r['rules_accept']),
                'learned': summarize([r for r in selected if str(r.get('stress_snr_db')) == snr and r['stress_noise_condition'] == condition], lambda r: bool(r['learned_accept']))}
                for snr, condition in combinations}
        target_coverage = reports[split]['learned']['coverage']
        # Comparison cutoff is matched by coverage only; error never chooses the rule point.
        matched = min(reports[split]['rules_curve'], key=lambda p: (abs(p['coverage']-target_coverage), p['threshold']))
        reports[split]['rules_nearest_learned_coverage'] = matched
        reports[split]['coverage_difference_for_rule_comparison'] = abs(matched['coverage']-target_coverage)
    test = reports['test']
    learned, baseline, matched = test['learned'], test['baseline'], test['rules_nearest_learned_coverage']
    checks = {'model_available': model is not None,
              'calibration_threshold_available': bool(model and model['threshold'] is not None),
              'coverage_at_least_40pct': learned['coverage'] >= .4,
              'retained_bad_fraction_at_most_5pct': learned['bad_fraction_among_retained'] is not None and learned['bad_fraction_among_retained'] <= .05,
              'retained_mae_at_most_2bpm': learned['mae_bpm'] is not None and learned['mae_bpm'] <= 2.,
              'mae_improvement_at_least_20pct': (learned['mae_bpm'] is not None and baseline['mae_bpm'] is not None and baseline['mae_bpm'] > 0 and
                                               learned['mae_bpm'] <= .8*baseline['mae_bpm']),
              'rules_coverage_comparable_within_5pct': test['coverage_difference_for_rule_comparison'] <= .05,
              'mae_no_worse_than_coverage_matched_rules': (learned['mae_bpm'] is not None and matched['mae_bpm'] is not None and learned['mae_bpm'] <= matched['mae_bpm'])}
    return reports, {'passed': all(checks.values()), 'checks': checks,
                     'scope': 'MITDB held-out subject count-error rejection only; stress report-only; no NASA or clinical validation'}


def create_report(output, results):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True)
    for column, split in enumerate(('test', 'stress')):
        report = results['splits'][split]
        for kind, color in (('learned', '#206b91'), ('rules', '#a55b21')):
            points = [p for p in report[kind+'_curve'] if p['retained_windows']]
            points.sort(key=lambda p: p['coverage'])
            axes[0, column].plot([p['coverage'] for p in points], [p['mae_bpm'] for p in points],
                                 label=kind, color=color, linewidth=1.8)
            axes[1, column].plot([p['coverage'] for p in points], [p['bad_fraction_among_retained'] for p in points], color=color, linewidth=1.8)
        baseline = report['baseline']['mae_bpm']
        if baseline is not None:
            axes[0, column].axhline(baseline, color='#666666', linestyle=':', label='unconditional detector')
        chosen = report['learned']
        if chosen['mae_bpm'] is not None:
            axes[0, column].scatter([chosen['coverage']], [chosen['mae_bpm']], color='#206b91', marker='D', s=50, label='calibration threshold')
            axes[1, column].scatter([chosen['coverage']], [chosen['bad_fraction_among_retained']], color='#206b91', marker='D', s=50)
        axes[0, column].set_title('Held-out people' if split == 'test' else 'Report-only stress variants (correlated)')
        axes[0, column].set_ylabel('Retained count-rate MAE (bpm)')
        axes[1, column].set_ylabel('Retained fraction with error >5 bpm')
        axes[1, column].set_xlabel('Coverage among reference-supported detector windows')
        axes[1, column].axhline(.05, color='#888888', linestyle='--', linewidth=.8)
        axes[0, column].legend(fontsize=8)
        for axis in axes[:, column]:
            axis.set_xlim(0, 1)
            axis.grid(alpha=.2)
    decision = 'PASS (benchmark scope only)' if results['gate']['passed'] else 'FAIL / experimental review'
    figure.suptitle('ECG count-error rejection: '+decision+'\nEngineering tolerance5 bpm; no clinical or NASA accuracy validation', fontsize=12)
    figure.tight_layout(rect=(0, 0, 1, .92))
    plot = output/'error-vs-coverage.png'
    if plot.exists():
        raise FileExistsError(plot)
    figure.savefig(plot, dpi=160)
    plt.close(figure)
    def cell(value):
        if value is None:
            return 'unavailable'
        return f'{value:.4f}' if isinstance(value, float) else html.escape(str(value))
    def table(items):
        keys = ('eligible_windows', 'retained_windows', 'coverage', 'all_window_coverage', 'mae_bpm',
                'bad_fraction_among_retained', 'false_acceptance_among_all_bad', 'beat_precision', 'beat_recall')
        return '<table><thead><tr><th>Group / gate</th>'+''.join('<th>'+html.escape(k)+'</th>' for k in keys)+'</tr></thead><tbody>'+''.join(
            '<tr><td>'+html.escape(label)+'</td>'+''.join('<td>'+cell(values[k])+'</td>' for k in keys)+'</tr>'
            for label, values in items)+'</tbody></table>'
    sections = []
    for split, report in results['splits'].items():
        sections.append('<h2>'+html.escape(split)+'</h2><p>Unsupported reference windows: '+str(report['unsupported_reference_windows'])+
                        '; unavailable detector windows: '+str(report['unavailable_detector_windows'])+'.</p>')
        sections.append(table([(kind, report[kind]) for kind in ('baseline', 'rules', 'learned')]))
        sections.append('<h3>Per person (windows correlated within person)</h3>'+table([
            (person+' / '+kind, values[kind]) for person, values in report['per_person'].items() for kind in ('baseline', 'rules', 'learned')]))
        sections.append('<h3>Overlapping annotation strata</h3>'+table([
            (stratum+' / '+kind, values[kind]) for stratum, values in report['annotation_strata_overlapping'].items() for kind in ('baseline', 'rules', 'learned')]))
        if split == 'stress':
            sections.append('<h3>SNR and actual added-noise intervals</h3>'+table([
                (stratum+' / '+kind, values[kind]) for stratum, values in report['by_snr_and_noise_condition'].items() for kind in ('baseline', 'rules', 'learned')]))
    document = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>ECG reliability experiment</title>'
                '<style>body{font:15px system-ui;margin:32px;line-height:1.5;color:#172b36}table{border-collapse:collapse;font-size:12px;display:block;overflow:auto;margin:16px 0}th,td{padding:7px;border:1px solid #c4d0d6;text-align:left}th{background:#eaf0f3}img{max-width:1100px;width:100%}pre{white-space:pre-wrap}</style>'
                '<h1>ECG count reliability: '+html.escape(decision)+'</h1><p>'+html.escape(WARNING)+'</p>'
                '<p>Diagnostic estimates remain visible when rejected. Coverage is not clinical usability. Reference-unavailable and detector-unavailable windows abstain. '
                'Paced and non-sinus annotations are retained; absence of annotations never proves normal physiology. Stress variants reuse only two people and are not independent.</p>'
                '<p>Frozen plan SHA-256: '+html.escape(results['plan_sha256'])+'</p><img src="error-vs-coverage.png" alt="Held-out and stress error versus coverage">'
                '<h2>Predeclared gate</h2><pre>'+html.escape(json.dumps(results['gate'], indent=2))+'</pre>'
                '<h2>Fit and calibration</h2><pre>'+html.escape(json.dumps(results['fit'], indent=2))+'</pre>'+''.join(sections)+
                '<h2>Measured resources</h2><pre>'+html.escape(json.dumps(results['resources'], indent=2))+'</pre></html>')
    with (output/'report.html').open('x', encoding='utf-8') as handle:
        handle.write(document)


def run(plan_path, approval, output):
    plan = read_finite_json(plan_path, limit=2_000_000)
    root = verify_plan(plan, approval)
    output = safe_root(output)
    output.mkdir(parents=True, exist_ok=True)
    # Claim a run once; never replace failures or previously inspected test results.
    write_new(output/'run-started.json', {'plan_sha256': approval, 'state': 'started', 'python': sys.version})
    start = time.perf_counter()
    cpu_start = time.process_time()
    tracemalloc.start()
    rows = []
    try:
        # Train/calibration are prepared and fitted before held-out waveforms are parsed.
        for record in plan['records']:
            if record['split'] in ('train', 'calibration'):
                rows.extend(prepare_record(root, record))
        fit_start = time.perf_counter()
        model, fit_report = fit([r for r in rows if r['split'] == 'train'], [r for r in rows if r['split'] == 'calibration'], plan)
        fit_seconds = time.perf_counter()-fit_start
        write_new(output/'prefit-to-test.json', {'fit': fit_report, 'fitted_model_sha256': model['sha256'] if model else None,
                                                'before_test_waveforms': True, 'elapsed_seconds': time.perf_counter()-start})
        if model:
            write_new(output/'model-before-test.json', model)
        for record in plan['records']:
            if record['split'] in ('test', 'stress'):
                rows.extend(prepare_record(root, record))
        reports, gate = evaluate(rows, model)
        if model:
            model['deployment'] = 'benchmark_gate_passed' if gate['passed'] else 'experimental_review'
            model['sha256'] = model_digest(model)
            write_new(output/'ecg-reliability-v1.json', model)
        # Scores/decisions remain available even when the learned experimental gate fails.
        for row in rows:
            row['learned_threshold_retained'] = row['learned_accept']
            row['learned_accept'] = bool(row['learned_accept'] and gate['passed']) if model else None
            row['deployment'] = model['deployment'] if model else 'unavailable'
        eligible = [r for r in rows if r['features'] is not None]
        timing_start = time.perf_counter()
        if model and eligible:
            for row in eligible:
                predict_error(model, row['features'])
        inference_seconds = time.perf_counter()-timing_start
        current, peak = tracemalloc.get_traced_memory()
        resources = {'wall_seconds': time.perf_counter()-start, 'fit_seconds': fit_seconds,
                     'process_cpu_seconds': time.process_time()-cpu_start,
                     'wall_scope': 'preparation, fit, heldout evaluation and inference; excludes acquisition/report plotting/output writes',
                     'tracemalloc_current_bytes': current, 'tracemalloc_peak_bytes': peak,
                     'tracemalloc_limit': 'Python allocations only; excludes native NumPy/sklearn process RSS',
                     'inference_feature_windows': len(eligible) if model else 0,
                     'inference_seconds': inference_seconds, 'inference_ms_per_feature_window': inference_seconds*1000/len(eligible) if model and eligible else None,
                     'inference_scope': 'stdlib prediction including model validation; excludes feature extraction/UI/startup',
                     'model_bytes': (output/'ecg-reliability-v1.json').stat().st_size if model else 0}
        results = {'schema_version': 1, 'plan_sha256': approval, 'fit': fit_report,
                   'gate': gate, 'splits': reports, 'resources': resources, 'warning': WARNING,
                   'physiology_handling': 'Paced/irregular beats retained; unsupported flutter/fibrillation/learning reference spans explicitly unavailable',
                   'annotation_symbol_counts': dict(sum((Counter(r['annotation_symbols']) for r in rows), Counter()))}
        write_new(output/'results.json', results)
        write_new(output/'window-scores.json', {'schema_version': 1, 'plan_sha256': approval, 'windows': rows})
        series = []
        for source in sorted({r['source_id'] for r in rows}):
            selected = [r for r in rows if r['source_id'] == source]
            series.append({'source': source, 'subject': selected[0]['subject_id'], 'split': selected[0]['split'],
                           'time_basis': 'source-relative-seconds', 'windows': selected})
        write_new(output/'reliability-series.json', {'schema_version': 1, 'series': series, 'plan_sha256': approval})
        create_report(output, results)
        write_new(output/'run-completed.json', {'plan_sha256': approval, 'state': 'completed', 'gate_passed': gate['passed'],
                                              'total_wall_seconds_including_outputs': time.perf_counter()-start})
        return results
    except Exception as exc:
        write_new(output/'run-failed.json', {'plan_sha256': approval, 'state': 'failed', 'error': str(exc),
                                           'elapsed_seconds': time.perf_counter()-start})
        raise
    finally:
        tracemalloc.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    planning = sub.add_parser('plan', help='metadata, source hashes and exact source fingerprints only')
    planning.add_argument('--data', required=True)
    planning.add_argument('--output', required=True)
    running = sub.add_parser('run', help='execute once after independent freeze approval')
    running.add_argument('--plan', required=True)
    running.add_argument('--approval-sha256', required=True)
    running.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        result = make_plan(args.data, args.output)
        print(json.dumps({'plan_sha256': result['sha256'], 'splits': result['splits']}))
    else:
        result = run(args.plan, args.approval_sha256, args.output)
        print(json.dumps({'gate': result['gate'], 'resources': result['resources']}))


if __name__ == '__main__':
    main()
