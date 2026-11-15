import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from biotwin.desktop_assets import EXAMPLE_FILES, asset_directory, load_examples, preview


class DesktopAssetsTests(unittest.TestCase):
    def test_verified_examples_preserve_samples_and_original_indices(self):
        examples = load_examples()
        self.assertEqual({e['file'] for e in examples}, set(EXAMPLE_FILES))
        for e in examples:
            self.assertLessEqual(e['times'][-1]-e['times'][0], 60)
            window = preview(e, e['times'][0], 10, 120)
            self.assertLessEqual(len(window['points']), 120)
            self.assertEqual(window['original_sample_start'], e['sample_start'])
            samples = dict(zip(e['times'], e['values']))
            for p in window['points']:
                self.assertEqual(p['value'], samples[p['source_seconds']])
            for mark in window['author_exclusions']:
                a, b = mark['source_sample_bounds']
                self.assertGreater(e['sample_start']+e['times'].index(mark['start']), a)
                self.assertLess(e['sample_start']+e['times'].index(mark['end']), b)

    def test_corrupt_missing_schema_and_allowlist_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ('manifest.json', *EXAMPLE_FILES):
                shutil.copyfile(asset_directory()/filename, root/filename)
            path = root/EXAMPLE_FILES[0]
            path.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_examples(root)
            manifest = json.loads((root/'manifest.json').read_text())
            manifest['examples'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest['examples'][0]['bytes'] = path.stat().st_size
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                load_examples(root)
            manifest['examples'][0]['file'] = '../unrelated.json'
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                load_examples(root)

    def test_complete_schema_checked_before_ui_use(self):
        for missing in ('nominal_rate_hz', 'source_key', 'sample_end_exclusive', 'attribution', 'annotation'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for filename in ('manifest.json', *EXAMPLE_FILES):
                    shutil.copyfile(asset_directory()/filename, root/filename)
                path = root/EXAMPLE_FILES[0]
                document = json.loads(path.read_text(encoding='utf-8'))
                del document[missing]
                path.write_text(json.dumps(document), encoding='utf-8')
                manifest = json.loads((root/'manifest.json').read_text())
                manifest['examples'][0].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
                (root/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    load_examples(root)


if __name__ == '__main__':
    unittest.main()
