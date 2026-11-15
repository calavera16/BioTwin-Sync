import copy
import json
import random
from pathlib import Path
import tempfile
import unittest

from biotwin.signal_review import (FEATURES, WARNING, analyze_local_signal, extract_features,
    load_model, model_digest, predict, validate_model, create_injected_demo)
from test_signal_measurements import signal


def model(score=.7):
    item = {'schema_version': 1, 'id': 'synthetic-test-only',
            'kind': 'injected-corruption-random-forest', 'features': list(FEATURES),
            'trees': [[[-1, -2., -1, -1, score]]], 'threshold': .5,
            'target': 'synthetic test only', 'warning': WARNING,
            'provenance': {'synthetic': 'a'*64}, 'deployment': 'experimental_scorer_only'}
    item['sha256'] = model_digest(item)
    return item


class SignalReview(unittest.TestCase):
    def test_json_inference_and_feature_translation_scale_invariance(self):
        item = model()
        rng = random.Random(2026)
        values = [rng.uniform(-2, 2) for _ in range(2560)]
        feature = extract_features(values)
        self.assertAlmostEqual(predict(item, feature), .7)
        second = extract_features([v*3+10 for v in values])
        for key in FEATURES:
            self.assertAlmostEqual(feature[key], second[key], places=8)

    def test_integrity_nonfinite_cycle_and_wrong_features_rejected(self):
        for patch in ({'sha256': '0'*64}, {'trees': [[[0, .1, 0, 0, .2]]]},
                      {'features': list(reversed(FEATURES))},
                      {'trees': [[[-1, -2, -1, -1, float('nan')]]]}):
            item = model()
            item.update(patch)
            with self.assertRaises(ValueError):
                validate_model(item)

    def test_duplicate_keys_and_secret_names_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'model.json'
            path.write_text('{"x":1,"x":2}')
            with self.assertRaises(ValueError):
                load_model(path)
            with self.assertRaises(ValueError):
                load_model(Path(root)/'.ENV.secret')

    def test_window_marks_source_identity_and_real_input_scorer_only(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'model.json'
            path.write_text(json.dumps(model()))
            result = analyze_local_signal(signal(seconds=20), path)
            self.assertEqual(result['status'], 'experimental_scorer_only')
            self.assertEqual(result['source_id'], 'synthetic-test')
            self.assertEqual(len(result['windows']), 2)
            self.assertTrue(all(w['status'] == 'uncertain' for w in result['windows']))
            self.assertEqual(result['windows'][1]['sample_start'], 2560)

    def test_missing_partial_and_wrong_rate_windows_abstain(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'model.json'
            path.write_text(json.dumps(model()))
            item = signal(seconds=11)
            item['samples'][15] = None
            result = analyze_local_signal(item, path)
            self.assertTrue(all(w['status'] == 'unavailable' for w in result['windows']))
            item = signal(seconds=10, rate=250)
            self.assertEqual(analyze_local_signal(item, path)['windows'][0]['status'], 'unavailable')

    def test_controlled_demo_retains_original_and_marks_actual_scored_copy(self):
        original = signal(seconds=10)
        before = copy.deepcopy(original)
        corrupted = create_injected_demo(original, 'hold')
        self.assertEqual(original, before)
        self.assertEqual(corrupted['subject_id'], original['subject_id'])
        self.assertNotEqual(corrupted['source_id'], original['source_id'])
        self.assertEqual(corrupted['quality'], 'review')
        self.assertEqual(corrupted['controlled_injection_demo']['original_source_id'], original['source_id'])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'model.json'
            path.write_text(json.dumps(model(.7)))
            result = analyze_local_signal(corrupted, path)
            self.assertEqual(result['windows'][0]['status'], 'review_recommended')
            self.assertIn('not a NASA-observed', result['windows'][0]['scope'])
            self.assertIsNone(result['measurements']['rate_per_minute'])

    def test_missing_model_and_untrained_bvp_explicit(self):
        self.assertEqual(analyze_local_signal(signal(), Path('nonexistent-model.json'))['status'], 'unavailable')
        result = analyze_local_signal(signal('BVP', rate=64))
        self.assertEqual(result['model']['status'], 'unavailable')
        self.assertIn('No trained BVP', result['model']['reason'])


if __name__ == '__main__':
    unittest.main()
