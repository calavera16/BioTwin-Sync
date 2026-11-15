import copy
import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from biotwin.quality import (CONFIG, POLICY, _verify_prepared, _write, engineering_windows,
                            metrics, parse_intervals, prepare_experiment, quality_status, run_experiment, window_features, window_label)
from biotwin.quality_model import FEATURES, load_model
from biotwin.waveforms import Waveform, sha256_file


class LabelsAndMetrics(unittest.TestCase):
    def test_open_sample_interiors_and_conservative_boundaries(self):
        intervals = [[20, 40], [60, 70]]
        self.assertEqual(window_label(21, 40, 100, intervals), 'author-excluded')
        self.assertEqual(window_label(0, 20, 100, intervals), 'not-marked-excluded')
        self.assertEqual(window_label(41, 60, 100, intervals), 'not-marked-excluded')
        for lo, hi in ((20, 30), (30, 41), (10, 50), (0, 100), (60, 70)):
            self.assertIsNone(window_label(lo, hi, 100, intervals))
        # Floor conversion and exclusive sample_end are explicit.
        self.assertEqual(window_label(21, 40, 101, intervals), 'author-excluded')

    def test_ambiguous_is_never_clean(self):
        for raw in ('nan', '', None, '1.231', '5:3', '2:5;4:7', '0:101'):
            with self.assertRaises(ValueError):
                parse_intervals(raw)
        self.assertEqual(parse_intervals('17:19;53:54;88:90'), [[17, 19], [53, 54], [88, 90]])

    def test_metrics_undefined_class_and_prediction_denominators(self):
        result = metrics([1, 1, 0, 0], [1, 0, 1, 0])
        self.assertEqual(result['balanced_accuracy'], .5)
        self.assertEqual(result['confusion_matrix'], {'tn': 1, 'fp': 1, 'fn': 1, 'tp': 1})
        result = metrics([0, 0], [0, 0])
        self.assertIsNone(result['recall'])
        self.assertIsNone(result['precision'])
        self.assertIsNone(result['balanced_accuracy'])


class ReviewGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'trace.png').write_bytes(b'synthetic trace bytes')
        self.traces = {'traces': [{'file': 'trace.png', 'sha256': sha256_file(self.root/'trace.png')}]}
        payloads = {'policy.json': POLICY, 'config.json': CONFIG, 'features.json': {'rows': []},
                    'source-audit.json': {}, 'trace-manifest.json': self.traces}
        for name, payload in payloads.items():
            _write(self.root/name, payload)
        manifest = {'files': {name: sha256_file(self.root/name) for name in payloads}, 'test_waveforms_inspected': False}
        _write(self.root/'prepared-manifest.json', manifest)
        self.receipt = {'schema_version': 1, 'approved': True, 'reviewer': 'synthetic-test',
                        'reviewed_at': '2026-11-14T00:00:00+00:00',
                        'prepared_manifest_sha256': sha256_file(self.root/'prepared-manifest.json'),
                        'label_policy_sha256': sha256_file(self.root/'policy.json'),
                        'trace_manifest_sha256': sha256_file(self.root/'trace-manifest.json'),
                        'reviewed_trace_sha256': [self.traces['traces'][0]['sha256']]}

    def test_exact_review_gate_and_mutation(self):
        _verify_prepared(self.root, self.receipt)
        for field, value in (('approved', False), ('reviewer', ''), ('reviewed_at', '2026-11-14'),
                             ('reviewed_trace_sha256', []), ('prepared_manifest_sha256', 'f'*64)):
            receipt = {**self.receipt, field: value}
            with self.assertRaises(ValueError):
                _verify_prepared(self.root, receipt)
        (self.root/'trace.png').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'trace review artifact changed'):
            _verify_prepared(self.root, self.receipt)

    def test_unapproved_benchmark_never_opens_waveforms(self):
        with patch('biotwin.quality.load_waveform') as loader:
            with self.assertRaises(ValueError):
                run_experiment(self.root, self.root/'results', {**self.receipt, 'approved': False})
            loader.assert_not_called()

    def test_changed_config_rejected_and_absent_status_explicit(self):
        _write(self.root/'config.json', {**CONFIG, 'window_seconds': 5})
        with self.assertRaises(ValueError):
            _verify_prepared(self.root, self.receipt)
        self.assertEqual(quality_status(self.root/'absent')['status'], 'unavailable')

    def test_preparation_does_not_open_test_waveforms(self):
        annotations = [{'crew': 'Train', 'split': 'train', 'source_key': 'train'},
                       {'crew': 'Validation', 'split': 'validation', 'source_key': 'validation'},
                       {'crew': 'HeldOut', 'split': 'test', 'source_key': 'test'}]
        waveform = SimpleNamespace(audit=lambda: {'synthetic': True})
        def plot(_waveform, _rows, path):
            path.write_bytes(b'synthetic')
            return []
        with patch('biotwin.quality.verify_annotations', return_value=(annotations, {})), \
             patch('biotwin.quality.load_waveform', return_value=waveform) as loader, \
             patch('biotwin.quality._extract', return_value=([], {})), \
             patch('biotwin.quality._plot_review', side_effect=plot):
            report = prepare_experiment(self.root/'data', self.root/'new-preparation')
        self.assertEqual(report['status'], 'prepared-awaiting-review')
        self.assertEqual([call.args[1] for call in loader.call_args_list], ['train', 'validation'])


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'optional NumPy absent')
class Features(unittest.TestCase):
    def test_flatline_missing_and_timing_reasons(self):
        times = tuple(i/256 for i in range(2560))
        features, reasons, flagged = window_features(times, (1.,)*2560)
        self.assertTrue(flagged)
        self.assertIn('flatline/repeated samples', reasons)
        self.assertEqual(features['high_frequency_fraction'], 0)
        features, reasons, flagged = window_features(times, (None,)+(1.,)*2559)
        self.assertIsNone(features)
        self.assertTrue(flagged)
        features, reasons, flagged = window_features(tuple(i/128 for i in range(1280)), tuple(i%10 for i in range(1280)))
        self.assertIn('nominal sample coverage outside 95-105%', reasons)

    def test_bvp_engineering_has_no_supervised_labels(self):
        waveform = Waveform('synthetic-bvp', 'a'*64, 'Bvp', 64.,
                            tuple(i/64 for i in range(641)), tuple(float(i%11) for i in range(641)), 0)
        rows = engineering_windows(waveform)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['supervised_label'])
        self.assertEqual(rows[0]['features']['coverage_ratio'], 1)


