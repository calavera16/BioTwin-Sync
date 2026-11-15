import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from biotwin.ecg_reliability import (FEATURES, analyze_reliability, estimate_window,
                                     load_model, match_beats, model_digest, predict_error, validate_model)
from biotwin.signal_review import analyze_local_signal


def pulse_signal(seconds=64, rate=360):
    values = [sum(math.exp(-((i/rate-beat)/.025)**2) for beat in range(max(0, int(i/rate)-1), int(i/rate)+2))
              for i in range(round(seconds*rate))]
    return {'channel': 'ECG', 'samples': values, 'sample_rate_hz': rate,
            'source_id': 'synthetic-clock-A', 'subject_id': 'synthetic-person-A',
            'observed_at': '2026-11-14T00:00:00+00:00', 'unit': 'synthetic',
            'method': 'synthetic test pulses', 'time_basis': 'source-relative-seconds', 'quality': 'usable'}


def synthetic_model():
    model = {'schema_version': 1, 'id': 'ecg-reliability-v1', 'kind': 'logistic-error-rejection',
             'features': list(FEATURES), 'mean': [0.]*len(FEATURES), 'scale': [1.]*len(FEATURES),
             'coefficients': [0.]*len(FEATURES), 'intercept': -5., 'threshold': .1,
             'deployment': 'benchmark_gate_passed', 'provenance': {'synthetic': 'a'*64}}
    model['sha256'] = model_digest(model)
    return model


class ECGReliability(unittest.TestCase):
    def test_exact_half_open_count_context_and_one_to_one_matching(self):
        signal = pulse_signal(seconds=34)
        estimate = estimate_window(signal['samples'], 360, 2*360, 32*360)
        self.assertEqual(estimate['beat_count'], 30)
        self.assertEqual(estimate['estimate_bpm'], 60.)
        self.assertTrue(all(720 <= i < 11520 for i in estimate['detected_beats_sample_indices']))
        self.assertEqual(match_beats([100, 102, 200], [101, 300], 2), {'tp': 1, 'fp': 2, 'fn': 1})

    def test_model_missing_preserves_diagnostic_evidence_and_old_measurements(self):
        original = pulse_signal(seconds=32)
        before = copy.deepcopy(original)
        result = analyze_local_signal(original, model_path='absent-old.json', reliability_model_path='absent-new.json')
        self.assertEqual(original, before)
        self.assertIn('measurements', result)
        self.assertEqual(result['model']['status'], 'unavailable')
        window = result['reliability']['windows'][0]
        self.assertIsNotNone(window['estimate_bpm'])
        self.assertIsNone(window['error_score'])
        self.assertIsNone(window['learned_accept'])
        self.assertEqual(window['availability_seconds'], 32.)
        self.assertEqual(result['reliability']['source_id'], original['source_id'])

    def test_source_quality_external_domain_and_future_context_withhold_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'synthetic-model.json'
            path.write_text(json.dumps(synthetic_model()), encoding='utf-8')
            source = pulse_signal()
            external = analyze_reliability(source, path)
            self.assertFalse(external['windows'][0]['learned_accept'])
            source['reliability_domain'] = 'mitdb-1.0.0'
            accepted = analyze_reliability(source, path)
            self.assertTrue(accepted['windows'][0]['learned_accept'])
            source['quality'] = 'review'
            rejected = analyze_reliability(source, path)
            self.assertFalse(rejected['windows'][0]['learned_accept'])
            self.assertIsNotNone(rejected['windows'][0]['estimate_bpm'])
            source['quality'] = 'usable'
            source['samples'] = source['samples'][:60*360]
            truncated = analyze_reliability(source, path)
            self.assertFalse(truncated['windows'][1]['learned_accept'])

    def test_report_only_stress_domain_never_receives_learned_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'synthetic-model.json'
            path.write_text(json.dumps(synthetic_model()), encoding='utf-8')
            source = pulse_signal(seconds=32)
            source['reliability_domain'] = 'mitdb-1.0.0'
            primary = analyze_reliability(source, path)['windows'][0]
            self.assertTrue(primary['learned_accept'])
            source['reliability_domain'] = 'nstdb-1.0.0'
            stress = analyze_reliability(source, path)['windows'][0]
            self.assertFalse(stress['learned_accept'])
            self.assertEqual(stress['error_score'], primary['error_score'])
            self.assertEqual(stress['estimate_bpm'], primary['estimate_bpm'])
            self.assertEqual(stress['status'], 'experimental_review')
            self.assertTrue(any('report-only' in reason for reason in stress['reasons']))

    def test_partial_missing_nonuniform_bvp_abstain_without_interpolation(self):
        source = pulse_signal(seconds=32)
        source['samples'][100] = None
        self.assertIsNone(analyze_reliability(source)['windows'][0]['estimate_bpm'])
        source = pulse_signal(seconds=32)
        source['sample_times_seconds'] = [i/360+(1 if i > 500 else 0) for i in range(len(source['samples']))]
        self.assertEqual(analyze_reliability(source)['windows'][0]['status'], 'unavailable')
        source = pulse_signal(seconds=8)
        self.assertEqual(analyze_reliability(source)['windows'][0]['status'], 'unavailable')
        source['channel'] = 'BVP'
        self.assertEqual(analyze_reliability(source)['windows'], [])

    def test_irregular_peaks_are_evidence_not_automatic_noise(self):
        source = pulse_signal(seconds=34)
        estimate = estimate_window(source['samples'], 360, 720, 11520)
        self.assertEqual(set(estimate['features']), set(FEATURES))
        self.assertTrue(all(math.isfinite(v) for v in estimate['features'].values()))
        constant = estimate_window([1.]*10800, 360)
        self.assertFalse(constant['rules_accept'])
        self.assertIn('physiology unknown', ' '.join(constant['reasons']))

    def test_strict_model_json_no_pickle_duplicates_nonfinite_and_secret_read(self):
        model = synthetic_model()
        self.assertAlmostEqual(predict_error(model, dict.fromkeys(FEATURES, 0.)), 1/(1+math.exp(5)))
        for patch in ({'sha256': '0'*64}, {'scale': [0.]*len(FEATURES)}, {'intercept': float('nan')},
                      {'features': list(reversed(FEATURES))}, {'deployment': 'clinically_validated'}):
            item = {**model, **patch}
            with self.assertRaises(ValueError):
                validate_model(item)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'model.json'
            path.write_text('{"x":1,"x":2}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_model(path)
            with self.assertRaises(ValueError):
                load_model(Path(root)/'.ENV.private')


if __name__ == '__main__':
    unittest.main()
