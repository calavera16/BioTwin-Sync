import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from biotwin.waveforms import Waveform, load_waveform, manifest_entry, read_json, replay_window


class Waveforms(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = 'Crew_01/Processing/abm_leftseat_scenario2.csv'
        self.path = self.root/'raw'/'soteria'/self.key
        self.path.parent.mkdir(parents=True)

    def source(self, text):
        self.path.write_text(text, encoding='utf-8')
        entry = {'source_key': self.key, 'local_path': 'data/raw/soteria/'+self.key,
                 'bytes': self.path.stat().st_size, 'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}
        (self.root/'download-manifest.json').write_text(json.dumps([entry]), encoding='utf-8')

    def test_verified_source_times_and_missingness(self):
        self.source('UserTimeStamp,ECG\n12,1\n12.004,nan\n12.02,3\n')
        waveform = load_waveform(self.root, self.key)
        self.assertEqual(waveform.times, (12., 12.004, 12.02))
        self.assertEqual(waveform.values, (1., None, 3.))
        self.assertEqual(waveform.audit()['nominal_rate_hz'], 256)
        self.assertEqual(waveform.nonfinite_count, 1)
        self.assertIn('nonfinite/missing channel samples', waveform.audit()['quality_reasons'])
        result = replay_window(waveform, 12, .02, 4)
        self.assertEqual(result['sample_end'], 2)
        self.assertTrue(any(p['value'] is None for p in result['points']))

    def test_integrity_and_path_rejection(self):
        self.source('UserTimeStamp,ECG\n0,1\n.004,2\n')
        self.path.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            load_waveform(self.root, self.key)
        for key in ('../.env', 'C:/bad.csv', 'x\\bad.csv'):
            with self.assertRaises(ValueError):
                manifest_entry(self.root, key)

    def test_timing_and_columns_rejected(self):
        for text in ('UserTimeStamp,ECG\n0,1\n0,2\n', 'UserTimeStamp,ECG\n0,1\nnan,2\n',
                     'UserTimeStamp,other\n0,1\n1,2\n', 'UserTimeStamp,ECG\n0,1\n'):
            self.source(text)
            with self.assertRaises(ValueError):
                load_waveform(self.root, self.key)

    def test_minmax_preview_bounded_and_retains_extrema(self):
        values = [0.]*1000
        values[100], values[101] = -123., 456.
        waveform = Waveform('synthetic', 'a'*64, 'Bvp', 64., tuple(i/64 for i in range(1000)), tuple(values), 0)
        preview = replay_window(waveform, 0, 10, 30)
        self.assertLessEqual(len(preview['points']), 30)
        self.assertIn(-123, [p['value'] for p in preview['points']])
        self.assertIn(456, [p['value'] for p in preview['points']])
        for args in ((0, 0, 30), (0, 301, 30), (0, 10, 3), (float('nan'), 10, 30)):
            with self.assertRaises(ValueError):
                replay_window(waveform, *args)

    def test_json_duplicate_and_nonfinite_rejection(self):
        path = self.root/'bad.json'
        for content in ('{"x":1,"x":2}', '{"x":NaN}'):
            path.write_text(content, encoding='utf-8')
            with self.assertRaises(ValueError):
                read_json(path)


if __name__ == '__main__':
    unittest.main()
