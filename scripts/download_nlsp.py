"""Bounded public NLSP download/resume; never overwrite mismatched raw files.

Run from the repository root. Only the two reviewed Campaign 3 catalogs and
their enumerated public file endpoints are used. No credentials or extra data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from pathlib import Path

ROOT = "https://nlsp.nasa.gov"
CATALOGS = {"winscat_campaign3": ("331549a1-8928-52d6-8bb7-00df9ecb1e55", 26),
            "bedrest0543_campaign3": ("0f2faf2f-5c58-5214-93c7-a8d23c77b3ba", 15)}
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024


def _fetch(url, limit, opener):
    request = urllib.request.Request(url, headers={"User-Agent": "BioTwinSync-public-data-audit/0.1"})
    with opener(request, timeout=30) as response:
        if response.geturl().split("/api/")[0] != ROOT:
            raise ValueError("unexpected download redirect host")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("response exceeds bounded download size")
    return data


def catalog_items(catalog, expected_count):
    try:
        related = catalog["related"]["data_files"]
        items = related["items"]
        if type(items) is not list or type(related["total"]) is not int or related["total"] != len(items) or len(items) != expected_count:
            raise ValueError("catalog total/pagination changed; stop for review")
        ids, names, normalized = set(), set(), []
        for item in items:
            identity, name, size = item["id"], item["__original_file_name"], item["file_size"]
            digest = str(item["__file_md5sum"]).lower().zfill(32)
            if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f-]{36}", identity):
                raise ValueError("unsafe file ID")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_(). -]+\.csv", name) or Path(name).name != name or ".." in name:
                raise ValueError("unsafe source filename")
            if type(size) is not int or not 0 < size <= MAX_FILE_BYTES or not re.fullmatch(r"[0-9a-f]{32}", digest):
                raise ValueError("invalid source size/hash")
            if identity in ids or name in names:
                raise ValueError("duplicate catalog identity/name")
            ids.add(identity)
            names.add(name)
            normalized.append({"id": identity, "filename": name, "bytes": size, "md5": digest})
        return sorted(normalized, key=lambda item: item["filename"])
    except (KeyError, TypeError) as exc:
        raise ValueError("incomplete catalog metadata") from exc


def _verify(data, metadata):
    if len(data) != metadata["bytes"] or hashlib.md5(data).hexdigest() != metadata["md5"]:
        raise ValueError(f"source size/MD5 mismatch: {metadata['filename']}")
    return hashlib.sha256(data).hexdigest()


def download(destination="data/raw/nlsp", manifest="outputs/nlsp-resume-manifest.json", opener=urllib.request.urlopen):
    """Fetch catalogs, verify existing files, download only missing files.

    Optional original project SHA-256 manifest is checked when present. Catalog
    snapshots/raw originals are never overwritten. A new verification report is
    written separately under outputs by default.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    original_manifest = destination.parents[1] / "nlsp-download-manifest.json"
    known = {}
    if original_manifest.is_file():
        for row in json.loads(original_manifest.read_text(encoding="utf-8")):
            known[(row["dataset"], row["source_filename"])] = row["sha256"]
    results = []
    for dataset, (identity, count) in CATALOGS.items():
        url = f"{ROOT}/api/v1/data/datasets/{identity}/full"
        raw_catalog = _fetch(url, MAX_CATALOG_BYTES, opener)
        catalog = json.loads(raw_catalog.decode("utf-8"))
        items = catalog_items(catalog, count)
        catalog_path = destination / f"{dataset}_catalog.json"
        if catalog_path.exists():
            if catalog_items(json.loads(catalog_path.read_text(encoding="utf-8")), count) != items:
                raise ValueError("catalog file listing changed; preserving original and stopping for review")
        else:
            with catalog_path.open("xb") as handle:
                handle.write(raw_catalog)
        folder = destination / dataset
        folder.mkdir(exist_ok=True)
        for metadata in items:
            target = folder / metadata["filename"]
            url = f"{ROOT}/api/v1/files/{metadata['id']}/download"
            if target.exists():
                if target.is_symlink() or not target.is_file() or target.stat().st_size != metadata["bytes"]:
                    raise ValueError(f"existing file mismatch; refusing overwrite: {target.name}")
                content, status = target.read_bytes(), "verified-existing"
            else:
                content, status = _fetch(url, MAX_FILE_BYTES, opener), "downloaded"
            digest = _verify(content, metadata)
            expected = known.get((dataset, metadata["filename"]))
            if expected and expected != digest:
                raise ValueError(f"recorded SHA-256 mismatch; refusing overwrite: {target.name}")
            if status == "downloaded":
                with tempfile.NamedTemporaryFile(dir=folder, prefix="nlsp-", suffix=".part", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                try:
                    # Windows rename fails if target appeared concurrently.
                    temporary.rename(target)
                finally:
                    temporary.unlink(missing_ok=True)
            results.append({"dataset": dataset, "catalog_id": identity, "file_id": metadata["id"],
                "source_filename": metadata["filename"], "local_path": target.as_posix(), "bytes": len(content),
                "sha256": digest, "source_md5": metadata["md5"], "source_md5_verified": True, "status": status, "url": url})
    report = Path(manifest)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default="data/raw/nlsp")
    parser.add_argument("--manifest", default="outputs/nlsp-resume-manifest.json")
    args = parser.parse_args()
    try:
        result = download(args.destination, args.manifest)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"NLSP download stopped: {exc}\n")
    print(json.dumps({"files": len(result), "bytes": sum(r["bytes"] for r in result), "manifest": args.manifest}, indent=2))


if __name__ == "__main__":
    main()
