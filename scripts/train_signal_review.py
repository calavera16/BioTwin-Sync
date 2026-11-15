"""One fixed, approved injected-corruption experiment. CPU training only; no uploads."""
from __future__ import annotations
import argparse
from bisect import bisect_left
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biotwin.signal_review import FEATURES, WARNING, extract_features, engineering_flag, model_digest, predict, validate_model
from biotwin.waveforms import read_json, sha256_file
from biotwin.quality import _workbook_cells, parse_intervals, window_label

SPLITS = {'train': ['Crew_01', 'Crew_02', 'Crew_03'], 'validation': ['Crew_05'], 'test': ['Crew_08', 'Crew_09']}
FAMILIES = ('original', 'hold', 'clip', 'burst')
OUTPUT = ROOT/'outputs'/'signal-review-v2'


def save(path, content):
    path.write_text(json.dumps(content, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def freeze():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT/'plan.json'
    if path.exists():
        raise ValueError('freeze already exists')
    acquired = read_json(ROOT/'data/download-manifest.json')
    sources = []
    for split, crews in SPLITS.items():
        for crew in crews:
            scenario = 7 if crew == 'Crew_08' else 5 if crew == 'Crew_09' else 2
            key = crew+f'/Processing/abm_leftseat_scenario{scenario}.csv'
            entry = next((e for e in acquired if e['source_key'] == key), None)
            if entry:
                entry = {k: entry[k] for k in ('source_key', 'url', 'bytes', 'sha256', 'etag')}
            else:
                url = 'https://nasa-soteria-data.s3.us-west-2.amazonaws.com/'+key
                with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=30) as response:
                    entry = {'source_key': key, 'url': url, 'bytes': int(response.headers['Content-Length']),
                             'etag': response.headers['ETag'], 'sha256': None}
            sources.append(dict(entry, crew=crew, split=split))
    plan = {'created_utc': datetime.now(timezone.utc).isoformat(), 'splits': SPLITS, 'sources': sources,
            'features': list(FEATURES), 'families': list(FAMILIES),
            'parameters': {'trees': 24, 'max_depth': 5, 'min_samples_leaf': 6, 'random_state': 2026,
                           'max_original_windows_per_person': 100, 'threshold': .5},
            'frozen_hashes': {p: sha256_file(ROOT/p) for p in ('docs/model-v2-experiment.md',
                'biotwin/signal_review.py', 'scripts/train_signal_review.py',
                'data/download-manifest.json', 'data/raw/soteria/Analysis/Analysis_ekg_quality_vector.xlsx')},
            'annotation_screen': {k: {c:v for c,v in cells.items() if c.endswith('1') or c.endswith('2')} for k,cells in _workbook_cells(ROOT/'data/raw/soteria/Analysis/Analysis_ekg_quality_vector.xlsx').items() if not any(k.startswith('Crew_'+i) for i in ('01','02','03','05','06','10'))},
            'approval_required': True, 'target': 'injected technical corruption, not natural artifact or clinical quality'}
    save(path, plan)
    return {'plan_sha256': sha256_file(path), 'path': str(path)}


def acquire(plan):
    sources = []
    for entry in plan['sources']:
        destination = ROOT/'data/raw/soteria'/entry['source_key']
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix('.download')
            if entry['bytes'] > 50_000_000:
                raise ValueError('source exceeds frozen acquisition size bound')
            count = 0
            try:
                with urllib.request.urlopen(entry['url'], timeout=40) as response, temporary.open('xb') as output:
                    if response.headers.get('ETag') != entry['etag']:
                        raise ValueError('source changed since freeze')
                    while block := response.read(1024*1024):
                        count += len(block)
                        if count > entry['bytes']:
                            raise ValueError('source length exceeds frozen metadata')
                        output.write(block)
                if count != entry['bytes']:
                    raise ValueError('source truncated')
                temporary.rename(destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
        if destination.stat().st_size != entry['bytes']:
            raise ValueError('source size mismatch')
        digest = sha256_file(destination)
        if entry['sha256'] and digest != entry['sha256']:
            raise ValueError('source digest mismatch')
        sources.append(dict(entry, sha256=digest, license='CC BY 4.0',
                            license_url='https://creativecommons.org/licenses/by/4.0/',
                            local_path=destination.relative_to(ROOT).as_posix(),
                            verified_utc=datetime.now(timezone.utc).isoformat()))
    save(OUTPUT/'source-manifest.json', sources)
    return sources


def inject(values, crew, index, family):
    if family == 'original':
        return list(values)
    seed = int.from_bytes(hashlib.sha256(f'biotwin-injection-v2|{crew}|{index}|{family}'.encode()).digest()[:8], 'big')
    rng = random.Random(seed)
    std = max(statistics.pstdev(values), 1e-12)
    result = list(values)
    if family == 'clip':
        middle = statistics.mean(values)
        width = rng.uniform(.35, .65)*std
        return [max(middle-width, min(middle+width, v)) for v in values]
    length = round(rng.uniform(.5, 2)*256)
    start = rng.randrange(len(values)-length+1)
    if family == 'hold':
        result[start:start+length] = [values[start]]*length
    else:
        noise = rng.uniform(2, 5)*std
        result[start:start+length] = [v+rng.gauss(0, noise) for v in result[start:start+length]]
    return result


def prepare_source(entry):
    times, values = [], []
    with (ROOT/entry['local_path']).open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames.count('UserTimeStamp') != 1 or reader.fieldnames.count('ECG') != 1:
            raise ValueError('source columns ambiguous')
        for row in reader:
            t = float(row['UserTimeStamp'])
            v = float(row['ECG'])
            if not math.isfinite(t) or times and t <= times[-1]:
                raise ValueError('source time invalid')
            times.append(t)
            values.append(v if math.isfinite(v) else None)
    rows, dropped = [], {'partial': 0, 'missing': 0, 'timing': 0, 'cap': 0}
    base = 0
    # Original sample blocks preserve exact 2560-sample support and actual offsets.
    for lo in range(0, len(values), 2560):
        hi = min(len(values), lo+2560)
        if hi-lo != 2560:
            dropped['partial'] += 1
            continue
        if any(v is None for v in values[lo:hi]):
            dropped['missing'] += 1
            continue
        if abs(times[hi-1]-times[lo]-2559/256) > .5 or any((times[i+1]-times[i])*256 > 3 for i in range(lo, hi-1)):
            dropped['timing'] += 1
            continue
        if base >= 100:
            dropped['cap'] += 1
            continue
        original = values[lo:hi]
        for family in FAMILIES:
            feature = extract_features(inject(original, entry['crew'], lo//2560, family))
            rows.append({'crew': entry['crew'], 'split': entry['split'], 'window_index': lo//2560,
                         'sample_start': lo, 'sample_end_exclusive': hi, 'total_samples': len(values),
                         'source_time_start': times[lo], 'source_time_end': times[hi-1],
                         'family': family, 'label': int(family != 'original'), 'features': feature})
        base += 1
    return rows, {'original_windows': base, 'dropped': dropped, 'total_source_samples': len(values)}


def metrics(rows, scores):
    tn = fp = fn = tp = 0
    for row, score in zip(rows, scores):
        if row['label']:
            tp += score >= .5
            fn += score < .5
        else:
            fp += score >= .5
            tn += score < .5
    recall = tp/(tp+fn) if tp+fn else None
    specificity = tn/(tn+fp) if tn+fp else None
    originals = sorted(s for r, s in zip(rows, scores) if r['family'] == 'original')
    return {'tn': tn, 'fp': fp, 'fn': fn, 'tp': tp, 'sensitivity': recall, 'specificity': specificity,
            'balanced_accuracy': (recall+specificity)/2 if recall is not None and specificity is not None else None,
            'precision': tp/(tp+fp) if tp+fp else None,
            'original_score_distribution': {'n': len(originals), 'min': min(originals),
                'median': statistics.median(originals), 'p90': originals[math.floor((len(originals)-1)*.9)],
                'max': max(originals), 'fraction_ge_0_5': sum(v >= .5 for v in originals)/len(originals)} if originals else None,
            'family_sensitivity': {f: sum(s >= .5 for r, s in zip(rows, scores) if r['family'] == f)/
                sum(r['family'] == f for r in rows) for f in FAMILIES[1:] if any(r['family'] == f for r in rows)}}



def natural_rows(rows, cells, source_by_crew):
    result, boundaries = [], {}
    for row in rows:
        if row['family'] != 'original':
            continue
        crew = row['crew']
        scenario = int(source_by_crew[crew]['source_key'].split('scenario')[1].split('.')[0])
        sheet = cells[crew+'_Left']
        column = next((k[0] for k,v in sheet.items() if k.endswith('1') and v == f'scen {scenario}'), None)
        if column is None or sheet.get(column+'2') in ('nan', '', None):
            raise ValueError('frozen natural annotation unavailable')
        label = window_label(row['sample_start'], row['sample_end_exclusive'], row['total_samples'], parse_intervals(sheet[column+'2']))
        if label is None:
            boundaries[crew] = boundaries.get(crew, 0)+1
        else:
            result.append(dict(row, label=int(label == 'author-excluded')))
    return result, boundaries


def exported_trees(clf):
    result = []
    for estimator in clf.estimators_:
        tree = estimator.tree_
        result.append([[int(tree.feature[i]) if tree.children_left[i] >= 0 else -1,
            float(tree.threshold[i]), int(tree.children_left[i]), int(tree.children_right[i]),
            float(tree.value[i][0][1]/sum(tree.value[i][0]))] for i in range(tree.node_count)])
    return result


def train(approved_hash):
    from sklearn.ensemble import RandomForestClassifier
    import sklearn
    if sha256_file(OUTPUT/'plan.json') != approved_hash:
        raise ValueError('approval does not match frozen plan')
    plan = read_json(OUTPUT/'plan.json')
    for p, digest in plan['frozen_hashes'].items():
        if sha256_file(ROOT/p) != digest:
            raise ValueError('frozen source changed: '+p)
    result_dir = OUTPUT/'results'
    result_dir.mkdir(exist_ok=False)
    started = time.perf_counter()
    sources = acquire(plan)
    all_rows, audit = [], {}
    # Only train/validation are opened before fitting and selection is fixed in plan.
    for entry in sources:
        if entry['split'] != 'test':
            rows, audit[entry['crew']] = prepare_source(entry)
            all_rows.extend(rows)
    training = [r for r in all_rows if r['split'] == 'train']
    clf = RandomForestClassifier(n_estimators=24, max_depth=5, min_samples_leaf=6,
                                 class_weight='balanced', random_state=2026, n_jobs=1)
    fit_started = time.perf_counter()
    clf.fit([[r['features'][f] for f in FEATURES] for r in training], [r['label'] for r in training])
    fit_seconds = time.perf_counter()-fit_started
    cells = _workbook_cells(ROOT/'data/raw/soteria/Analysis/Analysis_ekg_quality_vector.xlsx')
    source_by_crew = {e['crew']: e for e in sources}
    natural_training, _ = natural_rows(training, cells, source_by_crew)
    natural_clf = RandomForestClassifier(n_estimators=24, max_depth=5, min_samples_leaf=6, class_weight='balanced', random_state=2026, n_jobs=1)
    natural_fit_start = time.perf_counter()
    natural_clf.fit([[r['features'][f] for f in FEATURES] for r in natural_training], [r['label'] for r in natural_training])
    natural_fit_seconds = time.perf_counter()-natural_fit_start
    trees = []
    for estimator in clf.estimators_:
        tree = estimator.tree_
        trees.append([[int(tree.feature[i]) if tree.children_left[i] >= 0 else -1, float(tree.threshold[i]),
                       int(tree.children_left[i]), int(tree.children_right[i]),
                       float(tree.value[i][0][1]/sum(tree.value[i][0]))] for i in range(tree.node_count)])
    model = {'schema_version': 1, 'id': 'biotwin-injected-technical-corruption-v2',
             'kind': 'injected-corruption-random-forest', 'features': list(FEATURES), 'trees': trees,
             'threshold': .5, 'target': 'deliberately injected hold/clipping/burst versus original NASA pilot ECG',
             'warning': WARNING, 'provenance': {e['source_key']: e['sha256'] for e in sources},
             'deployment': 'experimental_scorer_only'}
    model['provenance']['frozen_plan'] = approved_hash
    natural_model = dict(model, id='biotwin-author-exclusion-review-v2', kind='author-exclusion-random-forest',
        trees=exported_trees(natural_clf), target='NASA author heartbeat-detector exclusions versus not-marked-excluded proxy',
        warning='Uncalibrated author-exclusion review score. Not artifact probability, cleanliness, disease or astronaut validation. All original evidence retained.')
    natural_model['sha256'] = model_digest(natural_model)
    model['natural_head'] = natural_model
    model['sha256'] = model_digest(model)
    validate_model(model)
    save(result_dir/'selection-before-test.json', {'threshold': .5, 'hyperparameter_selection': 'none',
          'model_sha256': model['sha256'], 'plan_sha256': approved_hash})
    for entry in sources:
        if entry['split'] == 'test':
            rows, audit[entry['crew']] = prepare_source(entry)
            all_rows.extend(rows)
    scores = [predict(model, row['features']) for row in all_rows]
    sklearn_scores = clf.predict_proba([[r['features'][f] for f in FEATURES] for r in all_rows])[:, 1]
    parity = max(abs(a-b) for a, b in zip(scores, sklearn_scores))
    if parity > 1e-12:
        raise ValueError('export parity failure')
    results = {'plan_sha256': approved_hash, 'sklearn_version': sklearn.__version__, 'fit_seconds': fit_seconds,
               'export_max_absolute_difference': parity, 'source_audit': audit, 'splits': {}}
    for split in SPLITS:
        chosen = [(r, s) for r, s in zip(all_rows, scores) if r['split'] == split]
        rows, selected = [x[0] for x in chosen], [x[1] for x in chosen]
        results['splits'][split] = {'model': metrics(rows, selected),
            'rules': metrics(rows, [float(engineering_flag(r['features'])) for r in rows]),
            'majority': metrics(rows, [1.]*len(rows)), 'per_person': {}}
        for crew in SPLITS[split]:
            items = [(r, s) for r, s in chosen if r['crew'] == crew]
            results['splits'][split]['per_person'][crew] = metrics([r for r, _ in items], [s for _, s in items])
    test = results['splits']['test']
    gate = (test['model']['balanced_accuracy'] is not None and test['model']['balanced_accuracy'] >= .8 and
            test['model']['balanced_accuracy'] >= test['rules']['balanced_accuracy']+.02 and
            all(m['sensitivity'] is not None and m['specificity'] is not None and
                m['sensitivity'] >= .8 and m['specificity'] >= .8 for m in test['per_person'].values()))
    results['injection_demo_gate_passed'] = gate
    results['deployment'] = 'experimental_scorer_only pending natural-target evaluation'
    natural_all, boundaries = natural_rows(all_rows, cells, source_by_crew)
    natural_scores = [predict(natural_model, r['features']) for r in natural_all]
    natural_reference = natural_clf.predict_proba([[r['features'][f] for f in FEATURES] for r in natural_all])[:,1]
    natural_parity = max(abs(a-b) for a,b in zip(natural_scores, natural_reference))
    if natural_parity > 1e-12:
        raise ValueError('natural export parity failed')
    natural_results = {'fit_seconds': natural_fit_seconds, 'export_max_absolute_difference': natural_parity,
                       'boundary_dropped': boundaries, 'splits': {}}
    for split in SPLITS:
        chosen = [(r,s) for r,s in zip(natural_all, natural_scores) if r['split'] == split]
        rows, selected = [x[0] for x in chosen], [x[1] for x in chosen]
        natural_results['splits'][split] = {'model': metrics(rows, selected),
            'rules': metrics(rows, [float(engineering_flag(r['features'])) for r in rows]), 'per_person': {}}
        for crew in SPLITS[split]:
            items = [(r,s) for r,s in chosen if r['crew'] == crew]
            natural_results['splits'][split]['per_person'][crew] = metrics([r for r,_ in items], [s for _,s in items])
    ntest = natural_results['splits']['test']
    natural_gate = (ntest['model']['balanced_accuracy'] is not None and ntest['model']['balanced_accuracy'] >= .8 and
        ntest['model']['balanced_accuracy'] >= ntest['rules']['balanced_accuracy']+.02 and
        all(m['tp']+m['fn'] >= 5 and m['tn']+m['fp'] >= 5 and m['sensitivity'] >= .8 and
            m['specificity'] >= .8 for m in ntest['per_person'].values()))
    natural_model['deployment'] = 'experimental_review' if natural_gate else 'experimental_scorer_only'
    natural_model['sha256'] = model_digest(natural_model)
    model['natural_head'] = natural_model
    model['sha256'] = model_digest(model)
    natural_results['deployment_gate_passed'] = natural_gate
    results['natural_author_exclusion_target'] = natural_results
    results['deployment'] = natural_model['deployment']
    results['natural_annotation_mappings'] = {crew: {'sheet': crew+'_Left', 'scenario': source_by_crew[crew]['source_key'],
        'cells': {k:v for k,v in cells[crew+'_Left'].items() if k.endswith('1') or k.endswith('2')}} for crew in SPLITS['test']}
    save(result_dir/'model.json', model)
    results['artifact_bytes'] = (result_dir/'model.json').stat().st_size
    bench = time.perf_counter()
    for _ in range(100):
        predict(model, all_rows[0]['features'])
    results['stdlib_seconds_per_feature_window_inference'] = (time.perf_counter()-bench)/100
    results['total_seconds'] = time.perf_counter()-started
    save(result_dir/'results.json', results)
    save(result_dir/'window-scores.json', [dict({k:v for k,v in r.items() if k != 'features'}, score=s)
                                         for r, s in zip(all_rows, scores)])
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze', 'train'])
    parser.add_argument('--approved-plan-sha256')
    args = parser.parse_args()
    print(json.dumps(freeze() if args.command == 'freeze' else train(args.approved_plan_sha256), indent=2))
