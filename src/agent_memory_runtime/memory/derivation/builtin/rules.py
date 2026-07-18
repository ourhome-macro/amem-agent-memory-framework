from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.domain.enums import (
    EventKind,
    MemoryLayer,
    MemoryOperation,
    MemoryScope,
    MemoryType,
)
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate


@dataclass(frozen=True)
class EpisodicRule:
    rule_id: str = "builtin.episodic.v1"

    def derive(self, event: Event) -> list[MemoryCandidate]:
        if event.kind not in {
            EventKind.MESSAGE.value,
            EventKind.OBSERVATION.value,
            EventKind.NOTE.value,
        }:
            return []
        text = str(event.payload.get("text") or event.payload.get("summary") or "").strip()
        if not text:
            return []
        target_agent = str(event.payload.get("agent_id") or event.actor_id)
        return [
            MemoryCandidate(
                memory_id=f"episodic:{event.session_id}:{event.event_id}",
                memory_type=MemoryType.EPISODIC.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=MemoryLayer.WORKING.value,
                session_id=event.session_id,
                subject_id=str(event.payload.get("subject_id") or event.actor_id),
                content=text,
                source_event_ids=(event.event_id,),
                rule_id=self.rule_id,
                owner_id=target_agent,
                visible_to=(target_agent,),
                labels=tuple(event.labels),
                tags=tuple(event.tags),
                salience=float(event.payload.get("salience", 0.45)),
                confidence=float(event.payload.get("confidence", 0.9)),
                metadata={
                    "event_kind": event.kind,
                    "speaker_id": event.actor_id,
                    "topic": event.payload.get("topic"),
                },
            )
        ]


@dataclass(frozen=True)
class BeliefRule:
    rule_id: str = "builtin.belief.v1"

    def derive(self, event: Event) -> list[MemoryCandidate]:
        if event.kind not in {EventKind.BELIEF.value, EventKind.PREFERENCE.value}:
            return []
        statement = str(
            event.payload.get("belief")
            or event.payload.get("preference")
            or event.payload.get("text")
            or ""
        ).strip()
        if not statement:
            return []
        owner = str(event.payload.get("agent_id") or event.actor_id)
        subject = str(event.payload.get("subject_id") or event.actor_id)
        key = _slug(str(event.payload.get("key") or subject))
        return [
            MemoryCandidate(
                memory_id=f"belief:{event.session_id}:{owner}:{key}",
                memory_type=MemoryType.BELIEF.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=str(event.payload.get("layer", MemoryLayer.CORE.value)),
                session_id=event.session_id,
                subject_id=subject,
                content=statement,
                source_event_ids=(event.event_id,),
                rule_id=self.rule_id,
                operation=str(event.payload.get("operation", MemoryOperation.CREATE.value)),
                owner_id=owner,
                visible_to=tuple(str(item) for item in event.payload.get("visible_to", (owner,))),
                labels=tuple(event.labels),
                tags=_sorted_tags(*event.tags, "belief"),
                salience=float(event.payload.get("salience", 0.75)),
                confidence=float(event.payload.get("confidence", 0.85)),
                metadata={
                    "key": key,
                    "truth_value": event.payload.get("truth_value"),
                    "authority": event.payload.get("authority", "event_observed"),
                },
            )
        ]


@dataclass(frozen=True)
class RelationshipRule:
    rule_id: str = "builtin.relationship.v1"

    def derive(self, event: Event) -> list[MemoryCandidate]:
        if event.kind != EventKind.RELATIONSHIP.value:
            return []
        source = str(event.payload.get("source_id") or event.actor_id)
        target = str(event.payload.get("target_id") or "")
        if not target:
            return []
        owner = str(event.payload.get("agent_id") or source)
        sentiment = str(event.payload.get("sentiment") or "noted")
        return [
            MemoryCandidate(
                memory_id=f"relationship:{event.session_id}:{source}:{target}",
                memory_type=MemoryType.RELATIONSHIP.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=MemoryLayer.WORKING.value,
                session_id=event.session_id,
                subject_id=target,
                content=f"{source} relationship signal toward {target}: {sentiment}",
                source_event_ids=(event.event_id,),
                rule_id=self.rule_id,
                owner_id=owner,
                visible_to=tuple(str(item) for item in event.payload.get("visible_to", (owner,))),
                labels=tuple(event.labels),
                tags=_sorted_tags(*event.tags, "relationship"),
                salience=float(event.payload.get("salience", 0.6)),
                confidence=float(event.payload.get("confidence", 0.8)),
                metadata={
                    "source_id": source,
                    "target_id": target,
                    "sentiment": sentiment,
                    "delta": event.payload.get("delta", {}),
                },
            )
        ]


@dataclass(frozen=True)
class StrategyRule:
    rule_id: str = "builtin.strategy.v1"

    def derive(self, event: Event) -> list[MemoryCandidate]:
        if event.kind != EventKind.TASK_OUTCOME.value:
            return []
        agent_id = str(event.payload.get("agent_id") or event.actor_id)
        task = str(event.payload.get("task") or event.payload.get("subject_id") or "task")
        outcome = str(event.payload.get("outcome") or "").strip()
        if not outcome:
            return []
        result = str(event.payload.get("result", "unknown"))
        return [
            MemoryCandidate(
                memory_id=f"strategy:{event.session_id}:{agent_id}:{_slug(task)}",
                memory_type=MemoryType.STRATEGY.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=MemoryLayer.CORE.value,
                session_id=event.session_id,
                subject_id=task,
                content=f"When handling {task}, outcome was {result}: {outcome}",
                source_event_ids=(event.event_id,),
                source_memory_ids=tuple(
                    str(item) for item in event.payload.get("source_memory_ids", ())
                ),
                rule_id=self.rule_id,
                owner_id=agent_id,
                visible_to=tuple(
                    str(item) for item in event.payload.get("visible_to", (agent_id,))
                ),
                labels=tuple(event.labels),
                tags=_sorted_tags(
                    *event.tags,
                    "strategy",
                    str(event.payload.get("domain", "general")),
                ),
                salience=float(event.payload.get("salience", 0.8)),
                confidence=float(event.payload.get("confidence", 0.9)),
                metadata={"task": task, "result": result},
            )
        ]


def builtin_rules() -> list[object]:
    return [EpisodicRule(), BeliefRule(), RelationshipRule(), StrategyRule()]


def _slug(value: str) -> str:
    slug = "_".join(part for part in value.casefold().replace("/", " ").split() if part)
    return slug[:80] or "item"


def _sorted_tags(*values: str) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))
