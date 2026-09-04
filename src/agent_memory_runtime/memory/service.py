from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from agent_memory_runtime.audit.stores.base import AuditStore
from agent_memory_runtime.domain.enums import (
    MemoryLabel,
    MemoryLevel,
    MemoryOperation,
    MemoryStatus,
    MemoryTemperature,
    MemoryType,
    MemoryVisibility,
)
from agent_memory_runtime.domain.memory import MemoryRecord, default_temperature
from agent_memory_runtime.domain.tombstone import MemoryTombstone
from agent_memory_runtime.memory.intake.models import (
    MemoryAuditLog,
    MemoryProposal,
    MemoryProposalResult,
)
from agent_memory_runtime.memory.stores.base import MemoryStore, TombstoneStore, TransactionManager
from agent_memory_runtime.memory.write_policy import MemoryWritePolicy


class MemoryService:
    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        audit_store: AuditStore,
        tombstone_store: TombstoneStore,
        transaction_manager: TransactionManager | None = None,
        write_policy: MemoryWritePolicy | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.audit_store = audit_store
        self.tombstone_store = tombstone_store
        self.transaction_manager = transaction_manager
        self.write_policy = write_policy or MemoryWritePolicy()

    def apply_proposal(self, proposal: MemoryProposal) -> MemoryProposalResult:
        existing_result = self._existing_result(proposal)
        if existing_result is not None:
            return existing_result
        with self._transaction():
            current = self._current_record(proposal)
            decision = self.write_policy.validate(proposal, current=current)
            if decision.status == "conflict":
                return MemoryProposalResult(
                    status="conflict",
                    action=proposal.action,
                    proposal=proposal,
                    before_record=current,
                    reason=decision.reason,
                    retryable=decision.retryable,
                )
            if not decision.allowed:
                return MemoryProposalResult(
                    status=decision.status,
                    action=proposal.action,
                    proposal=proposal,
                    before_record=current,
                    reason=decision.reason,
                    retryable=decision.retryable,
                )
            return self._apply_allowed(proposal, current=current)

    def _apply_allowed(
        self,
        proposal: MemoryProposal,
        *,
        current: MemoryRecord | None,
    ) -> MemoryProposalResult:
        action = _canonical_action(proposal.action)
        before = current
        now = datetime.now(UTC).isoformat()
        after: MemoryRecord | None
        archived: tuple[str, ...] = ()
        tombstoned: tuple[str, ...] = ()

        if action == MemoryOperation.IGNORE.value:
            return MemoryProposalResult(
                status="ignored",
                action=action,
                proposal=proposal,
                before_record=current,
                reason=proposal.reason or "ignored_by_policy",
            )

        if action == MemoryOperation.DELETE.value:
            if current is None:
                return MemoryProposalResult(
                    status="not_found",
                    action=action,
                    proposal=proposal,
                    reason="target_memory_not_found",
                )
            tombstone = MemoryTombstone(
                memory_id=current.memory_id,
                tenant_id=current.tenant_id,
                deleted_through_sequence=current.last_event_sequence + 1,
                deleted_at=now,
                reason=proposal.reason or "deleted_by_proposal",
                source_event_ids=current.source_event_ids,
                metadata={
                    "proposal_id": proposal.proposal_id,
                    "source": proposal.source,
                    "dream_run_id": proposal.dream_run_id,
                },
            )
            self.tombstone_store.put(tombstone)
            self.memory_store.delete(current.memory_id)
            after = None
            tombstoned = (current.memory_id,)
        elif action == MemoryOperation.SUPERSEDE.value:
            if current is None:
                return MemoryProposalResult(
                    status="not_found",
                    action=action,
                    proposal=proposal,
                    reason="target_memory_not_found",
            )
            after = replace(
                current,
                status=MemoryStatus.SUPERSEDED.value,
                temperature=MemoryTemperature.COLD.value,
                source_memory_ids=_dedupe(
                    (*current.source_memory_ids, *proposal.source_memory_ids)
                ),
                updated_at=now,
                last_operation=action,
                version=current.version + 1,
            )
            self.memory_store.upsert(after)
        elif current is None:
            after = self._new_record(proposal, now=now)
            self.memory_store.upsert(after)
        else:
            after = self._updated_record(current, proposal, now=now)
            self.memory_store.upsert(after)
            if after.status == MemoryStatus.ARCHIVED.value:
                archived = (after.memory_id,)

        audit_log = MemoryAuditLog(
            audit_id=f"memory-audit:{proposal.proposal_id}",
            memory_id=_audit_memory_id(current=current, after=after),
            proposal_id=proposal.proposal_id,
            action=action,
            actor_id=proposal.actor_id,
            agent_id=proposal.agent_id,
            tenant_id=proposal.tenant_id,
            user_id=proposal.user_id,
            before_record=before,
            after_record=after,
            source_message_ids=proposal.source_message_ids,
            source_memory_ids=proposal.source_memory_ids,
            evidence_text=proposal.evidence_text,
            confidence=proposal.confidence,
            reason=proposal.reason,
            created_at=now,
        )
        self.audit_store.append_memory_log(audit_log)
        return MemoryProposalResult(
            status="succeeded",
            action=action,
            proposal=proposal,
            memory=after,
            before_record=before,
            audit_log=audit_log,
            tombstoned_memory_ids=tombstoned,
            archived_memory_ids=archived,
        )

    def _current_record(self, proposal: MemoryProposal) -> MemoryRecord | None:
        if proposal.target_memory_id:
            return self.memory_store.get(proposal.target_memory_id)
        return self.memory_store.get(_memory_id_for_proposal(proposal))

    def _new_record(self, proposal: MemoryProposal, *, now: str) -> MemoryRecord:
        return MemoryRecord(
            memory_id=_memory_id_for_proposal(proposal),
            memory_type=proposal.memory_type,
            session_id=proposal.session_id,
            subject_id=proposal.subject_id,
            content=proposal.content,
            source_event_ids=proposal.source_message_ids,
            rule_id="proposal.direct.v1",
            owner_id=proposal.agent_id,
            visible_to=proposal.visible_to or _default_visible_to(proposal),
            source_memory_ids=proposal.source_memory_ids,
            labels=proposal.labels,
            tags=_dedupe((*proposal.tags, "proposal", proposal.source)),
            salience=_clamp(proposal.salience),
            confidence=_clamp(proposal.confidence),
            metadata=_metadata(proposal, current=None),
            status=proposal.status,
            reinforcement_count=1,
            created_at=now,
            updated_at=now,
            last_event_sequence=0,
            last_operation=MemoryOperation.CREATE.value,
            tenant_id=proposal.tenant_id,
            user_id=proposal.user_id,
            agent_id=proposal.agent_id,
            version=1,
            level=_proposal_level(proposal),
            visibility=_proposal_visibility(proposal),
            priority=_clamp(proposal.priority),
            temperature=_proposal_temperature(proposal, current=None),
        )

    def _updated_record(
        self,
        current: MemoryRecord,
        proposal: MemoryProposal,
        *,
        now: str,
    ) -> MemoryRecord:
        action = _canonical_action(proposal.action)
        if action == MemoryOperation.CREATE.value:
            action = MemoryOperation.MERGE.value
        content = current.content if proposal.content == current.content else proposal.content
        reinforcement_count = current.reinforcement_count + (
            1 if proposal.content == current.content else 0
        )
        return replace(
            current,
            content=content,
            salience=max(current.salience, _clamp(proposal.salience)),
            confidence=max(current.confidence, _clamp(proposal.confidence)),
            priority=max(current.priority, _clamp(proposal.priority)),
            source_event_ids=_dedupe((*current.source_event_ids, *proposal.source_message_ids)),
            source_memory_ids=_dedupe((*current.source_memory_ids, *proposal.source_memory_ids)),
            visible_to=proposal.visible_to or current.visible_to,
            labels=_dedupe((*current.labels, *proposal.labels)),
            tags=_dedupe((*current.tags, *proposal.tags, "proposal", proposal.source)),
            metadata={**current.metadata, **_metadata(proposal, current=current)},
            status=proposal.status,
            reinforcement_count=reinforcement_count,
            updated_at=now,
            last_operation=action,
            version=current.version + 1,
            level=_proposal_level(proposal),
            visibility=_proposal_visibility(proposal),
            temperature=_proposal_temperature(proposal, current=current),
        )

    def _existing_result(self, proposal: MemoryProposal) -> MemoryProposalResult | None:
        list_logs = getattr(self.audit_store, "list_memory_logs", None)
        if not callable(list_logs):
            return None
        for log in list_logs():
            if log.proposal_id == proposal.proposal_id:
                return MemoryProposalResult(
                    status="succeeded",
                    action=log.action,
                    proposal=proposal,
                    memory=log.after_record,
                    before_record=log.before_record,
                    audit_log=log,
                    tombstoned_memory_ids=_memory_id_tuple(log, MemoryOperation.DELETE.value),
                    archived_memory_ids=_archived_id_tuple(log),
                )
        return None

    def _transaction(self):
        if self.transaction_manager is None:
            return nullcontext()
        return self.transaction_manager.transaction()


