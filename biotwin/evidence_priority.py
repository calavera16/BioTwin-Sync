"""Causal evidence recommendations and a bounded, offline scalar link experiment.

This is an experimental codec, not the application's clinical/workflow transport.
Diagnostic estimates are not physiological truth. Hashes identify bytes, not senders.
"""
from __future__ import annotations

from collections import deque
import copy
import hashlib
import html
import json
import math
from pathlib import Path

SCHEMA_VERSION = 1
POLICIES = ("full_fifo", "periodic_full", "delta_every_update", "priority")
DEFAULT_RATES = (16, 64, 256, 1024)
DEFAULT_DELAYS = (0, 30, 120)
POLICY_PARAMETERS = {"estimate_change_bpm": 5, "reference_band_bpm": 10,
                     "stale_after_seconds": 180, "refresh_seconds": 30,
                     "period_seconds": 30, "cadence_seconds": 1,
                     "reference_minimum_prior_accepted": 1, "gate": "rules_accept"}
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_SERIES = 32
MAX_WINDOWS = 20000
MAX_DURATION_SECONDS = 7200
MAX_BENCHMARK_TICKS = 2_000_000
SERIES_FIELDS = {"source", "subject", "time_basis", "windows"}
WINDOW_FIELDS = {"sample_start", "sample_end_exclusive", "start_seconds", "end_seconds",
                 "availability_seconds", "estimate_bpm", "status", "reasons",
                 "rules_accept", "error_score", "learned_accept"}
WINDOW_OPTIONAL = {"reference_bpm", "source_id", "subject_id"}
SNAPSHOT_FIELDS = {"source", "subject", "time_basis", "sequence", "reference_bpm",
                   "reference_available_seconds", "observation"}
PACKET_FIELDS = {"schema_version", "packet_id", "kind", "base_digest",
                 "target_digest", "target_sequence", "content"}


