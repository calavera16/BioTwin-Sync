import math
import unittest

from biotwin.cusum import cusum_series


def row(identity, value, when, **changes):
    result = {"id": identity, "subject_id": "crew1", "source_id": "synthetic",
              "metric": "HR", "value": value, "unit": "bpm", "observed_at": when,
              "phase": "rest", "method": "demo", "quality": "usable",
              "provenance": "synthetic"}
    result.update(changes)
    return result


def baseline():
    # Sample mean=0, sample SD=1 exactly.
    return [row(f"r{i}", value, f"2026-01-01T00:0{i}:00Z")
            for i, value in enumerate((-1, -1, 0, 1, 1))]


def monitor(values):
    return [row(f"m{i}", value, f"2026-01-01T01:{i:02d}:00Z")
            for i, value in enumerate(values)]


class CusumTests(unittest.TestCase):
    def test_hand_calculated_two_sided_strict_threshold(self):
        result = cusum_series(baseline(), monitor([2, 2, -2, -2, -2]), "2026-01-01T02:00:00Z", h=3)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["reference"]["mean"], 0)
        self.assertEqual(result["reference"]["sd"], 1)
        trace = result["trace"]
        self.assertEqual([point["splus"] for point in trace], [1.5, 3, .5, 0, 0])
        self.assertEqual([point["sminus"] for point in trace], [0, 0, 1.5, 3, 4.5])
        self.assertEqual([point["flagged"] for point in trace], [False, False, False, False, True])
        self.assertTrue(trace[-1]["negative_flag"])
        self.assertFalse(trace[-1]["positive_flag"])

    def test_reference_is_frozen_not_refitted(self):
        result = cusum_series(baseline(), monitor([100, 100]), "2026-01-01T02:00:00Z")
        self.assertEqual([point["zscore"] for point in result["trace"]], [100, 100])
        self.assertEqual(result["reference"]["reference_ids"], [f"r{i}" for i in range(5)])

    def test_future_and_boundary_reference_excluded(self):
        refs = baseline() + [row("future", 1e6, "2026-01-02T00:00:00Z"),
                             row("equal", 1e6, "2026-01-01T01:00:00Z")]
        result = cusum_series(refs, monitor([2]), "2026-01-01T01:00:00Z")
        self.assertEqual(result["reference"]["mean"], 0)
        self.assertEqual(result["reference"]["exclusions"]["not strictly before first monitor"], 2)

    def test_monitoring_cutoff_is_inclusive(self):
        result = cusum_series(baseline(), monitor([2, 10]), "2026-01-01T01:00:00Z")
        self.assertEqual(len(result["trace"]), 1)
        self.assertEqual(result["excluded_future_count"], 1)

    def test_sort_aware_offsets_by_instant_not_text(self):
        points = [row("later", 2, "2026-01-01T03:00:00+01:00"),
                  row("earlier", 2, "2026-01-01T02:30:00+02:00")]
        result = cusum_series(baseline(), points, "2026-01-01T03:00:00Z")
        self.assertEqual([point["id"] for point in result["trace"]], ["earlier", "later"])

    def test_bad_quality_and_phase_reset_then_valid_restarts(self):
        points = monitor([2, 2, 2, 2, 2])
        points[1]["quality"] = "review"
        points[3]["phase"] = "exercise"
        result = cusum_series(baseline(), points, "2026-01-01T02:00:00Z")
        self.assertEqual([point["splus"] for point in result["trace"]], [1.5, None, 1.5, None, 1.5])
        self.assertIn("monitoring context changed", result["trace"][3]["reset_reasons"])

    def test_configured_gap_resets_before_accumulation(self):
        result = cusum_series(baseline(), monitor([2, 2]), "2026-01-01T02:00:00Z", max_gap_seconds=30)
        self.assertEqual(result["trace"][1]["splus"], 1.5)
        self.assertEqual(result["trace"][1]["reset_reasons"], ["configured gap exceeded"])

    def test_equal_time_evidence_is_unavailable_and_resets(self):
        points = monitor([2, 2, 2, 2])
        points[2]["observed_at"] = points[1]["observed_at"]
        result = cusum_series(baseline(), points, "2026-01-01T02:00:00Z")
        self.assertEqual([point["splus"] for point in result["trace"]], [1.5, None, None, 1.5])

    def test_nonfinite_missing_bool_monitoring_resets(self):
        for value in (None, float("nan"), float("inf"), True):
            with self.subTest(value=value):
                result = cusum_series(baseline(), monitor([2, value, 2]), "2026-01-01T02:00:00Z")
                self.assertEqual(result["trace"][1]["status"], "unavailable")
                self.assertEqual(result["trace"][2]["splus"], 1.5)

    def test_small_zero_nonfinite_reference_unavailable(self):
        for refs in (baseline()[:4], [dict(point, value=1) for point in baseline()],
                     [dict(point, value=math.inf) for point in baseline()]):
            with self.subTest(refs=refs):
                self.assertEqual(cusum_series(refs, monitor([2]), "2026-01-01T02:00:00Z")["status"], "unavailable")

    def test_ambiguous_reference_and_overlap_rejected(self):
        refs = baseline()
        refs[1]["observed_at"] = refs[0]["observed_at"]
        self.assertIn("equal-time", cusum_series(refs, monitor([2]), "2026-01-01T02:00:00Z")["reason"])
        refs = baseline()
        refs[0]["id"] = "m0"
        self.assertIn("overlap", cusum_series(refs, monitor([2]), "2026-01-01T02:00:00Z")["reason"])

    def test_mixed_invalid_and_day_precision_unavailable(self):
        for when, metadata in (("2026-01-01T01:00:00", {}), ("nonsense", {}),
                               ("2026-01-01", {"time_precision": "day"}),
                               ("2026-01-01T01:00:00Z", {"time_precision": "day"})):
            result = cusum_series(baseline(), [row("m", 2, when, **metadata)], "2026-01-01T02:00:00Z")
            self.assertEqual(result["status"], "unavailable")

    def test_reference_context_must_match_every_field(self):
        for field in ("subject_id", "source_id", "metric", "unit", "phase", "method"):
            refs = baseline()
            refs[0][field] = "different"
            self.assertIn("context", cusum_series(refs, monitor([2]), "2026-01-01T02:00:00Z")["reason"])

    def test_mixed_minute_second_precision_rejected(self):
        points = monitor([2, 2])
        points[1]["observed_at"] = "2026-01-01T01:01Z"
        result = cusum_series(baseline(), points, "2026-01-01T02:00:00Z")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("precision", result["reason"])

    def test_source_local_and_day_supported_without_fabricated_zone(self):
        refs = [dict(point, observed_at=point["observed_at"][:-1]) for point in baseline()]
        points = [dict(point, observed_at=point["observed_at"][:-1]) for point in monitor([2])]
        self.assertEqual(cusum_series(refs, points, "2026-01-01T02:00:00")["status"], "available")
        refs = [dict(point, observed_at=f"2026-01-0{i+1}", time_precision="day") for i, point in enumerate(baseline())]
        points = [row("day", 2, "2026-01-06", time_precision="day")]
        self.assertEqual(cusum_series(refs, points, "2026-01-06")["reference"]["temporal_basis"], "day")

    def test_empty_and_duplicate_monitors(self):
        self.assertEqual(cusum_series(baseline(), [], "2026-01-01T02:00:00Z")["status"], "unavailable")
        point = monitor([2])[0]
        self.assertIn("duplicate", cusum_series(baseline(), [point, point], "2026-01-01T02:00:00Z")["reason"])

    def test_parameters(self):
        for kwargs in ({"k": -1}, {"k": True}, {"h": 0}, {"h": math.inf},
                       {"min_count": 4}, {"min_count": True}, {"max_gap_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                cusum_series(baseline(), monitor([2]), "2026-01-01T02:00:00Z", **kwargs)


if __name__ == "__main__":
    unittest.main()
