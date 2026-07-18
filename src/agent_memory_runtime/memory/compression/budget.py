from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def estimate_tokens(record: MemoryRecord) -> int:
    return max(1, len(record.content.split()) + 12)

