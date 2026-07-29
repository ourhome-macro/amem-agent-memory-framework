from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent_memory_runtime.domain.enums import (
    EventKind,
    MemoryLayer,
    MemoryOperation,
    MemoryScope,
)
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.intake.models import (
    MemoryProposal,
    MemoryToolIdentity,
    MemoryToolResult,
)
from agent_memory_runtime.memory.semantic_state import state_fact_metadata
from agent_memory_runtime.memory.service import MemoryService, memory_type_from_kind

_SAVE_KINDS = {
    EventKind.PREFERENCE.value,
    EventKind.BELIEF.value,
    EventKind.TASK_OUTCOME.value,
}


class MemoryIntakeError(ValueError):
    pass


class MemoryIntakeService:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime
        self.memory_service = MemoryService(
            memory_store=runtime.memory_store,
            audit_store=runtime.audit_store,
            tombstone_store=runtime.tombstone_store,
            transaction_manager=getattr(runtime, "transaction_manager", None),
        )

    def save_memory(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
        idempotency_key: str | None = None,
    ) -> MemoryToolResult:
        proposal = self._build_write_proposal(
            arguments,
            identity=identity,
            idempotency_key=idempotency_key,
            source="save_memory",
            default_action="save_memory",
        )
        result = self.memory_service.apply_proposal(proposal)
        _refresh_snapshot(self.runtime)
        return MemoryToolResult(
            status=result.status,
            action="save_memory",
            proposal_id=proposal.proposal_id,
            audit_id=None if result.audit_log is None else result.audit_log.audit_id,
            memory_ids=result.memory_ids,
            reason=result.reason,
            retryable=result.retryable,
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
            record = self.runtime.memory_store.get(target_memory_id)
            if record is not None:
                values.setdefault("expected_version", record.version)
        proposal = self._build_write_proposal(
            values,
            identity=identity,
            idempotency_key=idempotency_key,
            source="revise_memory",
            default_action="revise_memory",
        )
        result = self.memory_service.apply_proposal(proposal)
        _refresh_snapshot(self.runtime)
        return MemoryToolResult(
            status=result.status,
            action="revise_memory",
            proposal_id=proposal.proposal_id,
            audit_id=None if result.audit_log is None else result.audit_log.audit_id,
            memory_ids=result.memory_ids,
            reason=result.reason,
            retryable=result.retryable,
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
        proposal = MemoryProposal(
            proposal_id=_proposal_id("forget_memory", idempotency_key),
            source="forget_memory",
            action=(
                MemoryOperation.DELETE.value
                if mode == "delete"
                else MemoryOperation.ARCHIVE.value
            ),
            target_memory_id=record.memory_id,
            subject_id=record.subject_id,
            key=str(record.metadata.get("key") or record.memory_id),
            content=record.content,
            memory_type=record.memory_type,
            layer=record.layer,
            scope=record.scope,
            visible_to=record.visible_to,
            confidence=record.confidence,
            salience=record.salience,
            source_message_ids=(),
            source_memory_ids=(record.memory_id,),
            evidence_text=str(arguments.get("query") or ""),
            reason=str(arguments.get("reason") or "user_requested_forget"),
            actor_id=identity.actor_id,
            agent_id=identity.agent_id,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            session_id=identity.session_id,
            labels=identity.labels,
            tags=_dedupe((*identity.tags, "memory-intake", "forget-memory")),
            expected_version=record.version,
        )
        result = self.memory_service.apply_proposal(proposal)
        _refresh_snapshot(self.runtime)
        return MemoryToolResult(
            status=result.status,
            action="forget_memory",
            proposal_id=proposal.proposal_id,
            audit_id=None if result.audit_log is None else result.audit_log.audit_id,
            memory_ids=result.memory_ids,
            tombstoned_memory_ids=result.tombstoned_memory_ids,
            archived_memory_ids=result.archived_memory_ids,
            reason=result.reason,
            retryable=result.retryable,
        )

    def _build_write_proposal(
        self,
        arguments: dict[str, Any],
        *,
        identity: MemoryToolIdentity,
        idempotency_key: str | None,
        source: str,
        default_action: str,
    ) -> MemoryProposal:
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
        action = str(arguments.get("operation") or _default_operation(kind, default_action))
        if action == MemoryOperation.SUPERSEDE.value:
            action = MemoryOperation.REVISE.value
        target_memory_id = _optional_str(arguments.get("target_memory_id"))
        content_value = _content_for_kind(kind, key=key, content=content, arguments=arguments)
        return MemoryProposal(
            proposal_id=_proposal_id(default_action, idempotency_key),
            source=source,
            action=action,
            target_memory_id=target_memory_id,
            subject_id=subject_id,
            key=key,
            content=content_value,
            memory_type=memory_type_from_kind(kind),
            layer=str(arguments.get("layer") or _default_layer(kind)),
            scope=str(arguments.get("scope") or MemoryScope.PRIVATE.value),
            visible_to=_tuple_str(arguments.get("visible_to")) or (identity.agent_id,),
            confidence=_clamp_float(arguments.get("confidence"), default=0.9),
            salience=_clamp_float(arguments.get("salience"), default=0.9),
            source_message_ids=_tuple_str(arguments.get("evidence_event_ids")),
            source_memory_ids=_tuple_str(arguments.get("source_memory_ids")),
            evidence_text=str(arguments.get("evidence_text") or content),
            reason=str(arguments.get("reason") or default_action),
            actor_id=identity.actor_id,
            agent_id=identity.agent_id,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            labels=identity.labels,
            tags=_dedupe((*identity.tags, "memory-intake", default_action)),
            metadata={
                **state_fact_metadata(content_value, source=f"{source}_state_v1"),
                **_metadata(arguments.get("metadata")),
            },
            session_id=identity.session_id,
            expected_version=(
                int(arguments["expected_version"])
                if arguments.get("expected_version") is not None
                else None
            ),
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
    return MemoryOperation.CREATE.value


def _default_layer(kind: str) -> str:
    if kind in {EventKind.PREFERENCE.value, EventKind.TASK_OUTCOME.value}:
        return MemoryLayer.CORE.value
    return MemoryLayer.WORKING.value


def _proposal_id(action: str, idempotency_key: str | None) -> str:
    if idempotency_key:
        return f"memory-intake:{action}:{idempotency_key}"
    return f"memory-intake:{action}:{uuid4()}"


def _content_for_kind(
    kind: str,
    *,
    key: str,
    content: str,
    arguments: dict[str, Any],
) -> str:
    if kind == EventKind.TASK_OUTCOME.value:
        result = str(arguments.get("result") or "remembered")
        return f"When handling {key}, outcome was {result}: {content}"
    return content


def _refresh_snapshot(runtime: object) -> None:
    refresh = getattr(runtime, "refresh_snapshot", None)
    if callable(refresh):
        refresh()


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


def _metadata(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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
