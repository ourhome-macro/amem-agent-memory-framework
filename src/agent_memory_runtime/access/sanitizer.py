from __future__ import annotations

from agent_memory_runtime.access.principal import Principal
from agent_memory_runtime.domain.enums import MemoryLabel
from agent_memory_runtime.domain.memory import MemoryRecord


def sanitize(record: MemoryRecord, principal: Principal) -> MemoryRecord:
    if MemoryLabel.SENSITIVE.value not in set(record.labels) or principal.is_auditor:
        return record
    return MemoryRecord.from_dict(
        {
            **record.to_dict(),
            "content": "[sensitive memory redacted]",
            "metadata": {},
        }
    )

