import copy
import hashlib
import unittest

from biotwin.protocol import canonical_bytes, evaluate_protocol, make_snapshot, verify_snapshot
from biotwin.simulation import replay, synthetic_fixture
from biotwin.transport import apply_delta, full_packet, make_delta, packet_sizes


class Snapshots(unittest.TestCase):
    def setUp(self):
        fixture = synthetic_fixture()
        self.old = fixture["events"][0]["snapshot"]
        self.new = fixture["events"][-1]["snapshot"]

    def test_deterministic_bytes_and_defensive_copy(self):
        original = copy.deepcopy(self.old["payload"]["observations"])
        snapshot = make_snapshot(self.old["payload"]["subject_id"], list(reversed(original)), self.old["payload"]["created_at"])
        self.assertEqual(snapshot, self.old)
        self.assertEqual(hashlib.sha256(canonical_bytes(snapshot["payload"])).hexdigest(), snapshot["digest"])
        original[0]["value"] = -100
        self.assertNotEqual(original[0]["value"], snapshot["payload"]["observations"][0]["value"])
        snapshot["payload"]["observations"][0]["value"] = -100
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            verify_snapshot(snapshot)

    def test_schema_subject_duplicate_and_unsafe_values(self):
        payload = self.old["payload"]
        row = payload["observations"][0]
        for rows in ([row, row], [{**row, "subject_id": "other"}], [{**row, "value": True}], [{**row, "value": float("nan")}], [{**row, "bad": object()}]):
            with self.assertRaises(ValueError):
                make_snapshot(payload["subject_id"], rows, payload["created_at"])
        for bad in ({1: "non-string key"}, (1, 2), float("inf")):
            with self.assertRaises(ValueError):
                canonical_bytes(bad)

    def test_delta_round_trip_and_fair_fallback(self):
        envelope = make_delta(self.old, self.new)
        self.assertEqual(apply_delta(self.old, envelope), self.new)
        sizes = packet_sizes(self.old, self.new)
        self.assertEqual(len(canonical_bytes(envelope)), min(sizes["full_bytes"], sizes["delta_bytes"]))
        # Changing only created_at makes the bounded delta clearly smaller.
        payload = self.old["payload"]
        target = make_snapshot(payload["subject_id"], payload["observations"], "2026-11-14T09:01:00+00:00")
        delta = make_delta(self.old, target)
        self.assertEqual(delta["kind"], "delta")
        self.assertEqual(apply_delta(self.old, delta), target)
        # Full omits a redundant embedded digest (target_digest already carries
        # it); this two-field update is measurably smaller as a full payload.
        self.assertEqual(envelope["kind"], "full")
        self.assertLess(sizes["full_bytes"], sizes["delta_bytes"])

    def test_wrong_base_target_extra_fields_and_rollback(self):
        envelope = make_delta(self.old, self.new)
        for bad in ({**envelope, "base_digest": "bad"}, {**envelope, "target_digest": "bad"}, {**envelope, "extra": 1}):
            with self.assertRaises(ValueError):
                apply_delta(self.old, bad)
        with self.assertRaises(ValueError):
            apply_delta(self.new, envelope)
        with self.assertRaises(ValueError):
            make_delta(self.old, self.old)
        with self.assertRaisesRegex(ValueError, "backward"):
            make_delta(self.new, self.old)
        with self.assertRaisesRegex(ValueError, "backward"):
            apply_delta(self.new, full_packet(self.old, self.new["digest"]))

    def test_patch_operation_subset_and_reconstructed_validation(self):
        for operation in ({"op": "remove", "path": "/observations"},
                          {"op": "replace", "path": "/observations/0", "value": {}},
                          {"op": "replace", "path": "/schema_version", "value": 9},
                          {"op": "replace", "path": "/unknown", "value": 1}):
            envelope = {"schema_version": 1, "kind": "delta", "base_digest": self.old["digest"], "target_digest": self.new["digest"], "content": [operation]}
            with self.assertRaises(ValueError):
                apply_delta(self.old, envelope)


