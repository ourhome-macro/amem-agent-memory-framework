from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def consolidate(records: list[MemoryRecord]) -> list[MemoryRecord]:
    return sorted(records, key=lambda item: (item.session_id, item.memory_type, item.memory_id))