@unittest.skipUnless(importlib.util.find_spec('sklearn'), 'optional training stack absent')
class SyntheticExperiment(unittest.TestCase):
    def test_fallback_train_only_scaler_and_selection_before_test(self):
        def row(crew, split, value, label):
            return {'crew': crew, 'split': split, 'features': {f: value for f in FEATURES},
                    'label': label, 'rules_flagged': False}
        train = [row('Crew_01', 'train', v, 'author-excluded' if v > 1 else 'not-marked-excluded') for v in (0., 1., 2., 3.)]
        validation = [row('Crew_05', 'validation', 100., 'author-excluded'), row('Crew_05', 'validation', 101., 'not-marked-excluded')]
        held_out = [row('Crew_06', 'test', 200., 'author-excluded'), row('Crew_06', 'test', 201., 'not-marked-excluded')]
        annotations = [{'crew': crew, 'split': split, 'source_key': crew+'.csv', 'sha256': 'a'*64}
                       for crew, split in (('Crew_01', 'train'), ('Crew_05', 'validation'), ('Crew_06', 'test'))]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared, results = root/'prepared', root/'results'
            prepared.mkdir()
            _write(prepared/'features.json', {'rows': train+validation, 'counts': []})
            _write(prepared/'source-audit.json', {'annotations': annotations, 'metadata_hashes': {}})
            receipt = {'prepared_manifest_sha256': 'a'*64}
            def load_test(*args):
                self.assertTrue((results/'selection.json').exists())
                return SimpleNamespace(audit=lambda: {'source_key': 'synthetic'})
            with patch('biotwin.quality._verify_prepared', return_value=({'data_dir': str(root/'data')}, receipt)), \
                 patch('biotwin.quality.verify_annotations', return_value=(annotations, {})), \
                 patch('biotwin.quality.manifest_entry'), \
                 patch('biotwin.quality.load_waveform', side_effect=load_test), \
                 patch('biotwin.quality._extract', return_value=(held_out, {})):
                report = run_experiment(prepared, results, receipt)
            exported = load_model(results/'model.json')
            self.assertEqual(exported['mean'], [1.5]*len(FEATURES))
            self.assertEqual(exported['threshold'], .5)
            self.assertFalse(report['selection_available'])
            self.assertEqual(report['selected_method'], 'rules')
            self.assertLessEqual(report['exported_inference_max_validation_difference'], 1e-12)


if __name__ == '__main__':
    unittest.main()
