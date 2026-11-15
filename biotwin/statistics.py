"""Past-only descriptive references; engineering thresholds, not clinical limits."""
from __future__ import annotations

import math
from statistics import mean, stdev
from .data import comparable, finite, timestamp


def reference_summary(history, current, min_count=5):
    if isinstance(min_count, bool) or not isinstance(min_count, int) or min_count < 2:
        raise ValueError("min_count must be an integer >= 2")
    result = {"status": "unavailable", "reason": None, "count": 0, "reference_ids": [],
              "mean": None, "sd": None, "zscore": None, "reference_interval": None,
              "min_count": min_count, "provenance": "past-only sample mean/sample SD; descriptive +/-2 SD"}
    def unavailable(reason):
        result["reason"] = reason
        return result
    if current.get("quality") != "usable" or not finite(current.get("value")):
        return unavailable("current measurement unusable")
    try:
        now, basis = timestamp(current.get("observed_at"))
    except (ValueError, TypeError):
        return unavailable("unsupported current timing")
    chosen, excluded, instants = [], {}, {}
    ids = set()
    for row in history:
        if row.get("id") == current.get("id") or not comparable(row, current, personal=True):
            continue
        try:
            when, other_basis = timestamp(row.get("observed_at"))
        except (ValueError, TypeError):
            excluded["invalid timing"] = excluded.get("invalid timing", 0) + 1
            continue
        if basis != other_basis:
            return unavailable("mixed temporal bases")
        if when == now and row.get("quality") == "usable" and finite(row.get("value")) and row["value"] != current["value"]:
            return unavailable("conflicting current-time evidence")
        if when >= now:
            excluded["not strictly earlier"] = excluded.get("not strictly earlier", 0) + 1
            continue
        if row.get("id") in ids:
            return unavailable("duplicate reference identity")
        ids.add(row.get("id"))
        if row.get("quality") == "usable" and finite(row.get("value")):
            if when in instants:
                return unavailable("ambiguous equal-time references")
            instants[when] = row["id"]
            chosen.append(row)
        else:
            excluded["unusable"] = excluded.get("unusable", 0) + 1
    chosen.sort(key=lambda o: (timestamp(o["observed_at"])[0], o["id"]))
    result.update(count=len(chosen), reference_ids=[o["id"] for o in chosen],
                  reference_dates=[o["observed_at"] for o in chosen], exclusions=excluded)
    if len(chosen) < min_count:
        return unavailable("insufficient earlier comparable observations")
    values = [o["value"] for o in chosen]
    try:
        avg, sd = mean(values), stdev(values)
        z = (current["value"] - avg) / sd if sd > 0 else None
    except (OverflowError, ValueError):
        return unavailable("nonfinite reference arithmetic")
    if not finite(avg) or not finite(sd) or (z is not None and not finite(z)):
        return unavailable("nonfinite reference arithmetic")
    result.update(mean=avg, sd=sd)
    if sd == 0:
        return unavailable("zero reference scale")
    interval = [avg - 2 * sd, avg + 2 * sd]
    if not all(finite(v) for v in interval):
        return unavailable("nonfinite reference arithmetic")
    result.update(status="available", reason="available", zscore=z, reference_interval=interval)
    return result


def detect_change(history, current, min_count=5, threshold=2.0, persistence=2):
    """Require N consecutive latest compatible usable points crossing |z| >= T.

    Each point uses its own strictly earlier reference. A bad point interrupts
    persistence; each point must cross in the same direction. No CUSUM is used.
    """
    if not finite(threshold) or threshold <= 0:
        raise ValueError("threshold must be positive and finite")
    if isinstance(persistence, bool) or not isinstance(persistence, int) or persistence < 1:
        raise ValueError("persistence must be a positive integer")
    history = list(history)
    summary = reference_summary(history, current, min_count)
    result = {"status": summary["status"], "flagged": None, "threshold": threshold,
              "persistence": persistence, "reference": summary, "evidence": []}
    if summary["status"] != "available":
        return result
    now, basis = timestamp(current["observed_at"])
    previous = []
    for row in history:
        if row.get("id") == current.get("id") or not comparable(row, current, personal=True):
            continue
        try:
            when, temporal_basis = timestamp(row.get("observed_at"))
        except (ValueError, TypeError):
            result.update(status="unavailable", reason="invalid persistence timing")
            return result
        if basis != temporal_basis:
            result.update(status="unavailable", reason="mixed temporal bases")
            return result
        if when < now:
            previous.append(row)
    points = sorted(previous, key=lambda o: (timestamp(o["observed_at"])[0], o["id"]))[-(persistence - 1):] if persistence > 1 else []
    points.append(current)
    directions = []
    for point in points:
        ref = reference_summary(history, point, min_count)
        z = ref["zscore"]
        result["evidence"].append({"id": point["id"], "reference_status": ref["status"], "zscore": z})
        directions.append(1 if z is not None and z >= threshold else (-1 if z is not None and z <= -threshold else 0))
    result["flagged"] = len(points) == persistence and directions[0] != 0 and len(set(directions)) == 1
    result["reason"] = "persistent change flagged" if result["flagged"] else "persistence rule not met"
    return result
