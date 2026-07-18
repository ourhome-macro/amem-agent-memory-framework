from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.audit.hashing import stable_hash
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord


@dataclass(frozen=True)
class RuntimeSnapshot:
    rule_version: str
    config_hash: str
    last_event_sequence: int
    state_hash: str
    memory_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_version": self.rule_version,
            "config_hash": self.config_hash,
            "last_event_sequence": self.last_event_sequence,
            "state_hash": self.state_hash,
            "memory_count": self.memory_count,
        }


def build_snapshot(
    *,
    config: RuntimeConfig,
    events: list[Event],
    records: list[MemoryRecord],
) -> RuntimeSnapshot:
    sorted_records = sorted(records, key=lambda item: item.memory_id)
    state_payload = {
        "records": [record.to_dict() for record in sorted_records],
        "rule_version": config.rule_version,
        "config_hash": config.config_hash,
        "last_event_sequence": events[-1].sequence if events else 0,
    }
    return RuntimeSnapshot(
        rule_version=config.rule_version,
        config_hash=config.config_hash,
        last_event_sequence=events[-1].sequence if events else 0,
        state_hash=stable_hash(state_payload),
        memory_count=len(records),
    )
