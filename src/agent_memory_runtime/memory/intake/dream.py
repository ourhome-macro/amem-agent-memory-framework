from __future__ import annotations

import re

from agent_memory_runtime.audit.hashing import stable_hash
from agent_memory_runtime.domain.enums import EventKind, MemoryStatus
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.memory.intake.models import (
    AutoDreamReport,
    DreamCheckpoint,
    DreamProposal,
)

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
        proposals: list[DreamProposal] = []
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

    def _message_proposal(self, event: Event) -> DreamProposal | None:
        if event.kind != EventKind.MESSAGE.value:
            return None
        text = str(event.payload.get("text") or "").strip()
        if not text:
            return None
        if _FORGET_RE.search(text):
            return _proposal(
                event,
                action="forget_memory",
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
                action="revise_memory",
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
                action="save_memory",
                kind=EventKind.PREFERENCE.value,
                key=_infer_key(text),
                content=_shorten(text),
                confidence=0.82,
                salience=0.82,
                reason="explicit_memory_marker",
                recommended_action="auto_apply",
            )
        return None

    def _missing_derivation_proposal(
        self,
        event: Event,
        records: list[MemoryRecord],
    ) -> DreamProposal | None:
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
            action="save_memory",
            kind=event.kind,
            key=str(event.payload.get("key") or event.payload.get("subject_id") or "item"),
            content=_shorten(content),
            confidence=0.9,
            salience=0.85,
            reason="typed_event_without_derived_memory",
        )

    def _state_proposals(self, records: list[MemoryRecord]) -> list[DreamProposal]:
        proposals: list[DreamProposal] = []
        active = [record for record in records if record.status == MemoryStatus.ACTIVE.value]
        by_content: dict[str, list[MemoryRecord]] = {}
        for record in records:
            if record.status == MemoryStatus.CONFLICTED.value:
                proposals.append(
                    DreamProposal(
                        proposal_id=f"auto-dream:conflict:{record.memory_id}",
                        action="revise_memory",
                        kind=None,
                        key=str(record.metadata.get("key") or ""),
                        content=record.content,
                        confidence=record.confidence,
                        salience=record.salience,
                        evidence_event_ids=record.source_event_ids,
                        target_memory_id=record.memory_id,
                        reason="conflicted_memory_requires_resolution",
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
                    DreamProposal(
                        proposal_id=f"auto-dream:duplicate:{duplicate.memory_id}",
                        action="forget_memory",
                        kind=None,
                        key=str(duplicate.metadata.get("key") or ""),
                        content=duplicate.content,
                        confidence=min(duplicate.confidence, keep.confidence),
                        salience=min(duplicate.salience, keep.salience),
                        evidence_event_ids=duplicate.source_event_ids,
                        target_memory_id=duplicate.memory_id,
                        reason=f"duplicate_of:{keep.memory_id}",
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
    recommended_action: str = "review",
) -> DreamProposal:
    return DreamProposal(
        proposal_id=f"auto-dream:{event.event_id}:{action}",
        action=action,
        kind=kind,
        key=key,
        content=content,
        confidence=confidence,
        salience=salience,
        evidence_event_ids=(event.event_id,),
        reason=reason,
        recommended_action=recommended_action,
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


def _dedupe_proposals(proposals: list[DreamProposal]) -> list[DreamProposal]:
    seen: set[str] = set()
    result: list[DreamProposal] = []
    for proposal in proposals:
        if proposal.proposal_id in seen:
            continue
        seen.add(proposal.proposal_id)
        result.append(proposal)
    return result
