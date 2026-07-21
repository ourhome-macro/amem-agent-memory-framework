from __future__ import annotations

from dataclasses import dataclass
from math import log2


@dataclass(frozen=True)
class RetrievalEvalResult:
    case_id: str
    passed: bool
    expected_memory_ids: tuple[str, ...]
    selected_memory_ids: tuple[str, ...]
    forbidden_memory_ids: tuple[str, ...] = ()
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    reciprocal_rank: float = 0.0
    ndcg_at_k: float = 0.0


def evaluate_contains(
    case_id: str,
    expected: list[str],
    selected: list[str],
) -> RetrievalEvalResult:
    return evaluate_retrieval(case_id, expected, selected)


def evaluate_retrieval(
    case_id: str,
    expected: list[str],
    selected: list[str],
    *,
    forbidden: list[str] | None = None,
    relevance: dict[str, float] | None = None,
    k: int | None = None,
) -> RetrievalEvalResult:
    if k is not None and k <= 0:
        raise ValueError("retrieval evaluation k must be positive")
    expected_set = set(expected)
    forbidden_set = set(forbidden or ())
    cutoff = max(1, k or len(selected) or len(expected) or 1)
    top = selected[:cutoff]
    hits = len(set(top) & expected_set)
    precision = hits / cutoff
    recall = hits / len(expected_set) if expected_set else 1.0
    first_rank = next(
        (index for index, item in enumerate(top, start=1) if item in expected_set),
        None,
    )
    grades = relevance or {item: 1.0 for item in expected}
    if any(float(value) < 0 for value in grades.values()):
        raise ValueError("retrieval relevance grades cannot be negative")
    actual_dcg = _dcg([float(grades.get(item, 0.0)) for item in top])
    ideal_dcg = _dcg(sorted((float(item) for item in grades.values()), reverse=True)[:cutoff])
    return RetrievalEvalResult(
        case_id=case_id,
        passed=expected_set <= set(top) and not forbidden_set.intersection(selected),
        expected_memory_ids=tuple(expected),
        selected_memory_ids=tuple(selected),
        forbidden_memory_ids=tuple(forbidden or ()),
        precision_at_k=round(precision, 4),
        recall_at_k=round(recall, 4),
        reciprocal_rank=round(0.0 if first_rank is None else 1 / first_rank, 4),
        ndcg_at_k=round(0.0 if ideal_dcg == 0 else actual_dcg / ideal_dcg, 4),
    )


def _dcg(grades: list[float]) -> float:
    return sum(
        (2**grade - 1) / log2(index + 1)
        for index, grade in enumerate(grades, start=1)
    )
