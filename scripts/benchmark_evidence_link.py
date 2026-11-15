"""Explicit offline recorded-data execution; use only after frozen-plan review."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from biotwin.evidence_priority import (DEFAULT_DELAYS, benchmark, canonical_bytes,
                                       load_input, validate_input, write_report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Bounded scalar-series JSON, not raw ECG or a secret file")
    parser.add_argument("--output-dir", required=True, help="New directory under an existing parent; never overwritten")
    parser.add_argument("--selection", required=True, help="Frozen recording IDs/ranges and descriptive-domain status")
    parser.add_argument("--max-source-seconds", type=float, default=600,
                        help="Original-time cutoff measured from source zero (default 600); no time stretching")
    parser.add_argument("--include-stress", action="store_true", help="Add separately labelled 1200-second propagation")
    args = parser.parse_args(argv)
    try:
        if not 0 < args.max_source_seconds <= 7200:
            raise ValueError("max-source-seconds must be finite and in (0, 7200]")
        ledger, input_receipt = load_input(args.input, with_receipt=True)
        selected = {"schema_version": 1, "series": []}
        for item in ledger["series"]:
            copy = {key: value for key, value in item.items() if key != "windows"}
            copy["windows"] = [window for window in item["windows"] if window["availability_seconds"] <= args.max_source_seconds]
            if not copy["windows"]:
                raise ValueError("a named series has no causally available window in the frozen range")
            selected["series"].append(copy)
        selected = validate_input(selected)
        result = benchmark(selected, selection=args.selection,
                           delays=DEFAULT_DELAYS + ((1200,) if args.include_stress else ()))
        result["selection_cutoff_seconds_from_original_zero"] = args.max_source_seconds
        result["input_receipt"] = input_receipt
        result["selection_counts"] = [{"source": before["source"], "subject": before["subject"],
                                       "input_windows": len(before["windows"]), "selected_windows": len(after["windows"]),
                                       "excluded_after_availability_cutoff": len(before["windows"]) - len(after["windows"])}
                                      for before, after in zip(ledger["series"], selected["series"])]
        receipt = write_report(result, selected, args.output_dir)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"evidence-link: {exc}\n")
    print(canonical_bytes(receipt).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
