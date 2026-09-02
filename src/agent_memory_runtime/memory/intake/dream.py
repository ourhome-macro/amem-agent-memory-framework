from __future__ import annotations

import re

from agent_memory_runtime.audit.hashing import stable_hash
from agent_memory_runtime.domain.enums import (
    EventKind,
    MemoryLevel,
    MemoryOperation,
    MemoryStatus,
    MemoryVisibility,
)
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.memory.intake.models import (
    AutoDreamReport,
    DreamCheckpoint,
    MemoryProposal,
)
from agent_memory_runtime.memory.semantic_state import (
    current_state_group_key,
    state_fact_from_record,
    state_fact_metadata,
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
_CORE_PROMOTION_MIN_REINFORCEMENTS = 3
_CORE_PROMOTION_MIN_CONFIDENCE = 0.85
_CORE_PROMOTION_MIN_SALIENCE = 0.8


class AutoDreamAnalyzer:
    def __init__(self, *, dream_version: str = "auto-dream-v1") -> None:
        self.dream_version = dream_version

    def analyze(
        self,
        *,
        events: list[Event],
        records: list[MemoryRecord],
        checkpoint: DreamCheckpoint | None = None,
        dream_run_id: str | None = None,
    ) -> AutoDreamReport:
        previous = checkpoint or DreamCheckpoint(dream_version=self.dream_version)
        new_events = [
            event
            for event in sorted(events, key=lambda item: item.sequence)
            if event.sequence > previous.last_processed_sequence
        ]
        proposals: list[MemoryProposal] = []
        for event in new_events:
            proposal = self._message_proposal(event, records, dream_run_id=dream_run_id)
            if proposal is not None:
                proposals.append(proposal)
            missing = self._missing_derivation_proposal(
                event,
                records,
                dream_run_id=dream_run_id,
            )
            if missing is not None:
                proposals.append(missing)
        proposals.extend(self._state_proposals(records, dream_run_id=dream_run_id))
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

    def _message_proposal(
        self,
        event: Event,
        records: list[MemoryRecord],
        *,
        dream_run_id: str | None,
    ) -> MemoryProposal | None:
        if event.kind != EventKind.MESSAGE.value:
            return None
        text = str(event.payload.get("text") or "").strip()
        if not text:
            return None
        key = _infer_key(text)
        target = _best_target(event, records, key=key, content=text)
        if _FORGET_RE.search(text):
            return _proposal(
                event,
                action=MemoryOperation.IGNORE.value,
                kind=target.memory_type if target is not None else None,
                key=str(target.metadata.get("key") or key) if target is not None else key,
                content=_shorten(text),
                target=target,
                confidence=0.78,
                salience=0.75,
                reason="explicit_forget_marker",
                dream_run_id=dream_run_id,
            )
        if _REVISE_RE.search(text):
            if target is not None:
                return _proposal(
                    event,
                    action=MemoryOperation.SUPERSEDE.value,
                    kind=target.memory_type,
                    key=str(target.metadata.get("key") or key),
                    content=_shorten(text),
                    target=target,
                    confidence=0.78,
                    salience=0.78,
                    reason=f"explicit_correction_marker:{target.memory_id}",
                    dream_run_id=dream_run_id,
                )
            return _proposal(
                event,
                action=MemoryOperation.IGNORE.value,
                kind=EventKind.BELIEF.value,
                key=key,
                content=_shorten(text),
                confidence=0.72,
                salience=0.7,
                reason="explicit_correction_marker",
                dream_run_id=dream_run_id,
            )
        if _REMEMBER_RE.search(text):
            if target is not None and _similarity(text, target.content) >= 0.82:
                return _proposal(
                    event,
                    action=MemoryOperation.MERGE.value,
                    kind=target.memory_type,
                    key=str(target.metadata.get("key") or key),
                    content=target.content,
                    target=target,
                    confidence=0.82,
                    salience=0.82,
                    reason=f"semantic_duplicate_of:{target.memory_id}",
                    dream_run_id=dream_run_id,
                )
            if target is not None:
                return _proposal(
                    event,
                    action=MemoryOperation.IGNORE.value,
                    kind=target.memory_type,
                    key=str(target.metadata.get("key") or key),
                    content=_shorten(text),
                    target=target,
                    confidence=0.76,
                    salience=0.78,
                    reason=f"same_key_conflict_requires_review:{target.memory_id}",
                    dream_run_id=dream_run_id,
                )
            return _proposal(
                event,
                action=MemoryOperation.CREATE.value,
                kind=EventKind.PREFERENCE.value,
                key=key,
                content=_shorten(text),
                confidence=0.82,
                salience=0.82,
                reason="explicit_memory_marker",
                dream_run_id=dream_run_id,
            )
        return None

    def _missing_derivation_proposal(
        self,
        event: Event,
        records: list[MemoryRecord],
        *,
        dream_run_id: str | None,
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
        key = str(event.payload.get("key") or event.payload.get("subject_id") or "item")
        target = _best_target(event, records, key=key, content=content)
        if target is not None:
            if _similarity(content, target.content) >= 0.82:
                return _proposal(
                    event,
                    action=MemoryOperation.MERGE.value,
                    kind=target.memory_type,
                    key=str(target.metadata.get("key") or key),
                    content=target.content,
                    target=target,
                    confidence=0.9,
                    salience=0.85,
                    reason=f"typed_event_semantic_duplicate:{target.memory_id}",
                    dream_run_id=dream_run_id,
                )
            requested_action = str(event.payload.get("operation") or "").casefold()
            if requested_action in {
                "revise",
                MemoryOperation.SUPERSEDE.value,
            }:
                if requested_action == "revise":
                    requested_action = MemoryOperation.MERGE.value
                return _proposal(
                    event,
                    action=requested_action,
                    kind=target.memory_type,
                    key=str(target.metadata.get("key") or key),
                    content=_shorten(content),
                    target=target,
                    confidence=0.9,
                    salience=0.85,
                    reason=f"typed_event_requested_{requested_action}:{target.memory_id}",
                    dream_run_id=dream_run_id,
                )
            return _proposal(
                event,
                action=MemoryOperation.IGNORE.value,
                kind=target.memory_type,
                key=str(target.metadata.get("key") or key),
                content=_shorten(content),
                target=target,
                confidence=0.84,
                salience=0.82,
                reason=f"typed_event_same_key_conflict:{target.memory_id}",
                dream_run_id=dream_run_id,
            )
        return _proposal(
            event,
            action=MemoryOperation.CREATE.value,
            kind=event.kind,
            key=key,
            content=_shorten(content),
            confidence=0.9,
            salience=0.85,
            reason="typed_event_without_derived_memory",
            dream_run_id=dream_run_id,
        )

    def _state_proposals(
        self,
        records: list[MemoryRecord],
        *,
        dream_run_id: str | None,
    ) -> list[MemoryProposal]:
        proposals: list[MemoryProposal] = []
        active = [record for record in records if record.status == MemoryStatus.ACTIVE.value]
        by_identity: dict[
            tuple[str, str | None, str | None, str, str, str],
            list[MemoryRecord],
        ] = {}
        for record in records:
            if record.status == MemoryStatus.CONFLICTED.value:
                proposals.append(
                    MemoryProposal(
                        proposal_id=f"auto-dream:conflict:{record.memory_id}",
                        source="auto_dream",
                        action=MemoryOperation.IGNORE.value,
                        target_memory_id=record.memory_id,
                        subject_id=record.subject_id,
                        key=str(record.metadata.get("key") or ""),
                        content=record.content,
                        memory_type=record.memory_type,
                        visible_to=record.visible_to,
                        confidence=record.confidence,
                        salience=record.salience,
                        source_message_ids=record.source_event_ids,
                        source_memory_ids=(record.memory_id,),
                        evidence_text=record.content,
                        reason="conflicted_memory_requires_resolution",
                        dream_run_id=dream_run_id,
                        dream_version=self.dream_version,
                        agent_id=record.agent_id,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        session_id=record.session_id,
                        labels=record.labels,
                        tags=record.tags,
                        level=record.level,
                        visibility=record.visibility,
                        priority=record.priority,
                        decision_status="pending_review",
                    )
                )
        for record in active:
            key = str(record.metadata.get("key") or _infer_key(record.content))
            by_identity.setdefault(_identity_key(record, key=key), []).append(record)
        proposals.extend(
            _current_state_conflict_proposals(
                active,
                dream_version=self.dream_version,
                dream_run_id=dream_run_id,
            )
        )
        for grouped in by_identity.values():
            proposals.extend(
                _merge_group(
                    grouped,
                    dream_version=self.dream_version,
                    dream_run_id=dream_run_id,
                )
            )
        proposals.extend(
            _profile_promotion_proposals(
                active,
                dream_version=self.dream_version,
                dream_run_id=dream_run_id,
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
    target: MemoryRecord | None = None,
    confidence: float,
    salience: float,
    reason: str,
    dream_run_id: str | None = None,
) -> MemoryProposal:
    kind_value = kind or EventKind.BELIEF.value
    agent_id = str(event.agent_id or event.payload.get("agent_id") or event.actor_id)
    subject_id = (
        target.subject_id
        if target is not None
        else str(event.payload.get("subject_id") or event.user_id or event.actor_id)
    )
    key_value = key or _infer_key(content)
    action_suffix = f"{action}:{target.memory_id}" if target is not None else action
    level = target.level if target is not None else _level_from_event(event, kind_value)
    visibility = (
        target.visibility
        if target is not None
        else _visibility_from_event(event)
    )
    return MemoryProposal(
        proposal_id=f"auto-dream:{event.event_id}:{action_suffix}",
        source="auto_dream",
        action=action,
        target_memory_id=None if target is None else target.memory_id,
        subject_id=subject_id,
        key=key_value,
        content=content,
        memory_type=memory_type_from_kind(kind_value),
        visible_to=(
            target.visible_to
            if target is not None
            else tuple(str(item) for item in event.payload.get("visible_to", (agent_id,)))
        ),
        confidence=confidence,
        salience=salience,
        source_message_ids=(event.event_id,),
        source_memory_ids=tuple(str(item) for item in event.payload.get("source_memory_ids", ())),
        evidence_text=content,
        reason=reason,
        dream_run_id=dream_run_id,
        dream_version="auto-dream-v1",
        actor_id=event.actor_id,
        agent_id=target.agent_id if target is not None else agent_id,
        tenant_id=target.tenant_id if target is not None else event.tenant_id,
        user_id=target.user_id if target is not None else event.user_id,
        session_id=target.session_id if target is not None else event.session_id,
        labels=target.labels if target is not None else tuple(event.labels),
        tags=tuple(dict.fromkeys((*event.tags, "auto_dream"))),
        metadata=state_fact_metadata(content, source="auto_dream_state_v1"),
        expected_version=None if target is None else target.version,
        level=level,
        visibility=visibility,
        priority=max(target.priority, salience) if target is not None else salience,
        decision_status=(
            "pending_review" if action == MemoryOperation.IGNORE.value else None
        ),
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


def _best_target(
    event: Event,
    records: list[MemoryRecord],
    *,
    key: str,
    content: str,
) -> MemoryRecord | None:
    subject_id = str(event.payload.get("subject_id") or event.user_id or event.actor_id)
    candidates = [
        record
        for record in records
        if record.status == MemoryStatus.ACTIVE.value
        and record.tenant_id == event.tenant_id
        and record.user_id == event.user_id
        and (event.agent_id is None or record.agent_id == event.agent_id)
        and record.subject_id == subject_id
    ]
    if not candidates:
        return None
    same_key = [record for record in candidates if str(record.metadata.get("key") or "") == key]
    if same_key:
        return max(
            same_key,
            key=lambda record: (
                _similarity(content, record.content),
                record.confidence,
                record.version,
            ),
        )
    similar = [
        record
        for record in candidates
        if _similarity(content, record.content) >= 0.82
    ]
    if not similar:
        return None
    return max(
        similar,
        key=lambda record: (
            _similarity(content, record.content),
            record.confidence,
            record.version,
        ),
    )


def _identity_key(
    record: MemoryRecord,
    *,
    key: str,
) -> tuple[str, str | None, str | None, str, str, str]:
    return (
        record.tenant_id,
        record.user_id,
        record.agent_id,
        record.subject_id,
        record.memory_type,
        key,
    )


def _merge_group(
    records: list[MemoryRecord],
    *,
    dream_version: str,
    dream_run_id: str | None,
) -> list[MemoryProposal]:
    if len(records) <= 1:
        return []
    ordered = sorted(
        records,
        key=lambda record: (
            -record.confidence,
            -record.salience,
            record.created_at,
            record.memory_id,
        ),
    )
    keep = ordered[0]
    proposals: list[MemoryProposal] = []
    for duplicate in ordered[1:]:
        similarity = _similarity(keep.content, duplicate.content)
        if similarity >= 0.82:
            proposals.append(
                _record_proposal(
                    action=MemoryOperation.MERGE.value,
                    target=keep,
                    source=duplicate,
                    content=keep.content,
                    confidence=min(duplicate.confidence, keep.confidence),
                    salience=min(duplicate.salience, keep.salience),
                    reason=f"semantic_duplicate_of:{keep.memory_id}",
                    dream_version=dream_version,
                    dream_run_id=dream_run_id,
                )
            )
            proposals.append(
                _record_proposal(
                    action=MemoryOperation.MERGE.value,
                    target=duplicate,
                    source=keep,
                    content=duplicate.content,
                    confidence=min(duplicate.confidence, keep.confidence),
                    salience=min(duplicate.salience, keep.salience),
                    reason=f"archived_duplicate_of:{keep.memory_id}",
                    dream_version=dream_version,
                    dream_run_id=dream_run_id,
                    status=MemoryStatus.ARCHIVED.value,
                )
            )
        elif keep.confidence >= 0.8 and duplicate.confidence >= 0.8:
            proposals.append(
                _record_proposal(
                    action=MemoryOperation.IGNORE.value,
                    target=duplicate,
                    source=keep,
                    content=duplicate.content,
                    confidence=min(duplicate.confidence, keep.confidence),
                    salience=max(duplicate.salience, keep.salience),
                    reason=f"same_key_conflict_with:{keep.memory_id}",
                    dream_version=dream_version,
                    dream_run_id=dream_run_id,
                    decision_status="pending_review",
                )
            )
        else:
            proposals.append(
                _record_proposal(
                    action=MemoryOperation.CREATE.value,
                    target=duplicate,
                    source=keep,
                    content=duplicate.content,
                    confidence=min(duplicate.confidence, keep.confidence),
                    salience=max(duplicate.salience, keep.salience),
                    reason=f"low_confidence_same_key_keep_both:{keep.memory_id}",
                    dream_version=dream_version,
                    dream_run_id=dream_run_id,
                )
            )
    return proposals


def _current_state_conflict_proposals(
    records: list[MemoryRecord],
    *,
    dream_version: str,
    dream_run_id: str | None,
) -> list[MemoryProposal]:
    by_state: dict[
        tuple[str, str | None, str | None, str, str, str],
        list[MemoryRecord],
    ] = {}
    for record in records:
        key = current_state_group_key(record)
        if key is not None:
            by_state.setdefault(key, []).append(record)

    proposals: list[MemoryProposal] = []
    for grouped in by_state.values():
        values = {
            fact.value
            for record in grouped
            if (fact := state_fact_from_record(record)) is not None
        }
        if len(values) <= 1:
            continue
        ordered = sorted(
            grouped,
            key=lambda record: (
                -record.confidence,
                -record.salience,
                record.updated_at,
                record.memory_id,
            ),
        )
        incumbent = ordered[0]
        for conflict in ordered[1:]:
            proposals.append(
                _record_proposal(
                    action=MemoryOperation.IGNORE.value,
                    target=conflict,
                    source=incumbent,
                    content=conflict.content,
                    confidence=min(conflict.confidence, incumbent.confidence),
                    salience=max(conflict.salience, incumbent.salience),
                    reason=f"current_state_conflict_with:{incumbent.memory_id}",
                    dream_version=dream_version,
                    dream_run_id=dream_run_id,
                    decision_status="pending_review",
                )
            )
    return proposals


def _profile_promotion_proposals(
    records: list[MemoryRecord],
    *,
    dream_version: str,
    dream_run_id: str | None,
) -> list[MemoryProposal]:
    proposals: list[MemoryProposal] = []
    for record in records:
        if record.level == MemoryLevel.PROFILE.value:
            continue
        if record.reinforcement_count < _CORE_PROMOTION_MIN_REINFORCEMENTS:
            continue
        if record.confidence < _CORE_PROMOTION_MIN_CONFIDENCE:
            continue
        if record.salience < _CORE_PROMOTION_MIN_SALIENCE:
            continue
        proposals.append(
            _profile_promotion_proposal(
                target=record,
                confidence=record.confidence,
                salience=record.salience,
                reason="working_memory_reinforced_for_core",
                dream_version=dream_version,
                dream_run_id=dream_run_id,
            )
        )
    return proposals


def _profile_promotion_proposal(
    *,
    target: MemoryRecord,
    confidence: float,
    salience: float,
    reason: str,
    dream_version: str,
    dream_run_id: str | None,
) -> MemoryProposal:
    key = str(target.metadata.get("key") or _infer_key(target.content))
    metadata = state_fact_metadata(target.content, source="auto_dream_state_v1")
    metadata.update(
        {
            "promotion_source_level": target.level,
            "migration_target_level": MemoryLevel.PROFILE.value,
            "migration_reason": reason,
        }
    )
    return MemoryProposal(
        proposal_id=f"auto-dream:promote-profile:{target.memory_id}",
        source="auto_dream",
        action=MemoryOperation.MERGE.value,
        target_memory_id=target.memory_id,
        subject_id=target.subject_id,
        key=key,
        content=target.content,
        memory_type=target.memory_type,
        visible_to=target.visible_to,
        confidence=confidence,
        salience=salience,
        source_message_ids=target.source_event_ids,
        source_memory_ids=(target.memory_id,),
        evidence_text=target.content,
        reason=reason,
        dream_run_id=dream_run_id,
        dream_version=dream_version,
        agent_id=target.agent_id,
        tenant_id=target.tenant_id,
        user_id=target.user_id,
        session_id=target.session_id,
        labels=target.labels,
        tags=tuple(dict.fromkeys((*target.tags, "auto_dream", "profile_promotion"))),
        metadata=metadata,
        expected_version=target.version,
        level=MemoryLevel.PROFILE.value,
        visibility=target.visibility,
        priority=max(target.priority, target.salience),
    )


def _record_proposal(
    *,
    action: str,
    target: MemoryRecord,
    source: MemoryRecord,
    content: str,
    confidence: float,
    salience: float,
    reason: str,
    dream_version: str,
    dream_run_id: str | None,
    status: str = MemoryStatus.ACTIVE.value,
    decision_status: str | None = None,
) -> MemoryProposal:
    key = str(target.metadata.get("key") or _infer_key(target.content))
    return MemoryProposal(
        proposal_id=f"auto-dream:{action}:{target.memory_id}:{source.memory_id}",
        source="auto_dream",
        action=action,
        target_memory_id=target.memory_id,
        subject_id=target.subject_id,
        key=key,
        content=content,
        memory_type=target.memory_type,
        visible_to=target.visible_to,
        confidence=confidence,
        salience=salience,
        source_message_ids=source.source_event_ids,
        source_memory_ids=(source.memory_id,),
        evidence_text=source.content,
        reason=reason,
        dream_run_id=dream_run_id,
        dream_version=dream_version,
        agent_id=target.agent_id,
        tenant_id=target.tenant_id,
        user_id=target.user_id,
        session_id=target.session_id,
        labels=target.labels,
        tags=tuple(dict.fromkeys((*target.tags, "auto_dream"))),
        metadata=state_fact_metadata(content, source="auto_dream_state_v1"),
        expected_version=target.version,
        level=target.level,
        visibility=target.visibility,
        priority=max(target.priority, salience),
        status=status,
        decision_status=decision_status,
    )


def _level_from_event(event: Event, kind: str) -> str:
    explicit = event.payload.get("level")
    if explicit is not None:
        return str(explicit)
    legacy_layer = str(event.payload.get("layer") or "")
    if legacy_layer == "core":
        return MemoryLevel.PROFILE.value
    if legacy_layer == "archival":
        return MemoryLevel.ATOM.value
    if kind in {EventKind.PREFERENCE.value, EventKind.TASK_OUTCOME.value}:
        return MemoryLevel.PROFILE.value
    return MemoryLevel.ATOM.value


def _visibility_from_event(event: Event) -> str:
    explicit = event.payload.get("visibility")
    if explicit is not None:
        return str(explicit)
    scope = str(event.payload.get("scope") or "private")
    if scope == "global":
        return MemoryVisibility.PUBLIC.value
    if scope == "shared":
        return MemoryVisibility.SHARED.value
    return MemoryVisibility.PRIVATE.value


def _similarity(left: str, right: str) -> float:
    if _normalize_text(left) == _normalize_text(right):
        return 1.0
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / len(left_tokens | right_tokens)


def _tokens(text: str) -> set[str]:
    normalized = _normalize_text(text)
    words = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    if not words and normalized:
        words.add(normalized)
    return words


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


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
