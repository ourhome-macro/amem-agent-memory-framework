from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def reinforcement_boost(record: MemoryRecord) -> float:
    if record.reinforcement_count <= 1:
        return 0.0
    return min(0.35, (record.reinforcement_count - 1) * 0.08)

