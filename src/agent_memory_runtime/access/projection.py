from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def project_record(record: MemoryRecord) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "memory_type": record.memory_type,
        "level": record.level,
        "visibility": record.visibility,
        "status": record.status,
        "temperature": record.temperature,
        "subject_id": record.subject_id,
        "content": record.content,
        "source_event_ids": list(record.source_event_ids),
        "source_memory_ids": list(record.source_memory_ids),
        "labels": list(record.labels),
        "tags": list(record.tags),
        "salience": record.salience,
        "confidence": record.confidence,
        "priority": record.priority,
        "reinforcement_count": record.reinforcement_count,
    }
