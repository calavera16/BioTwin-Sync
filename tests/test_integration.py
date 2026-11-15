import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from biotwin.__main__ import demo
from biotwin.simulation import replay, synthetic_fixture
from scripts import download_nlsp as downloader


class IntegratedDemo(unittest.TestCase):
    def test_part_a_calibrates_authored_rule_and_part_b_selects_checklist(self):
        fixture = synthetic_fixture()
        reviewed = fixture["part_a"]["reviewed"]
        changed = fixture["part_a"]["changed"]
        self.assertEqual(reviewed["reference"]["count"], 6)
        self.assertFalse(reviewed["flagged"])
        self.assertTrue(changed["flagged"])
        protocol = fixture["events"][2]["packet"]["protocol"]
        self.assertEqual(protocol["conditions"]["A"][0]["value"], reviewed["reference"]["reference_interval"][1])
        final = replay(fixture["events"], fixture["cutoffs"][-1])
        self.assertEqual(final["crew"]["evaluation"]["outcome"], "B")
        self.assertEqual(final["crew"]["evaluation"]["comparisons"][0]["reviewed_value"], 420)
        self.assertEqual(final["crew"]["evaluation"]["comparisons"][0]["current_value"], 585)
        self.assertTrue(all(o["provenance"] == "synthetic" for o in final["crew"]["current_snapshot"]["payload"]["observations"]))

    def test_exports_are_reproducible_and_escaped(self):
        fixture = synthetic_fixture()
        fixture["events"][2]["packet"]["protocol"]["original_reply"] = "<script>unsafe markup</script>"
        with tempfile.TemporaryDirectory() as temporary:
            with patch("biotwin.__main__.synthetic_fixture", return_value=fixture):
                result = demo(temporary, str(Path(temporary, "absent-real-source")))
            report = Path(result["html"]).read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;unsafe markup&lt;/script&gt;", report)
            self.assertNotIn("<script>", report)
            machine = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
            self.assertEqual(machine["timeline"][-1]["crew"]["evaluation"]["outcome"], "B")
            before = Path(result["json"]).read_bytes()
            with patch("biotwin.__main__.synthetic_fixture", return_value=fixture):
                demo(temporary, str(Path(temporary, "absent-real-source")))
            self.assertEqual(Path(result["json"]).read_bytes(), before)


class FakeResponse(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url

    def geturl(self):
        return self.url


class Downloader(unittest.TestCase):
    def setUp(self):
        self.content = b"column\n1\n"
        self.file_id = "00000000-0000-0000-0000-000000000001"
        self.catalog = {"related": {"data_files": {"total": 1, "items": [{"id": self.file_id,
            "__original_file_name": "example.csv", "file_size": len(self.content), "__file_md5sum": hashlib.md5(self.content).hexdigest()}]}}}
        self.calls = []

    def opener(self, request, timeout):
        self.calls.append(request.full_url)
        content = json.dumps(self.catalog).encode() if "/datasets/" in request.full_url else self.content
        return FakeResponse(content, request.full_url)

    def test_download_resume_and_mismatch_refusal_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, manifest = Path(temporary, "data/raw/nlsp"), Path(temporary, "report.json")
            with patch.dict(downloader.CATALOGS, {"test": ("00000000-0000-0000-0000-000000000002", 1)}, clear=True):
                first = downloader.download(destination, manifest, self.opener)
                self.assertEqual(first[0]["status"], "downloaded")
                self.assertEqual(len(self.calls), 2)
                second = downloader.download(destination, manifest, self.opener)
                self.assertEqual(second[0]["status"], "verified-existing")
                self.assertEqual(len(self.calls), 3)  # catalog only on resume
                target = destination / "test/example.csv"
                target.write_bytes(b"bad bytes")
                with self.assertRaises(ValueError):
                    downloader.download(destination, manifest, self.opener)
                self.assertEqual(target.read_bytes(), b"bad bytes")

    def test_incomplete_pagination_and_unsafe_names(self):
        bad = copy.deepcopy(self.catalog)
        bad["related"]["data_files"]["total"] = 2
        with self.assertRaisesRegex(ValueError, "pagination"):
            downloader.catalog_items(bad, 1)
        for name in ("../keys.csv", "a/b.csv", "C:\\keys.csv", ".env", "a..csv"):
            bad = copy.deepcopy(self.catalog)
            bad["related"]["data_files"]["items"][0]["__original_file_name"] = name
            with self.assertRaises(ValueError):
                downloader.catalog_items(bad, 1)

    def test_hash_mismatch_and_redirect_rejected(self):
        metadata = {"bytes": len(self.content), "md5": "0" * 32, "filename": "example.csv"}
        with self.assertRaises(ValueError):
            downloader._verify(self.content, metadata)
        with self.assertRaises(ValueError):
            downloader._fetch("https://nlsp.nasa.gov/api/test", 100, lambda *args, **kwargs: FakeResponse(b"x", "https://unreviewed.example/file"))
        with self.assertRaises(ValueError):
            downloader._fetch("https://nlsp.nasa.gov/api/test", 1, lambda *args, **kwargs: FakeResponse(b"xx", "https://nlsp.nasa.gov/api/test"))
