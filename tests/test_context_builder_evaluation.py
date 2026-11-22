"""Offline tests for the isolated Context Builder evaluator; no API keys needed."""
import asyncio
from datetime import date
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from evaluation.context_builder_run import (
    DEFAULT_DATASET,
    build_state,
    evaluate_one,
    load_dataset,
    main,
    score_output,
    summarize,
)


class DatasetTests(unittest.TestCase):
    def test_all_100_cases_validate_and_two_are_flagged(self):
        cases = load_dataset(DEFAULT_DATASET, as_of=date(2026, 11, 22))
        self.assertEqual(len(cases), 100)
        self.assertEqual(sum(bool(c["auto_eval_ready"]) for c in cases), 98)
        self.assertEqual([c["id"] for c in cases if not c["auto_eval_ready"]], ["CB086", "CB098"])

    def test_cli_offline(self):
        self.assertEqual(main(["--validate-only"]), 0)

    def test_duplicate_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "dup.jsonl"
            line = DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0]
            p.write_text(line + "\n" + line + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_dataset(p, as_of=date(2026, 11, 22))

    def test_bad_history_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "bad.jsonl"
            row = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
            row["chat_history_messages"] = [{"role": "admin", "content": "hi"}]
            p.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "chat_history_messages"):
                load_dataset(p, as_of=date(2026, 11, 22))

    def test_reference_date_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "date mismatch"):
            load_dataset(DEFAULT_DATASET, as_of=date(2027, 1, 1))

    def test_expected_query_format_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "bad.jsonl"
            row = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
            row["expected_output"] = {"contextual_queries": "not-a-list"}
            p.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contextual_queries"):
                load_dataset(p, as_of=date(2026, 11, 22))


class ScoringTests(unittest.TestCase):
    def test_near_exact_case_spacing_and_punctuation(self):
        result = score_output(["How many direct debits in 2025?"], [" HOW many   direct debits in 2025."])
        self.assertEqual(result["verdict"], "exact_match")

    def test_equivalent_paraphrase_requires_human_review(self):
        result = score_output(["Show my payments in 2024."], ["List all payments for 2024."])
        self.assertEqual(result["verdict"], "needs_human_review")
        self.assertFalse(result["normalized_exact_match"])

    def test_split_count_mismatch(self):
        result = score_output(["Show my payments", "Show refunds"], ["Show my payments and refunds"])
        self.assertEqual(result["verdict"], "potential_mismatch")
        self.assertFalse(result["query_count_match"])

    def test_missing_reference_date_anchor(self):
        result = score_output(["Show payments from 2026-10-01 to 2026-10-31"], ["Show payments last month"])
        self.assertEqual(result["verdict"], "potential_mismatch")
        self.assertFalse(result["date_anchors_match"])

    def test_invalid_output(self):
        self.assertEqual(score_output(["x"], [""])["verdict"], "invalid_output")
        self.assertEqual(score_output(["x"], None)["verdict"], "invalid_output")

    def test_summary_does_not_call_draft_match_accuracy(self):
        cases = load_dataset(DEFAULT_DATASET, as_of=date(2026, 11, 22))
        results = [
            {"verdict": "exact_match", "latency_seconds": 1.0, "total_tokens": 10},
            {"verdict": "needs_human_review", "latency_seconds": 2.0, "total_tokens": 20},
        ]
        summary = summarize(results, cases, as_of=date(2026, 11, 22))
        self.assertEqual(summary["exact_matches_to_draft_ground_truth"], 1)
        self.assertEqual(summary["requires_human_review"], 1)
        self.assertEqual(summary["excluded_pending_rule_decision"], ["CB086", "CB098"])
        self.assertEqual(summary["mean_latency_seconds"], 1.5)
        self.assertIn("DRAFT", summary["ground_truth_status"])


class IntegrationWithActualContextBuilderTests(unittest.TestCase):
    def test_actual_agent_receives_frozen_date_and_structured_history(self):
        """Import and call project's actual context_builder.py with a fake model.

        No LangChain dependency, network, or secret is needed in this offline test.
        """
        fake_models = types.ModuleType("app_models")

        class SimpleModel:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
            def __repr__(self):
                return f"SimpleModel({self.__dict__!r})"

        fake_models.UIUserRequest = SimpleModel
        fake_models.Graph_State = SimpleModel
        fake_models.ChatMessage = SimpleModel
        fake_models.FallbackResponse = SimpleModel
        fake_models.ContextBuilderResponse = SimpleModel

        fake_utils = types.ModuleType("utils")
        fake_metrics_module = types.ModuleType("utils.agent_utils")
        fake_metrics_module.agent_metrics = lambda raw, latency: SimpleModel(
            model_used="stub", latency=latency,
            model_cost_without_cache=0, model_cost_with_cache=0,
            input_tokens=5, output_tokens=2, total_tokens=7,
            cached_tokens=0, cache_creation_tokens=0,
        )
        fake_utils.agent_utils = fake_metrics_module

        src = Path(__file__).resolve().parents[1] / "src" / "agents" / "context_builder.py"
        spec = importlib.util.spec_from_file_location("_fintell_real_context_builder_test", src)
        self.assertIsNotNone(spec)
        real_agent_module = importlib.util.module_from_spec(spec)

        class FakeLLM:
            def __init__(self):
                self.messages = None
            def with_structured_output(self, schema, include_raw=False):
                self.test_schema = schema
                self.assert_raw = include_raw
                return self
            async def ainvoke(self, messages):
                self.messages = messages
                return {
                    "parsed": SimpleModel(contextual_queries=["How many direct debits were in 2025?"]),
                    "raw": object(),
                }

        dataset = load_dataset(DEFAULT_DATASET, as_of=date(2026, 11, 22))
        case = dataset[1]
        fake_llm = FakeLLM()
        with patch.dict(sys.modules, {
            "app_models": fake_models,
            "utils": fake_utils,
            "utils.agent_utils": fake_metrics_module,
        }):
            spec.loader.exec_module(real_agent_module)
            result = asyncio.run(evaluate_one(
                case, model=fake_llm, prompt=SimpleModel(template="Test context prompt"),
                logger=logging.getLogger("context_builder_fake_unit_test"),
                context_module=real_agent_module, models_module=fake_models,
                as_of=date(2026, 11, 22),
            ))
        self.assertEqual(result["verdict"], "needs_human_review")
        self.assertEqual(result["total_tokens"], 7)
        self.assertIn("Today's date: 2026-11-22", fake_llm.messages[1]["content"])
        self.assertIn("2024", fake_llm.messages[1]["content"])
        self.assertTrue(fake_llm.assert_raw)


if __name__ == "__main__":
    unittest.main()
