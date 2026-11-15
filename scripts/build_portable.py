"""Create a new bounded portable folder/ZIP; no overwrites or recursive deletions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tkinter
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biotwin import __version__
from biotwin.desktop_assets import ASSET_FILES, NOTICE_FILES, load_examples
from biotwin.signal_review import load_default_model
from biotwin.health_assistant import catalog as assistant_catalog, CATALOG_SHA256
from biotwin.presentation import ASSET_FILES as PRESENTATION_ASSETS, load_assets


def build(tag):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', tag):
        raise ValueError('Build tag must contain 1..48 ASCII letters, digits, underscores or hyphens.')
    if sys.platform != 'win32':
        raise ValueError('Build this Windows runtime on Windows.')
    work = ROOT/'build'/f'desktop-{tag}'
    dist = ROOT/'dist'/f'desktop-{tag}'
    if any(p.is_symlink() or p.is_junction() for target in (work, dist) for p in (target, *target.parents)):
        raise ValueError('Build paths must be regular local directories without linked ancestors.')
    if work.exists() or dist.exists():
        raise ValueError('Build tag already exists; use a new tag. Existing artifacts are preserved.')
    assets = ROOT/'assets'/'desktop'
    load_examples(assets)
    presentation_assets = load_assets(ROOT/'assets'/'presentation')
    review_model = load_default_model()
    assistant_library = assistant_catalog(assets)
    for name in ASSET_FILES:
        source = assets/name
        if not source.is_file() or source.is_symlink() or source.stat().st_size > 3_000_000:
            raise ValueError('Missing or unsafe allowlisted asset: ' + name)
    work.mkdir(parents=True)
    dist.mkdir(parents=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', str(ROOT/'BioTwinSync.spec'),
        '--distpath', str(dist), '--workpath', str(work), '--noconfirm'], cwd=ROOT, check=True)
    folder = dist/'BioTwinSync'
    files = sorted(folder.rglob('*'))
    forbidden = {'numpy', 'scipy', 'sklearn', 'matplotlib', 'pandas', 'raw', 'nlsp', 'outputs', '.git'}
    for path in files:
        if path.is_symlink() or path.is_junction() or any(p.casefold() in forbidden or p.casefold() == '.env' or p.casefold().startswith('.env.') for p in path.relative_to(folder).parts):
            raise ValueError('Unexpected or credential-like bundle path: ' + str(path.relative_to(folder)))
    # Convenient top-level notices: only exact known files are copied.
    for name in NOTICE_FILES:
        shutil.copyfile(assets/name, folder/name)
    tcl = tkinter.Tcl()
    info = {'format': 'biotwin-portable-build', 'version': __version__, 'build_tag': tag,
        'python': platform.python_version(), 'architecture': platform.machine(), 'platform': platform.platform(),
        'tcl_patchlevel': tcl.eval('info patchlevel'), 'tk': tkinter.TkVersion,
        'build_packages': {name: version(name) for name in ('pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph',
            'packaging', 'pefile', 'pywin32-ctypes', 'setuptools')},
        'runtime': 'stdlib/Tk with bundled React loopback presentation; no external Python, Node or Internet required',
        'data_allowlist': list(ASSET_FILES), 'excluded_optional_packages': sorted(forbidden),
        'presentation_allowlist': list(PRESENTATION_ASSETS),
        'presentation_bytes': sum(len(raw) for raw in presentation_assets.values()),
        'signal_model': {'id': review_model['id'], 'sha256': review_model['sha256'],
                         'deployment': review_model['deployment'],
                         'natural_target_deployment': review_model['natural_head']['deployment']},
        'assistant_library': {'version': assistant_library['library_version'],
                              'sha256': CATALOG_SHA256,
                              'clinical_review_status': assistant_library['clinical_review_status'],
                              'protocol_ids': [p['id'] for p in assistant_library['protocols']],
                              'method': 'offline source-linked rules; no medical model'},
        'limits': ['Unsigned prototype.', 'Same-machine relocation is not second-device verification.',
                   'Experimental signal scores; natural author-exclusion model failed its gate.',
                   'Assistant guidance has no clinician review or flight approval.',
                   'No clinical validation or automatic sensor measurement.', 'Raw NLSP excluded.']}
    (folder/'BUILD-INFO.json').write_text(json.dumps(info, indent=2)+'\n', encoding='utf-8')
    report_path = dist/'smoke-build.json'
    result = subprocess.run([str(folder/'BioTwinSync.exe'), '--smoke-test', '--report', str(report_path)],
        cwd=work, check=False, timeout=60)
    if result.returncode or not report_path.is_file() or json.loads(report_path.read_text(encoding='utf-8'))['status'] != 'passed':
        raise RuntimeError('Frozen runtime smoke test failed. Build artifacts preserved for inspection.')
    shutil.copyfile(report_path, folder/'SMOKE-TEST.json')
    package_files = sorted(p for p in folder.rglob('*') if p.is_file())
    total = sum(p.stat().st_size for p in package_files)
    if not 10 <= len(package_files) <= 2000 or total > 200_000_000:
        raise ValueError('Portable package exceeds the declared count/size bound.')
    # Sorted entries and fixed timestamps/permissions make archive metadata deterministic.
    archive = dist/f'BioTwinSync-{__version__}-windows-x64-{tag}.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_file:
        for path in package_files:
            entry = zipfile.ZipInfo('BioTwinSync/'+path.relative_to(folder).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            zip_file.writestr(entry, path.read_bytes(), compresslevel=9)
    result = {'folder': str(folder), 'archive': str(archive), 'archive_bytes': archive.stat().st_size,
        'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'runtime_bytes': total,
        'file_count': len(package_files), 'smoke_report': str(report_path), 'build_info': info}
    (dist/'build-result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    args = parser.parse_args()
    print(json.dumps(build(args.tag), indent=2))
