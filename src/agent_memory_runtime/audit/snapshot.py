from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.audit.hashing import stable_hash
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.enums import MemoryLayer, MemoryStatus
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord


@dataclass(frozen=True)
class RuntimeSnapshot:
    rule_version: str
    config_hash: str
    last_event_sequence: int
    state_hash: str
    memory_count: int
    hot_memory_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_version": self.rule_version,
            "config_hash": self.config_hash,
            "last_event_sequence": self.last_event_sequence,
            "state_hash": self.state_hash,
            "memory_count": self.memory_count,
            "hot_memory_ids": list(self.hot_memory_ids),
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
        hot_memory_ids=_hot_memory_ids(
            records,
            limit=config.fast_response.snapshot_hot_memory_limit,
        ),
    )


def _hot_memory_ids(records: list[MemoryRecord], *, limit: int) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    allowed_layers = {MemoryLayer.CORE.value, MemoryLayer.WORKING.value}
    hot_records = [
        record
        for record in records
        if record.status == MemoryStatus.ACTIVE.value and record.layer in allowed_layers
    ]
    hot_records.sort(
        key=lambda item: (
            item.salience,
            bool(item.source_memory_ids),
            item.reinforcement_count,
            item.updated_at,
            item.memory_id,
        ),
        reverse=True,
    )
    return tuple(record.memory_id for record in hot_records[:limit])
