import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from biotwin import reference_library as library

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'data'/'raw'/'nlsp'/'winscat_campaign3'


class Response(io.BytesIO):
    def __init__(self, raw, url):
        super().__init__(raw)
        self.url = url

    def geturl(self):
        return self.url


class ReferenceLibraryTests(unittest.TestCase):
    def setUp(self):
        self.entries = library._manifest()

    def test_metadata_is_exact_known_public_collection(self):
        original = json.loads((ROOT/'data'/'nlsp-download-manifest.json').read_text())
        expected = [{'file': r['source_filename'], 'url': r['url'], 'bytes': r['bytes'], 'sha256': r['sha256']}
                    for r in original if r['dataset'] == 'winscat_campaign3']
        self.assertEqual(self.entries, expected)
        self.assertEqual(len(self.entries), 26)
        self.assertEqual(sum(e['bytes'] for e in self.entries), 28204)

    @unittest.skipUnless(SOURCE.is_dir(), 'local public NASA source collection absent')
    def test_load_returns_actual_reference_query_and_three_dated_cards(self):
        result = library.load_reference_library(SOURCE.parent)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['query']['identity'], 'C3F:5683')
        self.assertEqual(result['query']['count'], 4)
        self.assertEqual(len(result['cards']), 3)
        self.assertEqual(result['baseline_coverage']['eligible_assessment_visits'], 2)
        for card in [result['query'], *result['cards']]:
            self.assertEqual(card['count'], len(card['points']))
            self.assertEqual(card['date'], card['points'][-1]['observed_at'])
            self.assertEqual(card['unit_status'], 'unverified')
            self.assertNotIn('SYNTHETIC', card['identity'])
            self.assertTrue(all(p['observed_at'].startswith('2008') for p in card['points']))

    def test_rejects_unknown_collection_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)/'winscat_campaign3'
            folder.mkdir()
            (folder/'unexpected.csv').touch()
            with self.assertRaisesRegex(ValueError, '26-file'):
                library.load_reference_library(temp)

    def test_casefold_secret_and_traversal_paths_rejected(self):
        for name in ('.ENV', '.Env.private', 'a/../b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                library.fetch_reference_library(Path(tempfile.gettempdir())/name,
                    opener=lambda *a, **k: self.fail('network called'))

    def test_symbolic_cache_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'target'
            target.mkdir()
            link = Path(temp)/'link'
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest('symlink privilege unavailable')
            with self.assertRaisesRegex(ValueError, 'Linked'):
                library.fetch_reference_library(link)

    def test_hash_mismatch_does_not_write_any_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'cache'
            def fake(request, **kwargs):
                return Response(b'x'*self.entries[0]['bytes'], request.full_url)
            with self.assertRaisesRegex(ValueError, 'integrity'):
                library.fetch_reference_library(root, opener=fake)
            self.assertFalse(root.exists())

    def test_external_redirect_rejected_before_body_read(self):
        with tempfile.TemporaryDirectory() as temp:
            class BadResponse(Response):
                def read(self, count):
                    raise AssertionError('unsafe body read')
            with self.assertRaisesRegex(ValueError, 'HTTPS nlsp'):
                library.fetch_reference_library(temp, opener=lambda *a, **k: BadResponse(b'', 'https://evil.example/data'))
        handler = library._NasaRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(Request(self.entries[0]['url']), None, 302, '', {}, 'http://nlsp.nasa.gov/plain')

    def test_existing_mismatch_preserved_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)/'winscat_campaign3'
            folder.mkdir()
            existing = folder/self.entries[-1]['file']
            original = b'preserve local contents'
            existing.write_bytes(original)
            with self.assertRaisesRegex(ValueError, 'length mismatch'):
                library.fetch_reference_library(temp, opener=lambda *a, **k: self.fail('network called'))
            self.assertEqual(existing.read_bytes(), original)

    @unittest.skipUnless(SOURCE.is_dir(), 'local public NASA source collection absent')
    def test_mock_fetch_exact_files_skip_cache_and_no_overwrite(self):
        calls = []
        by_url = {e['url']: e for e in self.entries}
        def fake(request, **kwargs):
            calls.append(request.full_url)
            entry = by_url[request.full_url]
            return Response((SOURCE/entry['file']).read_bytes(), request.full_url)
        with tempfile.TemporaryDirectory() as temp:
            first = library.fetch_reference_library(temp, opener=fake)
            self.assertEqual(first['downloaded_bytes'], 28204)
            self.assertEqual(len(calls), 26)
            second = library.fetch_reference_library(temp, opener=lambda *a, **k: self.fail('network called'))
            self.assertEqual(second['downloaded_files'], 0)
            self.assertEqual(library.load_reference_library(temp)['status'], 'available')

    def test_timeout_and_discovery_are_bounded(self):
        with self.assertRaises(ValueError):
            library.fetch_reference_library(timeout=16)
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(library.discover_reference_library([temp]))
            (Path(temp)/'winscat_campaign3').mkdir()
            self.assertEqual(library.discover_reference_library([temp]), Path(temp))
        with tempfile.TemporaryDirectory() as temp, patch.object(library.time, 'monotonic', side_effect=[0, 91]):
            with self.assertRaises(TimeoutError):
                library.fetch_reference_library(temp, opener=lambda *a, **k: self.fail('network called'))


if __name__ == '__main__':
    unittest.main()
