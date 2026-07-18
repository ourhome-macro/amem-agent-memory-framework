from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalEvalResult:
    case_id: str
    passed: bool
    expected_memory_ids: tuple[str, ...]
    selected_memory_ids: tuple[str, ...]


def evaluate_contains(
    case_id: str,
    expected: list[str],
    selected: list[str],
) -> RetrievalEvalResult:
    expected_set = set(expected)
    return RetrievalEvalResult(
        case_id=case_id,
        passed=expected_set <= set(selected),
        expected_memory_ids=tuple(expected),
        selected_memory_ids=tuple(selected),
    )
