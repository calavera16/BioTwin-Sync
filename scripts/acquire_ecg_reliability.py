"""Bounded PhysioNet-only acquisition, separate from any experiment execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import stat
import urllib.request

MITDB = tuple('100 101 102 103 104 105 106 107 108 109 111 112 113 114 115 116 117 118 119 121 122 123 124 200 201 202 203 205 207 208 209 210 212 213 214 215 217 219 220 221 222 223 228 230 231 232 233 234'.split())
NSTDB = tuple(f'{subject}e{snr}' for subject in ('118', '119') for snr in ('24', '18', '12', '06', '00', '_6'))
MAX_BYTES = {'hea': 20_000, 'atr': 200_000, 'dat': 3_000_000}


def allowed_files():
    return [(dataset, record, suffix) for dataset, records in (('mitdb', MITDB), ('nstdb', NSTDB))
            for record in records for suffix in ('hea', 'dat', 'atr')]


def source_url(dataset, record, suffix):
    if (dataset, record, suffix) not in allowed_files():
        raise ValueError('not an allowlisted dataset record/file')
    return f'https://physionet.org/files/{dataset}/1.0.0/{record}.{suffix}'


def paired_mirror_url(dataset, record, suffix):
    source_url(dataset, record, suffix)  # Validate exact allowlisted identity first.
    return f'https://physionet-open.s3.amazonaws.com/{dataset}/1.0.0/{record}.{suffix}'


class ExactSourceRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed):
        super().__init__()
        self.allowed = allowed

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if newurl not in self.allowed:
            raise ValueError('redirect outside exact official/paired-mirror source URL')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def safe_root(path):
    path = Path(path).absolute()
    if any(part.lower() == '.env' or part.lower().startswith('.env.') for part in path.parts):
        raise ValueError('credential path forbidden')
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise ValueError('symlink/junction paths forbidden')
    resolved = path.resolve()
    if any(part.lower() == '.env' or part.lower().startswith('.env.') for part in resolved.parts):
        raise ValueError('resolved credential path forbidden')
    return resolved


def acquire(output):
    root = safe_root(output)
    manifest = {'schema_version': 1, 'datasets': {'mitdb': '1.0.0', 'nstdb': '1.0.0'}, 'files': []}
    for dataset, record, suffix in allowed_files():
        destination = safe_root(root / dataset / f'{record}.{suffix}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or destination.resolve().parent != (root/dataset).resolve():
            raise ValueError('unexpected acquisition path')
        url = source_url(dataset, record, suffix)
        allowed_urls = {url, paired_mirror_url(dataset, record, suffix)}
        exists = destination.exists()
        print(f'{"check" if exists else "download"} {dataset}/{record}.{suffix}', file=sys.stderr, flush=True)
        primary_url = paired_mirror_url(dataset, record, suffix) if dataset == 'mitdb' else url
        request = urllib.request.Request(primary_url, method='HEAD' if exists else 'GET',
                                         headers={'User-Agent': 'BioTwin-research-acquisition/1'})
        opener = urllib.request.build_opener(ExactSourceRedirect(allowed_urls))
        try:
            with opener.open(request, timeout=60) as response:
                resolved_url = response.geturl()
                if resolved_url not in allowed_urls:
                    raise ValueError('redirect outside exact acquisition URLs')
                length = response.headers.get('Content-Length')
                if length is None or not length.isdecimal() or not 1 <= int(length) <= MAX_BYTES[suffix]:
                    raise ValueError('complete bounded Content-Length required')
                expected = int(length)
                blob = None if exists else response.read(expected+1)
        except Exception as exc:
            raise ValueError(f'acquisition failed for {dataset}/{record}.{suffix}: {exc}') from exc
        if exists:
            if destination.stat().st_size != expected:
                raise ValueError('existing source incomplete; preserve and investigate, never overwrite')
        else:
            if len(blob) != expected:
                raise ValueError('incomplete or oversized source download')
            # Same-directory hard-link publication is atomic and refuses replacement.
            # A killed process can leave a temp file but never a partial final source.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=record+'.download-', delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(blob)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temporary, destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        size = destination.stat().st_size
        if not 1 <= size <= MAX_BYTES[suffix]:
            raise ValueError('existing source has invalid size')
        manifest['files'].append({'path': f'{dataset}/{record}.{suffix}', 'dataset': dataset,
                                  'record': record, 'suffix': suffix, 'url': url, 'resolved_url': resolved_url, 'bytes': size,
                                  'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    path = safe_root(root/'source-manifest.json')
    if path.exists():
        if path.is_symlink() or json.loads(path.read_text(encoding='utf-8')) != manifest:
            raise ValueError('existing manifest differs; preserve prior evidence')
        return manifest
    with path.open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2, allow_nan=False)
        handle.write('\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    manifest = acquire(args.output)
    print(json.dumps({'files': len(manifest['files']), 'bytes': sum(f['bytes'] for f in manifest['files'])}))


if __name__ == '__main__':
    main()
