"""Frozen-reference descriptive tabular CUSUM; no clinical decision meaning.

``reference`` is an explicitly selected observation iterable, not a rolling
history. Reference values are frozen before the earliest monitoring observation
at/before ``as_of``. All times retain their actual temporal basis.
"""
from __future__ import annotations

from collections import Counter
import re
from statistics import mean, stdev

from .data import CONTEXT, finite, timestamp

_CONTEXT = ("subject_id", *CONTEXT)
_LIMIT = 100_000


def _rows(values):
    rows = []
    for row in values:
        if len(rows) >= _LIMIT:
            raise ValueError("at most 100000 observations per input")
        if type(row) is not dict:
            raise ValueError("observations must be dictionaries")
        rows.append(row)
    return rows


def _time(row):
    when, basis = timestamp(row.get("observed_at"))
    precision = _precision(row)
    if row.get("time_precision") not in (None, precision):
        raise ValueError("inconsistent observation precision")
    return when, basis


def _precision(row):
    value = row.get("observed_at")
    if not isinstance(value, str):
        raise ValueError("unsupported observation precision")
    if len(value) == 10:
        return "day"
    match = re.search(r"[Tt ]\d{2}:\d{2}(:\d{2})?([.,]\d+)?", value)
    if not match:
        raise ValueError("unsupported observation precision")
    return "microsecond" if match[2] else ("second" if match[1] else "minute")


def _context(row):
    return {key: row.get(key) for key in _CONTEXT}


