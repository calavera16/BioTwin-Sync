"""Finite JSON-only local inference; deliberately no pickle or optional imports."""
from __future__ import annotations

import math
from pathlib import Path

from .waveforms import read_json

FEATURES = ('log_std', 'log_peak_to_peak', 'log_rms', 'log_derivative_rms',
            'log_abs_difference_p95', 'flat_fraction', 'large_gap_fraction',
            'coverage_ratio', 'high_frequency_fraction')


def _finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def validate_model(model):
    required = {'schema_version', 'kind', 'features', 'mean', 'scale', 'coefficients',
                'intercept', 'threshold', 'config', 'data_hashes', 'warning'}
    if not isinstance(model, dict) or set(model) != required:
        raise ValueError('model schema fields mismatch')
    if type(model['schema_version']) is not int or model['schema_version'] != 1 or model['kind'] != 'author-exclusion-logistic-regression':
        raise ValueError('unsupported model version/kind')
    if model['features'] != list(FEATURES):
        raise ValueError('feature order mismatch')
    for field in ('mean', 'scale', 'coefficients'):
        if not isinstance(model[field], list) or len(model[field]) != len(FEATURES) or not all(map(_finite, model[field])):
            raise ValueError(f'invalid {field}')
    if any(v <= 0 for v in model['scale']):
        raise ValueError('scales must be positive')
    if not _finite(model['intercept']) or not _finite(model['threshold']) or not 0 <= model['threshold'] <= 1:
        raise ValueError('invalid intercept/threshold')
    if not isinstance(model['config'], dict) or not isinstance(model['data_hashes'], dict):
        raise ValueError('missing provenance/config')
    def safe_metadata(value, depth=0):
        if depth > 12:
            return False
        if value is None or type(value) in (str, bool):
            return True
        if type(value) in (int, float):
            return _finite(value)
        if isinstance(value, list):
            return len(value) <= 100 and all(safe_metadata(v, depth+1) for v in value)
        if isinstance(value, dict):
            return len(value) <= 100 and all(isinstance(k, str) and safe_metadata(v, depth+1) for k, v in value.items())
        return False
    if not safe_metadata(model['config']):
        raise ValueError('invalid config metadata')
    if not model['data_hashes'] or any(not isinstance(k, str) or not isinstance(v, str)
            or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
            for k, v in model['data_hashes'].items()):
        raise ValueError('invalid data hashes')
    if not isinstance(model['warning'], str) or not model['warning']:
        raise ValueError('missing model warning')
    return model


def load_model(path):
    return validate_model(read_json(Path(path), limit=100_000))


def predict(model, features):
    validate_model(model)
    if not isinstance(features, dict) or set(features) != set(FEATURES) or not all(map(_finite, features.values())):
        raise ValueError('inference requires exact finite feature mapping')
    try:
        score = model['intercept'] + sum(c*((features[f]-m)/s) for f, m, s, c in
            zip(FEATURES, model['mean'], model['scale'], model['coefficients']))
    except OverflowError:
        raise ValueError('inference overflow') from None
    if not math.isfinite(score):
        raise ValueError('inference overflow')
    probability = 1/(1+math.exp(-score)) if score >= 0 else math.exp(score)/(1+math.exp(score))
    return {'author_exclusion_score': probability, 'flagged': probability >= model['threshold'],
            'status': 'experimental', 'warning': model['warning']}
