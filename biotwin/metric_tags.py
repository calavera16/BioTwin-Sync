"""Bounded bilingual mention matching; suggestions never authorize dependencies.

Available input is an iterable of observation dictionaries or exact context
dictionaries, optionally wrapped as {"observations": [...]} or {"contexts": [...]}.
Only complete subject/source/metric/unit/phase/method contexts are suggested.
Text is never parsed into conditions, thresholds, treatments, or code.
"""
from __future__ import annotations

import re
import unicodedata

from .data import CONTEXT

_FIELDS = ("subject_id", *CONTEXT)
_MAX_TEXT = 20_000
_MAX_CONTEXTS = 100_000

# Alias matching is intentionally explicit and small. A mention is not evidence.
CATALOG = (
    ("reaction_time", "Reaction time / 반응시간", ("reaction time", "reaction-time", "reaction_time", "반응시간", "반응 시간"),
     ("reaction_time",), True),
    ("heart_rate", "Heart rate / 심박수", ("heart rate", "heart_rate", "hr", "심박수", "심박"),
     ("HR", "heart_rate", "Heart Rate"), True),
    ("pulse_rate", "Pulse rate / 맥박수", ("pulse rate", "pulse_rate", "맥박수", "맥박"),
     ("pulse_rate", "Pulse Rate", "PR"), True),
    ("blood_pressure", "Blood pressure / 혈압", ("blood pressure", "blood_pressure", "bp", "혈압"),
     ("blood_pressure", "systolic_bp", "diastolic_bp", "SBP", "DBP"), True),
    ("ecg", "ECG / 심전도", ("ecg", "ekg", "electrocardiogram", "심전도"),
     ("ECG", "ecg"), True),
    ("ppg", "PPG / 광용적맥파", ("ppg", "bvp", "photoplethysmogram", "광용적맥파"),
     ("PPG", "ppg", "BVP", "bvp"), True),
    ("spo2", "SpO2 / 산소포화도", ("spo2", "oxygen saturation", "산소포화도", "혈중산소"),
     ("SpO2", "spo2"), False),
    ("sleep", "Sleep / 수면", ("sleep", "sleep stages", "sleep stage", "수면", "수면단계"),
     ("sleep", "sleep_stage"), False),
    *((metric.lower(), metric, (metric.lower(),), (metric,), True)
      for metric in ("CPTRT", "CPTA", "CPTL", "MTHRT", "MTHA", "MTSRT", "MTSA", "CDDRT", "CDDA")),
)


def _matches(text, alias):
    # Unicode boundaries prevent HR in "through" or English/Hangul compounds.
    # Korean particles are a finite, explicit suffix allowance, not substring search.
    suffix = r"(?=(?:은|는|이|가|을|를|의|와|과|도|만|에|에서|으로|로)?(?:[^\w]|$))"
    pattern = r"(?<!\w)" + re.escape(alias) + suffix
    return bool(re.search(pattern, text, re.IGNORECASE))


def suggest_metric_tags(text, available_observations_or_catalog):
    """Return mention-only suggestions requiring explicit reviewer confirmation.

    Supported catalog entries still require exact available contexts. SpO2 and
    sleep stay unavailable in this backend, even if callers supply those names.
    No measurement, availability, dependency or approval is inferred from text.
    Unknown words produce no suggestion. This is not a natural-language parser.
    """
    if type(text) is not str or len(text) > _MAX_TEXT:
        raise ValueError("text must be a string of at most 20000 characters")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    supplied = available_observations_or_catalog
    if type(supplied) is dict:
        if set(supplied) == {"observations"}:
            supplied = supplied["observations"]
        elif set(supplied) == {"contexts"}:
            supplied = supplied["contexts"]
        else:
            raise ValueError("catalog wrapper must contain only observations or contexts")
    if isinstance(supplied, (str, bytes)) or supplied is None:
        raise ValueError("available contexts must be an iterable of dictionaries")
    contexts, ignored, seen = [], 0, set()
    try:
        iterator = iter(supplied)
    except TypeError as exc:
        raise ValueError("available contexts must be iterable") from exc
    for index, item in enumerate(iterator):
        if index >= _MAX_CONTEXTS:
            raise ValueError("at most 100000 context entries")
        if type(item) is not dict:
            raise ValueError("context entries must be dictionaries")
        if not all(type(item.get(key)) is str and bool(item[key].strip()) for key in _FIELDS):
            ignored += 1
            continue
        context = {key: item[key] for key in _FIELDS}
        identity = tuple(context[key] for key in _FIELDS)
        if identity not in seen:
            seen.add(identity)
            contexts.append(context)
    contexts.sort(key=lambda context: tuple(context[key] for key in _FIELDS))
    warnings = ["Mentions only: a reviewer must explicitly select exact contexts; no conditions or dependencies are generated."]
    if re.search(r"(?<!\w)(?:not|no|never|without|ignore|exclude|don't|do not)(?!\w)|제외|않|말고|아니|금지", normalized):
        warnings.append("Possible negation: suggestions do not establish that a metric should be used.")
    if re.search(r"(?<!\w)(?:take|administer|dose|treat|medication)(?!\w)|복용|투여|치료|처방", normalized):
        warnings.append("Treatment language is not converted to executable instructions.")
    suggestions = []
    for tag, label, aliases, metric_names, supported in CATALOG:
        matched = sorted(alias for alias in aliases if _matches(normalized, alias))
        if not matched:
            continue
        exact = [context.copy() for context in contexts if context["metric"] in metric_names] if supported else []
        if not supported:
            availability, reason = "unavailable", "unsupported capability; no validated SpO2 or sleep data pipeline"
        elif not exact:
            availability, reason = "unavailable", "no complete exact metric context available"
        else:
            availability, reason = "available", "exact contexts available for explicit reviewer selection; measurement quality/freshness still require validation"
        suggestions.append({"tag": tag, "label": label, "matched_aliases": matched,
                            "availability": availability, "reason": reason, "contexts": exact,
                            "requires_confirmation": True, "mention_only": True})
    return {"status": "suggestions" if suggestions else "no_known_mentions",
            "suggestions": suggestions, "requires_confirmation": True,
            "warnings": warnings, "ignored_incomplete_context_count": ignored,
            "catalog_version": 1}
