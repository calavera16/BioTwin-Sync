import unittest

from biotwin.metric_tags import suggest_metric_tags


def context(metric="HR", **changes):
    item = {"subject_id": "crew1", "source_id": "demo", "metric": metric,
            "unit": "bpm", "phase": "rest", "method": "demo"}
    item.update(changes)
    return item


class MetricTagTests(unittest.TestCase):
    def test_synthetic_reaction_time_is_explicit_and_separate_from_cptrt(self):
        for alias in ("reaction time", "reaction-time", "reaction_time", "반응시간", "반응 시간"):
            with self.subTest(alias=alias):
                result = suggest_metric_tags(alias, [context("reaction_time", unit="ms"), context("CPTRT", unit="source-unit-unverified:CPTRT")])
                suggestion, = result["suggestions"]
                self.assertEqual(suggestion["tag"], "reaction_time")
                self.assertEqual(suggestion["contexts"], [context("reaction_time", unit="ms")])
                self.assertTrue(suggestion["requires_confirmation"])
        absent = suggest_metric_tags("reaction time", [context("CPTRT")])["suggestions"][0]
        self.assertEqual(absent["availability"], "unavailable")
        self.assertEqual(absent["contexts"], [])
        self.assertEqual(suggest_metric_tags("reaction time", [])["suggestions"][0]["contexts"], [])

    def test_known_alias_returns_only_exact_contexts_requires_confirmation(self):
        hr = context()
        exercise = context(phase="exercise")
        result = suggest_metric_tags("Review heart rate and HR.", [hr, hr, exercise, context("CPTRT")])
        suggestion, = result["suggestions"]
        self.assertEqual(suggestion["tag"], "heart_rate")
        self.assertEqual(suggestion["availability"], "available")
        self.assertEqual(len(suggestion["contexts"]), 2)
        self.assertTrue(suggestion["requires_confirmation"])
        self.assertTrue(result["requires_confirmation"])
        self.assertNotIn("dependencies", result)
        self.assertNotIn("conditions", result)
        suggestion["contexts"][0]["phase"] = "edited"
        self.assertEqual(hr["phase"], "rest")

    def test_word_boundaries_unknown_and_ambiguous_text(self):
        for text in ("through chromium HRV", "heart rates", "unknown measurement", ""):
            with self.subTest(text=text):
                self.assertEqual(suggest_metric_tags(text, [context()])["suggestions"], [])
        tags = suggest_metric_tags("HR and pulse rate", [context(), context("pulse_rate")])["suggestions"]
        self.assertEqual([item["tag"] for item in tags], ["heart_rate", "pulse_rate"])

    def test_korean_particles_and_exact_test_codes(self):
        result = suggest_metric_tags("심박수와 혈압을 확인하고 CPTRT 검토", [context(), context("SBP"), context("CPTRT")])
        self.assertEqual([item["tag"] for item in result["suggestions"]], ["heart_rate", "blood_pressure", "cptrt"])
        self.assertEqual(suggest_metric_tags("HR을 검토", [context()])["suggestions"][0]["tag"], "heart_rate")

    def test_negation_and_treatment_never_become_actions(self):
        result = suggest_metric_tags("Do not use HR; administer medication if HR > 100. 혈압은 제외하고 투여 금지.", [context(), context("SBP")])
        self.assertEqual(len(result["suggestions"]), 2)
        self.assertTrue(any("negation" in warning for warning in result["warnings"]))
        self.assertTrue(any("Treatment" in warning for warning in result["warnings"]))
        for suggestion in result["suggestions"]:
            self.assertTrue(suggestion["mention_only"])
            self.assertTrue(suggestion["requires_confirmation"])
            self.assertNotIn("condition", suggestion)
            self.assertNotIn("approved", suggestion)

    def test_unsupported_even_when_named_in_available_input(self):
        result = suggest_metric_tags("SpO2 산소포화도 and 수면단계 sleep", [context("SpO2"), context("sleep_stage")])
        self.assertEqual([item["tag"] for item in result["suggestions"]], ["spo2", "sleep"])
        for item in result["suggestions"]:
            self.assertEqual(item["availability"], "unavailable")
            self.assertEqual(item["contexts"], [])

    def test_missing_incomplete_and_exact_case_contexts(self):
        result = suggest_metric_tags("HR", [context("hr"), {"metric": "HR"}])
        self.assertEqual(result["suggestions"][0]["availability"], "unavailable")
        self.assertEqual(result["ignored_incomplete_context_count"], 1)
        self.assertEqual(suggest_metric_tags("HR", {"contexts": [context()]})["suggestions"][0]["availability"], "available")

    def test_observation_wrapper_does_not_assert_measurement_quality(self):
        result = suggest_metric_tags("HR", {"observations": [dict(context(), quality="missing", value=None)]})
        self.assertEqual(result["suggestions"][0]["availability"], "available")
        self.assertIn("quality/freshness", result["suggestions"][0]["reason"])

    def test_bounds_bad_catalog_and_no_evaluation(self):
        for text, catalog in (("a" * 20001, []), (None, []), ("HR", None),
                              ("HR", "HR"), ("HR", {"unexpected": []}), ("HR", ["HR"])):
            with self.subTest(text=str(text)[:20]), self.assertRaises(ValueError):
                suggest_metric_tags(text, catalog)
        self.assertEqual(suggest_metric_tags("__import__('os').system('echo x')", [context()])["status"], "no_known_mentions")


if __name__ == "__main__":
    unittest.main()