def _memory_id_for_proposal(proposal: MemoryProposal) -> str:
    key = quote(str(proposal.key or proposal.subject_id or "item"), safe="")
    tenant = quote(proposal.tenant_id or "default", safe="")
    owner = quote(str(proposal.agent_id or proposal.actor_id), safe="")
    identity = quote(str(proposal.user_id or proposal.subject_id or proposal.actor_id), safe="")
    if _proposal_level(proposal) == MemoryLevel.PROFILE.value:
        return f"v3:{proposal.memory_type}:{tenant}:{identity}:{owner}:{key}"
    session = quote(proposal.session_id or "default", safe="")
    return f"proposal:{proposal.memory_type}:{tenant}:{session}:{identity}:{owner}:{key}"


def _audit_memory_id(
    *,
    current: MemoryRecord | None,
    after: MemoryRecord | None,
) -> str | None:
    if current is not None:
        return current.memory_id
    if after is not None:
        return after.memory_id
    return None


def _memory_id_tuple(log: MemoryAuditLog, action: str) -> tuple[str, ...]:
    if log.action != action or log.memory_id is None:
        return ()
    return (log.memory_id,)


def _archived_id_tuple(log: MemoryAuditLog) -> tuple[str, ...]:
    if log.memory_id is None or log.after_record is None:
        return ()
    if log.after_record.status != MemoryStatus.ARCHIVED.value:
        return ()
    return (log.memory_id,)


