from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryStatus
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
    statuses = query.statuses or _default_statuses(query.text)
    return MemoryQuery(
        agent_id=query.agent_id,
        text=query.text,
        tenant_id=query.tenant_id,
        user_id=query.user_id,
        session_id=query.session_id,
        memory_types=query.memory_types,
        levels=query.levels,
        statuses=statuses,
        visibilities=query.visibilities,
        tags=query.tags,
        source_memory_ids=query.source_memory_ids,
        limit=query.limit,
        session_policy=query.session_policy,
        retrieval_mode=query.retrieval_mode,
    )


def plan_query(query: MemoryQuery) -> tuple[MemoryQuery, dict[str, object]]:
    planned = normalize_query(query)
    metadata: dict[str, object] = {
        "source": "query_normalizer",
        "tool_called": False,
    }
    return planned, metadata


def _default_statuses(text: str) -> tuple[str, ...]:
    if requests_archival_recall(text):
        return (
            MemoryStatus.ACTIVE.value,
            MemoryStatus.ARCHIVED.value,
            MemoryStatus.SUPERSEDED.value,
        )
    return (MemoryStatus.ACTIVE.value,)


def requests_archival_recall(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in _ARCHIVAL_QUERY_MARKERS)
