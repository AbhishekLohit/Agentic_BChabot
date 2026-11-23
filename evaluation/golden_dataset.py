"""Shared loader for the suite-tagged evaluation golden dataset."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GOLDEN_DATASET_PATH = Path(__file__).with_name("evaluation_cases.jsonl")
SUITES = {"context_builder", "helpcenter", "sbi_rag"}


def load_golden_dataset(path: Path = GOLDEN_DATASET_PATH) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        raise ValueError(f"Golden evaluation dataset not found: {path}")

    cases_by_suite: dict[str, list[dict[str, Any]]] = {suite: [] for suite in SUITES}
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in golden dataset on line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected an object in golden dataset on line {line_number}")

        suite = row.get("suite")
        if not isinstance(suite, str) or suite not in SUITES:
            raise ValueError(f"Unknown or missing suite on golden dataset line {line_number}: {suite!r}")
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Missing case id on golden dataset line {line_number}")
        if case_id in seen_ids:
            raise ValueError(f"duplicate golden dataset case id on line {line_number}: {case_id}")
        seen_ids.add(case_id)
        cases_by_suite[suite].append({key: value for key, value in row.items() if key != "suite"})

    if not seen_ids:
        raise ValueError("Golden evaluation dataset is empty")
    return cases_by_suite


def load_suite_cases(path: Path, suite: str) -> list[dict[str, Any]]:
    if not isinstance(suite, str) or suite not in SUITES:
        raise ValueError(f"Unknown evaluation suite: {suite}")
    cases = load_golden_dataset(path)[suite]
    if not cases:
        raise ValueError(f"No {suite} cases in golden evaluation dataset: {path}")
    return cases
