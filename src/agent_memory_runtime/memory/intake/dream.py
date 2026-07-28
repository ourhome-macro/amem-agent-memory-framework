from __future__ import annotations

import re

from agent_memory_runtime.audit.hashing import stable_hash
from agent_memory_runtime.domain.enums import EventKind, MemoryLayer, MemoryOperation, MemoryStatus
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.memory.intake.models import (
    AutoDreamReport,
    DreamCheckpoint,
    MemoryProposal,
)
from agent_memory_runtime.memory.service import memory_type_from_kind

_REMEMBER_RE = re.compile(
    r"(\u8bb0\u4f4f|\u4ee5\u540e|\u9ed8\u8ba4|\u4e0d\u8981\u518d|\u522b\u518d)"
)
_REVISE_RE = re.compile(
    r"(\u521a\u624d\u8bf4\u9519|\u8bf4\u9519\u4e86|\u6539\u6210|\u5176\u5b9e\u662f)"
)
_FORGET_RE = re.compile(
    r"(\u5fd8\u6389|\u5220\u6389|\u4e0d\u8981\u8bb0|\u4e0d\u7528\u8bb0)"
)


class AutoDreamAnalyzer:
    def __init__(self, *, dream_version: str = "auto-dream-v1") -> None:
        self.dream_version = dream_version

    def analyze(
        self,
        *,
        events: list[Event],
        records: list[MemoryRecord],
        checkpoint: DreamCheckpoint | None = None,
    ) -> AutoDreamReport:
        previous = checkpoint or DreamCheckpoint(dream_version=self.dream_version)
        new_events = [
            event
            for event in sorted(events, key=lambda item: item.sequence)
            if event.sequence > previous.last_processed_sequence
        ]
        proposals: list[MemoryProposal] = []
        for event in new_events:
            proposal = self._message_proposal(event)
            if proposal is not None:
                proposals.append(proposal)
            missing = self._missing_derivation_proposal(event, records)
            if missing is not None:
                proposals.append(missing)
        proposals.extend(self._state_proposals(records))
        state_hash = _state_hash(records)
        max_sequence = max(
            [previous.last_processed_sequence, *(event.sequence for event in new_events)]
        )
        return AutoDreamReport(
            source_sequence_range=(
                None
                if not new_events
                else (new_events[0].sequence, new_events[-1].sequence)
            ),
            base_state_hash=state_hash,
            proposals=tuple(_dedupe_proposals(proposals)),
            checkpoint=DreamCheckpoint(
                last_processed_sequence=max_sequence,
                last_state_hash=state_hash,
                dream_version=self.dream_version,
            ),
        )

    def _message_proposal(self, event: Event) -> MemoryProposal | None:
        if event.kind != EventKind.MESSAGE.value:
            return None
        text = str(event.payload.get("text") or "").strip()
        if not text:
            return None
        if _FORGET_RE.search(text):
            return _proposal(
                event,
                action=MemoryOperation.NEEDS_REVIEW.value,
                kind=None,
                key=None,
                content=_shorten(text),
                confidence=0.78,
                salience=0.75,
                reason="explicit_forget_marker",
            )
        if _REVISE_RE.search(text):
            return _proposal(
                event,
                action=MemoryOperation.NEEDS_REVIEW.value,
                kind=EventKind.BELIEF.value,
                key=_infer_key(text),
                content=_shorten(text),
                confidence=0.72,
                salience=0.7,
                reason="explicit_correction_marker",
            )
        if _REMEMBER_RE.search(text):
            return _proposal(
                event,
                action=MemoryOperation.CREATE.value,
                kind=EventKind.PREFERENCE.value,
                key=_infer_key(text),
                content=_shorten(text),
                confidence=0.82,
                salience=0.82,
                reason="explicit_memory_marker",
            )
        return None

    def _missing_derivation_proposal(
        self,
        event: Event,
        records: list[MemoryRecord],
    ) -> MemoryProposal | None:
        if event.kind not in {
            EventKind.PREFERENCE.value,
            EventKind.BELIEF.value,
            EventKind.TASK_OUTCOME.value,
        }:
            return None
        if any(event.event_id in set(record.source_event_ids) for record in records):
            return None
        content = str(
            event.payload.get("preference")
            or event.payload.get("belief")
            or event.payload.get("outcome")
            or event.payload.get("text")
            or ""
        ).strip()
        if not content:
            return None
        return _proposal(
            event,
            action=MemoryOperation.CREATE.value,
            kind=event.kind,
            key=str(event.payload.get("key") or event.payload.get("subject_id") or "item"),
            content=_shorten(content),
            confidence=0.9,
            salience=0.85,
            reason="typed_event_without_derived_memory",
        )

    def _state_proposals(self, records: list[MemoryRecord]) -> list[MemoryProposal]:
        proposals: list[MemoryProposal] = []
        active = [record for record in records if record.status == MemoryStatus.ACTIVE.value]
        by_content: dict[str, list[MemoryRecord]] = {}
        for record in records:
            if record.status == MemoryStatus.CONFLICTED.value:
                proposals.append(
                    MemoryProposal(
                        proposal_id=f"auto-dream:conflict:{record.memory_id}",
                        source="auto_dream",
                        action=MemoryOperation.NEEDS_REVIEW.value,
                        target_memory_id=record.memory_id,
                        subject_id=record.subject_id,
                        key=str(record.metadata.get("key") or ""),
                        content=record.content,
                        memory_type=record.memory_type,
                        layer=record.layer,
                        scope=record.scope,
                        visible_to=record.visible_to,
                        confidence=record.confidence,
                        salience=record.salience,
                        source_message_ids=record.source_event_ids,
                        source_memory_ids=(record.memory_id,),
                        evidence_text=record.content,
                        reason="conflicted_memory_requires_resolution",
                        dream_version=self.dream_version,
                        agent_id=record.agent_id,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        session_id=record.session_id,
                        labels=record.labels,
                        tags=record.tags,
                    )
                )
        for record in active:
            normalized = " ".join(record.content.casefold().split())
            if normalized:
                by_content.setdefault(normalized, []).append(record)
        for duplicate_records in by_content.values():
            if len(duplicate_records) <= 1:
                continue
            keep = duplicate_records[0]
            for duplicate in duplicate_records[1:]:
                proposals.append(
                    MemoryProposal(
                        proposal_id=f"auto-dream:duplicate:{keep.memory_id}:{duplicate.memory_id}",
                        source="auto_dream",
                        action=MemoryOperation.REINFORCE.value,
                        target_memory_id=keep.memory_id,
                        subject_id=keep.subject_id,
                        key=str(duplicate.metadata.get("key") or ""),
                        content=duplicate.content,
                        memory_type=keep.memory_type,
                        layer=keep.layer,
                        scope=keep.scope,
                        visible_to=keep.visible_to,
                        confidence=min(duplicate.confidence, keep.confidence),
                        salience=min(duplicate.salience, keep.salience),
                        source_message_ids=duplicate.source_event_ids,
                        source_memory_ids=(duplicate.memory_id,),
                        evidence_text=duplicate.content,
                        reason=f"duplicate_of:{keep.memory_id}",
                        dream_version=self.dream_version,
                        agent_id=keep.agent_id,
                        tenant_id=keep.tenant_id,
                        user_id=keep.user_id,
                        session_id=keep.session_id,
                        labels=keep.labels,
                        tags=keep.tags,
                    )
                )
        return proposals


