import copy
import math
import tempfile
import unittest
from pathlib import Path

from biotwin.cohort import find_similar
from biotwin.data import load_bedrest, load_winscat
from biotwin.statistics import detect_change, reference_summary


def obs(identity, value=10, date="2026-01-10T09:00:00+00:00", subject="q", **changes):
    row = {"id": identity, "subject_id": subject, "source_id": "synthetic", "metric": "rt", "value": value,
           "unit": "ms", "observed_at": date, "phase": "pre", "method": "fixed", "quality": "usable", "provenance": "synthetic"}
    return {**row, **changes}


class References(unittest.TestCase):
    def setUp(self):
        self.history = [obs(str(i), value, f"2026-01-0{i+1}T09:00:00+00:00") for i, value in enumerate((8, 9, 10, 11, 12))]
        self.current = obs("current", 20)

    def test_past_only_sample_statistics(self):
        future = obs("future", 1000, "2026-02-01T09:00:00+00:00")
        result = reference_summary(self.history + [self.current, future], self.current)
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["mean"], 10)
        self.assertAlmostEqual(result["sd"], math.sqrt(2.5))
        self.assertNotIn("current", result["reference_ids"])
        self.assertNotIn("future", result["reference_ids"])
        self.assertEqual(result["exclusions"]["not strictly earlier"], 1)

    def test_context_and_subject_filters(self):
        for change in ({"phase": "post"}, {"method": "other"}, {"unit": "%"}, {"source_id": "other"}, {"subject_id": "other"}):
            rows = copy.deepcopy(self.history)
            rows[0].update(change)
            self.assertEqual(reference_summary(rows, self.current)["status"], "unavailable")

    def test_missing_nonfinite_bool_and_zero_scale(self):
        for value in (None, float("nan"), float("inf"), True):
            self.assertEqual(reference_summary(self.history, {**self.current, "value": value})["reason"], "current measurement unusable")
        result = reference_summary([{**row, "value": 1} for row in self.history], self.current)
        self.assertEqual(result["reason"], "zero reference scale")
        self.assertIsNone(result["zscore"])

    def test_mixed_basis_and_equal_time_conflict(self):
        rows = copy.deepcopy(self.history)
        rows[0]["observed_at"] = "2026-01-01"
        self.assertEqual(reference_summary(rows, self.current)["reason"], "mixed temporal bases")
        conflict = obs("conflict", 21)
        self.assertEqual(reference_summary(self.history + [conflict], self.current)["reason"], "conflicting current-time evidence")
        duplicate = {**self.history[0], "id": "duplicate", "observed_at": "2026-01-01T10:00:00+01:00"}
        self.assertEqual(reference_summary(self.history + [duplicate], self.current)["reason"], "ambiguous equal-time references")

    def test_persistence_and_bad_measurement_reset(self):
        first = obs("first", 20, "2026-01-08T09:00:00+00:00")
        self.current["value"] = 30
        result = detect_change(self.history + [first], self.current, threshold=2, persistence=2)
        self.assertTrue(result["flagged"])
        bad = obs("bad", None, "2026-01-09T09:00:00+00:00", quality="missing")
        result = detect_change(self.history + [first, bad], self.current, threshold=2, persistence=2)
        self.assertFalse(result["flagged"])
        self.assertEqual(result["evidence"][0]["id"], "bad")

    def test_offset_ordering(self):
        # +02 record sorts lexically later but is chronologically earlier.
        first = obs("early", None, "2026-01-10T09:00:00+02:00", quality="missing")
        second = obs("late", 20, "2026-01-10T08:00:00+00:00")
        result = detect_change(self.history + [first, second], self.current, persistence=2)
        self.assertEqual(result["evidence"][0]["id"], "late")

    def test_parameter_validation(self):
        for count in (1, True, 2.5):
            with self.assertRaises(ValueError):
                reference_summary([], self.current, count)
        for kwargs in ({"threshold": float("nan")}, {"threshold": 0}, {"persistence": 0}, {"persistence": True}):
            with self.assertRaises(ValueError):
                detect_change(self.history, self.current, **kwargs)


