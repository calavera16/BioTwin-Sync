"""Bounded public WinSCAT retrieval, separate from synthetic crew monitoring.

No network occurs at import, discovery or load. Call fetch_reference_library only
from an explicit operator action, outside the GUI thread. No health data is sent.
The immutable metadata asset names exactly the 26 previously verified files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MANIFEST_SHA256 = '4178d5ede26c39a280bd897a934e61ac9c2e69065101451ca83b7bac7ca4293b'
TOTAL_BYTES = 28_204
LIMITATIONS = [
    'Separate NASA historical reference cases; synthetic crew observations are not matched to these people.',
    'Source units unverified; source-local dates have no known timezone.',
    'Short trajectory retrieval is descriptive, not clinical similarity or a diagnosis.',
    'Personal references require five earlier comparable observations; that rule is unchanged.',
    'Public source downloads; raw NLSP records are not bundled with the application.',
]


def _safe_path(path):
    path = Path(path).absolute()
    if any(p.casefold() in ('.env', '..') or p.casefold().startswith('.env.') for p in path.parts):
        raise ValueError('Secret-file and parent traversal paths are unsupported.')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Linked and junction paths are unsupported.')
    return path


def _manifest():
    from .desktop_assets import asset_directory
    path = _safe_path(asset_directory()/'winscat-reference-manifest.json')
    if not path.is_file() or path.stat().st_size > 20_000:
        raise ValueError('NASA reference metadata unavailable.')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('NASA reference metadata integrity mismatch.')
    entries = json.loads(raw)['files']
    if len(entries) != 26 or sum(e['bytes'] for e in entries) != TOTAL_BYTES:
        raise ValueError('NASA reference allowlist bound mismatch.')
    return entries


def default_reference_cache():
    """Machine-local app cache, never a synced project or raw-data package."""
    local = os.environ.get('LOCALAPPDATA')
    return _safe_path((Path(local) if local else Path.home()/'.cache')/'BioTwinSync'/'nasa-references')


def discover_reference_library(paths=None):
    """Return a known existing NLSP root or None; never scan user directories."""
    if paths is None:
        project = Path(__file__).resolve().parents[1]/'data'/'raw'/'nlsp'
        paths = (project, default_reference_cache())
    for candidate in paths:
        try:
            root = _safe_path(candidate)
            if (root/'winscat_campaign3').is_dir():
                _safe_path(root/'winscat_campaign3')
                return root
        except (OSError, ValueError):
            continue
    return None


def _verified_file(path, entry):
    path = _safe_path(path)
    if not path.is_file() or path.stat().st_size != entry['bytes']:
        raise ValueError('NASA reference file length mismatch: '+entry['file'])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('NASA reference file SHA256 mismatch: '+entry['file'])
    return raw


def load_reference_library(path):
    """Load only the exact verified 26-file collection; return real chart cards.

    ``query`` and each of at most three ``cards`` contain identity, metric, points
    (id/observed_at/value), date, count, context, unit, unit_status. This is a NASA
    source-case query, independent from the selected synthetic astronaut.
    """
    from .data import load_winscat
    from .diagnostics import select_cohort_example
    from .statistics import reference_summary
    root = _safe_path(path)
    folder = root if root.name.casefold() == 'winscat_campaign3' else root/'winscat_campaign3'
    folder = _safe_path(folder)
    entries = _manifest()
    actual = {p.name for p in folder.glob('*.csv')}
    if actual != {e['file'] for e in entries}:
        raise ValueError('Select the known 26-file WinSCAT collection; unexpected or missing CSVs.')
    for entry in entries:
        _verified_file(folder/entry['file'], entry)
    observations, audit = load_winscat(folder)
    if len(observations) > 5000:
        raise ValueError('NASA reference observation bound exceeded.')
    result = select_cohort_example(observations)
    anchor = result.get('anchor', {})
    def card(identity, points, context, **extra):
        return {'identity': identity, 'metric': context.get('metric'), 'points': points,
            'date': points[-1]['observed_at'] if points else None, 'count': len(points),
            'context': context, 'unit': context.get('unit'), 'unit_status': 'unverified', **extra}
    context = {k: anchor.get(k) for k in ('source_id', 'metric', 'unit', 'phase', 'method')}
    query = card(anchor.get('subject_id'), result.get('query', []), context)
    cards = [card(m['subject_id'], m['observations'], m['context'], distance=m['distance'],
                  comparison_kind=m['comparison_kind']) for m in result['matches']]
    available = [o for o in observations if reference_summary(observations, o)['status'] == 'available']
    visits = {(o['subject_id'], o['observed_at'], o['source_file'], o['source_row']) for o in available}
    return {'status': result['status'], 'reason': result['reason'], 'root': str(folder.parent),
        'query': query, 'cards': cards, 'source_audit': audit, 'cohort_example': result,
        'baseline_coverage': {'minimum_earlier_comparable_tests': 5,
            'total_metric_observations': len(observations), 'eligible_metric_observations': len(available),
            'eligible_assessment_visits': len(visits)}, 'limitations': list(LIMITATIONS)}


def _nasa_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'nlsp.nasa.gov' or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('NASA reference redirects must remain on HTTPS nlsp.nasa.gov.')
    return url


class _NasaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _nasa_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_reference_library(cache_root=None, *, opener=None, timeout=10, total_timeout=90):
    """Explicit opt-in, bounded download; existing mismatches are never replaced.

    ``opener`` may be a test callable(request, timeout=...) or urllib opener.
    Errors raise ValueError/OSError; callers retain offline monitoring and show
    the error. Fetches at most 26 exact public URLs and 28,204 accepted CSV bytes.
    """
    if not 0 < timeout <= 15 or not 0 < total_timeout <= 120:
        raise ValueError('NASA reference timeout bound exceeded.')
    root = _safe_path(cache_root if cache_root is not None else default_reference_cache())
    folder = _safe_path(root/'winscat_campaign3')
    entries = _manifest()
    # Validate every existing target before any network, directory or file write.
    for entry in entries:
        path = _safe_path(folder/entry['file'])
        if path.exists():
            _verified_file(path, entry)
    if opener is None:
        opener = build_opener(_NasaRedirect())
    open_url = opener.open if hasattr(opener, 'open') else opener
    deadline = time.monotonic()+total_timeout
    staged = []
    downloaded = 0
    for entry in entries:
        path = _safe_path(folder/entry['file'])
        if path.exists():
            continue
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('NASA reference download deadline exceeded.')
        request = Request(_nasa_url(entry['url']), headers={'User-Agent': 'BioTwinSync-public-reference/1'})
        response = open_url(request, timeout=min(timeout, remaining))
        try:
            _nasa_url(response.geturl())
            # Exact per-file bound plus one byte detects oversized responses.
            raw = response.read(entry['bytes']+1)
        finally:
            response.close()
        if time.monotonic() > deadline:
            raise TimeoutError('NASA reference download deadline exceeded.')
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('NASA reference download integrity mismatch: '+entry['file'])
        staged.append((entry, raw))
        downloaded += len(raw)
        if downloaded > TOTAL_BYTES:
            raise ValueError('NASA reference byte bound exceeded.')
    _safe_path(folder).mkdir(parents=True, exist_ok=True)
    for entry, raw in staged:
        path = _safe_path(folder/entry['file'])
        # Exclusive creation, with a second safety check, never overwrites files.
        with path.open('xb') as handle:
            handle.write(raw)
    return {'status': 'available', 'root': str(root), 'downloaded_files': len(staged),
        'downloaded_bytes': downloaded, 'total_files': 26, 'total_bytes': TOTAL_BYTES,
        'limitations': list(LIMITATIONS)}
