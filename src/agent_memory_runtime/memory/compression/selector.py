from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.memory.compression.budget import estimate_tokens


def select_under_budget(records: list[MemoryRecord], *, token_budget: int) -> list[MemoryRecord]:
    ordered = sorted(
        records,
        key=lambda item: (
            item.salience,
            bool(item.source_memory_ids),
            item.reinforcement_count,
            item.updated_at,
        ),
        reverse=True,
    )
    selected: list[MemoryRecord] = []
    used = 0
    for record in ordered:
        cost = estimate_tokens(record)
        if used + cost > token_budget and selected:
            continue
        selected.append(record)
        used += cost
        if used >= token_budget:
            break
    return selected