class Protocols(unittest.TestCase):
    def setUp(self):
        fixture = synthetic_fixture()
        self.old = fixture["events"][0]["snapshot"]
        self.new = fixture["events"][-1]["snapshot"]
        self.protocol = fixture["events"][2]["packet"]["protocol"]
        self.now = "2026-11-14T09:40:00+00:00"

    def evaluate(self, protocol=None, current=None, reviewed=None, now=None):
        return evaluate_protocol(protocol if protocol is not None else self.protocol,
            reviewed if reviewed is not None else self.old, current if current is not None else self.new, now or self.now)

    def snapshot_with(self, change, remove=False):
        payload = self.new["payload"]
        observations = copy.deepcopy(payload["observations"])
        observations = [o for o in observations if o["id"] != "changed"] if remove else [{**o, **change} if o["id"] == "changed" else o for o in observations]
        return make_snapshot(payload["subject_id"], observations, payload["created_at"])

    def test_stable_changed_and_irrelevant_evidence(self):
        result = self.evaluate()
        self.assertEqual(result["outcome"], "B")
        self.assertEqual(result["original_reply"], self.protocol["original_reply"])
        self.assertEqual(self.evaluate(current=self.old)["outcome"], "A")
        payload = self.new["payload"]
        unrelated = {**payload["observations"][-1], "id": "unrelated", "metric": "temperature", "value": 999}
        snapshot = make_snapshot(payload["subject_id"], payload["observations"] + [unrelated], payload["created_at"])
        self.assertEqual(self.evaluate(current=snapshot)["outcome"], "B")

    def test_latest_bad_or_incompatible_does_not_fall_back(self):
        for change in ({"value": None, "quality": "missing"}, {"quality": "review"}, {"unit": "seconds"}, {"phase": "other"}, {"source_id": "other"}, {"method": "other"}):
            result = self.evaluate(current=self.snapshot_with(change))
            self.assertEqual(result["outcome"], "UNKNOWN")
            self.assertEqual(result["checklist"], self.protocol["checklists"]["UNKNOWN"])

    def test_missing_stale_future_and_unsupported_real_timing(self):
        empty = make_snapshot(self.new["payload"]["subject_id"], [], self.new["payload"]["created_at"])
        self.assertEqual(self.evaluate(current=empty)["outcome"], "UNKNOWN")
        protocol = {**self.protocol, "freshness_seconds": 60}
        self.assertEqual(self.evaluate(protocol=protocol)["outcome"], "UNKNOWN")
        for change in ({"observed_at": "2026-11-14T09:41:00+00:00"}, {"observed_at": "2026-11-14T09:30:00"}, {"observed_at": "2026-11-14"}):
            self.assertEqual(self.evaluate(current=self.snapshot_with(change))["outcome"], "UNKNOWN")
        self.assertEqual(self.evaluate(now="2026-11-14T11:00:00+00:00")["outcome"], "UNKNOWN")

    def test_identity_integrity_and_conflicting_equal_times(self):
        self.assertEqual(self.evaluate(protocol={**self.protocol, "subject_id": "other"})["outcome"], "UNKNOWN")
        self.assertEqual(self.evaluate(protocol={**self.protocol, "reviewed_digest": "other"})["outcome"], "UNKNOWN")
        corrupted = copy.deepcopy(self.new)
        corrupted["payload"]["observations"][0]["value"] = 999
        self.assertEqual(self.evaluate(current=corrupted)["outcome"], "UNKNOWN")
        payload = self.new["payload"]
        latest = next(o for o in payload["observations"] if o["id"] == "changed")
        duplicate = {**latest, "id": "equal-time", "observed_at": "2026-11-14T10:30:00+01:00"}
        snapshot = make_snapshot(payload["subject_id"], payload["observations"] + [duplicate], payload["created_at"])
        self.assertIn("conflicting equal-time", self.evaluate(current=snapshot)["reasons"][0])

    def test_branch_conflict_gap_and_malformed_schema(self):
        for rules in ({"A": [{"metric": "reaction_time", "op": "gt", "value": 0}], "B": [{"metric": "reaction_time", "op": "gt", "value": 0}]},
                      {"A": [{"metric": "reaction_time", "op": "lt", "value": 0}], "B": [{"metric": "reaction_time", "op": "lt", "value": 0}]},
                      {"A": [{"metric": "unknown", "op": "gt", "value": 0}], "B": []}):
            self.assertEqual(self.evaluate(protocol={**self.protocol, "conditions": rules})["outcome"], "UNKNOWN")
        for change in ({"checklists": ["A", "B", "UNKNOWN"]}, {"version": True}, {"dependencies": []}, {"freshness_seconds": True}, {"conditions": "eval('bad')"}, {"extra": 1}):
            self.assertEqual(self.evaluate(protocol={**self.protocol, **change})["outcome"], "UNKNOWN")