def _metadata(
    proposal: MemoryProposal,
    *,
    current: MemoryRecord | None,
) -> dict[str, Any]:
    metadata = dict(proposal.metadata)
    metadata.update(
        {
            "key": proposal.key,
            "level": _proposal_level(proposal),
            "visibility": _proposal_visibility(proposal),
            "priority": _clamp(proposal.priority),
            "temperature": _proposal_temperature(proposal, current=current),
            "profile_key": "|".join(
                (
                    proposal.tenant_id or "default",
                    str(proposal.user_id or proposal.subject_id or proposal.actor_id),
                    str(proposal.agent_id or proposal.actor_id),
                    str(proposal.key or "item"),
                )
            ),
            "proposal_id": proposal.proposal_id,
            "proposal_source": proposal.source,
            "dream_run_id": proposal.dream_run_id,
            "dream_version": proposal.dream_version,
            "evidence_text": proposal.evidence_text,
            "reason": proposal.reason,
        }
    )
    return metadata


def _default_visible_to(proposal: MemoryProposal) -> tuple[str, ...]:
    if MemoryLabel.PRIVATE.value in set(proposal.labels) and proposal.agent_id:
        return (proposal.agent_id,)
    if proposal.visibility == MemoryVisibility.PUBLIC.value:
        return ("*",)
    return ()


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _clamp(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def _canonical_action(action: str) -> str:
    if action in {"reinforce", "revise"}:
        return MemoryOperation.MERGE.value
    if action == "keep_both":
        return MemoryOperation.CREATE.value
    if action == "archive":
        return MemoryOperation.MERGE.value
    if action in {"needs_review", "move_layer"}:
        return MemoryOperation.IGNORE.value
    return action


def _proposal_level(proposal: MemoryProposal) -> str:
    if proposal.level:
        return proposal.level
    return MemoryLevel.ATOM.value


def _proposal_visibility(proposal: MemoryProposal) -> str:
    if proposal.visibility:
        return proposal.visibility
    return MemoryVisibility.PRIVATE.value


def _proposal_temperature(
    proposal: MemoryProposal,
    *,
    current: MemoryRecord | None,
) -> str:
    level = _proposal_level(proposal)
    if proposal.status in {
        MemoryStatus.ARCHIVED.value,
        MemoryStatus.SUPERSEDED.value,
        MemoryStatus.DELETED.value,
    }:
        return MemoryTemperature.COLD.value
    if proposal.temperature:
        return proposal.temperature
    if current is not None and current.status == proposal.status and current.level == level:
        return current.temperature
    return default_temperature(status=proposal.status, level=level)


def memory_type_from_kind(kind: str | None) -> str:
    if kind == "task.outcome":
        return MemoryType.STRATEGY.value
    return MemoryType.BELIEF.value
