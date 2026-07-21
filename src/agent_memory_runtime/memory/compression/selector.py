from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.memory.compression.budget import estimate_tokens
from agent_memory_runtime.tokens import TokenEstimator


def select_under_budget(
    records: list[MemoryRecord],
    *,
    token_budget: int,
    estimator: TokenEstimator | None = None,
    model: str | None = None,
) -> list[MemoryRecord]:
    # Retrieval has already combined lexical relevance, salience, confidence,
    # recency, type and source signals. Re-sorting only by salience here would
    # silently destroy query relevance and make evaluation differ from the
    # actual prompt order.
    ordered = records
    selected: list[MemoryRecord] = []
    used = 0
    for record in ordered:
        cost = estimate_tokens(record, estimator=estimator, model=model)
        if used + cost > token_budget:
            continue
        selected.append(record)
        used += cost
        if used >= token_budget:
            break
    return selected
