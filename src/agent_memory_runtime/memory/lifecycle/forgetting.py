from __future__ import annotations

from dataclasses import replace

from agent_memory_runtime.domain.enums import MemoryLayer, MemoryOperation, MemoryStatus
from agent_memory_runtime.domain.memory import MemoryRecord


def archive_low_value(record: MemoryRecord, *, threshold: float) -> MemoryRecord:
    if record.salience >= threshold:
        return record
    return replace(
        record,
        layer=MemoryLayer.ARCHIVAL.value,
        status=MemoryStatus.ARCHIVED.value,
        last_operation=MemoryOperation.ARCHIVE.value,
    )