class Cohorts(unittest.TestCase):
    def assert_ambiguous_trajectory(self, rows, subject, **kwargs):
        result = find_similar(rows, "q", "rt", "2026-01-10T09:00:00+00:00", **kwargs)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["matches"], [])
        if subject == "q":
            self.assertEqual(result["reason"], "ambiguous equal-time trajectory")
            self.assertEqual(result["query"], [])
        else:
            self.assertEqual(result["candidate_count"], 0)
            self.assertEqual(result["exclusions"]["ambiguous equal-time trajectory"], 1)

    def test_window_one_rejects_conflicting_equal_instants(self):
        for subject in ("q", "a"):
            rows = [obs(f"{subject}-a", 1, subject=subject),
                    obs(f"{subject}-b", 99, date="2026-01-10T10:00:00+01:00", subject=subject),
                    obs("other", subject="a" if subject == "q" else "q")]
            for order in (rows, list(reversed(rows))):
                with self.subTest(subject=subject, first=order[0]["id"]):
                    self.assert_ambiguous_trajectory(order, subject, window=1)

    def test_default_window_rejects_duplicate_across_left_boundary(self):
        for subject in ("q", "a"):
            rows = [obs(f"{subject}-a", 1, date="2026-01-06T09:00:00+00:00", subject=subject),
                    obs(f"{subject}-b", 99, date="2026-01-06T10:00:00+01:00", subject=subject)]
            rows += [obs(f"{subject}-{day}", day, date=f"2026-01-{day:02}T09:00:00+00:00", subject=subject)
                     for day in range(7, 11)]
            rows.append(obs("other", subject="a" if subject == "q" else "q"))
            with self.subTest(subject=subject):
                self.assert_ambiguous_trajectory(rows, subject)

    def test_old_duplicate_instants_do_not_disable_current_window(self):
        for subject in ("q", "a"):
            rows = [obs(f"{subject}-old-a", 1, date="2026-01-01T09:00:00+00:00", subject=subject),
                    obs(f"{subject}-old-b", 99, date="2026-01-01T10:00:00+01:00", subject=subject)]
            rows += [obs(f"{subject}-{day}", day, date=f"2026-01-{day:02}T09:00:00+00:00", subject=subject)
                     for day in range(6, 11)]
            rows.append(obs("other", subject="a" if subject == "q" else "q"))
            with self.subTest(subject=subject):
                result = find_similar(rows, "q", "rt", "2026-01-10T09:00:00+00:00")
                self.assertEqual(result["status"], "available")
                trajectory = result["query"] if subject == "q" else result["matches"][0]["observations"]
                self.assertEqual(len(trajectory), 5)
                self.assertFalse(any("old" in row["id"] for row in trajectory))

    def test_distinct_subjects_raw_values_and_deterministic_ties(self):
        rows = [obs("q", 10)] + [obs(s, 11, subject=s) for s in ("d", "c", "b", "a")]
        result = find_similar(rows, "q", "rt", "2026-01-10T09:00:00+00:00")
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual([m["subject_id"] for m in result["matches"]], ["a", "b", "c"])
        self.assertEqual(result["matches"][0]["distance"], 1)
        self.assertEqual(result["matches"][0]["count"], 1)
        self.assertEqual(result["query_count"], 1)

    def test_filters_gaps_and_no_padding(self):
        rows = [obs("q"), obs("a", subject="a", method="incompatible"), obs("b", subject="b", quality="review")]
        result = find_similar(rows, "q", "rt", "2026-01-10T09:00:00+00:00")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["status"], "unavailable")
        rows = [obs("q"), obs("a1", subject="a", date="2025-01-01T09:00:00+00:00"), obs("a2", subject="a")]
        self.assertEqual(find_similar(rows, "q", "rt", "2026-01-10T09:00:00+00:00")["candidate_count"], 0)

    def test_offset_latest_and_equal_time(self):
        rows = [obs("early", date="2026-01-10T09:00:00+02:00", phase="other"), obs("late", date="2026-01-10T08:00:00+00:00"), obs("a", subject="a")]
        result = find_similar(rows, "q", "rt", "2026-01-10T10:00:00+00:00")
        self.assertEqual(result["query"][0]["id"], "late")
        rows.append(obs("duplicate", date="2026-01-10T09:00:00+01:00"))
        self.assertEqual(find_similar(rows, "q", "rt", "2026-01-10T10:00:00+00:00")["reason"], "ambiguous equal-time trajectory")

    def test_validation(self):
        for kwargs in ({"window": 6}, {"window": True}, {"top_k": 4}, {"max_gap_days": -1}):
            with self.assertRaises(ValueError):
                find_similar([], "q", "rt", "2026-01-10", **kwargs)


