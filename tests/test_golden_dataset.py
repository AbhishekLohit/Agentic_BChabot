from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from evaluation.golden_dataset import (
    GOLDEN_DATASET_PATH,
    load_golden_dataset,
    load_suite_cases,
)


class GoldenDatasetTests(unittest.TestCase):
    def test_master_contains_all_three_suite_slices(self) -> None:
        cases = load_golden_dataset()
        self.assertEqual(
            {suite: len(rows) for suite, rows in cases.items()},
            {"context_builder": 100, "helpcenter": 6, "sbi_rag": 22},
        )

    def test_suite_loader_returns_only_unwrapped_cases(self) -> None:
        cases = load_suite_cases(GOLDEN_DATASET_PATH, "helpcenter")
        self.assertEqual(len(cases), 6)
        self.assertTrue(all("suite" not in case for case in cases))
        self.assertTrue(all(case["id"].startswith("help-") for case in cases))

    def test_duplicate_case_ids_are_rejected_across_the_master(self) -> None:
        row = {"suite": "context_builder", "id": "CB001"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "duplicate.jsonl"
            path.write_text(
                json.dumps(row) + "\n" + json.dumps(row) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_golden_dataset(path)

    def test_unknown_suite_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unknown_suite.jsonl"
            path.write_text(
                json.dumps({"suite": "unknown", "id": "X001"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unknown or missing suite"):
                load_golden_dataset(path)


if __name__ == "__main__":
    unittest.main()
