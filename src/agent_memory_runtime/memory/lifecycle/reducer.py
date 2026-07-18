from __future__ import annotations

from dataclasses import replace

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.enums import MemoryLayer, MemoryOperation, MemoryStatus
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate, MemoryRecord


class LifecycleReducer:
    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()

    def reduce(
        self,
        current: MemoryRecord | None,
        candidate: MemoryCandidate,
        source_event: Event,
    ) -> MemoryRecord:
        if current is None:
            record = MemoryRecord.from_candidate(
                candidate,
                now=source_event.occurred_at,
                sequence=source_event.sequence,
            )
        else:
            record = self._merge(current, candidate, source_event)
        if record.salience < self.config.low_salience_archive_threshold:
            record = replace(
                record,
                layer=MemoryLayer.ARCHIVAL.value,
                status=MemoryStatus.ARCHIVED.value,
                last_operation=MemoryOperation.ARCHIVE.value,
            )
        return record

    def _merge(
        self,
        current: MemoryRecord,
        candidate: MemoryCandidate,
        source_event: Event,
    ) -> MemoryRecord:
        operation = candidate.operation
        source_event_ids = _dedupe([*current.source_event_ids, *candidate.source_event_ids])
        source_memory_ids = _dedupe([*current.source_memory_ids, *candidate.source_memory_ids])
        metadata = {**current.metadata, **candidate.metadata}
        status = current.status
        conflict = _detect_conflict(current, candidate)
        if conflict is not None:
            status = MemoryStatus.CONFLICTED.value
            metadata["conflict"] = conflict

        if operation in {MemoryOperation.REVISE.value, MemoryOperation.SUPERSEDE.value}:
            content = candidate.content
        else:
            content = current.content

        if operation == MemoryOperation.SUPERSEDE.value:
            base = MemoryRecord.from_candidate(
                candidate,
                now=source_event.occurred_at,
                sequence=source_event.sequence,
            )
            return replace(
                base,
                created_at=current.created_at,
                source_event_ids=source_event_ids,
                source_memory_ids=source_memory_ids,
                metadata=metadata,
                status=status,
                reinforcement_count=current.reinforcement_count + 1,
            )

        if operation == MemoryOperation.ARCHIVE.value:
            return replace(
                current,
                layer=MemoryLayer.ARCHIVAL.value,
                status=MemoryStatus.ARCHIVED.value,
                updated_at=source_event.occurred_at,
                last_event_sequence=source_event.sequence,
                last_operation=operation,
                source_event_ids=source_event_ids,
                source_memory_ids=source_memory_ids,
                metadata=metadata,
            )

        return replace(
            current,
            content=content,
            source_event_ids=source_event_ids,
            source_memory_ids=source_memory_ids,
            salience=max(current.salience, candidate.salience),
            confidence=max(current.confidence, candidate.confidence),
            labels=_dedupe([*current.labels, *candidate.labels]),
            tags=_dedupe([*current.tags, *candidate.tags]),
            metadata=metadata,
            status=status,
            reinforcement_count=current.reinforcement_count
            + len(set(candidate.source_event_ids) - set(current.source_event_ids)),
            updated_at=source_event.occurred_at,
            last_event_sequence=source_event.sequence,
            last_operation=MemoryOperation.REINFORCE.value
            if operation == MemoryOperation.CREATE.value
            else operation,
        )


def _detect_conflict(current: MemoryRecord, candidate: MemoryCandidate) -> dict[str, object] | None:
    current_truth = current.metadata.get("truth_value")
    candidate_truth = candidate.metadata.get("truth_value")
    if current_truth is None or candidate_truth is None or current_truth == candidate_truth:
        return None
    return {
        "current_truth_value": current_truth,
        "candidate_truth_value": candidate_truth,
        "candidate_source_event_ids": list(candidate.source_event_ids),
    }


def _dedupe(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)

