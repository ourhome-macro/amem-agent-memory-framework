from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.retrieval.filters import hard_filter
from agent_memory_runtime.memory.retrieval.lexical import (
    lexical_tokens,
    searchable_record_text,
)


def select_candidates(
    records: list[MemoryRecord],
    query: MemoryQuery,
    *,
    limit: int,
    offset: int = 0,
) -> list[MemoryRecord]:
    query_terms = lexical_tokens(query.text)

    def order_key(record: MemoryRecord) -> tuple[object, ...]:
        overlap = len(query_terms & lexical_tokens(searchable_record_text(record)))
        return (
            overlap,
            _temperature_rank(record.temperature),
            record.priority,
            record.salience,
            record.confidence,
            record.reinforcement_count,
            record.updated_at,
            record.memory_id,
        )

    eligible = [record for record in records if hard_filter(record, query)]
    eligible.sort(key=order_key, reverse=True)
    start = max(0, offset)
    return eligible[start : start + max(0, limit)]


def _temperature_rank(value: str) -> int:
    if value == "hot":
        return 3
    if value == "warm":
        return 2
    if value == "cold":
        return 1
    return 0