def cusum_series(reference, observations, as_of, k=.5, h=5, min_count=5,
                 max_gap_seconds=None):
    """Return a frozen reference and inclusive-cutoff trace, never a treatment.

    Invalid parameters raise ValueError. Unsupported evidence returns explicit
    unavailable status. A bad/context-changing/equal-time point interrupts the
    sequence and zeros both sums. The next valid point starts from zero using
    the *same* reference. A configured excessive gap resets before accumulation.
    Unorderable or mixed-basis monitoring times make the whole trace unavailable.
    """
    if not finite(k) or k < 0 or not finite(h) or h <= 0:
        raise ValueError("k must be finite/nonnegative and h finite/positive")
    if type(min_count) is not int or min_count < 5:
        raise ValueError("min_count must be an integer >= 5")
    if max_gap_seconds is not None and (not finite(max_gap_seconds) or max_gap_seconds <= 0):
        raise ValueError("max_gap_seconds must be positive and finite")
    cutoff, basis = timestamp(as_of)
    refs, monitors = _rows(reference), _rows(observations)
    ref = {"status": "unavailable", "reason": None, "count": 0,
           "reference_ids": [], "reference_dates": [], "mean": None, "sd": None,
           "context": None, "temporal_basis": basis, "exclusions": {},
           "selection": "explicit input; usable, strictly earlier than first monitor; frozen"}
    result = {"status": "unavailable", "reason": None, "as_of": as_of,
              "parameters": {"k": k, "h": h, "min_count": min_count,
                             "max_gap_seconds": max_gap_seconds},
              "reference": ref, "trace": [], "excluded_future_count": 0,
              "interpretation": "descriptive engineering flag; not independent of zscore or a clinical limit"}

    def unavailable(reason):
        result["reason"] = reason
        if ref["status"] != "available":
            ref["reason"] = reason
        return result

    ordered = []
    identities = set()
    monitor_precision = None
    try:
        for row in monitors:
            when, other_basis = _time(row)
            if other_basis != basis:
                raise ValueError("mixed temporal bases")
            if when > cutoff:
                result["excluded_future_count"] += 1
                continue
            precision = _precision(row)
            if monitor_precision is not None and monitor_precision != precision:
                raise ValueError("mixed monitoring precision")
            monitor_precision = precision
            identity = row.get("id")
            if type(identity) is not str or not identity or identity in identities:
                raise ValueError("missing or duplicate monitoring identity")
            identities.add(identity)
            ordered.append((when, identity, row))
    except (ValueError, TypeError, OverflowError):
        result["trace"] = [{"id": row.get("id"), "status": "unavailable",
                            "reason": "unorderable, mixed-basis/precision or duplicate monitoring evidence",
                            "reset_reasons": ["whole sequence unavailable"], "flagged": None,
                            "splus": None, "sminus": None, "zscore": None}
                           for row in monitors]
        return unavailable("unorderable, mixed-basis/precision or duplicate monitoring evidence")
    ordered.sort(key=lambda item: (item[0], item[1]))
    if not ordered:
        return unavailable("no monitoring observations at/before cutoff")
    boundary = ordered[0][0]
    if not refs:
        return unavailable("no explicitly selected reference")
    context = _context(refs[0])
    ref["context"] = context
    if any(type(value) is not str or not value for value in context.values()):
        return unavailable("incomplete reference context")
    selected, excluded, ref_ids, instants = [], Counter(), set(), set()
    for row in refs:
        if _context(row) != context:
            return unavailable("incomparable explicit reference context")
        identity = row.get("id")
        if type(identity) is not str or not identity or identity in ref_ids or identity in identities:
            return unavailable("missing, duplicate or monitoring/reference identity overlap")
        ref_ids.add(identity)
        try:
            when, other_basis = _time(row)
        except (ValueError, TypeError, OverflowError):
            return unavailable("invalid reference timing")
        if other_basis != basis:
            return unavailable("mixed reference temporal bases")
        if _precision(row) != monitor_precision:
            return unavailable("mixed reference/monitoring precision")
        if when >= boundary:
            excluded["not strictly before first monitor"] += 1
            continue
        if row.get("quality") != "usable" or not finite(row.get("value")):
            excluded["unusable reference"] += 1
            continue
        if row.get("provenance") not in {"recorded", "synthetic"}:
            return unavailable("unsupported reference provenance")
        if when in instants:
            return unavailable("ambiguous equal-time reference evidence")
        instants.add(when)
        selected.append((when, identity, row))
    selected.sort(key=lambda item: (item[0], item[1]))
    ref.update(count=len(selected), reference_ids=[item[1] for item in selected],
               reference_dates=[item[2]["observed_at"] for item in selected],
               exclusions=dict(excluded), monitoring_boundary=ordered[0][2]["observed_at"],
               time_precision=monitor_precision,
               provenance=sorted({item[2]["provenance"] for item in selected}))
    if len(selected) < min_count:
        return unavailable("insufficient earlier comparable usable reference observations")
    try:
        average = mean(item[2]["value"] for item in selected)
        sd = stdev(item[2]["value"] for item in selected)
    except (ValueError, OverflowError):
        return unavailable("nonfinite reference arithmetic")
    if not finite(average) or not finite(sd):
        return unavailable("nonfinite reference arithmetic")
    ref.update(mean=average, sd=sd)
    if sd <= 0:
        return unavailable("zero reference scale")
    ref.update(status="available", reason="frozen earlier reference available")
    counts = Counter(when for when, _, _ in ordered)
    plus = minus = 0.0
    previous = None
    for when, identity, row in ordered:
        reasons = []
        problem = None
        if counts[when] > 1:
            problem = "ambiguous equal-time monitoring evidence"
        elif _context(row) != context:
            problem = "monitoring context changed"
        elif row.get("quality") != "usable" or not finite(row.get("value")):
            problem = "monitoring measurement unusable"
        elif row.get("provenance") not in {"recorded", "synthetic"}:
            problem = "unsupported monitoring provenance"
        if max_gap_seconds is not None and previous is not None and (when - previous).total_seconds() > max_gap_seconds:
            reasons.append("configured gap exceeded")
        if problem:
            reasons.append(problem)
        if reasons:
            plus = minus = 0.0
        point = {"id": identity, "observed_at": row["observed_at"],
                 "status": "unavailable" if problem else "available", "reason": problem,
                 "reset_reasons": reasons, "context": _context(row),
                 "provenance": row.get("provenance"), "quality": row.get("quality"),
                 "reference_ids": list(ref["reference_ids"]),
                 "zscore": None, "splus": None, "sminus": None, "flagged": None,
                 "positive_flag": None, "negative_flag": None}
        if not problem:
            try:
                z = (row["value"] - average) / sd
                new_plus, new_minus = max(0., plus + z - k), max(0., minus - z - k)
                if not all(finite(value) for value in (z, new_plus, new_minus)):
                    raise ValueError("nonfinite monitoring arithmetic")
            except (ValueError, OverflowError):
                plus = minus = 0.0
                point.update(status="unavailable", reason="nonfinite monitoring arithmetic")
                point["reset_reasons"].append("nonfinite monitoring arithmetic")
            else:
                plus, minus = new_plus, new_minus
                point.update(zscore=z, splus=plus, sminus=minus,
                             positive_flag=plus > h, negative_flag=minus > h,
                             flagged=plus > h or minus > h,
                             reason="descriptive threshold exceeded" if plus > h or minus > h else "threshold not exceeded")
        previous = when
        result["trace"].append(point)
    available = sum(point["status"] == "available" for point in result["trace"])
    result.update(status="available" if available else "unavailable",
                  reason="trace includes explicit resets/unavailable points" if available != len(ordered) else "frozen-reference trace available")
    return result
