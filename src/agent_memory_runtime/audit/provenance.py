from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def provenance(record: MemoryRecord) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "source_event_ids": list(record.source_event_ids),
        "source_memory_ids": list(record.source_memory_ids),
        "rule_id": record.rule_id,
        "last_event_sequence": record.last_event_sequence,
    }