def canonical_bytes(value):
    """Actual experiment wire encoding; strict finite compact sorted UTF-8 JSON."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("not finite JSON") from exc


def _number(value, name, *, nullable=False, minimum=None, maximum=None):
    if value is None and nullable:
        return
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} outside supported bounds")


def _text(value, name, limit=256):
    if type(value) is not str or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {name}")


def _fields(value, required, optional=frozenset()):
    if type(value) is not dict or not required <= set(value) or set(value) - required - optional:
        raise ValueError("invalid object fields")


def _window(value):
    _fields(value, WINDOW_FIELDS, WINDOW_OPTIONAL)
    for name in ("sample_start", "sample_end_exclusive"):
        if type(value[name]) is not int or not 0 <= value[name] <= 10**12:
            raise ValueError("invalid sample index")
    if value["sample_end_exclusive"] <= value["sample_start"]:
        raise ValueError("empty sample interval")
    for name in ("start_seconds", "end_seconds", "availability_seconds"):
        _number(value[name], name, minimum=0, maximum=10**9)
    if not value["start_seconds"] < value["end_seconds"] <= value["availability_seconds"]:
        raise ValueError("window context is not causally available")
    _number(value["estimate_bpm"], "estimate_bpm", nullable=True, minimum=0, maximum=10**6)
    _number(value["error_score"], "error_score", nullable=True, minimum=0, maximum=1)
    if value["status"] not in ("experimental_review", "unavailable", "synthetic"):
        raise ValueError("unsupported diagnostic status")
    if type(value["rules_accept"]) is not bool or (value["learned_accept"] is not None and type(value["learned_accept"]) is not bool):
        raise ValueError("acceptance must be explicit boolean or learned null")
    if type(value["reasons"]) is not list or len(value["reasons"]) > 32:
        raise ValueError("invalid reasons")
    for reason in value["reasons"]:
        _text(reason, "reason", 1024)
    if "reference_bpm" in value:
        _number(value["reference_bpm"], "reference_bpm", nullable=True, minimum=0, maximum=10**6)
    for name in ("source_id", "subject_id"):
        if name in value:
            _text(value[name], name)


def _series(value):
    _fields(value, SERIES_FIELDS, {"split"})
    for name in ("source", "subject"):
        _text(value[name], name)
    if value["time_basis"] != "source-relative-seconds":
        raise ValueError("only original source-relative time is supported")
    if "split" in value:
        _text(value["split"], "split", 64)
    windows = value["windows"]
    if type(windows) is not list or not 1 <= len(windows) <= MAX_WINDOWS:
        raise ValueError("invalid window count")
    previous = None
    for window in windows:
        _window(window)
        if window.get("source_id", value["source"]) != value["source"] or window.get("subject_id", value["subject"]) != value["subject"]:
            raise ValueError("window identity does not match series")
        if previous is not None and (window["availability_seconds"] <= previous["availability_seconds"]
                or window["start_seconds"] < previous["end_seconds"]
                or window["sample_start"] < previous["sample_end_exclusive"]):
            raise ValueError("windows must be ordered, nonoverlapping and causally distinct")
        previous = window
    duration = windows[-1]["availability_seconds"] - windows[0]["start_seconds"]
    if not 0 < duration <= MAX_DURATION_SECONDS:
        raise ValueError("source duration exceeds bounded experiment")


def validate_input(value):
    """Validate and copy the complete selected ledger; never filter rejected windows."""
    _fields(value, {"schema_version", "series"})
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported input version")
    items = value["series"]
    if type(items) is not list or not 1 <= len(items) <= MAX_SERIES:
        raise ValueError("invalid series count")
    identities = set()
    total = 0
    for item in items:
        _series(item)
        identity = (item["source"], item["subject"])
        if identity in identities:
            raise ValueError("duplicate source/subject series")
        identities.add(identity)
        total += len(item["windows"])
    if total > MAX_WINDOWS or len(canonical_bytes(value)) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds bounded ledger size")
    return copy.deepcopy(value)


def make_snapshots(series):
    """Only an earlier accepted observation can create a frozen link reference."""
    _series(series)
    reference, reference_time = None, None
    snapshots = []
    for index, observation in enumerate(series["windows"]):
        snapshots.append({"source": series["source"], "subject": series["subject"],
                          "time_basis": series["time_basis"], "sequence": index,
                          "reference_bpm": reference, "reference_available_seconds": reference_time,
                          "observation": copy.deepcopy(observation)})
        if reference is None and observation["status"] != "unavailable" and observation["rules_accept"] and observation["estimate_bpm"] is not None:
            reference = observation["estimate_bpm"]
            reference_time = observation["availability_seconds"]
    return snapshots


def _snapshot(value):
    _fields(value, SNAPSHOT_FIELDS)
    for name in ("source", "subject"):
        _text(value[name], name)
    if value["time_basis"] != "source-relative-seconds" or type(value["sequence"]) is not int or not 0 <= value["sequence"] < MAX_WINDOWS:
        raise ValueError("invalid snapshot identity/sequence")
    _window(value["observation"])
    _number(value["reference_bpm"], "reference_bpm", nullable=True, minimum=0, maximum=10**6)
    _number(value["reference_available_seconds"], "reference availability", nullable=True, minimum=0)
    if (value["reference_bpm"] is None) != (value["reference_available_seconds"] is None):
        raise ValueError("incomplete reference")
    if value["reference_available_seconds"] is not None and value["reference_available_seconds"] >= value["observation"]["availability_seconds"]:
        raise ValueError("reference must be strictly past")
    for key, name in (("source_id", "source"), ("subject_id", "subject")):
        if value["observation"].get(key, value[name]) != value[name]:
            raise ValueError("snapshot observation identity mismatch")


def _identity(value):
    return value["source"], value["subject"], value["time_basis"]


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _available(snapshot):
    return snapshot["observation"]["availability_seconds"]


def _compatible(base, target):
    if _identity(base) != _identity(target):
        raise ValueError("cross-source/subject/base is unsupported")


def condition(snapshot, now):
    """Arbitrary engineering band with explicit UNKNOWN; never a clinical state."""
    _number(now, "now", minimum=0, maximum=10**9)
    if snapshot is None:
        return "UNKNOWN"
    _snapshot(snapshot)
    observation = snapshot["observation"]
    age = now - observation["availability_seconds"]
    if age < 0 or age > POLICY_PARAMETERS["stale_after_seconds"] or observation["status"] == "unavailable" or not observation["rules_accept"] or observation["estimate_bpm"] is None or snapshot["reference_bpm"] is None:
        return "UNKNOWN"
    return "outside-reference-band" if abs(observation["estimate_bpm"] - snapshot["reference_bpm"]) >= POLICY_PARAMETERS["reference_band_bpm"] else "within-reference-band"


def encode_packet(base, target, packet_id, kind="smallest"):
    _snapshot(target)
    _text(packet_id, "packet_id", 128)
    if kind not in ("full", "delta", "smallest"):
        raise ValueError("unsupported encoding")
    full = {"schema_version": 1, "packet_id": packet_id, "kind": "full", "base_digest": None,
            "target_digest": _digest(target), "target_sequence": target["sequence"],
            "content": copy.deepcopy(target)}
    if base is None or kind == "full":
        return full
    _snapshot(base)
    _compatible(base, target)
    if target["sequence"] <= base["sequence"] or _available(target) <= _available(base):
        raise ValueError("delta target must advance exact receiver base")
    delta = {"schema_version": 1, "packet_id": packet_id, "kind": "delta", "base_digest": _digest(base),
             "target_digest": _digest(target), "target_sequence": target["sequence"],
             "content": {key: copy.deepcopy(target[key]) for key in sorted(SNAPSHOT_FIELDS) if target[key] != base[key]}}
    if kind == "delta" or len(canonical_bytes(delta)) < len(canonical_bytes(full)):
        return delta
    return full


def receive_packet(current, packet, seen_ids):
    """Return (copied state, outcome); caller owns ACK/seen IDs and explicit resync."""
    _fields(packet, PACKET_FIELDS)
    if current is not None:
        _snapshot(current)
    if type(seen_ids) is not set or any(type(item) is not str for item in seen_ids):
        raise ValueError("seen IDs must be a set of strings")
    if type(packet["schema_version"]) is not int or packet["schema_version"] != 1 or packet["kind"] not in ("full", "delta"):
        raise ValueError("unsupported packet")
    _text(packet["packet_id"], "packet_id", 128)
    for name in ("base_digest", "target_digest"):
        value = packet[name]
        if value is None and name == "base_digest":
            continue
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("invalid digest")
    if type(packet["target_sequence"]) is not int or not 0 <= packet["target_sequence"] < MAX_WINDOWS:
        raise ValueError("invalid target sequence")
    if len(canonical_bytes(packet)) > MAX_INPUT_BYTES:
        raise ValueError("oversize packet")
    if packet["kind"] == "full":
        if packet["base_digest"] is not None:
            raise ValueError("full recovery must be standalone")
        target = copy.deepcopy(packet["content"])
        _snapshot(target)
    else:
        changes = packet["content"]
        if type(changes) is not dict or not changes or set(changes) - SNAPSHOT_FIELDS or packet["base_digest"] is None:
            raise ValueError("invalid replacement delta")
        if packet["packet_id"] in seen_ids:
            return copy.deepcopy(current), "duplicate"
        if current is not None and packet["target_sequence"] < current["sequence"]:
            return copy.deepcopy(current), "stale"
        if current is None or _digest(current) != packet["base_digest"]:
            return copy.deepcopy(current), "resync_required"
        target = copy.deepcopy(current)
        target.update(copy.deepcopy(changes))
        _snapshot(target)
    if _digest(target) != packet["target_digest"] or target["sequence"] != packet["target_sequence"]:
        raise ValueError("target identity mismatch")
    if current is not None:
        _compatible(current, target)
    if packet["packet_id"] in seen_ids:
        return copy.deepcopy(current), "duplicate"
    if current is not None:
        if target["sequence"] < current["sequence"] or _available(target) < _available(current):
            return copy.deepcopy(current), "stale"
        if target["sequence"] == current["sequence"] or _available(target) == _available(current):
            if target != current:
                raise ValueError("conflicting same-time/sequence target")
            return copy.deepcopy(current), "duplicate-target"
    return target, "applied"


def recommend(current, receiver, now, in_flight=False):
    """Recommend only from current evidence and last receiver knowledge; no I/O."""
    _number(now, "now", minimum=0, maximum=10**9)
    if type(in_flight) is not bool:
        raise ValueError("in_flight must be boolean")
    _snapshot(current)
    if _available(current) > now:
        raise ValueError("future current evidence")
    if receiver is not None:
        _snapshot(receiver)
        _compatible(receiver, current)
        if receiver["sequence"] > current["sequence"] or _available(receiver) > _available(current):
            raise ValueError("receiver knowledge cannot be later than current")
        if receiver["sequence"] == current["sequence"] and receiver != current:
            raise ValueError("conflicting receiver knowledge")
    if in_flight:
        return {"action": "defer", "reasons": ["in-flight-dependency"], "packet": None, "serialized_bytes": 0}
    reasons = []
    if receiver is None:
        reasons.append("receiver-missing")
    elif receiver != current:
        old, new = receiver["observation"], current["observation"]
        if (old["status"], old["rules_accept"], old["learned_accept"], old["estimate_bpm"] is None) != (new["status"], new["rules_accept"], new["learned_accept"], new["estimate_bpm"] is None):
            reasons.append("quality-change")
        if condition(receiver, now) != condition(current, now):
            reasons.append("engineering-condition-change")
        if old["estimate_bpm"] is not None and new["estimate_bpm"] is not None and abs(new["estimate_bpm"] - old["estimate_bpm"]) >= POLICY_PARAMETERS["estimate_change_bpm"]:
            reasons.append("estimate-change")
        if _available(current) - _available(receiver) >= POLICY_PARAMETERS["refresh_seconds"]:
            reasons.append("freshness-refresh")
    if not reasons:
        return {"action": "hold", "reasons": ["no-prespecified-trigger"], "packet": None, "serialized_bytes": 0}
    packet = encode_packet(receiver, current, f"evidence-{current['sequence']}", "smallest")
    return {"action": "send", "reasons": reasons, "packet": packet,
            "serialized_bytes": len(canonical_bytes(packet))}


def _mean(values):
    return sum(values) / len(values) if values else None


def _fraction(numerator, denominator):
    return numerator / denominator if denominator else None


def _metrics(ticks, packets, allowance, coalesced, queued, blocked):
    finite = [t["absolute_error_bpm"] for t in ticks if t["absolute_error_bpm"] is not None]
    fresh = [t["absolute_error_bpm"] for t in ticks if t["absolute_error_bpm"] is not None and not t["receiver_stale"]]
    both_fresh = [t["absolute_error_bpm"] for t in ticks if t["absolute_error_bpm"] is not None and not t["receiver_stale"] and not t["source_stale"]]
    source_fresh_finite = sum(t["source_estimate_bpm"] is not None and not t["source_stale"] for t in ticks)
    source_finite = sum(t["source_estimate_bpm"] is not None for t in ticks)
    source_known = [t for t in ticks if t["source_condition"] != "UNKNOWN"]
    ages = [t["receiver_age_seconds"] for t in ticks if t["receiver_age_seconds"] is not None]
    count = len(ticks)
    within = [p for p in packets if p["within_horizon"]]
    return {"ticks": count, "byte_allowance": allowance,
            "started_bytes": sum(p["serialized_bytes"] for p in packets),
            "unused_bytes": allowance - sum(p["serialized_bytes"] for p in packets),
            "started_packets": len(packets), "delivered_within_horizon": len(within),
            "started_full_packets": sum(p["packet"]["kind"] == "full" for p in packets),
            "started_delta_packets": sum(p["packet"]["kind"] == "delta" for p in packets),
            "coalesced_unsent_nominations": coalesced, "queued_unsent_at_horizon": queued,
            "budget_blocked_distinct_targets": len(blocked),
            "source_finite_ticks": source_finite,
            "finite_pair_count": len(finite), "finite_pair_mae_bpm": _mean(finite),
            "finite_pair_coverage_all_ticks": len(finite) / count,
            "finite_pair_coverage_source_finite": _fraction(len(finite), source_finite),
            "receiver_nonstale_finite_pair_count": len(fresh), "receiver_nonstale_finite_pair_mae_bpm": _mean(fresh),
            "receiver_nonstale_finite_pair_coverage_source_finite": _fraction(len(fresh), source_finite),
            "both_nonstale_finite_pair_count": len(both_fresh), "both_nonstale_finite_pair_mae_bpm": _mean(both_fresh),
            "both_nonstale_finite_pair_coverage_all_ticks": len(both_fresh) / count,
            "both_nonstale_finite_pair_coverage_source_finite": _fraction(len(both_fresh), source_finite),
            "both_nonstale_finite_pair_coverage_source_nonstale_finite": _fraction(len(both_fresh), source_fresh_finite),
            "receiver_unknown_fraction": sum(t["receiver_condition"] == "UNKNOWN" for t in ticks) / count,
            "receiver_stale_fraction": sum(t["receiver_stale"] for t in ticks) / count,
            "receiver_missing_fraction": sum(t["receiver_sequence"] is None for t in ticks) / count,
            "source_unknown_fraction": sum(t["source_condition"] == "UNKNOWN" for t in ticks) / count,
            "source_stale_fraction": sum(t["source_stale"] for t in ticks) / count,
            "source_gate_accepted_fraction": sum(t["source_gate_accepted"] for t in ticks) / count,
            "receiver_gate_accepted_fraction": sum(t["receiver_gate_accepted"] for t in ticks) / count,
            "mean_receiver_age_seconds": _mean(ages), "receiver_age_coverage": len(ages) / count,
            "state_disagreement_fraction": sum(t["receiver_condition"] != t["source_condition"] for t in ticks) / count,
            "source_known_ticks": len(source_known),
            "source_known_disagreement_fraction": _fraction(sum(t["receiver_condition"] != t["source_condition"] for t in source_known), len(source_known)),
            "mean_queue_delay_seconds_started": _mean([p["queue_delay_seconds"] for p in packets]),
            "mean_serialization_seconds_started": _mean([p["serialization_seconds"] for p in packets]),
            "mean_propagation_seconds_started": _mean([p["propagation_seconds"] for p in packets]),
            "mean_total_delay_seconds_delivered_within_horizon": _mean([p["arrives_at_seconds"] - p["source_available_seconds"] for p in within])}


def _transitions(ticks):
    changes = []
    prior = "UNKNOWN"
    for index, tick in enumerate(ticks):
        if tick["source_condition"] != prior:
            update_trigger = index == 0 or tick["source_sequence"] != ticks[index - 1]["source_sequence"]
            onset = tick["source_available_seconds"] if update_trigger else tick["time_seconds"]
            changes.append({"tick_index": index, "time_seconds": onset,
                            "first_scored_at_seconds": tick["time_seconds"],
                            "trigger": "source-update" if update_trigger else "local-expiry",
                            "from_state": prior, "to_state": tick["source_condition"],
                            "source_sequence": tick["source_sequence"]})
            prior = tick["source_condition"]
    for index, change in enumerate(changes):
        end = changes[index + 1]["tick_index"] if index + 1 < len(changes) else len(ticks)
        detected = next((t for t in ticks[change["tick_index"]:end]
                         if t["receiver_condition"] == change["to_state"] and t["receiver_available_seconds"] is not None
                         and t["receiver_available_seconds"] >= change["time_seconds"]), None)
        change["detected_at_seconds"] = detected["time_seconds"] if detected else None
        change["delay_seconds"] = detected["time_seconds"] - change["time_seconds"] if detected else None
    return changes


def simulate(series, policy, rate_bps, propagation_seconds):
    """Exact event timing, fixed-cadence scoring, one in-flight packet, no real I/O."""
    _series(series)
    if policy not in POLICIES:
        raise ValueError("unknown policy")
    _number(rate_bps, "rate_bps", minimum=1, maximum=10**6)
    _number(propagation_seconds, "propagation_seconds", minimum=0, maximum=1200)
    snapshots = make_snapshots(series)
    origin = series["windows"][0]["start_seconds"]
    horizon = _available(snapshots[-1])
    allowance = math.floor(rate_bps * (horizon - origin))
    queue = deque()
    receiver = current = flight = None
    seen, blocked = set(), set()
    packets, ticks = [], []
    source_index = 0
    spent = coalesced = 0
    next_tick = origin
    next_period = origin
    now = origin
    while now <= horizon:
        # Complete evidence becomes known only at its detector availability.
        while source_index < len(snapshots) and _available(snapshots[source_index]) <= now:
            current = snapshots[source_index]
            if policy in ("full_fifo", "delta_every_update"):
                queue.append(current)
            source_index += 1
        if flight is not None and flight["arrives_at_seconds"] <= now:
            receiver, outcome = receive_packet(receiver, flight["packet"], seen)
            if outcome != "applied":
                raise ValueError("unexpected transport failure; explicit full resync required")
            seen.add(flight["packet"]["packet_id"])
            flight["delivery_outcome"] = outcome
            flight = None
        if policy == "periodic_full" and next_period <= now:
            if current is not None and (receiver is None or current["sequence"] > receiver["sequence"]):
                if flight is None or flight["target_sequence"] != current["sequence"]:
                    if queue and queue[0]["sequence"] != current["sequence"]:
                        coalesced += 1
                    queue.clear()
                    queue.append(current)
            next_period += POLICY_PARAMETERS["period_seconds"]
        if policy == "priority" and current is not None:
            # Nominations may coalesce while busy; no delta is encoded/enqueued.
            decision = recommend(current, receiver, now)
            if decision["action"] == "send" and (flight is None or flight["target_sequence"] != current["sequence"]):
                if queue and queue[0]["sequence"] != current["sequence"]:
                    coalesced += 1
                queue.clear()
                queue.append(current)
            elif decision["action"] == "hold" and queue:
                coalesced += 1
                queue.clear()
        if flight is None and queue and now < horizon:
            target = queue[0]
            if receiver is not None and target["sequence"] <= receiver["sequence"]:
                queue.popleft()
            else:
                encoding = "full" if policy in ("full_fifo", "periodic_full") else "delta" if policy == "delta_every_update" else "smallest"
                packet = encode_packet(receiver, target, f"evidence-{target['sequence']}", encoding)
                size = len(canonical_bytes(packet))
                if spent + size <= allowance:
                    queue.popleft()
                    spent += size
                    arrival = now + size / rate_bps + propagation_seconds
                    flight = {"target_sequence": target["sequence"], "packet": packet,
                              "source_available_seconds": _available(target), "started_at_seconds": now,
                              "arrives_at_seconds": arrival, "serialized_bytes": size,
                              "queue_delay_seconds": now - _available(target),
                              "serialization_seconds": size / rate_bps, "propagation_seconds": propagation_seconds,
                              "within_horizon": arrival <= horizon, "delivery_outcome": "pending"}
                    packets.append(flight)
                else:
                    blocked.add(target["sequence"])
        if next_tick <= now:
            source_value = current["observation"]["estimate_bpm"] if current else None
            receiver_value = receiver["observation"]["estimate_bpm"] if receiver else None
            age = now - _available(receiver) if receiver else None
            ticks.append({"time_seconds": now, "source_sequence": current["sequence"] if current else None,
                          "receiver_sequence": receiver["sequence"] if receiver else None,
                          "source_available_seconds": _available(current) if current else None,
                          "receiver_available_seconds": _available(receiver) if receiver else None,
                          "source_estimate_bpm": source_value, "receiver_estimate_bpm": receiver_value,
                          "absolute_error_bpm": abs(source_value - receiver_value) if source_value is not None and receiver_value is not None else None,
                          "source_condition": condition(current, now), "receiver_condition": condition(receiver, now),
                          "source_gate_accepted": bool(current and current["observation"]["status"] != "unavailable" and current["observation"]["rules_accept"] and source_value is not None),
                          "receiver_gate_accepted": bool(receiver and receiver["observation"]["status"] != "unavailable" and receiver["observation"]["rules_accept"] and receiver_value is not None),
                          "receiver_age_seconds": age,
                          "source_stale": current is not None and now - _available(current) > POLICY_PARAMETERS["stale_after_seconds"],
                          "receiver_stale": age is not None and age > POLICY_PARAMETERS["stale_after_seconds"]})
            next_tick += POLICY_PARAMETERS["cadence_seconds"]
        candidates = [next_tick]
        if source_index < len(snapshots):
            candidates.append(_available(snapshots[source_index]))
        if flight is not None:
            candidates.append(flight["arrives_at_seconds"])
        if policy == "periodic_full":
            candidates.append(next_period)
        next_time = min(candidates)
        if next_time <= now:
            raise ValueError("non-forward simulation event")
        now = next_time
    # Only the already-started final packet drains; accuracy stops at horizon.
    drain = {"delivered_packets": 0, "final_delivery_seconds": None, "outcome": None}
    if flight is not None:
        _, outcome = receive_packet(receiver, flight["packet"], seen)
        flight["delivery_outcome"] = outcome
        drain = {"delivered_packets": 1 if outcome == "applied" else 0,
                 "final_delivery_seconds": flight["arrives_at_seconds"], "outcome": outcome}
    changes = _transitions(ticks)
    metrics = _metrics(ticks, packets, allowance, coalesced, len(queue), blocked)
    detected = [change["delay_seconds"] for change in changes if change["delay_seconds"] is not None]
    metrics.update({"source_condition_changes": len(changes), "detected_condition_changes": len(detected),
                    "missed_condition_changes": len(changes) - len(detected),
                    "detected_only_mean_condition_delay_seconds": _mean(detected),
                    "delivered_packets_including_drain": metrics["delivered_within_horizon"] + drain["delivered_packets"]})
    return {"source": series["source"], "subject": series["subject"], "time_basis": series["time_basis"],
            "policy": policy, "rate_bytes_per_second": rate_bps, "propagation_seconds": propagation_seconds,
            "stress_setting": propagation_seconds == 1200, "source_origin_seconds": origin,
            "source_horizon_seconds": horizon, "metrics": metrics, "condition_changes": changes,
            "packets": packets, "ticks": ticks, "drain": drain}


def benchmark(value, *, rates=DEFAULT_RATES, delays=DEFAULT_DELAYS, selection):
    """Run a fixed grid with no outcome-adaptive selection or parameter fitting."""
    ledger = validate_input(value)
    _text(selection, "prespecified source selection", 4096)
    rates, delays = tuple(rates), tuple(delays)
    if not 1 <= len(rates) <= 8 or not 1 <= len(delays) <= 4 or len(set(rates)) != len(rates) or len(set(delays)) != len(delays):
        raise ValueError("invalid network grid")
    for rate in rates:
        _number(rate, "rate", minimum=1, maximum=10**6)
    for delay in delays:
        _number(delay, "delay", minimum=0, maximum=1200)
    tick_count = sum(math.floor(s["windows"][-1]["availability_seconds"] - s["windows"][0]["start_seconds"]) + 1 for s in ledger["series"])
    if tick_count * len(rates) * len(delays) * len(POLICIES) > MAX_BENCHMARK_TICKS:
        raise ValueError("requested grid exceeds bounded tick count")
    runs = [simulate(item, policy, rate, delay) for item in ledger["series"]
            for delay in delays for rate in rates for policy in POLICIES]
    return {"format": "biotwin-evidence-link-experiment", "schema_version": 1,
            "plan_version": "evidence-link-v1", "selection": selection,
            "input_canonical_sha256": _digest(ledger), "policy_parameters": copy.deepcopy(POLICY_PARAMETERS),
            "settings": {"rates_bytes_per_second": list(rates), "propagation_seconds": list(delays),
                         "acknowledgement": "ideal instantaneous sender knowledge on delivery; no ACK bytes",
                         "channel": "single in-flight, no loss, serialize then propagate",
                         "encoding": "compact sorted-key UTF-8 JSON; no compression or network framing",
                         "total_allowance": "floor(rate * original source duration)",
                         "metrics_horizon": "fixed 1-second original source clock; drain separate"},
            "series": [{"source": item["source"], "subject": item["subject"], "split": item.get("split"),
                        "source_start_seconds": item["windows"][0]["start_seconds"],
                        "source_end_seconds": item["windows"][-1]["end_seconds"],
                        "horizon_seconds": item["windows"][-1]["availability_seconds"],
                        "observations": len(item["windows"])} for item in ledger["series"]],
            "limitations": ["Diagnostic local estimates are not true physiology or validated clinical values.",
                            "The engineering condition is arbitrary; UNKNOWN is not normal.",
                            "Rates/delays are network test settings, not mission measurements.",
                            "Stop-and-wait ideal ACK model omits reverse traffic and real network overhead.",
                            "Already-seen descriptive sequences do not establish optimality or generalization.",
                            "Checksums establish byte identity, not sender authentication."],
            "runs": runs}


def _safe_path(path, *, must_exist=False):
    path = Path(path).absolute()
    for part in path.parts:
        lowered = part.lower()
        if part == "..":
            raise ValueError("parent traversal paths are prohibited")
        if lowered == ".env" or lowered.startswith(".env."):
            raise ValueError("secret paths are prohibited")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("symlink paths are prohibited")
        if ancestor.exists():
            attrs = getattr(ancestor.stat(), "st_file_attributes", 0)
            if attrs & 0x400:
                raise ValueError("reparse/junction paths are prohibited")
    if must_exist and not path.is_file():
        raise ValueError("input must be an existing regular file")
    return path


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def _tree_bounds(value, depth=0, counter=None):
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > 16 or counter[0] > 1_000_000:
        raise ValueError("JSON exceeds bounded depth/value count")
    if type(value) is dict:
        for item in value.values():
            _tree_bounds(item, depth + 1, counter)
    elif type(value) is list:
        for item in value:
            _tree_bounds(item, depth + 1, counter)


def load_input(path, *, with_receipt=False):
    path = _safe_path(path, must_exist=True)
    if path.suffix.lower() != ".json" or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input must be bounded JSON")
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("input grew beyond bounded size")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid bounded JSON input") from exc
    _tree_bounds(value)
    ledger = validate_input(value)
    if with_receipt:
        return ledger, {"input_file": str(path), "input_file_sha256": hashlib.sha256(raw).hexdigest(),
                        "input_file_bytes": len(raw), "unselected_canonical_sha256": _digest(ledger)}
    return ledger


def _display(value, digits=3):
    return "unavailable" if value is None else f"{value:.{digits}f}"


def _comparison_svg(result):
    """Escaped standalone error/coverage plots, retaining unavailable MAE points."""
    colors = {"full_fifo": "#1757aa", "periodic_full": "#8955a3", "delta_every_update": "#c56917", "priority": "#087b64"}
    groups = []
    for item in result["series"]:
        for delay in result["settings"]["propagation_seconds"]:
            runs = [r for r in result["runs"] if r["source"] == item["source"] and r["subject"] == item["subject"] and r["propagation_seconds"] == delay]
            groups.append((item, delay, runs))
    height = 100 + len(groups) * 330
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="{height}" viewBox="0 0 1120 {height}" role="img" aria-label="Diagnostic estimate error and coverage versus actual started bytes">',
             '<rect width="100%" height="100%" fill="#fff"/>',
             '<style>text{font-family:Arial,sans-serif;fill:#182536;font-size:12px}.title{font-size:17px;font-weight:bold}.axis{stroke:#677889;stroke-width:1}.grid{stroke:#dce4ed;stroke-width:1}</style>',
             '<text x="30" y="26" class="title">Diagnostic estimate error AND finite paired coverage</text>',
             '<text x="30" y="48">Lower error with low coverage is not a win. Missing estimates remain unavailable. X = actual started JSON bytes.</text>']
    for i, policy in enumerate(POLICIES):
        parts.append(f'<text x="{30+i*265}" y="73" style="fill:{colors[policy]}">{html.escape(policy)}</text>')
    for group_index, (item, delay, runs) in enumerate(groups):
        top = 110 + group_index * 330
        label = f'{item["source"]} | {item["subject"]} | propagation {delay}s'
        parts.append(f'<text x="30" y="{top}" class="title">{html.escape(label)}</text>')
        max_bytes = max([r["metrics"]["started_bytes"] for r in runs] + [1])
        max_error = max([r["metrics"]["finite_pair_mae_bpm"] or 0 for r in runs] + [1]) * 1.1
        for chart, left in (("error", 85), ("coverage", 640)):
            right, bottom = left + 400, top + 235
            plot_top = top + 35
            parts.extend([f'<line x1="{left}" y1="{plot_top}" x2="{left}" y2="{bottom}" class="axis"/>',
                          f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="axis"/>',
                          f'<text x="{left}" y="{plot_top-9}">{"Finite-pair MAE (bpm)" if chart == "error" else "Finite-pair / source-finite ticks"}</text>'])
            for step in range(5):
                value = step / 4
                y = bottom - value * (bottom - plot_top)
                parts.append(f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" class="grid"/>')
                parts.append(f'<text x="{left-8}" y="{y+4}" text-anchor="end">{value*max_error:.1f}</text>' if chart == "error" else f'<text x="{left-8}" y="{y+4}" text-anchor="end">{value:.2f}</text>')
            parts.append(f'<text x="{left}" y="{bottom+22}">0 bytes</text><text x="{right}" y="{bottom+22}" text-anchor="end">{max_bytes} bytes</text>')
            for policy in POLICIES:
                selected = sorted([r for r in runs if r["policy"] == policy], key=lambda r: r["rate_bytes_per_second"])
                points = []
                for run in selected:
                    metrics = run["metrics"]
                    value = metrics["finite_pair_mae_bpm"] if chart == "error" else metrics["finite_pair_coverage_source_finite"]
                    x = left + metrics["started_bytes"] / max_bytes * 400
                    title = html.escape(f'{policy}; rate={run["rate_bytes_per_second"]} B/s; bytes={metrics["started_bytes"]}; MAE={_display(metrics["finite_pair_mae_bpm"])}; coverage={_display(metrics["finite_pair_coverage_source_finite"])}')
                    if value is None:
                        parts.append(f'<text x="{x}" y="{bottom-3}" fill="{colors[policy]}"><title>{title}</title>×</text>')
                    else:
                        y = bottom - value / (max_error if chart == "error" else 1) * (bottom - plot_top)
                        points.append(f'{x:.2f},{y:.2f}')
                        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{colors[policy]}"><title>{title}</title></circle>')
                if len(points) > 1:
                    parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{colors[policy]}" stroke-width="1.5" opacity="0.7"/>')
        parts.append(f'<text x="30" y="{top+290}">Lines connect fixed-rate settings within one policy; × = unavailable. Source time and all four policies are preserved.</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def write_report(result, ledger, output_dir):
    """Create a new directory only. Existing results are never replaced."""
    ledger = validate_input(ledger)
    if result.get("input_canonical_sha256") != _digest(ledger):
        raise ValueError("result does not match source ledger")
    target = _safe_path(output_dir)
    if target.exists():
        raise ValueError("output directory already exists; earlier results are preserved")
    if not target.parent.is_dir():
        raise ValueError("output parent must already exist")
    svg = _comparison_svg(result)
    rows = []
    for run in result["runs"]:
        m = run["metrics"]
        cells = [run["source"], run["subject"], run["policy"], run["rate_bytes_per_second"], run["propagation_seconds"],
                 m["started_bytes"], m["byte_allowance"], m["delivered_within_horizon"], _display(m["finite_pair_mae_bpm"]),
                 _display(m["finite_pair_coverage_source_finite"]), _display(m["both_nonstale_finite_pair_mae_bpm"]),
                 _display(m["receiver_unknown_fraction"]), _display(m["receiver_stale_fraction"]),
                 _display(m["mean_receiver_age_seconds"]), _display(m["receiver_age_coverage"]),
                 _display(m["source_known_disagreement_fraction"]), m["detected_condition_changes"], m["missed_condition_changes"],
                 _display(m["detected_only_mean_condition_delay_seconds"])]
        rows.append('<tr>' + ''.join('<td>' + html.escape(str(c)) + '</td>' for c in cells) + '</tr>')
    headings = ["Source", "Subject", "Policy", "B/s", "Delay s", "Started bytes", "Allowance bytes", "On-time packets",
                "Finite-pair MAE bpm", "Source-finite paired coverage", "Nonstale MAE bpm", "Receiver UNKNOWN", "Receiver stale",
                "Mean age s", "Age coverage", "Source-known disagreement", "Detected changes", "Missed changes", "Detected-only delay s"]
    document = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Evidence link experiment</title>'
                '<style>body{font:15px Arial,sans-serif;margin:24px;color:#182536}table{border-collapse:collapse;font-size:12px}th,td{border:1px solid #bbc9d7;padding:6px}th{background:#e8eff7}figure{margin:0;overflow:auto}.table{overflow:auto}</style>'
                '<h1>Evidence under a constrained link</h1><p>Descriptive engineering simulation. Local diagnostic estimates are not true physiology. UNKNOWN is not normal. No clinical urgency or optimal-value claim.</p>'
                '<p>Prespecified source selection: ' + html.escape(result["selection"]) + '</p><p>Ledger SHA-256: ' + html.escape(result["input_canonical_sha256"]) + '</p>'
                '<p>Exact compact UTF-8 JSON bytes; single in-flight packet; serialization then propagation; ideal instantaneous ACK knowledge. Accuracy stops at source horizon; drain is separate. No real network.</p>'
                '<p>' + ' '.join(html.escape(x) for x in result["limitations"]) + '</p>'
                '<figure>' + svg + '</figure><h2>All settings and policies</h2><p>Fractions are 0–1; unavailable denominators stay unavailable. Condition delays exclude misses; miss counts remain visible. Full tick/packet/change traces are in results.json.</p><div class="table"><table><thead><tr>'
                + ''.join('<th>' + html.escape(x) + '</th>' for x in headings) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></html>')
    target.mkdir(exist_ok=False)
    for name, content in (("source-ledger.json", canonical_bytes(ledger).decode("utf-8")),
                          ("results.json", canonical_bytes(result).decode("utf-8")),
                          ("comparison.svg", svg), ("report.html", document)):
        with (target / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    return {"output_dir": str(target), "runs": len(result["runs"]),
            "files": ["source-ledger.json", "results.json", "comparison.svg", "report.html"]}
