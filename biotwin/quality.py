"""Bounded, weak-label ECG engineering experiment. Custom code, not NASA software.

Preparation never opens held-out waveforms. Benchmark requires a hash-bound
human review receipt and freezes selection before evaluating held-out crews.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .quality_model import FEATURES, predict, validate_model
from .waveforms import load_waveform, manifest_entry, read_json, sha256_file

WARNING = ('Experimental uncalibrated author-exclusion score. Author detector-exclusion '
           'labels and not-marked-excluded proxy negatives do not certify signal cleanliness '
           'or clinical safety. Six selected people; one validation crew, two test crews.')
EXPECTED = {
    'Crew_01': ('train', 2, '76:80'), 'Crew_02': ('train', 2, '23:48'),
    'Crew_03': ('train', 2, '10:49'), 'Crew_05': ('validation', 2, '17:19;53:54;88:90'),
    'Crew_06': ('test', 1, '3:5;15:16'), 'Crew_10': ('test', 2, '5:19')}
CONFIG = {'window_seconds': 10, 'overlap_seconds': 0, 'features': list(FEATURES),
          'model': {'C': 1.0, 'solver': 'liblinear', 'class_weight': 'balanced',
                    'max_iter': 1000, 'random_state': 2026},
          'threshold_candidates': [.1, .2, .3, .4, .5, .6, .7, .8, .9],
          'minimum_validation_class_count_for_selection': 5,
          'insufficient_validation_policy': 'fixed logistic threshold 0.5; model selection unavailable; rules engineering default; report both frozen methods on test',
          'selection_guard_declaration': 'added after reviewing validation label availability, before any model fit or test feature/outcome inspection',
          'threshold_selection': 'maximum validation balanced accuracy; ties closest to 0.5 then lower',
          'model_selection': 'maximum validation balanced accuracy; ties prefer rules',
          'rules': {'flat_range_max': 1e-6, 'flat_fraction_min': .95,
                    'large_gap_fraction_max': .01, 'coverage_min': .95, 'coverage_max': 1.05,
                    'amplitude_max': 10000, 'difference_p95_max': 2000},
          'missing_feature_policy': 'drop window from both benchmark methods; report count',
          'spectral_policy': 'demeaned Hann rFFT using nominal 256 Hz; high power above 40Hz / non-DC power; timestamp jitter uncorrected'}
POLICY = {'schema_version': 1, 'positive': 'author-excluded', 'negative': 'not-marked-excluded',
          'negative_warning': 'proxy negative, not certified clean',
          'annotation': 'exact six explicit XLSX cells; nan/blank/ambiguous never negative labels',
          'denominator': 'N original ECG samples, not elapsed time; no trimming before label mapping',
          'interval_indices': 'floor(percentage / 100 * N); conservative open interior',
          'windows': 'source-relative 10-second half-open time windows anchored to first timestamp; trailing partial dropped',
          'boundary': 'drop any window whose sample support touches or crosses either floored endpoint; positive only strictly interior; negative only wholly outside all closed intervals',
          'source_semantics': 'NASA preprocess_ekg.py floor percentage*N; source good-index comparisons <=lower or >=upper; source linspace endpoint artifact not reproduced',
          'reference_url': 'https://raw.githubusercontent.com/nasa/nasa-soteria-processing-code/f8661c015346db2ef8895c91be3d4734519a97a4/python-AWS/preprocess_ekg.py',
          'reference_sha256': '9d2841d21006567db5eda72c9bd3096684b11a363c60a91891d0f08786049d4c',
          'warning': WARNING}


def _numpy():
    try:
        import numpy as np
        return np
    except ImportError as error:
        raise RuntimeError('Optional signal dependencies unavailable; install project [signals] extras in isolated environment') from error


def _write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + '\n'
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(encoded, encoding='utf-8')
    temp.replace(path)


def _output_dir(output_dir, data_dir=None):
    out = Path(output_dir).resolve()
    if data_dir is not None and out.is_relative_to(Path(data_dir).resolve()):
        raise ValueError('outputs must be outside data directory')
    out.mkdir(parents=True, exist_ok=True)
    return out


def parse_intervals(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'\d+(?:\.\d+)?:\d+(?:\.\d+)?(?:;\d+(?:\.\d+)?:\d+(?:\.\d+)?)*', raw):
        raise ValueError('annotation is blank, nan or ambiguous, not a clean label')
    result = [[float(v) for v in part.split(':')] for part in raw.split(';')]
    if any(not 0 <= a < b <= 100 for a, b in result) or any(b >= c for (_, b), (c, _) in zip(result, result[1:])):
        raise ValueError('invalid/overlapping annotation intervals')
    return result


def _workbook_cells(path):
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    rel_ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    with zipfile.ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist()) > 20_000_000:
            raise ValueError('workbook uncompressed size exceeds bound')
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(s.itertext()) for s in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        result = {}
        for sheet in ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns):
            target = rels[sheet.attrib[rel_ns]]
            member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            cells = {}
            for cell in ET.fromstring(archive.read(member)).findall('.//s:sheetData/s:row/s:c', ns):
                value = cell.find('s:v', ns)
                if cell.attrib.get('t') == 'inlineStr':
                    text = ''.join(cell.find('s:is', ns).itertext())
                elif value is None:
                    text = ''
                else:
                    text = shared[int(value.text)] if cell.attrib.get('t') == 's' else value.text
                cells[cell.attrib['r']] = text
            result[sheet.attrib['name']] = cells
        return result


def verify_annotations(data_dir):
    """Cross-check original workbook, acquisition hashes and frozen six-record mapping."""
    root = Path(data_dir)
    split = read_json(root / 'split-manifest.json')
    for group in ('train', 'validation', 'test'):
        if split.get(group) != [crew for crew, (g, _, _) in EXPECTED.items() if g == group]:
            raise ValueError('frozen crew group changed')
    workbook, entry = manifest_entry(root, 'Analysis/Analysis_ekg_quality_vector.xlsx')
    cells = _workbook_cells(workbook)
    audited = read_json(root / 'validation.json')['annotation_workbook']['selected_record_entries']
    primary = [r for r in split['records'] if r.get('primary_quality_experiment') is True]
    if len(primary) != 6 or {r['crew'] for r in primary} != set(EXPECTED):
        raise ValueError('experiment requires exactly six primary records')
    annotations = []
    manifest = read_json(root / 'download-manifest.json')
    for record in primary:
        crew = record['crew']
        group, scenario, raw = EXPECTED[crew]
        source = f'{crew}/Processing/abm_leftseat_scenario{scenario}.csv'
        sheet, cell, header = crew + '_Left', ('A2' if scenario == 1 else 'B2'), f'scen {scenario}'
        acquired = [x for x in manifest if x['source_key'] == source]
        if record['source_key'] != source or record['split'] != group or record['participant'] != sheet or len(acquired) != 1 or record['sha256'] != acquired[0]['sha256']:
            raise ValueError('primary source/split/provenance mismatch')
        actual = cells[sheet]
        if actual.get(cell) != raw or actual.get(cell[0]+'1') != header:
            raise ValueError('exact annotation workbook cell/header mismatch')
        intervals = parse_intervals(raw)
        previous = [x for x in audited if x['source_key'] == source]
        expected = {'source_key': source, 'sheet': sheet, 'header': header, 'cell': cell,
                    'raw': raw, 'percentage_intervals': intervals}
        if previous != [expected]:
            raise ValueError('structural annotation audit mismatch')
        annotations.append({**record, **expected})
    return annotations, {'workbook_sha256': entry['sha256'], 'split_manifest_sha256': sha256_file(root/'split-manifest.json'),
                         'download_manifest_sha256': sha256_file(root/'download-manifest.json'),
                         'validation_sha256': sha256_file(root/'validation.json')}


def window_label(sample_start, sample_end, total_samples, intervals):
    """sample_end is exclusive; endpoint contact drops the entire window."""
    if not 0 <= sample_start < sample_end <= total_samples:
        raise ValueError('invalid sample support')
    last = sample_end - 1
    bounds = [(math.floor(a/100*total_samples), math.floor(b/100*total_samples)) for a, b in intervals]
    if any(sample_start <= a <= last or sample_start <= b <= last for a, b in bounds):
        return None
    if any(a < sample_start and last < b for a, b in bounds):
        return 'author-excluded'
    if all(last < a or sample_start > b for a, b in bounds):
        return 'not-marked-excluded'
    return None


def window_features(times, values, nominal_rate=256.0, duration=10.0):
    np = _numpy()
    if not len(values) or any(v is None or not math.isfinite(v) for v in values):
        return None, ['missing/nonfinite samples'], True
    x, t = np.asarray(values, dtype=float), np.asarray(times, dtype=float)
    if len(x) < 2 or len(t) != len(x) or not np.isfinite(t).all() or not (np.diff(t) > 0).all():
        return None, ['invalid timing/insufficient samples'], True
    differences = np.diff(x)
    flat_fraction = float(np.mean(np.abs(differences) <= 1e-6))
    gap_fraction = float(np.mean(np.diff(t) > 3/nominal_rate))
    coverage = len(x)/(duration*nominal_rate)
    ptp = float(np.ptp(x))
    p95 = float(np.percentile(np.abs(differences), 95))
    spectrum = np.abs(np.fft.rfft((x-x.mean()) * np.hanning(len(x))))**2
    frequencies = np.fft.rfftfreq(len(x), 1/nominal_rate)
    power = float(np.sum(spectrum[1:]))
    high = float(np.sum(spectrum[frequencies > 40])/power) if power > 0 else 0.0
    features = dict(zip(FEATURES, [float(np.log1p(np.std(x))), float(np.log1p(ptp)),
                    float(np.log1p(np.sqrt(np.mean(x*x)))),
                    float(np.log1p(np.sqrt(np.mean(differences*differences)))), float(np.log1p(p95)),
                    flat_fraction, gap_fraction, float(coverage), high]))
    if not all(math.isfinite(v) for v in features.values()):
        return None, ['nonfinite feature calculation'], True
    r, reasons = CONFIG['rules'], []
    if ptp <= r['flat_range_max'] or flat_fraction >= r['flat_fraction_min']:
        reasons.append('flatline/repeated samples')
    if gap_fraction > r['large_gap_fraction_max']:
        reasons.append('suspicious timing gap fraction')
    if not r['coverage_min'] <= coverage <= r['coverage_max']:
        reasons.append('nominal sample coverage outside 95-105%')
    if float(np.max(np.abs(x))) > r['amplitude_max']:
        reasons.append('large source-unit amplitude')
    if p95 > r['difference_p95_max']:
        reasons.append('large source-unit sample derivative')
    return features, reasons, bool(reasons)


def _extract(waveform, annotation):
    from bisect import bisect_left
    n = len(waveform.times)
    rows, dropped = [], Counter()
    count = math.floor((waveform.times[-1]-waveform.times[0])/10)
    for index in range(count):
        start = waveform.times[0]+index*10
        lo = bisect_left(waveform.times, start)
        hi = bisect_left(waveform.times, start+10)
        label = window_label(lo, hi, n, annotation['percentage_intervals']) if hi > lo else None
        if label is None:
            dropped['boundary_or_empty'] += 1
            continue
        features, reasons, rule = window_features(waveform.times[lo:hi], waveform.values[lo:hi])
        if features is None:
            dropped['missing_or_invalid_features'] += 1
            continue
        rows.append({'crew': annotation['crew'], 'split': annotation['split'], 'source_key': waveform.source_key,
                     'source_sha256': waveform.source_sha256, 'window_index': index,
                     'source_start_seconds': start, 'source_end_seconds': start+10,
                     'sample_start': lo, 'sample_end': hi, 'label': label,
                     'features': features, 'rules_flagged': rule, 'quality_reasons': reasons})
    return rows, {'crew': annotation['crew'], 'split': annotation['split'], 'full_windows': count,
                  'kept_windows': len(rows), 'labels': dict(Counter(r['label'] for r in rows)),
                  'dropped': dict(dropped), 'trailing_partial_seconds': waveform.times[-1]-(waveform.times[0]+count*10)}


def engineering_windows(waveform):
    """ECG/BVP acquisition flags without supervised labels or medical inference."""
    from bisect import bisect_left
    rows = []
    for index in range(math.floor((waveform.times[-1]-waveform.times[0])/10)):
        start = waveform.times[0]+index*10
        lo, hi = bisect_left(waveform.times, start), bisect_left(waveform.times, start+10)
        features, reasons, flagged = window_features(waveform.times[lo:hi], waveform.values[lo:hi], waveform.nominal_rate_hz)
        rows.append({'source_key': waveform.source_key, 'source_sha256': waveform.source_sha256,
                     'channel': waveform.channel, 'source_start_seconds': start, 'source_end_seconds': start+10,
                     'sample_start': lo, 'sample_end': hi, 'features': features, 'flagged': flagged,
                     'quality_reasons': reasons, 'status': 'engineering-only',
                     'supervised_label': None, 'warning': 'Heuristic flags cannot certify cleanliness or clinical safety'})
    return rows


def _plot_review(waveform, rows, output):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError('Matplotlib required for pre-training trace review; install [training] extras') from error
    # First and midpoint windows from each proxy class; predetermined, no score ranking.
    chosen = []
    singleton_labels = []
    for label in ('author-excluded', 'not-marked-excluded'):
        candidates = [r for r in rows if r['label'] == label]
        if candidates:
            chosen.append(candidates[0])
            if len(candidates) > 1:
                chosen.append(candidates[len(candidates)//2])
            else:
                singleton_labels.append(label)
    fig, axes = plt.subplots(max(1, len(chosen)), 1, figsize=(12, 2.5*max(1, len(chosen))), squeeze=False)
    for ax, row in zip(axes[:, 0], chosen):
        lo, hi = row['sample_start'], row['sample_end']
        ax.plot(waveform.times[lo:hi], waveform.values[lo:hi], linewidth=.65)
        note = ' | only one eligible window in this class' if row['label'] in singleton_labels else ''
        ax.set_title(f"{row['crew']} {row['split']} | {row['label']} | window {row['window_index']} samples [{lo},{hi}){note}", fontsize=10)
        ax.set_xlabel('Source-relative seconds (jitter retained)')
        ax.set_ylabel('Source unit\nunverified')
        ax.grid(alpha=.2)
    fig.suptitle('Author detector exclusions vs proxy negatives; neither is clinical ground truth', fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, .97))
    fig.savefig(output, dpi=120)
    plt.close(fig)
    return [{'window_index': r['window_index'], 'label': r['label'], 'sample_start': r['sample_start'], 'sample_end': r['sample_end']} for r in chosen]


def prepare_experiment(data_dir='data', output_dir='outputs/quality'):
    started = time.perf_counter()
    root = Path(data_dir).resolve()
    out = _output_dir(output_dir, root)
    if (out/'prepared-manifest.json').exists():
        raise ValueError('preparation already exists; use a new output directory to preserve review evidence')
    annotations, hashes = verify_annotations(root)
    rows, counts, audits, traces = [], [], [], []
    for annotation in annotations:
        if annotation['split'] == 'test':
            continue  # no held-out waveform opened, hashed, featured, plotted or scored here
        waveform = load_waveform(root, annotation['source_key'])
        selected, summary = _extract(waveform, annotation)
        rows.extend(selected)
        counts.append(summary)
        audits.append(waveform.audit())
        filename = annotation['crew'] + '-review.png'
        windows = _plot_review(waveform, selected, out/filename)
        traces.append({'file': filename, 'sha256': sha256_file(out/filename), 'crew': annotation['crew'],
                       'split': annotation['split'], 'windows': windows})
    _write(out/'policy.json', POLICY)
    _write(out/'config.json', CONFIG)
    _write(out/'features.json', {'schema_version': 1, 'rows': rows, 'counts': counts})
    _write(out/'source-audit.json', {'annotations': annotations, 'metadata_hashes': hashes,
                                    'waveforms': audits, 'test_waveforms_inspected': False, 'warning': WARNING})
    _write(out/'trace-manifest.json', {'selection': 'first and midpoint available windows per class, train/validation only; singleton shown once and labelled', 'traces': traces})
    files = ('policy.json', 'config.json', 'features.json', 'source-audit.json', 'trace-manifest.json')
    manifest = {'schema_version': 1, 'data_dir': str(root), 'files': {name: sha256_file(out/name) for name in files},
                'status': 'prepared-awaiting-review', 'preparation_seconds': time.perf_counter()-started,
                'test_waveforms_inspected': False}
    _write(out/'prepared-manifest.json', manifest)
    receipt = {'schema_version': 1, 'approved': False, 'reviewer': '', 'reviewed_at': '',
               'prepared_manifest_sha256': sha256_file(out/'prepared-manifest.json'),
               'label_policy_sha256': sha256_file(out/'policy.json'),
               'trace_manifest_sha256': sha256_file(out/'trace-manifest.json'),
               'reviewed_trace_sha256': [t['sha256'] for t in traces]}
    _write(out/'review-receipt-template.json', receipt)
    return {'status': 'prepared-awaiting-review', 'output_dir': str(out), 'counts': counts,
            'review_receipt_template': str(out/'review-receipt-template.json'),
            'prepared_manifest_sha256': receipt['prepared_manifest_sha256'], 'warning': WARNING}


def _verify_prepared(prepared_dir, receipt):
    root = Path(prepared_dir).resolve()
    manifest = read_json(root/'prepared-manifest.json')
    expected_files = {'policy.json', 'config.json', 'features.json', 'source-audit.json', 'trace-manifest.json'}
    if set(manifest['files']) != expected_files or manifest.get('test_waveforms_inspected') is not False:
        raise ValueError('invalid preparation manifest')
    for name, digest in manifest['files'].items():
        if sha256_file(root/name) != digest:
            raise ValueError('prepared artifact changed after preparation')
    if read_json(root/'policy.json') != POLICY or read_json(root/'config.json') != CONFIG:
        raise ValueError('predeclared policy/config changed')
    traces = read_json(root/'trace-manifest.json')['traces']
    for trace in traces:
        if Path(trace['file']).name != trace['file'] or sha256_file(root/trace['file']) != trace['sha256']:
            raise ValueError('trace review artifact changed')
    receipt = read_json(receipt) if isinstance(receipt, (str, Path)) else receipt
    fields = {'schema_version', 'approved', 'reviewer', 'reviewed_at', 'prepared_manifest_sha256',
              'label_policy_sha256', 'trace_manifest_sha256', 'reviewed_trace_sha256'}
    if not isinstance(receipt, dict) or set(receipt) != fields or receipt['schema_version'] != 1 or receipt['approved'] is not True:
        raise ValueError('approved review receipt required')
    if not isinstance(receipt['reviewer'], str) or not receipt['reviewer'].strip():
        raise ValueError('reviewer identity required')
    try:
        reviewed = datetime.fromisoformat(receipt['reviewed_at'].replace('Z', '+00:00'))
        if reviewed.utcoffset() is None:
            raise ValueError('review time requires timezone')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('valid aware review time required') from None
    if receipt['prepared_manifest_sha256'] != sha256_file(root/'prepared-manifest.json') or receipt['label_policy_sha256'] != manifest['files']['policy.json'] or receipt['trace_manifest_sha256'] != manifest['files']['trace-manifest.json'] or receipt['reviewed_trace_sha256'] != [t['sha256'] for t in traces]:
        raise ValueError('receipt does not match prepared policy/trace evidence')
    return manifest, receipt


def metrics(labels, predictions):
    if len(labels) != len(predictions) or any(v not in (0, 1) for v in labels+predictions):
        raise ValueError('binary equal-length labels/predictions required')
    tp = sum(y == p == 1 for y, p in zip(labels, predictions))
    tn = sum(y == p == 0 for y, p in zip(labels, predictions))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions))
    recall = tp/(tp+fn) if tp+fn else None
    specificity = tn/(tn+fp) if tn+fp else None
    return {'n': len(labels), 'author_excluded': sum(labels), 'not_marked_excluded': len(labels)-sum(labels),
            'confusion_matrix': {'tn': tn, 'fp': fp, 'fn': fn, 'tp': tp},
            'precision': tp/(tp+fp) if tp+fp else None, 'recall': recall, 'specificity': specificity,
            'balanced_accuracy': (recall+specificity)/2 if recall is not None and specificity is not None else None,
            'undefined_metric_policy': 'null when denominator or a required class is absent'}


def _evaluate(rows, model):
    labels = [int(r['label'] == 'author-excluded') for r in rows]
    predictions = {'rules': [int(r['rules_flagged']) for r in rows],
                   'logistic': [int(predict(model, r['features'])['flagged']) for r in rows]}
    return {method: {'pooled': metrics(labels, pred), 'per_crew': {
        crew: metrics([labels[i] for i, r in enumerate(rows) if r['crew'] == crew],
                      [pred[i] for i, r in enumerate(rows) if r['crew'] == crew])
        for crew in sorted({r['crew'] for r in rows})}} for method, pred in predictions.items()}


def run_experiment(prepared_dir, output_dir, review_receipt):
    started = time.perf_counter()
    prepared = Path(prepared_dir).resolve()
    manifest, receipt = _verify_prepared(prepared, review_receipt)
    data_root = Path(manifest['data_dir']).resolve()
    out = _output_dir(output_dir, data_root)
    if out == prepared or (out/'selection.json').exists() or (out/'results.json').exists():
        raise ValueError('use a new results directory; held-out benchmark is not a tuning loop')
    annotations, hashes = verify_annotations(data_root)
    audit = read_json(prepared/'source-audit.json')
    if hashes != audit['metadata_hashes'] or annotations != audit['annotations']:
        raise ValueError('source metadata changed since review')
    for annotation in annotations:
        if annotation['split'] != 'test':
            manifest_entry(data_root, annotation['source_key'])  # reverify reviewed train/validation bytes
    try:
        import sklearn
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.exceptions import ConvergenceWarning
        import warnings
    except ImportError as error:
        raise RuntimeError('scikit-learn required; install project [training] extras') from error
    np = _numpy()
    rows = read_json(prepared/'features.json')['rows']
    train, validation = ([r for r in rows if r['split'] == group] for group in ('train', 'validation'))
    def x(group):
        return np.asarray([[r['features'][f] for f in FEATURES] for r in group], dtype=float)
    def y(group):
        return [int(r['label'] == 'author-excluded') for r in group]
    if set(y(train)) != {0, 1}:
        raise ValueError('training requires both proxy classes')
    validation_counts = Counter(y(validation))
    selection_available = all(validation_counts[label] >= CONFIG['minimum_validation_class_count_for_selection'] for label in (0, 1))
    fit_started = time.perf_counter()
    scaler = StandardScaler().fit(x(train))
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        estimator = LogisticRegression(**CONFIG['model']).fit(scaler.transform(x(train)), y(train))
    fitting_seconds = time.perf_counter()-fit_started
    scores = estimator.predict_proba(scaler.transform(x(validation)))[:, 1].tolist() if validation else []
    candidates = []
    for threshold in (CONFIG['threshold_candidates'] if selection_available else [.5]):
        result = metrics(y(validation), [int(s >= threshold) for s in scores])
        candidates.append({'threshold': threshold, **result})
    choice = (min(candidates, key=lambda r: (-r['balanced_accuracy'], abs(r['threshold']-.5), r['threshold']))
              if selection_available else candidates[0])
    model = {'schema_version': 1, 'kind': 'author-exclusion-logistic-regression', 'features': list(FEATURES),
             'mean': scaler.mean_.tolist(), 'scale': scaler.scale_.tolist(), 'coefficients': estimator.coef_[0].tolist(),
             'intercept': float(estimator.intercept_[0]), 'threshold': choice['threshold'], 'config': CONFIG,
             'data_hashes': {a['source_key']: a['sha256'] for a in annotations}, 'warning': WARNING}
    validate_model(model)
    exported_scores = [predict(model, r['features'])['author_exclusion_score'] for r in validation]
    inference_max_difference = max((abs(a-b) for a, b in zip(scores, exported_scores)), default=0.0)
    if inference_max_difference > 1e-12:
        raise ValueError('exported inference does not match standard estimator')
    validation_report = _evaluate(validation, model)
    rules_ba = validation_report['rules']['pooled']['balanced_accuracy']
    logistic_ba = validation_report['logistic']['pooled']['balanced_accuracy']
    selected = 'logistic' if selection_available and logistic_ba > rules_ba else 'rules'
    _write(out/'model.json', model)
    # Committed selection exists before a held-out waveform is opened.
    selection = {'selected_method': selected, 'threshold': choice['threshold'], 'validation': validation_report,
                 'selection_available': selection_available,
                 'selection_reason': ('validation minimum class count satisfied' if selection_available else
                    'validation has fewer than five windows in at least one class; no optimization or model selection; rules engineering default'),
                 'threshold_candidates': candidates, 'prepared_manifest_sha256': receipt['prepared_manifest_sha256'],
                 'model_sha256': sha256_file(out/'model.json'), 'test_inspected_at_selection': False}
    _write(out/'selection.json', selection)
    _write(out/'review-receipt.json', receipt)
    held_out, test_counts, test_audits = [], [], []
    for annotation in annotations:
        if annotation['split'] == 'test':
            waveform = load_waveform(data_root, annotation['source_key'])
            selected_rows, count = _extract(waveform, annotation)
            held_out.extend(selected_rows)
            test_counts.append(count)
            test_audits.append(waveform.audit())
    inference_start = time.perf_counter()
    test_report = _evaluate(held_out, model)
    inference_seconds = time.perf_counter()-inference_start
    results = {'schema_version': 1, 'status': 'experimental-benchmark-complete', 'warning': WARNING,
               'selected_method': selected, 'selection_sha256': sha256_file(out/'selection.json'),
               'selection_available': selection_available, 'selection_reason': selection['selection_reason'],
               'train': _evaluate(train, model), 'validation': validation_report, 'test': test_report,
               'counts': read_json(prepared/'features.json')['counts']+test_counts,
               'test_source_audits': test_audits, 'model_bytes': (out/'model.json').stat().st_size,
               'timing': {'fitting_seconds': fitting_seconds, 'test_inference_both_methods_seconds': inference_seconds,
                          'benchmark_seconds': time.perf_counter()-started},
               'runtime': {'numpy': np.__version__, 'scikit_learn': sklearn.__version__},
               'exported_inference_max_validation_difference': inference_max_difference,
               'limitations': ['annotation-selected six people; windows are not independent people',
                   'negative means not-marked-excluded, not certified clean',
                   'Crew_06 scenario differs; one validation crew; no external replication',
                   'spectral feature uses nominal rate despite timestamp jitter',
                   'PPG has replay/engineering heuristics only, no supervised labels',
                   'No SpO2, sleep stage, HRV, pulse, diagnosis or clinical inference']}
    _write(out/'results.json', results)
    return results


def quality_status(output_dir='outputs/quality'):
    root = Path(output_dir)
    result_path = root/'results.json'
    if not result_path.exists() and (root/'results'/'results.json').exists():
        result_path = root/'results'/'results.json'
    if result_path.exists():
        try:
            results = read_json(result_path)
            selection = read_json(result_path.parent/'selection.json')
            if (results.get('schema_version') != 1 or results.get('status') != 'experimental-benchmark-complete'
                    or results.get('selected_method') not in ('rules', 'logistic')
                    or type(results.get('selection_available')) is not bool
                    or results['selection_sha256'] != sha256_file(result_path.parent/'selection.json')
                    or selection['model_sha256'] != sha256_file(result_path.parent/'model.json')):
                raise ValueError('result/selection/model integrity mismatch')
            return {'status': results['status'], 'selected_method': results['selected_method'],
                    'selection_available': results['selection_available'], 'selection_reason': results['selection_reason'],
                    'test_metrics': {method: results['test'][method]['pooled'] for method in ('rules', 'logistic')},
                    'results_path': str(result_path), 'warning': WARNING,
                    'ppg_supervised': 'unavailable: no reviewed labels', 'clinical_inference': 'unavailable'}
        except (ValueError, KeyError, OSError, TypeError):
            return {'status': 'unavailable', 'reason': 'invalid quality result artifact', 'warning': WARNING}
    if (root/'prepared-manifest.json').exists():
        return {'status': 'prepared-awaiting-review', 'warning': WARNING}
    return {'status': 'unavailable', 'reason': 'no prepared experiment or benchmark results',
            'waveform_replay': 'supported with verified local manifests', 'warning': WARNING}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--data-dir', default='data')
    prepare.add_argument('--output-dir', default='outputs/quality')
    benchmark = commands.add_parser('benchmark')
    benchmark.add_argument('--prepared-dir', default='outputs/quality')
    benchmark.add_argument('--output-dir', default='outputs/quality/results')
    benchmark.add_argument('--review-receipt', required=True)
    status = commands.add_parser('status')
    status.add_argument('--output-dir', default='outputs/quality')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare_experiment(args.data_dir, args.output_dir)
        elif args.command == 'benchmark':
            result = run_experiment(args.prepared_dir, args.output_dir, args.review_receipt)
        else:
            result = quality_status(args.output_dir)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f'Quality experiment unavailable: {error}\n')


if __name__ == '__main__':
    main()
