from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryStatus
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery


def hard_filter(record: MemoryRecord, query: MemoryQuery) -> bool:
    if record.status != MemoryStatus.ACTIVE.value:
        return False
    if query.session_id is not None and record.session_id != query.session_id:
        return False
    if query.scopes and record.scope not in set(query.scopes):
        return False
    if query.memory_types and record.memory_type not in set(query.memory_types):
        return False
    if query.layers and record.layer not in set(query.layers):
        return False
    if query.tags and not set(query.tags) & set(record.tags):
        return False
    return True

