from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryScope
from agent_memory_runtime.domain.memory import MemoryRecord


def can_flow_to_scope(record: MemoryRecord, target_scope: str) -> bool:
    if record.scope == MemoryScope.PRIVATE.value:
        return target_scope == MemoryScope.PRIVATE.value
    if record.scope == MemoryScope.SHARED.value:
        return target_scope in {MemoryScope.SHARED.value, MemoryScope.PRIVATE.value}
    return True