def _proposal(
    event: Event,
    *,
    action: str,
    kind: str | None,
    key: str | None,
    content: str,
    confidence: float,
    salience: float,
    reason: str,
) -> MemoryProposal:
    kind_value = kind or EventKind.BELIEF.value
    agent_id = str(event.agent_id or event.payload.get("agent_id") or event.actor_id)
    subject_id = str(event.payload.get("subject_id") or event.user_id or event.actor_id)
    key_value = key or _infer_key(content)
    return MemoryProposal(
        proposal_id=f"auto-dream:{event.event_id}:{action}",
        source="auto_dream",
        action=action,
        target_memory_id=None,
        subject_id=subject_id,
        key=key_value,
        content=content,
        memory_type=memory_type_from_kind(kind_value),
        layer=str(event.payload.get("layer") or MemoryLayer.CORE.value),
        scope=str(event.payload.get("scope") or "private"),
        visible_to=tuple(str(item) for item in event.payload.get("visible_to", (agent_id,))),
        confidence=confidence,
        salience=salience,
        source_message_ids=(event.event_id,),
        source_memory_ids=tuple(str(item) for item in event.payload.get("source_memory_ids", ())),
        evidence_text=content,
        reason=reason,
        dream_version="auto-dream-v1",
        actor_id=event.actor_id,
        agent_id=agent_id,
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        session_id=event.session_id,
        labels=tuple(event.labels),
        tags=tuple(event.tags),
    )


def _infer_key(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9_]{2,}", text.casefold())
    if words:
        return "_".join(words[:4])[:80]
    normalized = re.sub(r"\s+", "", text)
    return normalized[:24] or "memory"


def _shorten(text: str) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= 500 else f"{compact[:497]}..."


def _state_hash(records: list[MemoryRecord]) -> str:
    return stable_hash(
        [
            {
                "memory_id": record.memory_id,
                "content": record.content,
                "status": record.status,
                "updated_at": record.updated_at,
                "source_event_ids": list(record.source_event_ids),
            }
            for record in sorted(records, key=lambda item: item.memory_id)
        ]
    )


def _dedupe_proposals(proposals: list[MemoryProposal]) -> list[MemoryProposal]:
    seen: set[str] = set()
    result: list[MemoryProposal] = []
    for proposal in proposals:
        if proposal.proposal_id in seen:
            continue
        seen.add(proposal.proposal_id)
        result.append(proposal)
    return result