class Replay(unittest.TestCase):
    def setUp(self):
        self.fixture = synthetic_fixture()

    def test_cutoffs_no_future_leak_and_backward_rebuild(self):
        states = [replay(self.fixture["events"], at) for at in self.fixture["cutoffs"]]
        self.assertIsNone(states[0]["doctor"]["current_snapshot"])
        old = self.fixture["events"][0]["snapshot"]
        self.assertEqual(states[2]["doctor"]["current_snapshot"], old)
        self.assertNotEqual(states[2]["crew"]["current_snapshot"], old)
        self.assertIsNone(states[2]["crew"]["evaluation"])
        self.assertEqual(states[3]["crew"]["evaluation"]["outcome"], "B")
        self.assertEqual(replay(self.fixture["events"], self.fixture["cutoffs"][0]), states[0])

    def test_duplicate_packet_ledger(self):
        events = copy.deepcopy(self.fixture["events"])
        events.append({**events[1], "event_id": "duplicate-consultation", "arrives_at": "2026-11-14T09:21:00+00:00"})
        state = replay(events, self.fixture["cutoffs"][-1])
        self.assertEqual(len(state["doctor"]["inbox"]), 1)
        self.assertEqual(state["rejected"][0]["reason"], "duplicate packet ID")

    def test_send_before_arrival_and_possession(self):
        events = copy.deepcopy(self.fixture["events"])
        events[1]["arrives_at"] = "2026-11-14T08:59:00+00:00"
        with self.assertRaises(ValueError):
            replay(events, self.fixture["cutoffs"][-1])
        events = copy.deepcopy(self.fixture["events"])
        events[1]["packet"]["snapshot"] = events[-1]["snapshot"]
        with self.assertRaisesRegex(ValueError, "does not possess"):
            replay(events, self.fixture["cutoffs"][-1])

    def test_delayed_older_snapshot_preserves_latest_state(self):
        events = copy.deepcopy(self.fixture["events"])
        events.append({"event_id": "05-new-send", "kind": "packet", "packet_id": "new-snapshot", "sender": "crew", "recipient": "doctor", "sent_at": "2026-11-14T09:30:00+00:00", "arrives_at": "2026-11-14T09:35:00+00:00", "packet": {"type": "snapshot", "snapshot": events[-1]["snapshot"]}})
        events.append({**events[1], "event_id": "06-late-old", "packet_id": "late-old", "sent_at": "2026-11-14T09:01:00+00:00", "arrives_at": "2026-11-14T09:39:00+00:00"})
        state = replay(events, self.fixture["cutoffs"][-1])
        self.assertEqual(state["doctor"]["current_snapshot"], self.fixture["events"][-1]["snapshot"])
        self.assertEqual(state["rejected"][-1]["reason"], "out-of-order snapshot")
        self.assertEqual(len(state["doctor"]["inbox"]), 3)
        events.append({"event_id": "07-backward-local", "kind": "local", "actor": "crew", "at": "2026-11-14T09:40:00+00:00", "snapshot": events[0]["snapshot"]})
        with self.assertRaisesRegex(ValueError, "backward local"):
            replay(events, self.fixture["cutoffs"][-1])
