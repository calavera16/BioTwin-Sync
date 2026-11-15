import math
import unittest

from biotwin.signal_measurements import analyze_signal, validate_signal


def signal(channel='ECG', seconds=40, rate=256):
    if channel == 'ECG':
        values = [math.exp(-(((i/rate-.25) % 1)-.5)**2/.0008) for i in range(seconds*rate)]
    else:
        values = [math.sin(2*math.pi*i/rate) for i in range(seconds*rate)]
    return {'channel': channel, 'samples': values, 'sample_rate_hz': rate,
            'source_id': 'synthetic-test', 'subject_id': 'test-only', 'unit': 'arbitrary',
            'method': 'deterministic synthetic fixture', 'observed_at': '2026-11-14T10:00:00+09:00',
            'quality': 'usable', 'time_basis': 'synthetic regular sample clock'}


class Measurements(unittest.TestCase):
    def test_regular_synthetic_ecg_bvp_rates_are_distinct_engineering_outputs(self):
        for channel, rate in [('ECG', 256), ('BVP', 64)]:
            result = analyze_signal(signal(channel, rate=rate))
            self.assertEqual(result['status'], 'experimental_estimate')
            self.assertAlmostEqual(result['rate_per_minute'], 60, delta=.5)
            self.assertIn(channel, result['rate_kind'])
            self.assertIn('Not astronaut/clinical validation', result['warning'])
            self.assertIsNotNone(result['interval_rmssd_ms'])

    def test_quality_missing_review_and_null_samples_abstain(self):
        for quality in ('review', 'missing', 'unusable'):
            item = signal()
            item['quality'] = quality
            self.assertIsNone(analyze_signal(item)['rate_per_minute'])
        item = signal()
        item['samples'][100] = None
        self.assertIsNone(analyze_signal(item)['rate_per_minute'])

    def test_irregular_clock_flat_and_short_abstain(self):
        item = signal(seconds=10)
        item['sample_times_seconds'] = [i/256 + (1 if i > 100 else 0) for i in range(2560)]
        self.assertEqual(analyze_signal(item)['status'], 'unavailable')
        item = signal()
        item['samples'] = [3.]*len(item['samples'])
        self.assertEqual(analyze_signal(item)['status'], 'unavailable')
        self.assertEqual(analyze_signal(signal(seconds=4))['status'], 'unavailable')

    def test_explicit_clock_without_nominal_rate_preserved_but_unavailable(self):
        item = signal(seconds=10)
        item['sample_times_seconds'] = [5+i/256 for i in range(2560)]
        item['sample_rate_hz'] = None
        result = analyze_signal(item)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['time_basis'], item['time_basis'])

    def test_bounded_schema_rejects_nonfinite_bad_clock_naive_time(self):
        for patch in ({'samples': [0, float('inf')]}, {'samples': [0]*30001},
                      {'observed_at': '2026-11-14T00:00:00'},
                      {'sample_times_seconds': [0]*10240}, {'sample_rate_hz': 0}):
            item = signal()
            item.update(patch)
            with self.assertRaises(ValueError):
                validate_signal(item)


if __name__ == '__main__':
    unittest.main()
