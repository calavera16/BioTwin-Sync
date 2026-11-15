"""Synthetic adapter checks; no recorded estimates or waveform files are opened."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from biotwin import evidence_priority as ep
from scripts import prepare_evidence_link as adapter


def fixtures():
    sources, items = [], []
    for index, key in enumerate(sorted(adapter.SOURCE_KEYS)):
        source, subject = "nasa-soteria:" + key, key.split("/")[0]
        digest = str(index) * 64
        url = "https://nasa-soteria-data.s3.us-west-2.amazonaws.com/" + key
        sources.append({"source_key": key, "local_path": "data/raw/soteria/" + key,
                        "url": url, "bytes": 1234, "sha256": digest})
        rows = []
        for i in range(2):
            rows.append({"source_id": source, "subject_id": subject, "sample_start": i * 7680,
                         "sample_end_exclusive": (i + 1) * 7680,
                         "start_seconds": i * 30, "end_seconds": (i + 1) * 30,
                         "availability_seconds": 32 if i == 0 else 60,
                         "estimate_bpm": 72.5 if i == 0 else None,
                         "status": "experimental_review" if i == 0 else "unavailable",
                         "reasons": ["synthetic <reason> & unicode Ω", "external source"],
                         "rules_accept": i == 0, "learned_accept": False,
                         "error_score": 0.25 if i == 0 else None, "reference_bpm": None,
                         "beat_count": 36 if i == 0 else None,
                         "features": {"synthetic_feature": .25} if i == 0 else None,
                         "reference_status": "unavailable: synthetic no independent annotation",
                         "provenance": {"nominal_rate_hz": 256, "context_sample_start": 0,
                                        "context_sample_end_exclusive": 8192 if i == 0 else 15360}})
        items.append({"source": source, "subject": subject, "split": "previously_seen_external_descriptive",
                      "time_basis": "source-relative-seconds", "source_start_seconds": 0,
                      "source_end_seconds": 60, "url": url, "sha256": digest, "windows": rows})
    selection = {"schema_version": 1, "id": "soteria-recorded-first600-v1",
                 "selection_basis": "synthetic test only", "interpretation": "synthetic test only",
                 "duration_seconds": 600, "duration_rule": "original first600 or available",
                 "sources": sources}
    selection_raw = json.dumps(selection, ensure_ascii=False).encode("utf-8")
    selection_hash = hashlib.sha256(selection_raw).hexdigest()
    exporter_hash = "a" * 64
    rich = {"schema_version": 1, "selection_sha256": selection_hash,
            "model_sha256": None, "exporter_sha256": exporter_hash,
            "warning": "synthetic; no physiological truth", "interpretation": "synthetic test only",
            "series": items}
    return selection, selection_raw, rich, exporter_hash


class PrepareEvidenceLinkTests(unittest.TestCase):
    def test_projection_preserves_every_scalar_window_and_source_without_mutation(self):
        selection, selection_raw, rich, exporter_hash = fixtures()
        original = copy.deepcopy(rich)
        result, details = adapter.project(rich, selection, selection_sha256=hashlib.sha256(selection_raw).hexdigest(),
                                          exporter_sha256=exporter_hash)
        self.assertEqual(rich, original)
        self.assertEqual(len(result["series"]), 6)
        for before, after in zip(rich["series"], result["series"]):
            self.assertEqual(before["source"], after["source"])
            self.assertEqual(before["subject"], after["subject"])
            self.assertEqual(len(before["windows"]), len(after["windows"]))
            for old, new in zip(before["windows"], after["windows"]):
                self.assertEqual(new, {k: v for k, v in old.items() if k in adapter.PROJECTED_WINDOW_FIELDS})
        self.assertTrue(all(d["excluded_windows"] == 0 for d in details))
        self.assertTrue(all("features" in d["omitted_rich_window_fields"] for d in details))

    def test_selection_membership_hash_binding_identity_and_clock_are_required(self):
        selection, selection_raw, rich, exporter_hash = fixtures()
        digest = hashlib.sha256(selection_raw).hexdigest()
        mutations = [lambda x: x.update(selection_sha256="b" * 64),
                     lambda x: x.update(exporter_sha256="b" * 64),
                     lambda x: x["series"].pop(),
                     lambda x: x["series"].__setitem__(1, copy.deepcopy(x["series"][0])),
                     lambda x: x["series"][0].update(sha256="f" * 64),
                     lambda x: x["series"][0].update(subject="invented"),
                     lambda x: x["series"][0].update(time_basis="UTC"),
                     lambda x: x["series"][0].update(source_start_seconds=1),
                     lambda x: x["series"][0]["windows"].pop(),
                     lambda x: x["series"][0]["windows"][0].update(learned_accept=True),
                     lambda x: x["series"][0]["windows"][0].update(reference_bpm=70),
                     lambda x: x["series"][0]["windows"][0].update(availability_seconds=29)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(rich)
                mutate(changed)
                with self.assertRaises(ValueError):
                    adapter.project(changed, selection, selection_sha256=digest, exporter_sha256=exporter_hash)

    def test_finite_rich_auxiliary_values_and_strict_schema(self):
        selection, selection_raw, rich, exporter_hash = fixtures()
        for bad in (float("nan"), float("inf")):
            changed = copy.deepcopy(rich)
            changed["series"][0]["windows"][0]["features"]["synthetic_feature"] = bad
            with self.assertRaises(ValueError):
                adapter.project(changed, selection, selection_sha256=hashlib.sha256(selection_raw).hexdigest(), exporter_sha256=exporter_hash)
        changed = copy.deepcopy(rich)
        changed["series"][0]["windows"][0]["new_unreviewed_field"] = 1
        with self.assertRaises(ValueError):
            adapter.project(changed, selection, selection_sha256=hashlib.sha256(selection_raw).hexdigest(), exporter_sha256=exporter_hash)

    def test_exclusive_save_hash_receipt_and_no_earlier_result_overwrite(self):
        selection, selection_raw, rich, exporter_hash = fixtures()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "selection.json").write_bytes(selection_raw)
            raw = json.dumps(rich, ensure_ascii=False).encode("utf-8")
            (root / "rich.json").write_bytes(raw)
            options = {"input_sha256": hashlib.sha256(raw).hexdigest(),
                       "selection_sha256": hashlib.sha256(selection_raw).hexdigest(),
                       "exporter_sha256": exporter_hash}
            receipt = adapter.prepare(root / "rich.json", root / "selection.json", root / "prepared", **options)
            actual_raw = (root / "prepared/link-input.json").read_bytes()
            self.assertEqual(receipt["output"]["sha256"], hashlib.sha256(actual_raw).hexdigest())
            self.assertEqual(receipt["output_windows"], 12)
            self.assertEqual(receipt["input_windows"], receipt["output_windows"])
            self.assertEqual(receipt, json.loads((root / "prepared/preparation-receipt.json").read_text(encoding="utf-8")))
            ep.load_input(root / "prepared/link-input.json")
            with self.assertRaises(ValueError):
                adapter.prepare(root / "rich.json", root / "selection.json", root / "prepared", **options)
            bad_options = dict(options, input_sha256="c" * 64)
            with self.assertRaises(ValueError):
                adapter.prepare(root / "rich.json", root / "selection.json", root / "wrong-hash", **bad_options)
            self.assertFalse((root / "wrong-hash").exists())

    def test_secret_duplicate_oversize_and_traversal_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ValueError):
                adapter._load_bound_json(root / ".env.json", "a" * 64, limit=100)
            source = root / "duplicate.json"
            raw = b'{"schema_version":1,"schema_version":1}'
            source.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(ValueError):
                adapter._load_bound_json(source, digest, limit=100)
            with self.assertRaises(ValueError):
                adapter._load_bound_json(source, digest, limit=10)
            with self.assertRaises(ValueError):
                adapter._load_bound_json(root / "missing/../duplicate.json", digest, limit=100)


if __name__ == "__main__":
    unittest.main()
