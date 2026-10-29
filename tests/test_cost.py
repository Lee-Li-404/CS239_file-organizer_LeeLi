import json
import tempfile
import unittest
from pathlib import Path

from downloads_organizer.live_test import usage_report


class UsageReportTests(unittest.TestCase):
    def report(self, events, model="gpt-5.6-luna", provider="openai"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("\n".join(json.dumps(event) for event in events))
            return usage_report(path, model, provider)

    def test_cached_input_and_reasoning_are_not_double_counted(self):
        events = []
        for agent in ["analyzer", "planner"]:
            events.extend([
                {"kind": "model_request", "agent": agent},
                {"kind": "model_response", "agent": agent, "usage": {
                    "prompt_tokens": 5000, "prompt_tokens_details": {"cached_tokens": 4000},
                    "completion_tokens": 500, "completion_tokens_details": {"reasoning_tokens": 50}}},
            ])
        result = self.report(events)
        self.assertEqual(result["usage"]["uncached_input_tokens"], 2000)
        self.assertEqual(result["usage"]["reasoning_tokens"], 100)
        self.assertAlmostEqual(result["estimated_usd"], 0.00176)
        self.assertAlmostEqual(result["estimated_usd_without_input_cache"], 0.0032)
        self.assertTrue(result["usage_complete"])

    def test_missing_response_usage_marks_estimate_incomplete(self):
        result = self.report([{"kind": "model_request", "agent": "analyzer"}])
        self.assertFalse(result["usage_complete"])
        self.assertEqual(result["usage"]["responses_with_usage"], 0)

    def test_reported_cache_writes_replace_ordinary_input_charges(self):
        result = self.report([
            {"kind": "model_request", "agent": "executor"},
            {"kind": "model_response", "agent": "executor", "usage": {
                "prompt_tokens": 5000,
                "prompt_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 800},
                "completion_tokens": 500, "completion_tokens_details": {"reasoning_tokens": 50}}},
        ])
        self.assertEqual(result["usage"]["ordinary_input_tokens"], 200)
        self.assertEqual(result["usage"]["cache_write_tokens"], 800)
        self.assertEqual(result["by_agent"]["executor"]["cache_write_tokens"], 800)
        self.assertAlmostEqual(result["estimated_usd"], 0.00092)
        self.assertAlmostEqual(result["estimated_cache_write_premium_usd"], 0.00004)

    def test_impossible_cache_token_totals_are_not_priced(self):
        with self.assertRaisesRegex(ValueError, "exceeds total"):
            self.report([{"kind": "model_response", "agent": "executor", "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 30}}}])

    def test_other_provider_usage_has_unknown_cost_even_if_model_name_matches_openai(self):
        for provider in ["nvidia", "doubao"]:
            with self.subTest(provider=provider):
                result = self.report([
                    {"kind": "model_request", "agent": "analyzer"},
                    {"kind": "model_response", "agent": "analyzer", "usage": {
                        "prompt_tokens": 100, "completion_tokens": 20}},
                ], provider=provider)
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["usage"]["input_tokens"], 100)
                self.assertTrue(result["usage_complete"])
                self.assertIsNone(result["estimated_usd"])
                self.assertNotIn("rates_usd_per_million", result)
                self.assertNotIn("source", result)


if __name__ == "__main__":
    unittest.main()
