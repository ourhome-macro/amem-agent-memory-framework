from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryVisibility
from agent_memory_runtime.domain.memory import MemoryRecord


def can_flow_to_visibility(record: MemoryRecord, target_visibility: str) -> bool:
    if record.visibility == MemoryVisibility.PRIVATE.value:
        return target_visibility == MemoryVisibility.PRIVATE.value
    if record.visibility == MemoryVisibility.SHARED.value:
        return target_visibility in {
            MemoryVisibility.SHARED.value,
            MemoryVisibility.PRIVATE.value,
        }
    return True