class SourceNormalization(unittest.TestCase):
    def test_truncated_rows_preserve_absent_cells_and_audit_reasons(self):
        header = "ID,Campaign,Subject,Test,Run,Test_Phase,BR_Day,Date,Time,CPTRT,CPTA,CPTL,MTHRT,MTHA,MTSRT,MTSA,CDDRT,CDDA\n"
        valid = "2,C3A,1,Nasa1,1,PRE_TEST,1,1/1/2026,09:00:00,1,80,1,2,3,4,5,6,7\n"
        for truncated, subject in (("1,C3A", None), ("1,C3A,1,Nasa1,1,PRE_TEST,1", "1")):
            with self.subTest(truncated=truncated), tempfile.TemporaryDirectory() as temp:
                path = Path(temp, "MR085G_Campaign_3_WinSCAT_C3A_0001.csv")
                path.write_text(header + valid + truncated + "\n", encoding="utf-8")
                rows, audit = load_winscat(temp)
                self.assertEqual((audit["rows"], len(rows)), (2, 18))
                self.assertTrue(all(row["quality"] == "usable" for row in rows[:9]))
                for row in rows[9:]:
                    self.assertEqual(row["quality"], "missing")
                    self.assertIsNone(row["value"])
                    self.assertIsNone(row["observed_at"])
                    self.assertIsNone(row["source_date"])
                    self.assertIsNone(row["source_time"])
                    self.assertEqual(row["source_subject"], subject)
                    self.assertEqual((row["source_file"], row["source_row"], row["source_id_cell"]), (path.name, 3, "1"))
                    self.assertIn("missing required source cell: Date", row["quality_reasons"])
                    self.assertIn("invalid source date/time", row["quality_reasons"])
                    self.assertIn("missing or nonfinite source cell", row["quality_reasons"])
                    if subject is None:
                        self.assertIn("missing required source cell: Subject", row["quality_reasons"])
                self.assertEqual(audit["issues"][0]["line"], 3)
                self.assertIn("missing required source cell: Date", audit["issues"][0]["reasons"])

    def test_missing_subject_or_date_requires_review_without_fabrication(self):
        header = "ID,Campaign,Subject,Test,Run,Test_Phase,BR_Day,Date,Time,CPTRT,CPTA,CPTL,MTHRT,MTHA,MTSRT,MTSA,CDDRT,CDDA\n"
        for subject, date, missing in (("", "1/1/2026", "Subject"), ("1", "", "Date")):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                path = Path(temp, "MR085G_Campaign_3_WinSCAT_C3A_0001.csv")
                path.write_text(header + f"1,C3A,{subject},Nasa1,1,PRE_TEST,1,{date},09:00:00,1,80,1,2,3,4,5,6,7\n", encoding="utf-8")
                rows, audit = load_winscat(temp)
                for row in rows:
                    self.assertEqual(row["quality"], "review")
                    self.assertIsNotNone(row["value"])
                    self.assertEqual(row["source_subject"], subject)
                    self.assertEqual(row["source_date"], date)
                    self.assertIn(f"missing required source cell: {missing}", row["quality_reasons"])
                    if missing == "Date":
                        self.assertIsNone(row["observed_at"])
                        self.assertEqual(row["time_precision"], "unknown")
                self.assertTrue(audit["issues"])

    def test_real_source_counts_and_padding(self):
        root = Path(__file__).resolve().parents[1] / "data/raw/nlsp"
        if not root.is_dir():
            self.skipTest("public NASA data not downloaded")
        observations, audit = load_winscat(root / "winscat_campaign3")
        self.assertEqual((audit["files"], audit["rows"], audit["participants"]), (26, 272, 26))
        self.assertEqual(len(observations), 272 * 9)
        row = next(o for o in observations if o["source_subject"] == "992")
        self.assertEqual(row["subject_id"], "C3A:0992")
        self.assertEqual(row["timezone_status"], "source-local-unspecified")
        self.assertNotIn("+00", row["observed_at"])
        self.assertEqual(row["unit"], "source-unit-unverified:" + row["metric"])
        bed, bed_audit = load_bedrest(root / "bedrest0543_campaign3")
        self.assertEqual((bed_audit["files"], bed_audit["rows"]), (15, 2425))
        self.assertTrue(all(o["quality"] != "usable" and not o["baseline_eligible"] for o in bed))
        self.assertFalse(any(o["metric"] in {"Sex", "Day Group", "Day", "PE/SNP", "end time", ""} for o in bed))

    def test_missing_cell_and_bad_identity_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "MR085G_Campaign_3_WinSCAT_C3A_0001.csv")
            path.write_text("ID,Campaign,Subject,Test,Run,Test_Phase,BR_Day,Date,Time,CPTRT,CPTA,CPTL,MTHRT,MTHA,MTSRT,MTSA,CDDRT,CDDA\n1,C3B,2,Nasa1,1,PRE_TEST,1,1/1/2026,09:00:00,,80,1,2,3,4,5,6,7\n", encoding="utf-8")
            rows, audit = load_winscat(temp)
            self.assertIsNone(rows[0]["value"])
            self.assertEqual(rows[0]["quality"], "missing")
            self.assertEqual(rows[1]["quality"], "review")
            self.assertTrue(audit["issues"])
