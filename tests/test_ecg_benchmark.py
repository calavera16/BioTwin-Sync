import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import acquire_ecg_reliability as acquisition
from scripts import benchmark_ecg_reliability as benchmark
from scripts.export_nasa_reliability import score_trace, source_timing


def row(error=0., score=.1, person='person-A', split='test'):
    return {'absolute_error_bpm': error, 'bad_error': error > 5, 'error_score': score,
            'rules_accept': True, 'learned_accept': None, 'features': {
                'flat_fraction': 0., 'edge_fraction': 0., 'detector_agreement': .9},
            'beat_count': 30, 'beat_matching': {'tp': 30, 'fp': 0, 'fn': 0},
            'subject_id': person, 'record': person, 'split': split, 'strata': [],
            'reference_supported': True}


class ECGBenchmark(unittest.TestCase):
    def test_nasa_export_preserves_source_clock_and_never_accepts_as_clinical(self):
        times = [1000+i/256 for i in range(32*256)]
        rows = score_trace(times, [1.]*len(times), 'nasa:test-clock', 'Crew_test')
        self.assertEqual(rows[0]['start_seconds'], 1000)
        self.assertEqual(rows[0]['availability_seconds'], 1032)
        self.assertFalse(rows[0]['learned_accept'])
        self.assertIsNone(rows[0]['reference_bpm'])
        self.assertNotIn('observed_at', rows[0])

    def test_jittered_nasa_crop_caps_declared_horizon_without_relaxing_abstention(self):
        times = [i/256+(.0002 if i % 2 else -.0002) for i in range(600*256)]
        times[0] = 0.
        times[-1] = 599.999
        timing = source_timing(times)
        self.assertEqual(timing['source_end_seconds'], 600.)
        self.assertEqual(timing['selected_crop_end_seconds'], 600.)
        self.assertEqual(timing['source_last_timestamp_seconds'], 599.999)
        self.assertGreater(timing['source_nominal_endpoint_seconds'], 600.)
        with patch('scripts.export_nasa_reliability.estimate_window', side_effect=AssertionError('ineligible samples must stay unavailable')):
            rows = score_trace(times, [1.]*len(times), 'nasa:test-clock', 'Crew_test')
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[-1]['availability_seconds'], 600.)
        self.assertEqual(rows[-1]['end_seconds'], 600.)
        self.assertEqual(rows[-1]['provenance']['source_last_timestamp_seconds'], 599.999)
        self.assertEqual(rows[-1]['provenance']['selected_crop_end_seconds'], 600.)
        for window in rows:
            self.assertLessEqual(window['availability_seconds'], 600.)
            self.assertEqual(window['status'], 'unavailable')
            self.assertIsNone(window['estimate_bpm'])
            self.assertIsNone(window['error_score'])
            self.assertFalse(window['rules_accept'])
            self.assertFalse(window['learned_accept'])
            expected = ('partial 30-second source window; no interpolation'
                        if window['sample_end_exclusive']-window['sample_start'] != 7680
                        else 'source timing inconsistent with declared nominal rate')
            self.assertEqual(window['reasons'], [expected])

    def test_full_person_grouping_and_stress_variant_exclusion(self):
        splits = benchmark.fixed_splits()
        nonstress = [record for split in ('train', 'calibration', 'test') for record in splits[split]]
        self.assertEqual(len(nonstress), 46)
        self.assertEqual(len(set(nonstress)), 46)
        for split, expected in (('train', 27), ('calibration', 9), ('test', 9)):
            self.assertEqual(len({benchmark.subject_id(r) for r in splits[split]}), expected)
        self.assertTrue(any('201' in ids and '202' in ids for ids in splits.values()))
        self.assertEqual(set(splits['stress']), {'118', '119', *acquisition.NSTDB})
        self.assertFalse({'118', '119'} & set(nonstress))

    def test_reference_half_open_paced_irregular_unreadable_and_open_vf(self):
        rows = benchmark.reference_windows([0, 5, 10, 20, 25, 30, 35, 40, 50],
            ['+', '/', 'f', '+', 'V', '~', 'N', '[', '!'], 60, 30,
            [0, 0, 0, 0, 0, 16, 0, 0, 0], ['(P', '', '', '(AFIB', '', '', '', '', ''])
        self.assertEqual(rows[0]['beats'], [5, 10, 25])
        self.assertTrue(rows[0]['reference_supported'])
        self.assertIn('paced_present', rows[0]['strata'])
        self.assertIn('non_sinus_rhythm_annotation_present', rows[0]['strata'])
        self.assertFalse(rows[1]['reference_supported'])
        self.assertIn('unreadable', rows[1]['source_quality_annotations'])
        self.assertTrue(any('open ventricular' in reason for reason in rows[1]['reasons']))
        boundary = benchmark.reference_windows([29, 30], ['N', 'N'], 60, 30)
        self.assertEqual([r['beats'] for r in boundary], [[29], [30]])
        unknown = benchmark.reference_windows([4], ['?'], 30, 30)
        self.assertFalse(unknown[0]['reference_supported'])

    def test_calibration_only_selection_and_error_coverage_denominators(self):
        calibration = [row(0, .1) for _ in range(8)]+[row(10, .8) for _ in range(2)]
        self.assertEqual(benchmark.select_threshold(calibration), .1)
        report = benchmark.summarize(calibration, lambda r: r['error_score'] <= .1)
        self.assertEqual(report['coverage'], .8)
        self.assertEqual(report['bad_fraction_among_retained'], 0.)
        self.assertEqual(report['false_acceptance_among_all_bad'], 0.)
        self.assertIsNone(benchmark.select_threshold([row(10, .1)]*10))
        self.assertIsNone(benchmark.summarize([row()], lambda r: False)['mae_bpm'])

    def test_allowlist_and_incomplete_acquisition_never_publishes_final(self):
        with self.assertRaises(ValueError):
            acquisition.source_url('mitdb', '../.env', 'dat')
        response = SimpleNamespace(headers={'Content-Length': '10'}, geturl=lambda: acquisition.source_url('mitdb', '100', 'hea'), read=lambda n: b'abc')
        from unittest.mock import MagicMock
        manager = MagicMock()
        manager.__enter__.return_value = response
        opener = SimpleNamespace(open=lambda *args, **kwargs: manager)
        with tempfile.TemporaryDirectory() as root, patch.object(acquisition, 'allowed_files', return_value=[('mitdb', '100', 'hea')]), patch.object(acquisition.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                acquisition.acquire(root)
            self.assertFalse((Path(root)/'mitdb/100.hea').exists())

    def test_plan_only_reads_headers_and_freeze_detects_source_change(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            files = []
            for dataset, record, suffix in acquisition.allowed_files():
                path = root/dataset/f'{record}.{suffix}'
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'fixture metadata only')
                files.append({'path': f'{dataset}/{record}.{suffix}', 'resolved_url': acquisition.source_url(dataset, record, suffix)})
            (root/'source-manifest.json').write_text(json.dumps({'files': files}), encoding='utf-8')
            def header(base):
                record = Path(base).name
                return SimpleNamespace(fs=360, sig_len=650000, sig_name=['V5', 'MLII'],
                                       file_name=[record+'.dat']*2, units=['mV']*2)
            with patch('wfdb.rdheader', side_effect=header), patch('wfdb.rdrecord', side_effect=AssertionError('waveforms forbidden before freeze')), patch('wfdb.rdann', side_effect=AssertionError('annotations forbidden before freeze')):
                plan = benchmark.make_plan(root, root/'plan.json')
                self.assertTrue(all(r['lead_index'] == 1 for r in plan['records']))
                benchmark.verify_plan(plan, plan['sha256'])
                (root/'mitdb/100.dat').write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    benchmark.verify_plan(plan, plan['sha256'])
            with self.assertRaises(ValueError):
                benchmark.verify_plan(plan, '0'*64)


if __name__ == '__main__':
    unittest.main()
