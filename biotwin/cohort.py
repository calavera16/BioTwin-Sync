"""Deterministic raw-value DTW analog retrieval, not medical similarity."""
from collections import Counter, defaultdict
from .data import CONTEXT, comparable, finite, timestamp


def _dtw(a, b):
    # Unconstrained paths for <=5 points. Minimize total absolute raw difference;
    # ties prefer diagonal, then vertical, then horizontal; divide by path length.
    table = {(0, 0): (0.0, 0)}
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            options = [(table[p], rank) for rank, p in enumerate(((i-1, j-1), (i-1, j), (i, j-1))) if p in table]
            (cost, length), _ = min(options, key=lambda item: (item[0][0], item[1]))
            table[i, j] = (cost + abs(a[i-1] - b[j-1]), length + 1)
    cost, length = table[len(a), len(b)]
    return cost / length


def find_similar(observations, query_subject, metric, as_of, window=5, top_k=3, context=None, max_gap_days=45):
    if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 5:
        raise ValueError("window must be 1..5")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 3:
        raise ValueError("top_k must be 1..3")
    if not finite(max_gap_days) or max_gap_days <= 0:
        raise ValueError("max_gap_days must be positive")
    cutoff, basis = timestamp(as_of)
    observations = list(observations)
    excluded = Counter()
    eligible = []
    for row in observations:
        if row.get("metric") != metric:
            continue
        try:
            when, row_basis = timestamp(row.get("observed_at"))
        except (ValueError, TypeError):
            excluded["invalid timing"] += 1
            continue
        if basis != row_basis:
            excluded["mixed temporal basis"] += 1
        elif when > cutoff:
            excluded["future"] += 1
        elif row.get("quality") != "usable" or not finite(row.get("value")):
            excluded["unusable"] += 1
        else:
            eligible.append(row)
    query = [o for o in eligible if o.get("subject_id") == query_subject]
    result = {"status": "unavailable", "reason": "no comparable query history", "matches": [],
              "candidate_count": 0, "exclusions": dict(excluded), "query": [],
              "configuration": {"window": window, "top_k": top_k, "max_gap_days": max_gap_days,
                  "distance": "raw absolute DTW cost / chosen path length; unconstrained; diagonal-first ties"}}
    if context is not None:
        query = [o for o in query if all(o.get(k) == v for k, v in context.items())]
    if not query:
        return result
    latest_time = max(timestamp(o["observed_at"])[0] for o in query)
    latest = [o for o in query if timestamp(o["observed_at"])[0] == latest_time]
    if len({tuple(o[k] for k in CONTEXT) for o in latest}) != 1:
        result["reason"] = "ambiguous latest query context"
        return result
    anchor = latest[0]
    groups = defaultdict(list)
    for row in eligible:
        if comparable(row, anchor):
            groups[row["subject_id"]].append(row)
        else:
            excluded["incompatible context"] += 1
    def trajectory(rows):
        rows = sorted(rows, key=lambda o: (timestamp(o["observed_at"])[0], o["id"]))
        if not rows:
            return [], None
        # Include every row tied at the retained window's oldest instant before
        # checking ambiguity. An ID sort must not hide its conflicting twin.
        oldest = timestamp(rows[max(0, len(rows) - window)]["observed_at"])[0]
        rows = [o for o in rows if timestamp(o["observed_at"])[0] >= oldest]
        if len({timestamp(o["observed_at"])[0] for o in rows}) != len(rows):
            return [], "ambiguous equal-time trajectory"
        if any((timestamp(b["observed_at"])[0] - timestamp(a["observed_at"])[0]).total_seconds() > max_gap_days * 86400 for a, b in zip(rows, rows[1:])):
            return [], "trajectory gap exceeds limit"
        return rows, None
    query, error = trajectory(groups.pop(query_subject, []))
    result["query"] = [{"id": o["id"], "observed_at": o["observed_at"], "value": o["value"]} for o in query]
    result["query_count"] = len(query)
    if error or not query:
        result["reason"] = error or "no comparable query history"
        return result
    candidates = []
    for subject, rows in sorted(groups.items()):
        trajectory_rows, error = trajectory(rows)
        if error:
            excluded[error] += 1
            continue
        distance = _dtw([o["value"] for o in query], [o["value"] for o in trajectory_rows])
        if not finite(distance):
            excluded["nonfinite distance"] += 1
            continue
        candidates.append({"subject_id": subject, "distance": distance, "count": len(trajectory_rows),
            "comparison_kind": "point comparison" if min(len(query), len(trajectory_rows)) == 1 else "short trajectory comparison",
            "observations": [{"id": o["id"], "observed_at": o["observed_at"], "value": o["value"]} for o in trajectory_rows],
            "context": {k: anchor[k] for k in CONTEXT}})
    result.update(status="available" if candidates else "unavailable", reason="available" if candidates else "no comparable other participants",
                  candidate_count=len(candidates), exclusions=dict(sorted(excluded.items())),
                  matches=sorted(candidates, key=lambda c: (c["distance"], c["subject_id"]))[:top_k])
    return result
