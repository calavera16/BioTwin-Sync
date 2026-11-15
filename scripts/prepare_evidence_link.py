"""Project the approved six-source rich NASA export without changing observations.

No signal processing, policy selection, benchmark execution or external service.
Hashes bind specified bytes; they do not authenticate the exporter or its sources.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from biotwin import evidence_priority as ep

SOURCE_KEYS = {f"Crew_{crew}/Processing/abm_leftseat_scenario{scenario}.csv"
               for crew, scenario in (("01", 2), ("02", 2), ("03", 2), ("05", 2), ("06", 1), ("10", 2))}
ROOT_FIELDS = {"schema_version", "selection_sha256", "model_sha256", "exporter_sha256",
               "warning", "interpretation", "series"}
RICH_SERIES_FIELDS = {"source", "subject", "split", "time_basis", "source_start_seconds",
                      "source_end_seconds", "url", "sha256", "windows"}
SELECTION_FIELDS = {"schema_version", "id", "selection_basis", "interpretation", "duration_seconds",
                    "duration_rule", "sources"}
SELECTION_SOURCE_FIELDS = {"source_key", "local_path", "url", "bytes", "sha256"}
RICH_WINDOW_FIELDS = ep.WINDOW_FIELDS | ep.WINDOW_OPTIONAL | {
    "beat_count", "secondary_beat_count", "detected_beats_sample_indices", "features",
    "reference_status", "provenance"}
PROJECTED_WINDOW_FIELDS = ep.WINDOW_FIELDS | ep.WINDOW_OPTIONAL


def _hash(value, name):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be an explicit lowercase SHA-256")
    return value


def _object(value, fields, name):
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"unsupported {name} schema")


def _version(value):
    if type(value) is not int or value != 1:
        raise ValueError("unsupported schema version")


def _finite_tree(value, depth=0, counter=None):
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > 24 or counter[0] > 500_000:
        raise ValueError("rich JSON exceeds depth/value bounds")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise ValueError("invalid rich JSON key")
            _finite_tree(item, depth + 1, counter)
    elif type(value) is list:
        for item in value:
            _finite_tree(item, depth + 1, counter)
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite rich JSON value")
    elif type(value) is int:
        if abs(value) > 10**15:
            raise ValueError("rich JSON integer exceeds supported bound")
    elif type(value) is str:
        if len(value) > 100_000:
            raise ValueError("rich JSON string exceeds supported bound")
    elif value is not None and type(value) is not bool:
        raise ValueError("unsupported rich JSON type")


def _load_bound_json(path, expected_sha256, *, limit):
    """Validate a nonsecret regular path before any read/hash; read its bytes once."""
    _hash(expected_sha256, "expected input hash")
    path = ep._safe_path(path, must_exist=True)
    if path.suffix.lower() != ".json" or path.stat().st_size > limit:
        raise ValueError("input must be bounded JSON")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("input grew beyond bounded size")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError("input bytes do not match frozen SHA-256")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=ep._unique_pairs,
                           parse_constant=ep._reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid finite rich JSON") from exc
    _finite_tree(value)
    return value, {"path": str(path), "bytes": len(raw), "sha256": actual,
                   "canonical_sha256": hashlib.sha256(ep.canonical_bytes(value)).hexdigest()}


def _selection(value):
    _object(value, SELECTION_FIELDS, "frozen selection")
    _version(value["schema_version"])
    if value["id"] != "soteria-recorded-first600-v1" or type(value["duration_seconds"]) is not int or value["duration_seconds"] != 600:
        raise ValueError("exact frozen first600 selection is required")
    for name in ("selection_basis", "interpretation", "duration_rule"):
        ep._text(value[name], name, 4096)
    sources = value["sources"]
    if type(sources) is not list or len(sources) != 6:
        raise ValueError("exactly all six frozen sources are required")
    by_key = {}
    for entry in sources:
        _object(entry, SELECTION_SOURCE_FIELDS, "selection source")
        key = entry["source_key"]
        if type(key) is not str or key not in SOURCE_KEYS or key in by_key:
            raise ValueError("source identity differs from exact six-source receipt")
        if entry["local_path"] != "data/raw/soteria/" + key or entry["url"] != "https://nasa-soteria-data.s3.us-west-2.amazonaws.com/" + key:
            raise ValueError("source provenance differs from frozen source")
        if type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= 10**10:
            raise ValueError("invalid source byte count")
        _hash(entry["sha256"], "source hash")
        by_key[key] = entry
    return by_key


def project(rich, selection, *, selection_sha256, exporter_sha256):
    """Pure projection; no filtering, estimation, clock rewrite or gate alteration."""
    _finite_tree(rich)
    _hash(selection_sha256, "selection hash")
    _hash(exporter_sha256, "exporter hash")
    selected = _selection(selection)
    _object(rich, ROOT_FIELDS, "rich NASA export")
    _version(rich["schema_version"])
    if rich["selection_sha256"] != selection_sha256 or rich["exporter_sha256"] != exporter_sha256:
        raise ValueError("rich export is not bound to approved selection/exporter bytes")
    if rich["model_sha256"] is not None:
        _hash(rich["model_sha256"], "model hash")
    for name in ("warning", "interpretation"):
        ep._text(rich[name], name, 100_000)
    items = rich["series"]
    if type(items) is not list or len(items) != 6:
        raise ValueError("rich export must contain all six sources")
    output, details, seen = [], [], set()
    for item in items:
        _object(item, RICH_SERIES_FIELDS, "rich source series")
        source = item["source"]
        if type(source) is not str or not source.startswith("nasa-soteria:"):
            raise ValueError("unsupported NASA source ID")
        key = source.removeprefix("nasa-soteria:")
        if key not in selected or key in seen:
            raise ValueError("missing, duplicate or unselected NASA source")
        seen.add(key)
        entry = selected[key]
        if item["subject"] != key.split("/")[0] or item["sha256"] != entry["sha256"] or item["url"] != entry["url"]:
            raise ValueError("rich source/subject/hash/URL differs from frozen receipt")
        if item["time_basis"] != "source-relative-seconds" or item["split"] != "previously_seen_external_descriptive":
            raise ValueError("NASA source domain or original clock differs")
        start, end = item["source_start_seconds"], item["source_end_seconds"]
        ep._number(start, "source start", minimum=0, maximum=10**9)
        ep._number(end, "source end", minimum=0, maximum=10**9)
        if start != 0 or not 0 < end - start <= 600:
            raise ValueError("source range differs from original-zero first600 selection")
        windows = item["windows"]
        expected_count = math.ceil((end - start) / 30)
        if type(windows) is not list or len(windows) != expected_count:
            raise ValueError("every exporter source window must be retained")
        projected = []
        omitted = set()
        prior_sample_end = 0
        for index, window in enumerate(windows):
            if type(window) is not dict or not ep.WINDOW_FIELDS <= set(window) or set(window) - RICH_WINDOW_FIELDS:
                raise ValueError("unsupported rich NASA window schema")
            if window.get("source_id") != source or window.get("subject_id") != item["subject"]:
                raise ValueError("window identity differs from series")
            if window.get("reference_bpm") is not None or window["learned_accept"] is not False:
                raise ValueError("NASA reference or learned acceptance exceeds exporter domain")
            ep._number(window["start_seconds"], "window start", minimum=0, maximum=600)
            ep._number(window["end_seconds"], "window end", minimum=0, maximum=600)
            ep._number(window["availability_seconds"], "window availability", minimum=0, maximum=600)
            if (not math.isclose(window["start_seconds"], start + index * 30, rel_tol=0, abs_tol=1e-8)
                    or not math.isclose(window["end_seconds"], min(start + (index + 1) * 30, end), rel_tol=0, abs_tol=1e-8)
                    or window["availability_seconds"] > end
                    or window["sample_start"] != prior_sample_end):
                raise ValueError("source windows do not cover the frozen original range unchanged")
            scalar = {name: copy.deepcopy(value) for name, value in window.items() if name in PROJECTED_WINDOW_FIELDS}
            projected.append(scalar)
            prior_sample_end = window["sample_end_exclusive"]
            omitted.update(set(window) - PROJECTED_WINDOW_FIELDS)
        output.append({"source": source, "subject": item["subject"], "split": item["split"],
                       "time_basis": item["time_basis"], "windows": projected})
        details.append({"source": source, "subject": item["subject"], "source_key": key,
                        "source_sha256": item["sha256"], "source_bytes_from_selection": entry["bytes"],
                        "url": item["url"], "source_start_seconds": start, "source_end_seconds": end,
                        "input_windows": len(windows), "output_windows": len(projected), "excluded_windows": 0,
                        "omitted_rich_window_fields": sorted(omitted),
                        "preserved_scalar_window_fields": sorted(set().union(*(set(w) for w in projected)))})
    if seen != SOURCE_KEYS:
        raise ValueError("six-source membership mismatch")
    return ep.validate_input({"schema_version": 1, "series": output}), details


def prepare(input_path, selection_path, output_dir, *, input_sha256, selection_sha256, exporter_sha256):
    """Write only a new directory; bind raw input/selection/output and adapter bytes."""
    destination = ep._safe_path(output_dir)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("a new output directory under an existing parent is required")
    selection, selection_receipt = _load_bound_json(selection_path, selection_sha256, limit=100_000)
    rich, input_receipt = _load_bound_json(input_path, input_sha256, limit=ep.MAX_INPUT_BYTES)
    result, details = project(rich, selection, selection_sha256=selection_sha256, exporter_sha256=exporter_sha256)
    raw = ep.canonical_bytes(result)
    # Only this explicit, nonsecret source file is read for the reproducibility receipt.
    adapter_path = ep._safe_path(__file__, must_exist=True)
    receipt = {"format": "biotwin-evidence-link-adapter-receipt", "schema_version": 1,
               "adapter_sha256": hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
               "input": input_receipt, "selection": selection_receipt,
               "selection_id": selection["id"], "exporter_sha256": exporter_sha256,
               "model_sha256_claim_from_export": rich["model_sha256"],
               "output": {"file": "link-input.json", "bytes": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest(),
                          "canonical_sha256": hashlib.sha256(raw).hexdigest()},
               "series": details, "input_series": len(rich["series"]), "output_series": len(result["series"]),
               "input_windows": sum(d["input_windows"] for d in details),
               "output_windows": sum(d["output_windows"] for d in details),
               "excluded_windows": 0,
               "interpretation": "Projection only; diagnostic values/gates/reasons/nulls/identity/time unchanged. Rich auxiliary fields remain in hash-bound input, not scalar wire format.",
               "integrity_limit": "Hashes bind specified bytes; source files/model are not reread or independently authenticated by this adapter."}
    receipt_raw = ep.canonical_bytes(receipt)
    destination.mkdir(exist_ok=False)
    with (destination / "link-input.json").open("xb") as stream:
        stream.write(raw)
    with (destination / "preparation-receipt.json").open("xb") as stream:
        stream.write(receipt_raw)
    # Verify actual persisted bytes and the strict runtime schema without changing them.
    actual = ep.load_input(destination / "link-input.json")
    if actual != result or hashlib.sha256((destination / "link-input.json").read_bytes()).hexdigest() != receipt["output"]["sha256"]:
        raise ValueError("saved adapter input failed verification")
    if (destination / "preparation-receipt.json").read_bytes() != receipt_raw:
        raise ValueError("saved adapter receipt failed verification")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Approved rich nasa-series.json; no raw ECG/secret files")
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--exporter-sha256", required=True, help="Reviewed exporter source SHA-256")
    parser.add_argument("--output-dir", required=True, help="New output directory; never overwritten")
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.input, args.selection, args.output_dir, input_sha256=args.input_sha256,
                          selection_sha256=args.selection_sha256, exporter_sha256=args.exporter_sha256)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"prepare-evidence-link: {exc}\n")
    print(ep.canonical_bytes({"output_dir": str(Path(args.output_dir).absolute()),
                             "series": receipt["output_series"], "windows": receipt["output_windows"],
                             "output_sha256": receipt["output"]["sha256"]}).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
