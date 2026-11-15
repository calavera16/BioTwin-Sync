import copy
import json
import tempfile
import unittest
from pathlib import Path

from biotwin.quality_model import FEATURES, load_model, predict, validate_model


def model():
    return {'schema_version': 1, 'kind': 'author-exclusion-logistic-regression', 'features': list(FEATURES),
            'mean': [0.]*len(FEATURES), 'scale': [1.]*len(FEATURES),
            'coefficients': [1.]+[0.]*(len(FEATURES)-1), 'intercept': 0., 'threshold': .7,
            'config': {}, 'data_hashes': {'synthetic': 'a'*64}, 'warning': 'experimental proxy labels'}


class QualityModel(unittest.TestCase):
    def test_standard_logistic_and_extremes(self):
        features = {f: 0. for f in FEATURES}
        self.assertEqual(predict(model(), features)['author_exclusion_score'], .5)
        for value, expected in ((1000., 1.), (-1000., 0.)):
            features[FEATURES[0]] = value
            self.assertEqual(predict(model(), features)['author_exclusion_score'], expected)

    def test_strict_schema_rejects_bad_coefficients_features_scale(self):
        for mutate in (lambda m: m.update(extra=True), lambda m: m['scale'].__setitem__(0, 0),
                       lambda m: m['coefficients'].__setitem__(0, float('inf')),
                       lambda m: m['features'].reverse(), lambda m: m.update(schema_version=True),
                       lambda m: m['data_hashes'].update(synthetic='not a hash'),
                       lambda m: m.update(config={'unsafe': float('nan')})):
            candidate = copy.deepcopy(model())
            mutate(candidate)
            with self.assertRaises(ValueError):
                validate_model(candidate)
        for features in ({}, {**{f: 0 for f in FEATURES}, 'extra': 1}, {f: True for f in FEATURES}):
            with self.assertRaises(ValueError):
                predict(model(), features)

    def test_json_load_and_overflow_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'model.json'
            path.write_text(json.dumps(model()), encoding='utf-8')
            self.assertEqual(load_model(path), model())
            features = {f: 1e308 for f in FEATURES}
            candidate = model()
            candidate['coefficients'][0] = 1e308
            with self.assertRaisesRegex(ValueError, 'overflow'):
                predict(candidate, features)


if __name__ == '__main__':
    unittest.main()
