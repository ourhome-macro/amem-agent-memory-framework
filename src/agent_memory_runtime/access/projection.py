from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def project_record(record: MemoryRecord) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "memory_type": record.memory_type,
        "scope": record.scope,
        "layer": record.layer,
        "subject_id": record.subject_id,
        "content": record.content,
        "source_event_ids": list(record.source_event_ids),
        "source_memory_ids": list(record.source_memory_ids),
        "labels": list(record.labels),
        "tags": list(record.tags),
        "salience": record.salience,
        "confidence": record.confidence,
        "reinforcement_count": record.reinforcement_count,
    }

