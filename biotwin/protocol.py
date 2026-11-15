"""Verified snapshots and reviewer-authored raw-value conditional checklists.

Canonical project bytes: sorted keys, compact JSON, ensure_ascii=False,
allow_nan=False, encoded UTF-8. This is not RFC8785 or authentication.
"""
from __future__ import annotations

import copy
import hashlib
import json
import operator
from .data import CONTEXT, finite, timestamp

REQUIRED_OBSERVATION = {"id", "subject_id", "source_id", "metric", "value", "unit", "observed_at", "phase", "method", "quality", "provenance"}
PAYLOAD_FIELDS = {"schema_version", "subject_id", "created_at", "observations"}
OPERATORS = {"lt": operator.lt, "le": operator.le, "eq": operator.eq, "ge": operator.ge, "gt": operator.gt}


def canonical_bytes(value):
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and finite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("only finite plain JSON values are supported")
    try:
        check(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (RecursionError, UnicodeError, TypeError, OverflowError) as exc:
        raise ValueError("unsafe JSON payload") from exc


def _nonempty(value):
    return type(value) is str and bool(value.strip())


def validate_payload(payload):
    canonical_bytes(payload)
    if type(payload) is not dict or set(payload) != PAYLOAD_FIELDS or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported snapshot schema")
    if not _nonempty(payload["subject_id"]) or type(payload["observations"]) is not list:
        raise ValueError("invalid snapshot subject/observations")
    timestamp(payload["created_at"])
    seen = set()
    for observation in payload["observations"]:
        if type(observation) is not dict or not REQUIRED_OBSERVATION.issubset(observation):
            raise ValueError("missing observation fields")
        if any(not _nonempty(observation[k]) for k in REQUIRED_OBSERVATION - {"value"}):
            raise ValueError("invalid observation identity/context")
        if observation["id"] in seen or observation["subject_id"] != payload["subject_id"]:
            raise ValueError("duplicate observation or mismatched subject")
        seen.add(observation["id"])
        if observation["value"] is not None and not finite(observation["value"]):
            raise ValueError("invalid measurement value")
        if observation["quality"] not in {"usable", "review", "missing"} or observation["provenance"] not in {"recorded", "synthetic"}:
            raise ValueError("invalid quality/provenance")
        timestamp(observation["observed_at"])
        # Typed narrative assistance cannot be forged as a scalar observation.
        # Semantic history validation runs once at the session boundary.
        from .health_assistant import validate_observation
        validate_observation(observation)
    if [o["id"] for o in payload["observations"]] != sorted(seen):
        raise ValueError("observations must be ordered by unique ID")


def make_snapshot(subject_id, observations, created_at):
    payload = {"schema_version": 1, "subject_id": subject_id, "created_at": created_at,
               "observations": copy.deepcopy(list(observations))}
    try:
        payload["observations"].sort(key=lambda o: o["id"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid observation identity") from exc
    validate_payload(payload)
    return {"payload": payload, "digest": hashlib.sha256(canonical_bytes(payload)).hexdigest()}


def verify_snapshot(snapshot):
    if type(snapshot) is not dict or set(snapshot) != {"payload", "digest"}:
        raise ValueError("invalid snapshot envelope")
    validate_payload(snapshot["payload"])
    digest = hashlib.sha256(canonical_bytes(snapshot["payload"])).hexdigest()
    if snapshot["digest"] != digest:
        raise ValueError("snapshot digest mismatch")
    return snapshot["payload"]


def _aware(value):
    when, basis = timestamp(value)
    if basis != "aware":
        raise ValueError("unsupported timing: aware timestamps required")
    return when


def evaluate_protocol(protocol, reviewed_snapshot, current_snapshot, now):
    """AND within A/B; exactly one true branch selects it, else UNKNOWN.

    Dependencies have unique metric names and exact CONTEXT fields. Latest
    same-metric record is checked before context/quality, so newer bad or
    incompatible records cannot be hidden by an older good record. Raw values
    only; standardized scores are intentionally unsupported in this version.
    """
    original = protocol.get("original_reply") if isinstance(protocol, dict) else None
    checklists = protocol.get("checklists", {}) if isinstance(protocol, dict) else {}
    result = {"outcome": "UNKNOWN", "reasons": [], "original_reply": original,
              "checklist": copy.deepcopy(checklists.get("UNKNOWN", [])) if isinstance(checklists, dict) else [],
              "comparisons": [], "protocol_id": protocol.get("id") if isinstance(protocol, dict) else None}
    def unknown(reason):
        result["reasons"].append(reason)
        return result
    try:
        canonical_bytes(protocol)
        required = {"id", "version", "subject_id", "reviewed_digest", "valid_from", "valid_until", "dependencies", "freshness_seconds", "conditions", "original_reply", "checklists"}
        if type(protocol) is not dict or set(protocol) != required:
            raise ValueError("invalid protocol schema")
        if not _nonempty(protocol["id"]) or type(protocol["version"]) is not int or protocol["version"] != 1:
            raise ValueError("unsupported protocol version/identity")
        if not _nonempty(original) or type(checklists) is not dict or set(checklists) != {"A", "B", "UNKNOWN"} or any(type(items) is not list or not items or any(not _nonempty(item) for item in items) for items in checklists.values()):
            raise ValueError("invalid authored reply/checklists")
        reviewed, current = verify_snapshot(reviewed_snapshot), verify_snapshot(current_snapshot)
        if protocol["subject_id"] != reviewed["subject_id"] or reviewed["subject_id"] != current["subject_id"]:
            raise ValueError("subject identity mismatch")
        if protocol["reviewed_digest"] != reviewed_snapshot["digest"]:
            raise ValueError("reviewed snapshot mismatch")
        instant, start, end = _aware(now), _aware(protocol["valid_from"]), _aware(protocol["valid_until"])
        if end < start or not start <= instant <= end:
            raise ValueError("protocol outside validity interval")
        if _aware(reviewed["created_at"]) > instant or _aware(current["created_at"]) > instant:
            raise ValueError("future snapshot")
        if _aware(current["created_at"]) < _aware(reviewed["created_at"]):
            raise ValueError("current snapshot predates reviewed snapshot")
        freshness = protocol["freshness_seconds"]
        if not finite(freshness) or freshness <= 0:
            raise ValueError("invalid freshness limit")
        dependencies = protocol["dependencies"]
        if type(dependencies) is not list or not dependencies:
            raise ValueError("no declared dependencies")
        names, current_values = set(), {}
        for dep in dependencies:
            if type(dep) is not dict or set(dep) != set(CONTEXT) or any(not _nonempty(v) for v in dep.values()):
                raise ValueError("invalid declared dependency")
            metric = dep["metric"]
            if metric in names:
                raise ValueError("duplicate metric dependency")
            names.add(metric)
            values = []
            for label, payload in (("reviewed", reviewed), ("current", current)):
                rows = [o for o in payload["observations"] if o["metric"] == metric]
                if not rows:
                    raise ValueError(f"{label}: missing dependency {metric}")
                timed = [(_aware(o["observed_at"]), o) for o in rows]
                if any(t > instant or t > _aware(payload["created_at"]) for t, _ in timed):
                    raise ValueError(f"{label}: future dependency evidence {metric}")
                latest_time = max(t for t, _ in timed)
                latest = [o for t, o in timed if t == latest_time]
                if len(latest) != 1:
                    raise ValueError(f"{label}: conflicting equal-time evidence {metric}")
                row = latest[0]
                if any(row[k] != dep[k] for k in CONTEXT):
                    raise ValueError(f"{label}: incompatible dependency {metric}")
                if row["quality"] != "usable" or not finite(row["value"]):
                    raise ValueError(f"{label}: unusable dependency {metric}")
                # Reviewed evidence freshness is relative to its own review time;
                # current freshness is relative to evaluation/receipt time.
                age = ((_aware(payload["created_at"]) if label == "reviewed" else instant) - latest_time).total_seconds()
                if age > freshness:
                    raise ValueError(f"{label}: stale dependency {metric}")
                values.append(row)
            old, new = values
            current_values[metric] = new["value"]
            result["comparisons"].append({"metric": metric, "unit": dep["unit"], "reviewed_value": old["value"],
                "current_value": new["value"], "reviewed_id": old["id"], "current_id": new["id"],
                "reviewed_at": old["observed_at"], "current_at": new["observed_at"]})
        conditions = protocol["conditions"]
        if type(conditions) is not dict or set(conditions) != {"A", "B"}:
            raise ValueError("invalid branch conditions")
        truth = {}
        for branch in ("A", "B"):
            rules = conditions[branch]
            if type(rules) is not list or not rules:
                raise ValueError("empty branch conditions")
            hits, used = [], set()
            for rule in rules:
                if type(rule) is not dict or set(rule) != {"metric", "op", "value"} or rule["metric"] not in names or rule["op"] not in OPERATORS or not finite(rule["value"]):
                    raise ValueError("invalid/undeclared raw-value condition")
                used.add(rule["metric"])
                hits.append(OPERATORS[rule["op"]](current_values[rule["metric"]], rule["value"]))
            if used != names:
                raise ValueError("each branch must cover all declared dependencies")
            truth[branch] = all(hits)
        if truth["A"] == truth["B"]:
            return unknown("both branches true" if truth["A"] else "neither branch true")
        branch = "A" if truth["A"] else "B"
        result.update(outcome=branch, checklist=copy.deepcopy(checklists[branch]), reasons=[f"only branch {branch} conditions met"])
        return result
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return unknown(str(exc))
