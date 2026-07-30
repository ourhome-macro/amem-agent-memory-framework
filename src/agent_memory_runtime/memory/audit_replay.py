from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.domain.enums import MemoryOperation
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.tombstone import MemoryTombstone
from agent_memory_runtime.memory.stores.base import AuditStore, MemoryStore, TombstoneStore


@dataclass(frozen=True)
class MemoryAuditReplayReport:
    applied_logs: int
    skipped_logs: int
    upserted_memory_ids: tuple[str, ...]
    deleted_memory_ids: tuple[str, ...]
    tombstoned_memory_ids: tuple[str, ...]
    final_memory_ids: tuple[str, ...]


def replay_memory_audit_logs(
    *,
    audit_store: AuditStore,
    memory_store: MemoryStore,
    tombstone_store: TombstoneStore | None = None,
    clear_existing: bool = True,
) -> MemoryAuditReplayReport:
    """Rebuild current MemoryRecord state from MemoryAuditLog before/after entries."""

    logs = audit_store.list_memory_logs()
    state: dict[str, MemoryRecord] = {}
    applied = 0
    skipped = 0
    upserted: list[str] = []
    deleted: list[str] = []
    tombstoned: list[str] = []

    for log in logs:
        memory_id = _memory_id_for_log(log)
        if memory_id is None:
            skipped += 1
            continue
        if log.after_record is None:
            state.pop(memory_id, None)
            deleted.append(memory_id)
            tombstone = _tombstone_for_log(log, memory_id)
            if tombstone is not None:
                tombstoned.append(memory_id)
            applied += 1
            continue
        state[log.after_record.memory_id] = log.after_record
        upserted.append(log.after_record.memory_id)
        applied += 1

    records = sorted(state.values(), key=lambda item: item.memory_id)
    if clear_existing:
        memory_store.replace_all(records)
        if tombstone_store is not None:
            tombstone_store.clear()
            for log in logs:
                memory_id = _memory_id_for_log(log)
                if memory_id is None or log.after_record is not None:
                    continue
                tombstone = _tombstone_for_log(log, memory_id)
                if tombstone is not None:
                    tombstone_store.put(tombstone)
    else:
        for record in records:
            memory_store.upsert(record)
        if tombstone_store is not None:
            for log in logs:
                memory_id = _memory_id_for_log(log)
                if memory_id is None or log.after_record is not None:
                    continue
                tombstone = _tombstone_for_log(log, memory_id)
                if tombstone is not None:
                    tombstone_store.put(tombstone)
                    memory_store.delete(memory_id)

    return MemoryAuditReplayReport(
        applied_logs=applied,
        skipped_logs=skipped,
        upserted_memory_ids=tuple(dict.fromkeys(upserted)),
        deleted_memory_ids=tuple(dict.fromkeys(deleted)),
        tombstoned_memory_ids=tuple(dict.fromkeys(tombstoned)),
        final_memory_ids=tuple(record.memory_id for record in records),
    )


def _memory_id_for_log(log: object) -> str | None:
    after = getattr(log, "after_record", None)
    before = getattr(log, "before_record", None)
    if after is not None:
        return str(after.memory_id)
    if getattr(log, "memory_id", None) is not None:
        return str(log.memory_id)
    if before is not None:
        return str(before.memory_id)
    return None


def _tombstone_for_log(log: object, memory_id: str) -> MemoryTombstone | None:
    if getattr(log, "action", None) != MemoryOperation.DELETE.value:
        return None
    before = getattr(log, "before_record", None)
    if before is None:
        return None
    return MemoryTombstone(
        memory_id=memory_id,
        tenant_id=str(getattr(log, "tenant_id", before.tenant_id)),
        deleted_through_sequence=before.last_event_sequence + 1,
        deleted_at=str(getattr(log, "created_at", "")),
        reason=str(getattr(log, "reason", "") or "audit_replay_delete"),
        source_event_ids=before.source_event_ids,
        metadata={
            "audit_id": str(getattr(log, "audit_id", "")),
            "proposal_id": str(getattr(log, "proposal_id", "")),
            "replayed_from": "memory_audit_log",
        },
    )
