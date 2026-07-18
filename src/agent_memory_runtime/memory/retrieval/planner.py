from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryLayer
from agent_memory_runtime.domain.query import MemoryQuery


def normalize_query(query: MemoryQuery) -> MemoryQuery:
    layers = query.layers or (MemoryLayer.CORE.value, MemoryLayer.WORKING.value)
    return MemoryQuery(
        agent_id=query.agent_id,
        text=query.text,
        session_id=query.session_id,
        scopes=query.scopes,
        memory_types=query.memory_types,
        layers=layers,
        tags=query.tags,
        source_memory_ids=query.source_memory_ids,
        limit=query.limit,
    )

