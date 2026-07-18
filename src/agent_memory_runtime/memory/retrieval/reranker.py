from __future__ import annotations

from agent_memory_runtime.domain.query import RetrievalResult


def rerank(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return sorted(results, key=lambda item: (item.score.total, item.memory_id), reverse=True)

