"""Build a fresh Electron + Python portable release; preserve existing releases.

Only the exact pinned official Electron runtime is downloaded. Node is not a
packaged dependency and npm lifecycle scripts are not involved in this build.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection
from importlib.metadata import version
import json
import os
from pathlib import Path, PurePosixPath
import re
import queue
import shutil
import stat
import subprocess
import sys
import threading
import urllib.request
from urllib.parse import urlsplit, parse_qs
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / 'ui' / 'desktop'
CACHE = ROOT / 'build' / 'electron-runtime'
MAX_DOWNLOAD = 220_000_000
MAX_RUNTIME = 900_000_000
SHELL_FILES = ('package.json', 'main.cjs', 'host-process.cjs', 'boundaries.cjs',
               'startup-error.html', 'runtime-lock.json', 'biotwin.ico', 'biotwin.svg',
               'source-links-preload.cjs', 'external-links.json')
sys.path.insert(0, str(ROOT))
from biotwin import __version__
from biotwin.desktop_assets import ASSET_FILES, NOTICE_FILES, load_examples
from biotwin.presentation import ASSET_FILES as PRESENTATION_ASSETS, load_assets


def regular_path(path):
    path = Path(path)
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('Linked paths are unsupported.')
    return path


def allowed_name(name):
    parts = name.rstrip('/').split('/')
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    if (not parts or name.startswith('/') or '\\' in name or ':' in name or
            any(not p or p in ('.', '..') or p[-1:] in (' ', '.') or p.split('.')[0].casefold() in reserved
                or p.casefold() == '.env' or p.casefold().startswith('.env.')
                or any(ord(c) < 32 for c in p) for p in parts)):
        raise ValueError('Unsafe or secret-like package path.')
    return parts


def digest(path):
    regular_path(path)
    allowed_name(Path(path).name)
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def pinned_runtime():
    lock = json.loads((SHELL / 'runtime-lock.json').read_text(encoding='utf-8'))
    # Do not accept an edited lock redirecting the builder to another host/path.
    if (not re.fullmatch(r'\d+\.\d+\.\d+', lock['version']) or lock['platform'] != 'win32-x64'
            or not re.fullmatch('[0-9a-f]{64}', lock['sha256'])):
        raise ValueError('Invalid runtime lock.')
    base = f"https://github.com/electron/electron/releases/download/v{lock['version']}/"
    name = f"electron-v{lock['version']}-win32-x64.zip"
    if lock['url'] != base + name or lock['checksum_source'] != base + 'SHASUMS256.txt':
        raise ValueError('Only the official pinned Electron release is permitted.')
    return lock, name


def acquire_runtime():
    lock, name = pinned_runtime()
    regular_path(CACHE)
    CACHE.mkdir(parents=True, exist_ok=True)
    target = regular_path(CACHE / name)
    if not target.exists():
        # An interrupted partial download is preserved; choose a fresh build cache
        # location rather than silently replacing potentially concurrent work.
        temporary = regular_path(CACHE / (name + '.partial'))
        with urllib.request.urlopen(lock['url'], timeout=60) as response, temporary.open('xb') as output:
            count = 0
            while block := response.read(1024 * 1024):
                count += len(block)
                if count > MAX_DOWNLOAD:
                    raise ValueError('Electron download exceeds its bound.')
                output.write(block)
        if digest(temporary) != lock['sha256']:
            raise ValueError('Electron download checksum mismatch; partial retained.')
        # Hard link creation fails atomically if another builder has already won.
        os.link(temporary, target)
        temporary.unlink()
    if not target.is_file() or target.stat().st_size > MAX_DOWNLOAD or digest(target) != lock['sha256']:
        raise ValueError('Cached runtime does not match the official pinned checksum.')
    checksum_file = CACHE / ('v' + lock['version'] + '-SHASUMS256.txt')
    if not checksum_file.exists():
        with urllib.request.urlopen(lock['checksum_source'], timeout=30) as response:
            raw = response.read(100_001)
        if len(raw) > 100_000:
            raise ValueError('Official checksum list exceeds bound.')
        expected = lock['sha256'] + ' *' + name
        if expected not in raw.decode('ascii').splitlines():
            raise ValueError('Pinned checksum differs from official release metadata.')
        with checksum_file.open('xb') as output:
            output.write(raw)
    checksum_file = regular_path(checksum_file)
    if checksum_file.stat().st_size > 100_000 or lock['sha256'] + ' *' + name not in checksum_file.read_text(encoding='ascii').splitlines():
        raise ValueError('Invalid cached official checksum receipt.')
    return target, lock, checksum_file


def safe_members(archive):
    infos = archive.infolist()
    if not 10 <= len(infos) <= 2000:
        raise ValueError('Runtime archive count exceeds its bound.')
    total = 0
    seen = set()
    for info in infos:
        allowed_name(info.filename)
        key = info.filename.rstrip('/').casefold()
        if key in seen:
            raise ValueError('Duplicate runtime archive path.')
        seen.add(key)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
            raise ValueError('Linked or special runtime archive member.')
        total += info.file_size
        if info.file_size > 400_000_000 or total > MAX_RUNTIME:
            raise ValueError('Expanded runtime exceeds its size bound.')
    required = {'electron.exe', 'LICENSE', 'LICENSES.chromium.html', 'resources/default_app.asar'}
    if not required <= set(i.filename for i in infos):
        raise ValueError('Official Electron runtime files are missing.')
    return infos


def extract_runtime(archive_path, folder):
    with zipfile.ZipFile(archive_path) as archive:
        infos = safe_members(archive)
        for info in infos:
            # The app directory replaces Electron's development launcher.
            if info.filename == 'resources/default_app.asar':
                continue
            target = folder.joinpath(*PurePosixPath(info.filename).parts)
            if info.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
    (folder / 'electron.exe').rename(folder / 'BioTwinSync.exe')


def brand_executable(executable):
    """Brand this new build's PE resources with already pinned build tools.

    The resulting application is unsigned; the original verified archive remains
    untouched in the cache. Runtime notices retain Electron's MIT copyright.
    """
    from PyInstaller.utils.win32.icon import CopyIcons_FromIco
    from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo,
        StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
        write_version_info_to_executable, read_version_info_from_executable)
    from PyInstaller.utils.win32.winutils import update_exe_pe_checksum
    values = {'CompanyName': 'BioTwin Sync prototype', 'FileDescription': 'BioTwin Sync',
        'FileVersion': __version__, 'InternalName': 'BioTwinSync',
        'OriginalFilename': 'BioTwinSync.exe', 'ProductName': 'BioTwin Sync',
        'ProductVersion': __version__, 'LegalCopyright': 'BioTwin Sync contributors; Electron contributors. See LICENSE.'}
    numbers = tuple(int(v) for v in __version__.split('.')) + (0,)
    info = VSVersionInfo(ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f,
        flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=[
        StringFileInfo([StringTable('040904B0', [StringStruct(k, v) for k, v in values.items()])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
    CopyIcons_FromIco(str(executable), [str(SHELL / 'biotwin.ico')])
    write_version_info_to_executable(str(executable), info)
    update_exe_pe_checksum(str(executable))
    readback = read_version_info_from_executable(str(executable))
    if readback is None or 'BioTwin Sync' not in str(readback):
        raise ValueError('Executable branding verification failed.')


def copy_backend(source, destination):
    """Validate generated names/links before copying any generated file bytes."""
    regular_path(source)
    files = sorted(source.rglob('*'))
    total = 0
    for file in files:
        allowed_name(file.relative_to(source).as_posix())
        regular_path(file)
        if file.is_file(): total += file.stat().st_size
    if not 5 <= len(files) <= 2000 or total > 200_000_000:
        raise ValueError('Generated Python bundle exceeds its bound.')
    shutil.copytree(source, destination)


def source_manifest():
    # Explicit source inputs only; no repository-wide traversal or secret reads.
    sources = [ROOT / 'run_webhost.py', ROOT / 'BioTwinHost.spec', Path(__file__),
               SHELL / 'generate_icon.py',
               *[SHELL / name for name in SHELL_FILES],
               *sorted((ROOT / 'biotwin').glob('*.py')),
               *[ROOT / 'assets' / 'desktop' / name for name in ASSET_FILES],
               *[ROOT / 'assets' / 'presentation' / name for name in PRESENTATION_ASSETS],
               ROOT / 'requirements-build.lock.txt']
    result = []
    for source in sorted(set(sources)):
        relative = source.relative_to(ROOT).as_posix()
        allowed_name(relative)
        regular_path(source)
        if not source.is_file() or source.stat().st_size > 4_000_000:
            raise ValueError('Unsafe source manifest input.')
        result.append({'file': relative, 'bytes': source.stat().st_size, 'sha256': digest(source)})
    return result


def smoke_host(executable, cwd, arguments=()):
    """Frozen transport/lifetime only. Visible UI acceptance belongs to CUA."""
    process = subprocess.Popen([str(executable), *map(str, arguments)], cwd=cwd, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = queue.Queue(maxsize=1)
    def read_ready():
        try: lines.put(process.stdout.readline(1026))
        except (OSError, ValueError): lines.put(b'')
    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    checks = {}
    try:
        line = lines.get(timeout=20)
        value = json.loads(line)
        if len(line) > 1025 or type(value) is not dict or set(value) != {'type', 'version', 'url'} or value['type'] != 'ready' or value['version'] != 1:
            raise ValueError('Invalid frozen host readiness.')
        url = urlsplit(value['url'])
        fragment = parse_qs(url.fragment, strict_parsing=True)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not 1024 <= url.port <= 65535 or
                url.netloc != f'127.0.0.1:{url.port}' or url.path != '/' or url.query or
                set(fragment) != {'token', 'role'} or fragment['role'] != ['crew'] or
                len(fragment['token']) != 1 or not re.fullmatch('[A-Za-z0-9_-]{43}', fragment['token'][0])):
            raise ValueError('Invalid frozen host address.')
        token = fragment['token'][0]
        checks['bounded_private_ready'] = True
        def request(target, authenticated=True, payload=None):
            connection = HTTPConnection(url.hostname, url.port, timeout=5)
            headers = {'X-BioTwin-Token': token} if authenticated else {}
            if payload is not None:
                headers.update({'Origin': f'http://127.0.0.1:{url.port}', 'Content-Type': 'application/json'})
            connection.request('POST' if payload is not None else 'GET', target,
                body=json.dumps(payload).encode('utf-8') if payload is not None else None, headers=headers)
            response = connection.getresponse()
            body = response.read(4_000_001)
            status = response.status
            connection.close()
            if len(body) > 4_000_000: raise ValueError('Frozen HTTP response exceeds bound.')
            return status, body
        status, body = request('/')
        checks['bundled_frontend_served'] = status == 200 and b'<html' in body.lower()
        for role in ('crew', 'doctor'):
            status, body = request(f'/api/state?role={role}&crew=Crew%20001')
            checks[role + '_backend_state'] = status == 200 and 'projection' in json.loads(body)
        checks['unauthenticated_API_denied'] = request('/api/state?role=crew&crew=Crew%20001', False)[0] == 403
        state = json.loads(request('/api/state?role=crew&crew=Crew%20001')[1])
        def command(action, payload=None):
            nonlocal state
            status, raw = request('/api/action', payload={'role': 'crew', 'crew': 'Crew 001',
                'action': action, 'expected_revision': state['revision'],
                'expected_session_id': state['session_id'], 'expected_digest': state['snapshot_digest'],
                'payload': payload or {}})
            if status != 200: raise ValueError('Frozen recorded workflow command failed.')
            state = json.loads(raw)
        command('start_local')
        checks['recorded_workspace_started'] = state['mode'] == 'local' and state['local']['observations'] == []
        row = {'subject_id': 'LOCAL:crew-001', 'metric': 'heart_rate', 'value': 72, 'unit': 'bpm',
            'observed_at': datetime.now(timezone.utc).isoformat(), 'source_id': 'packaged-smoke-fixture',
            'method': 'operator device reading', 'quality': 'usable'}
        command('capture_manual', {'row': row})
        checks['manual_capture'] = len(state['local']['observations']) == 1 and state['local']['observations'][0]['value'] == 72
        from urllib.parse import urlencode
        status, body = request('/api/export?' + urlencode({'role': 'crew', 'crew': 'Crew 001',
            'session_id': state['session_id'], 'revision': state['revision']}))
        exported = json.loads(body)
        checks['session_export'] = status == 200 and exported['filename'].endswith('.json') and bool(json.loads(exported['text'])['payload'])
        command('send_consultation')
        doctor = json.loads(request('/api/state?role=doctor&crew=Crew%20001')[1])
        checks['undelivered_doctor_isolation'] = doctor['local']['observations'] == []
        command('advance')
        doctor = json.loads(request('/api/state?role=doctor&crew=Crew%20001')[1])
        checks['delivered_manual_evidence'] = len(doctor['local']['observations']) == 1 and doctor['local']['observations'][0]['value'] == 72
        process.stdin.write(b'stop\n'); process.stdin.flush(); process.stdin.close()
        checks['owned_shutdown'] = process.wait(timeout=8) == 0
        checks['no_extra_stdout_or_diagnostics'] = process.stdout.read(1026) == b'' and process.stderr.read(4097) == b''
        connection = HTTPConnection(url.hostname, url.port, timeout=.5)
        try:
            connection.connect()
            checks['loopback_closed'] = False
        except OSError:
            checks['loopback_closed'] = True
        finally:
            connection.close()
        return {'format': 'biotwin-frozen-host-smoke', 'status': 'passed' if all(checks.values()) else 'failed',
            'checks': checks, 'scope': 'Host stdio, HTTP assets, recorded capture/export, delayed role isolation and owned shutdown; no GUI inspection.'}
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
        for handle in (process.stdin, process.stdout, process.stderr):
            if handle and not handle.closed: handle.close()
        reader.join(timeout=1)


def build(tag):
    if sys.platform != 'win32':
        raise ValueError('Build Windows runtime on Windows.')
    if not re.fullmatch('[A-Za-z0-9_-]{1,48}', tag):
        raise ValueError('Use a new ASCII build tag (1..48 letters, digits, _ or -).')
    work, dist = ROOT / 'build' / ('desktop-' + tag), ROOT / 'dist' / ('desktop-' + tag)
    for directory in (work, dist):
        regular_path(directory)
        if directory.exists(): raise ValueError('Build tag already exists; existing artifacts are preserved.')
    load_examples(ROOT / 'assets' / 'desktop')
    load_assets(ROOT / 'assets' / 'presentation')
    library = json.loads((ROOT / 'assets' / 'desktop' / 'assistant-protocols-v1.json').read_text(encoding='utf-8'))
    allowed_sources = json.loads((SHELL / 'external-links.json').read_text(encoding='utf-8'))
    if allowed_sources != ['https://doi.org/10.1038/s41597-025-05336-7', *[s['url'] for s in library['sources']]]:
        raise ValueError('Reviewed external-link allowlist differs from the pinned source catalog.')
    sources = source_manifest()
    runtime, lock, checksum_receipt = acquire_runtime()
    work.mkdir(parents=True)
    dist.mkdir(parents=True)
    folder = dist / 'BioTwinSync'
    folder.mkdir()
    extract_runtime(runtime, folder)
    brand_executable(folder / 'BioTwinSync.exe')
    subprocess.run([sys.executable, '-m', 'PyInstaller', str(ROOT / 'BioTwinHost.spec'),
        '--distpath', str(work / 'python-dist'), '--workpath', str(work / 'python-work'),
        '--noconfirm'], cwd=ROOT, check=True)
    copy_backend(work / 'python-dist' / 'BioTwinHost', folder / 'resources' / 'backend')
    app_dir = folder / 'resources' / 'app'
    app_dir.mkdir()
    for name in SHELL_FILES:
        shutil.copyfile(SHELL / name, app_dir / name)
    notices = folder / 'licenses'
    notices.mkdir()
    for name in NOTICE_FILES:
        shutil.copyfile(ROOT / 'assets' / 'desktop' / name, notices / name)
    shutil.copyfile(ROOT / 'assets' / 'presentation' / 'THIRD-PARTY-NOTICES.txt', notices / 'REACT-NOTICES.txt')
    shutil.copyfile(checksum_receipt, notices / checksum_receipt.name)
    (folder / 'START-HERE.txt').write_text(
        'BioTwin Sync — offline Windows x64 prototype\n\n'
        'Extract this entire folder, then double-click BioTwinSync.exe.\n'
        'Chromium and Python are included. No browser, Node, Python, WebView2 or Internet setup is required.\n'
        'Use Other terminal for the separate Crew / Flight surgeon window. Both windows share one local workspace.\n'
        'Start with the 30-second example, then follow the workspace next-step guide.\n'
        'Use Export session JSON and complete the Windows Save dialog; Open saved session uses the file picker.\n'
        'The workspace is temporary until exported. Closing the app stops its own local host.\n'
        'This unsigned hackathon prototype is not clinically validated or approved for flight.\n'
        'License notices: LICENSE, LICENSES.chromium.html and licenses/.\n', encoding='utf-8')
    (folder / 'SOURCE-IDENTITY.json').write_text(json.dumps({'format': 'biotwin-source-identity', 'files': sources}, indent=2) + '\n', encoding='utf-8')
    info = {'format': 'biotwin-unified-desktop-build', 'version': __version__, 'build_tag': tag,
        'built_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version.split()[0], 'pyinstaller': version('pyinstaller'),
        'runtime': {'electron': lock['version'], 'official_url': lock['url'], 'archive_sha256': lock['sha256']},
        'architecture': 'Windows x64; bundled Chromium and Python; authenticated loopback-only HTTP',
        'executable_branding': 'BioTwin Sync PE version resources and flat pulse icon; unsigned modified launcher; verified upstream archive preserved',
        'entrypoint': 'BioTwinSync.exe', 'host_entrypoint': 'resources/backend/BioTwinHost.exe',
        'security': {'renderer_sandbox': True, 'context_isolation': True, 'node_integration': False,
            'persistent_browser_partition': False, 'permissions': 'denied', 'external_navigation': 'trusted source-link click; exact DOI and pinned NASA/NHS/FDA source URLs only'},
        'limits': ['Unsigned prototype.', 'Not clinically validated or flight-approved.',
            'Same-machine relocation is not second-device verification.', 'Workspace must be exported to persist.',
            'Raw NLSP excluded; experimental model deployment gates remain unchanged.']}
    (folder / 'BUILD-INFO.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    smoke_report = dist / 'smoke-build.json'
    frozen_check = smoke_host(folder / 'resources' / 'backend' / 'BioTwinHost.exe', work)
    smoke_report.write_text(json.dumps(frozen_check, indent=2) + '\n', encoding='utf-8')
    if frozen_check['status'] != 'passed':
        raise RuntimeError('Bundled shell smoke failed; build retained for inspection.')
    shutil.copyfile(smoke_report, folder / 'SMOKE-TEST.json')
    if sources != source_manifest():
        raise RuntimeError('Source inputs changed during build; do not release this preserved candidate.')
    files = sorted(p for p in folder.rglob('*') if p.is_file())
    total = 0
    members = []
    for file in files:
        relative = file.relative_to(folder).as_posix()
        allowed_name(relative); regular_path(file)
        total += file.stat().st_size
        members.append({'file': relative, 'bytes': file.stat().st_size, 'sha256': digest(file)})
    if not 20 <= len(members) <= 3000 or total > 1_000_000_000:
        raise ValueError('Portable package exceeds its file or byte bound.')
    # Manifest cannot hash itself; all other runtime files are covered.
    (folder / 'PACKAGE-MANIFEST.json').write_text(json.dumps({'format': 'biotwin-package-manifest', 'files': members}, indent=2) + '\n', encoding='utf-8')
    files.append(folder / 'PACKAGE-MANIFEST.json')
    archive = dist / f'BioTwinSync-{__version__}-windows-x64-{tag}.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for file in sorted(files):
            entry = zipfile.ZipInfo('BioTwinSync/' + file.relative_to(folder).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            with file.open('rb') as source, output.open(entry, 'w') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    result = {'folder': str(folder), 'archive': str(archive), 'archive_bytes': archive.stat().st_size,
        'archive_sha256': digest(archive), 'runtime_bytes': total, 'file_count': len(files),
        'smoke_report': str(smoke_report), 'build_info': info}
    (dist / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='unified-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--download-runtime', action='store_true', help='Acquire/verify the pinned official runtime only; do not build a release.')
    args = parser.parse_args()
    if args.download_runtime:
        archive, lock, receipt = acquire_runtime()
        with zipfile.ZipFile(archive) as file: members = safe_members(file)
        print(json.dumps({'runtime': str(archive), 'version': lock['version'], 'sha256': lock['sha256'],
            'download_bytes': archive.stat().st_size, 'expanded_bytes': sum(m.file_size for m in members),
            'official_checksum_receipt': str(receipt)}, indent=2))
    else:
        print(json.dumps(build(args.tag), indent=2))
