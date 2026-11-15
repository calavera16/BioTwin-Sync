"""NASA CSV normalization. Unknown source units stay explicitly unverified."""
from __future__ import annotations

import csv
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

CONTEXT = ("source_id", "metric", "unit", "phase", "method")
WINSCAT_METRICS = ("CPTRT", "CPTA", "CPTL", "MTHRT", "MTHA", "MTSRT", "MTSA", "CDDRT", "CDDA")


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timestamp(value):
    """Return (datetime, temporal basis); never attach an invented timezone."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    basis = "day" if len(value) == 10 else ("aware" if dt.tzinfo and dt.utcoffset() is not None else "source-local")
    return dt, basis


def comparable(left, right, personal=False):
    return all(left.get(k) == right.get(k) and left.get(k) not in (None, "") for k in CONTEXT) and (not personal or left.get("subject_id") == right.get("subject_id"))


def _number(raw):
    try:
        value = float(raw)
        if math.isfinite(value):
            return value, None
    except (ValueError, TypeError):
        pass
    return None, "missing or nonfinite source cell"


def _source_time(row):
    raw = row.get("Date", "")
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            day = datetime.strptime(raw, fmt)
            if row.get("Time"):
                clock = datetime.strptime(row["Time"], "%H:%M:%S").time()
                return datetime.combine(day.date(), clock).isoformat(), "second", None
            return day.date().isoformat(), "day", None
        except (ValueError, TypeError):
            continue
    return None, "unknown", "invalid source date/time"


def _audit(observations, files, rows, issues):
    return {"files": files, "rows": rows, "observations": len(observations),
            "participants": len({o["subject_id"] for o in observations}),
            "phases": dict(sorted(Counter(o["phase"] for o in observations).items(), key=lambda item: str(item[0]))),
            "quality": dict(sorted(Counter(o["quality"] for o in observations).items())),
            "methods": dict(sorted(Counter(o["method"] for o in observations).items(), key=lambda item: str(item[0]))),
            "issues": issues, "timezone": "source-local-unspecified"}


def load_winscat(folder):
    """Return (observations, audit). One observation per source row/metric.

    Subject IDs retain filename zero padding and Campaign; original cells remain
    in metadata. Units are unverified source units until a column dictionary is
    reviewed. Nasa1 and Nasa1p remain different methods.
    """
    observations, issues, rows = [], [], 0
    paths = sorted(Path(folder).glob("*.csv"))
    if not paths:
        raise ValueError(f"no WinSCAT CSV files in {folder}")
    for path in paths:
        match = re.fullmatch(r"MR085G_Campaign_3_WinSCAT_(C3[A-Z])_(\d+)\.csv", path.name)
        if not match:
            raise ValueError(f"unexpected WinSCAT filename: {path.name}")
        campaign, subject = match.groups()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"ID", "Campaign", "Subject", "Test", "Test_Phase", "BR_Day", "Date", "Time", *WINSCAT_METRICS}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"missing WinSCAT columns: {path.name}")
            seen = set()
            for line, row in enumerate(reader, 2):
                rows += 1
                errors = [f"missing required source cell: {column}"
                          for column in sorted(required - set(WINSCAT_METRICS))
                          if row[column] in (None, "")]
                if row["ID"] in seen:
                    errors.append("duplicate source row ID")
                seen.add(row["ID"])
                source_subject = row["Subject"]
                if row["Campaign"] != campaign or not source_subject or not source_subject.isdigit() or int(source_subject) != int(subject):
                    errors.append("filename/row participant mismatch")
                when, precision, error = _source_time(row)
                if error:
                    errors.append(error)
                if not row["Test"] or row["Test_Phase"] not in {"PRE_TEST", "IN_TEST", "POST_TEST"}:
                    errors.append("unsupported test/phase")
                if errors:
                    issues.append({"file": path.name, "line": line, "reasons": errors})
                for metric in WINSCAT_METRICS:
                    value, cell_error = _number(row[metric])
                    reasons = errors + ([cell_error] if cell_error else [])
                    observations.append({"id": f"{path.stem}:{line}:{metric}",
                        "subject_id": f"{campaign}:{subject}", "source_id": f"NLSP:MR085G:Campaign3:{campaign}",
                        "metric": metric, "value": value, "unit": f"source-unit-unverified:{metric}",
                        "observed_at": when, "phase": row["Test_Phase"], "method": row["Test"],
                        "quality": "missing" if value is None else ("review" if errors else "usable"),
                        "provenance": "recorded", "quality_reasons": reasons,
                        "time_precision": precision, "timezone_status": "source-local-unspecified",
                        "source_file": path.name, "source_row": line, "source_date": row["Date"],
                        "source_time": row["Time"], "source_subject": row["Subject"],
                        "source_id_cell": row["ID"], "br_day": row["BR_Day"], "run": row.get("Run"),
                        "unit_status": "unverified"})
    return observations, _audit(observations, len(paths), rows, issues)


def load_bedrest(folder):
    """Generic long records only; no automatic physiological baseline eligibility."""
    observations, issues, rows, paths = [], [], 0, sorted(Path(folder).glob("*.csv"))
    if not paths:
        raise ValueError(f"no BEDREST CSV files in {folder}")
    metadata = {"ID", "Campaign", "Type", "Subject", "Date", "Test_Phase", "BR_Day", "Position", "Time", "Drug", "Dose",
                "Sex", "Day Group", "Day", "PE/SNP", "end time", ""}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for line, row in enumerate(reader, 2):
                rows += 1
                when, precision, error = _source_time(row)
                for column in reader.fieldnames or []:
                    if column in metadata:
                        continue
                    value, cell_error = _number(row[column])
                    unit = re.search(r"\(([^()]*)\)$", column)
                    observations.append({"id": f"{path.stem}:{line}:{column}",
                        "subject_id": f"{row.get('Campaign')}:{row.get('Subject')}",
                        "source_id": f"NLSP:BEDREST0543:Campaign3:{row.get('Campaign')}",
                        "metric": column, "value": value, "unit": unit[1] if unit else "source-unit-unverified",
                        "observed_at": when, "phase": row.get("Test_Phase", "unknown"),
                        "method": "unmapped BEDREST0543 protocol", "quality": "missing" if value is None else "review",
                        "provenance": "recorded", "time_precision": precision,
                        "timezone_status": "source-local-unspecified", "source_file": path.name,
                        "source_row": line, "source_record": row.copy(), "baseline_eligible": False,
                        "quality_reasons": ["protocol/condition mapping unverified"] + [e for e in (error, cell_error) if e]})
    audit = _audit(observations, len(paths), rows, issues)
    audit["raw_identity_pairs"] = audit.pop("participants")
    audit["participant_count_status"] = "unresolved identifiers: source padding varies; raw pairs are not verified unique people"
    return observations, audit


def audit_directory(folder):
    root = Path(folder)
    winscat, win_audit = load_winscat(root / "winscat_campaign3")
    _, bed_audit = load_bedrest(root / "bedrest0543_campaign3")
    from .statistics import reference_summary
    summaries = [reference_summary(winscat, current) for current in winscat]
    reasons = Counter(s["reason"] for s in summaries)
    return {"winscat": win_audit, "bedrest0543": bed_audit,
            "personal_reference_availability": dict(sorted(reasons.items())),
            "limitations": ["WinSCAT units unverified; labels retained verbatim.",
                "Nasa1/Nasa1p and campaign subgroups are kept separate.",
                "Source-local times have no known timezone; not a delayed medical event.",
                "BEDREST protocols are unmapped and ineligible for automatic baselines."]}
