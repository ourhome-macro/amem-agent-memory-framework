from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryLayer
from agent_memory_runtime.domain.query import MemoryQuery

_ARCHIVAL_QUERY_MARKERS = (
    "previous",
    "last time",
    "remember",
    "之前",
    "以前",
    "上次",
    "还记得",
    "曾经",
)


def normalize_query(query: MemoryQuery) -> MemoryQuery:
    layers = query.layers or _default_layers(query.text)
    return MemoryQuery(
        agent_id=query.agent_id,
        text=query.text,
        tenant_id=query.tenant_id,
        user_id=query.user_id,
        session_id=query.session_id,
        scopes=query.scopes,
        memory_types=query.memory_types,
        layers=layers,
        tags=query.tags,
        source_memory_ids=query.source_memory_ids,
        limit=query.limit,
    )


def _default_layers(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    if any(marker in normalized for marker in _ARCHIVAL_QUERY_MARKERS):
        return (
            MemoryLayer.CORE.value,
            MemoryLayer.WORKING.value,
            MemoryLayer.ARCHIVAL.value,
        )
    return (MemoryLayer.CORE.value, MemoryLayer.WORKING.value)
