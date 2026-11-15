import json
from pathlib import Path
import tempfile
import unittest
from biotwin.diagnostics import dataset_report, select_cohort_example, synthetic_case_matrix, self_check
from biotwin.data import load_winscat
from biotwin.__main__ import headless_demo, signal_command
from types import SimpleNamespace
from unittest.mock import patch


class Diagnostics(unittest.TestCase):
    def test_predeclared_real_coverage_selects_multi_point_matches(self):
        observations, _ = load_winscat('data/raw/nlsp/winscat_campaign3')
        result = select_cohort_example(observations)
        self.assertEqual(result['selection']['cutoffs_evaluated'], 272)
        self.assertEqual(result['selection']['cutoffs_with_multi_point_match'], 126)
        self.assertEqual(result['query_count'], 4)
        self.assertEqual([m['count'] for m in result['matches']], [4, 4, 4])
        self.assertEqual(len({m['subject_id'] for m in result['matches']}), 3)
        self.assertNotIn(result['anchor']['subject_id'], {m['subject_id'] for m in result['matches']})
        self.assertEqual(select_cohort_example(list(reversed(observations))), result)
        self.assertIn('source-unit-unverified', result['anchor']['unit'])
        self.assertNotIn('+', result['anchor']['observed_at'])

    def test_real_baseline_count_and_limits_do_not_claim_persistence(self):
        report = dataset_report()
        baseline = report['baseline_coverage']
        self.assertEqual(baseline['minimum_earlier_comparable_tests'], 5)
        self.assertEqual(baseline['eligible_metric_observations'], 18)
        self.assertEqual(baseline['eligible_assessment_visits'], 2)
        self.assertEqual(baseline['persistent_flags'], 0)
        self.assertIn('unresolved', report['source_licenses']['NLSP'])
        self.assertTrue(any('persistence' in s for s in report['data_limitations']))

    def test_synthetic_comparison_is_specification_not_accuracy(self):
        matrix = synthetic_case_matrix()
        self.assertEqual(matrix['case_count'], 8)
        self.assertEqual(matrix['context_aware_specification_matches'], 8)
        self.assertEqual(matrix['static_specification_matches'], 1)
        self.assertIn('NOT clinical', matrix['label'])
        self.assertEqual({r['provenance'] for r in matrix['cases']}, {'synthetic'})

    def test_offline_self_check_and_headless_artifacts_with_missing_real_data(self):
        self.assertEqual(self_check()['status'], 'passed')
        with tempfile.TemporaryDirectory() as directory:
            result = headless_demo(directory, str(Path(directory, 'missing')))
            self.assertEqual(result['followups_received'], 1)
            for key in ('session', 'view_model', 'report_json', 'report_html'):
                self.assertTrue(Path(result[key]).is_file())
            report = json.loads(Path(result['report_json']).read_text(encoding='utf-8'))
            self.assertEqual(report['diagnostics']['source_audit']['status'], 'unavailable')
            self.assertEqual(len(report['four_stop_story']), 4)
            earlier = report['timeline'][2]
            self.assertEqual(earlier['doctor']['completions'], [])
            self.assertIsNone(earlier['crew']['evaluation'])
            self.assertIn('placeholder', report['gui_status'])

    def test_optional_dependency_failure_is_actionable(self):
        with patch('biotwin.quality.prepare_experiment', side_effect=RuntimeError('Optional NumPy unavailable')):
            result = signal_command(SimpleNamespace(command='quality-prepare', data_dir='data', output_dir='outputs/quality'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('isolated environment', result['action'])
