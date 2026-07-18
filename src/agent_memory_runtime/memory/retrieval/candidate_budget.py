from __future__ import annotations

from agent_memory_runtime.domain.query import RetrievalResult


def apply_candidate_budget(results: list[RetrievalResult], limit: int) -> list[RetrievalResult]:
    return results[: max(0, limit)]

