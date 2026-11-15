"""Archive boundaries without network, runtime execution or credential files."""
import io
from pathlib import Path
import sys
import unittest
import zipfile
from unittest.mock import patch

from scripts.build_desktop_shell import allowed_name, safe_members, pinned_runtime, smoke_host


class DesktopShellBuildTests(unittest.TestCase):
    def test_safe_names_reject_traversal_windows_aliases_and_secret_names(self):
        for name in ('../x', 'x/../x', '/x', 'C:/x', 'x\\y', 'x//y', './x',
                     '.env', '.env.local', 'config/.env.production', 'CON', 'con.txt',
                     'x/NUL.json', 'x./file', 'x /file', 'x\nfile'):
            with self.subTest(name=name), self.assertRaises(ValueError): allowed_name(name)
        self.assertEqual(allowed_name('locales/en-US.pak'), ['locales', 'en-US.pak'])

    def test_runtime_archive_requires_exact_core_files_and_rejects_collisions(self):
        required = ('electron.exe', 'LICENSE', 'LICENSES.chromium.html', 'resources/default_app.asar')
        def archive(extra):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as handle:
                for name in (*required, *(f'locales/{i}.pak' for i in range(6)), *extra):
                    handle.writestr(name, b'runtime fixture')
            buffer.seek(0)
            return zipfile.ZipFile(buffer)
        with archive([]) as handle: self.assertEqual(len(safe_members(handle)), 10)
        for extra in [('locales/../x',), ('license',), ('private/.env',)]:
            with archive(extra) as handle, self.assertRaises(ValueError): safe_members(handle)

    def test_archive_rejects_special_files_and_size_expansion(self):
        class Archive:
            def __init__(self, infos): self.infos = infos
            def infolist(self): return self.infos
        infos = []
        for i in range(10):
            info = zipfile.ZipInfo(f'{i}.bin'); info.file_size = 1; infos.append(info)
        infos[0].external_attr = 0o120777 << 16
        with self.assertRaisesRegex(ValueError, 'special'): safe_members(Archive(infos))
        infos[0].external_attr = 0o100644 << 16
        infos[0].file_size = 400_000_001
        with self.assertRaisesRegex(ValueError, 'size'): safe_members(Archive(infos))

    def test_runtime_lock_cannot_redirect_to_unapproved_host(self):
        real = pinned_runtime()[0]
        self.assertEqual(real['version'], '45.3.2')
        import json
        bad = {**real, 'url': 'https://example.org/runtime.zip'}
        with patch('pathlib.Path.read_text', return_value=json.dumps(bad)):
            with self.assertRaises(ValueError): pinned_runtime()

    def test_release_transport_smoke_exercises_recorded_workflow_and_shutdown(self):
        root = Path(__file__).resolve().parents[1]
        result = smoke_host(sys.executable, root.parent, [root / 'run_webhost.py'])
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['checks']['manual_capture'])
        self.assertTrue(result['checks']['session_export'])
        self.assertTrue(result['checks']['undelivered_doctor_isolation'])
        self.assertTrue(result['checks']['delivered_manual_evidence'])
        self.assertNotIn('token', result)


if __name__ == '__main__': unittest.main()
