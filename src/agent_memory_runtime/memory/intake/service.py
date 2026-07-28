from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_memory_runtime.domain.enums import (
    EventKind,
    MemoryLayer,
    MemoryOperation,
    MemoryScope,
)
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.domain.tombstone import MemoryTombstone
from agent_memory_runtime.governance.retention import (
    RetentionAction,
    RetentionExecutor,
    RetentionPlan,
)
from agent_memory_runtime.memory.intake.models import (
    MemoryToolIdentity,
    MemoryToolResult,
)

_SAVE_KINDS = {
    EventKind.PREFERENCE.value,
    EventKind.BELIEF.value,
    EventKind.TASK_OUTCOME.value,
}
_DELETE_EVENT_KIND = "memory.delete_requested"
_ARCHIVE_EVENT_KIND = "memory.archive_requested"


class MemoryIntakeError(ValueError):
    pass


class MemoryIntakeService:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime

    def save_memory(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
        idempotency_key: str | None = None,
    ) -> MemoryToolResult:
        event = self._build_write_event(
            arguments,
            identity=identity,
            idempotency_key=idempotency_key,
            default_action="save_memory",
        )
        result = self.runtime.ingest(event)
        return MemoryToolResult(
            status="succeeded",
            action="save_memory",
            event=result.event,
            memory_ids=tuple(record.memory_id for record in result.records),
        )

    def revise_memory(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
        idempotency_key: str | None = None,
    ) -> MemoryToolResult:
        target_memory_id = _optional_str(arguments.get("target_memory_id"))
        values = dict(arguments)
        values["operation"] = str(values.get("operation") or MemoryOperation.REVISE.value)
        if target_memory_id:
            source_memory_ids = list(values.get("source_memory_ids") or [])
            if target_memory_id not in source_memory_ids:
                source_memory_ids.insert(0, target_memory_id)
            values["source_memory_ids"] = source_memory_ids
        event = self._build_write_event(
            values,
            identity=identity,
            idempotency_key=idempotency_key,
            default_action="revise_memory",
        )
        result = self.runtime.ingest(event)
        return MemoryToolResult(
            status="succeeded",
            action="revise_memory",
            event=result.event,
            memory_ids=tuple(record.memory_id for record in result.records),
        )

    def forget_memory(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
        idempotency_key: str | None = None,
    ) -> MemoryToolResult:
        mode = str(arguments.get("mode") or "delete").strip().casefold()
        if mode not in {"delete", "archive"}:
            raise MemoryIntakeError("forget_memory mode must be delete or archive")
        record = self._resolve_forget_target(arguments, identity=identity)
        if record is None:
            return MemoryToolResult(
                status="not_found",
                action="forget_memory",
                reason="target_memory_not_found",
            )
        reason = str(arguments.get("reason") or "user_requested_forget")
        event = Event(
            event_id=_event_id("forget_memory", idempotency_key),
            kind=_DELETE_EVENT_KIND if mode == "delete" else _ARCHIVE_EVENT_KIND,
            actor_id=identity.actor_id,
            session_id=identity.session_id,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            labels=identity.labels,
            tags=_dedupe((*identity.tags, "memory-intake", "forget-memory")),
            payload={
                "agent_id": identity.agent_id,
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
                "subject_id": record.subject_id,
                "memory_id": record.memory_id,
                "mode": mode,
                "reason": reason,
                "query": arguments.get("query"),
            },
        )
        stored_event = self.runtime.event_store.append(event)
        if mode == "archive":
            self.runtime.memory_store.upsert(
                replace(
                    record,
                    layer=MemoryLayer.ARCHIVAL.value,
                    status="archived",
                    last_operation=MemoryOperation.ARCHIVE.value,
                    source_event_ids=_dedupe((*record.source_event_ids, stored_event.event_id)),
                    updated_at=stored_event.occurred_at,
                    last_event_sequence=stored_event.sequence,
                )
            )
            self.runtime.refresh_snapshot()
            return MemoryToolResult(
                status="succeeded",
                action="forget_memory",
                event=stored_event,
                archived_memory_ids=(record.memory_id,),
            )

        snapshot = self.runtime.snapshot()
        report = RetentionExecutor(
            memory_store=self.runtime.memory_store,
            audit_store=self.runtime.audit_store,
            tombstone_store=self.runtime.tombstone_store,
            transaction_manager=getattr(self.runtime, "transaction_manager", None),
        ).apply(
            RetentionPlan(
                actions=(RetentionAction(record.memory_id, "delete", reason),),
                current_sequence=stored_event.sequence,
            ),
            snapshot=snapshot,
        )
        self.runtime.refresh_snapshot()
        if not report.deleted_memory_ids:
            # The projection may already be absent. Keep a tombstone anyway so replay
            # does not resurrect older source events for this memory id.
            self.runtime.tombstone_store.put(
                MemoryTombstone(
                    memory_id=record.memory_id,
                    tenant_id=record.tenant_id,
                    deleted_through_sequence=stored_event.sequence,
                    deleted_at=datetime.now(UTC).isoformat(),
                    reason=reason,
                    source_event_ids=record.source_event_ids,
                )
            )
        return MemoryToolResult(
            status="succeeded",
            action="forget_memory",
            event=stored_event,
            tombstoned_memory_ids=(record.memory_id,),
        )

    def _build_write_event(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
        idempotency_key: str | None,
        default_action: str,
    ) -> Event:
        kind = str(arguments.get("kind") or EventKind.PREFERENCE.value)
        if kind not in _SAVE_KINDS:
            raise MemoryIntakeError(f"unsupported memory event kind: {kind}")
        content = str(arguments.get("content") or "").strip()
        if not content:
            raise MemoryIntakeError("memory content cannot be empty")
        key = str(arguments.get("key") or "").strip()
        if not key:
            raise MemoryIntakeError("memory key cannot be empty")
        subject_id = str(arguments.get("subject_id") or identity.user_id or identity.actor_id)
        payload: dict[str, Any] = {
            "agent_id": identity.agent_id,
            "tenant_id": identity.tenant_id,
            "user_id": identity.user_id,
            "subject_id": subject_id,
            "key": key,
            "operation": str(
                arguments.get("operation") or _default_operation(kind, default_action)
            ),
            "scope": str(arguments.get("scope") or MemoryScope.PRIVATE.value),
            "layer": str(arguments.get("layer") or _default_layer(kind)),
            "salience": _clamp_float(arguments.get("salience"), default=0.9),
            "confidence": _clamp_float(arguments.get("confidence"), default=0.9),
            "source_memory_ids": _tuple_str(arguments.get("source_memory_ids")),
            "evidence_event_ids": _tuple_str(arguments.get("evidence_event_ids")),
            "reason": arguments.get("reason"),
            "explicit": bool(arguments.get("explicit", True)),
            "intake_action": default_action,
        }
        if "visible_to" in arguments:
            payload["visible_to"] = _tuple_str(arguments.get("visible_to"))
        if "value" in arguments:
            payload["value"] = arguments.get("value")
        if "truth_value" in arguments:
            payload["truth_value"] = arguments.get("truth_value")
        if kind == EventKind.PREFERENCE.value:
            payload["preference"] = content
        elif kind == EventKind.BELIEF.value:
            payload["belief"] = content
        elif kind == EventKind.TASK_OUTCOME.value:
            payload["task"] = key
            payload["outcome"] = content
            payload["result"] = str(arguments.get("result") or "remembered")
        return Event(
            event_id=_event_id(default_action, idempotency_key),
            kind=kind,
            actor_id=identity.actor_id,
            session_id=identity.session_id,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            labels=identity.labels,
            tags=_dedupe((*identity.tags, "memory-intake", default_action)),
            payload=payload,
        )

    def _resolve_forget_target(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
    ) -> object | None:
        memory_id = _optional_str(arguments.get("memory_id"))
        if memory_id:
            record = self.runtime.memory_store.get(memory_id)
            if record is None or record.tenant_id != identity.tenant_id:
                return None
            if record.user_id is not None and record.user_id != identity.user_id:
                return None
            return record
        query_text = _optional_str(arguments.get("query"))
        if not query_text:
            raise MemoryIntakeError("forget_memory requires memory_id or query")
        records, _trace = self.runtime.retrieve(
            MemoryQuery(
                agent_id=identity.agent_id,
                text=query_text,
                session_id=identity.session_id,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                session_policy="profile",
                limit=2,
            )
        )
        if len(records) != 1:
            return None
        return records[0]


def _default_operation(kind: str, action: str) -> str:
    if action == "revise_memory":
        return MemoryOperation.REVISE.value
    if kind in {EventKind.PREFERENCE.value, EventKind.TASK_OUTCOME.value}:
        return MemoryOperation.REVISE.value
    return MemoryOperation.CREATE.value


def _default_layer(kind: str) -> str:
    if kind in {EventKind.PREFERENCE.value, EventKind.TASK_OUTCOME.value}:
        return MemoryLayer.CORE.value
    return MemoryLayer.WORKING.value


def _event_id(action: str, idempotency_key: str | None) -> str:
    if idempotency_key:
        return f"memory-intake:{action}:{idempotency_key}"
    return f"memory-intake:{action}:{uuid4()}"


def _clamp_float(value: object, *, default: float) -> float:
    if value is None:
        return default
    return round(min(max(float(value), 0.0), 1.0), 4)


def _tuple_str(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),)


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
