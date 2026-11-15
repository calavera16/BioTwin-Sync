"""Synthetic-only prespecified causal and transport correctness checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from biotwin import evidence_priority as ep


def window(i, value=70, accepted=True):
    return {"sample_start": i * 100, "sample_end_exclusive": (i + 1) * 100,
            "start_seconds": i * 10, "end_seconds": (i + 1) * 10,
            "availability_seconds": (i + 1) * 10, "estimate_bpm": value,
            "status": "synthetic", "reasons": [] if accepted else ["synthetic reject"],
            "rules_accept": accepted, "error_score": 0.1, "learned_accept": None}


def series(values=(70, 71, 90, None, 72)):
    return {"source": "synthetic-source", "subject": "person-A",
            "time_basis": "source-relative-seconds",
            "windows": [window(i, x, x is not None) for i, x in enumerate(values)]}


class EvidencePriorityTests(unittest.TestCase):
    def test_schema_finite_bounds_and_no_unknown_fields(self):
        value = {"schema_version": 1, "series": [series()]}
        self.assertEqual(ep.validate_input(value), value)
        for field, bad in [("estimate_bpm", float("nan")), ("rules_accept", 1),
                           ("availability_seconds", -1), ("sample_start", True)]:
            changed = copy.deepcopy(value)
            changed["series"][0]["windows"][0][field] = bad
            with self.assertRaises(ValueError):
                ep.validate_input(changed)
        changed = copy.deepcopy(value)
        changed["series"][0]["windows"][0]["invented"] = True
        with self.assertRaises(ValueError):
            ep.validate_input(changed)

    def test_causal_reference_and_future_independence(self):
        early = series((None, 70, 71))
        later = series((None, 70, 71, 200))
        a, b = ep.make_snapshots(early), ep.make_snapshots(later)
        self.assertEqual(a, b[:3])
        self.assertIsNone(a[0]["reference_bpm"])
        self.assertIsNone(a[1]["reference_bpm"])
        self.assertEqual(a[2]["reference_bpm"], 70)
        self.assertEqual(ep.recommend(a[2], a[1], 30),
                         ep.recommend(b[2], b[1], 30))
        self.assertEqual(ep.condition(a[0], 10), "UNKNOWN")

    def test_unknown_quality_and_staleness_never_become_within_band(self):
        snapshots = ep.make_snapshots(series((70, None, 70)))
        snapshots[2]["observation"]["rules_accept"] = False
        self.assertEqual(ep.condition(None, 10), "UNKNOWN")
        self.assertEqual(ep.condition(snapshots[1], 20), "UNKNOWN")
        self.assertEqual(ep.condition(snapshots[2], 30), "UNKNOWN")
        self.assertEqual(ep.condition(snapshots[0], 191), "UNKNOWN")
        self.assertEqual(ep.condition(snapshots[0], 10), "UNKNOWN")
        fresh = ep.make_snapshots(series((70, 70)))[1]
        self.assertEqual(ep.condition(fresh, 20), "within-reference-band")
        fresh["observation"]["status"] = "unavailable"
        self.assertEqual(ep.condition(fresh, 20), "UNKNOWN")

    def test_local_expiry_does_not_count_as_new_evidence_detection(self):
        item = series((70, 70, 90))
        item["windows"][2].update({"sample_start": 2400, "sample_end_exclusive": 2500,
                                    "start_seconds": 240, "end_seconds": 250, "availability_seconds": 250})
        result = ep.simulate(item, "full_fifo", 1024, 0)
        expiry = next(c for c in result["condition_changes"] if c["trigger"] == "local-expiry")
        self.assertEqual(expiry["time_seconds"], 201)
        self.assertIsNone(expiry["delay_seconds"])
        self.assertIsNone(expiry["detected_at_seconds"])
        self.assertGreater(result["metrics"]["source_stale_fraction"], 0)
        self.assertLess(result["metrics"]["both_nonstale_finite_pair_count"], result["metrics"]["finite_pair_count"])

    def test_quality_transition_and_dependency_are_explicit(self):
        a, b = ep.make_snapshots(series((70, None)))
        result = ep.recommend(b, a, 20)
        self.assertEqual(result["action"], "send")
        self.assertIn("quality-change", result["reasons"])
        self.assertEqual(ep.recommend(b, a, 20, in_flight=True)["action"], "defer")
        self.assertEqual(ep.recommend(a, a, 10)["action"], "hold")

    def test_delta_wrong_base_drop_resync_and_duplicate(self):
        a, b, c = ep.make_snapshots(series((70, 75, 80)))
        full = ep.encode_packet(None, a, "p1", "full")
        receiver, outcome = ep.receive_packet(None, full, set())
        self.assertEqual(outcome, "applied")
        delta = ep.encode_packet(b, c, "p3", "delta")  # p2 was dropped
        state, outcome = ep.receive_packet(receiver, delta, {"p1"})
        self.assertEqual(outcome, "resync_required")
        self.assertEqual(state, a)
        recovery = ep.encode_packet(None, c, "resync", "full")
        state, outcome = ep.receive_packet(state, recovery, {"p1"})
        self.assertEqual(outcome, "applied")
        self.assertEqual(state, c)
        state, outcome = ep.receive_packet(state, recovery, {"resync"})
        self.assertEqual(outcome, "duplicate")
        self.assertEqual(state, c)

    def test_stale_and_conflicting_packets_cannot_roll_back(self):
        a, b = ep.make_snapshots(series((70, 80)))
        state, outcome = ep.receive_packet(b, ep.encode_packet(None, a, "old", "full"), set())
        self.assertEqual(outcome, "stale")
        self.assertEqual(state, b)
        conflict = copy.deepcopy(b)
        conflict["observation"]["estimate_bpm"] = 79
        with self.assertRaises(ValueError):
            ep.receive_packet(b, ep.encode_packet(None, conflict, "conflict", "full"), set())

    def test_budget_uses_actual_envelope_bytes_and_missing_penalty(self):
        item = series((70, 80, 90))
        result = ep.simulate(item, "full_fifo", 1, 0)
        self.assertEqual(result["metrics"]["started_bytes"], 0)
        self.assertIsNone(result["metrics"]["finite_pair_mae_bpm"])
        self.assertEqual(result["metrics"]["receiver_unknown_fraction"], 1)
        self.assertEqual(result["metrics"]["source_known_disagreement_fraction"], 1)
        fast = ep.simulate(item, "delta_every_update", 1024, 0)
        actual = sum(len(ep.canonical_bytes(p["packet"])) for p in fast["packets"])
        self.assertEqual(actual, fast["metrics"]["started_bytes"])
        self.assertLessEqual(actual, fast["metrics"]["byte_allowance"])

    def test_context_availability_and_fixed_cadence(self):
        item = series((70, 80))
        item["windows"][0]["availability_seconds"] = 15
        result = ep.simulate(item, "full_fifo", 1024, 0)
        self.assertEqual(len(result["ticks"]), 21)
        self.assertTrue(all(t["source_sequence"] is None for t in result["ticks"] if t["time_seconds"] < 15))
        self.assertGreaterEqual(result["packets"][0]["started_at_seconds"], 15)

    def test_determinism_and_causal_prefix(self):
        item = series()
        first = ep.simulate(item, "priority", 64, 30)
        self.assertEqual(first, ep.simulate(item, "priority", 64, 30))
        short = ep.simulate(series((70, 71, 90)), "priority", 1024, 0)
        long = ep.simulate(item, "priority", 1024, 0)
        self.assertEqual(short["ticks"], long["ticks"][:len(short["ticks"])])

    def test_high_delay_reports_no_contemporaneous_delivery_and_drain(self):
        result = ep.simulate(series(), "priority", 1024, 1200)
        self.assertEqual(result["metrics"]["delivered_within_horizon"], 0)
        self.assertEqual(result["metrics"]["receiver_unknown_fraction"], 1)
        self.assertEqual(result["drain"]["delivered_packets"], 1)
        self.assertEqual(result["metrics"]["finite_pair_count"], 0)

    def test_input_paths_json_duplicates_and_output_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "input.json"
            source.write_text('{"schema_version":1,"schema_version":1,"series":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                ep.load_input(source)
            with self.assertRaises(ValueError):
                ep.load_input(root / ".env.json")
            source.write_text(json.dumps({"schema_version": 1, "series": [series()]}), encoding="utf-8")
            value = ep.load_input(source)
            result = ep.benchmark(value, rates=(1024,), delays=(0,), selection="synthetic test")
            out = root / "report"
            ep.write_report(result, value, out)
            self.assertTrue((out / "comparison.svg").is_file())
            self.assertTrue((out / "report.html").is_file())
            with self.assertRaises(ValueError):
                ep.write_report(result, value, out)


if __name__ == "__main__":
    unittest.main()
