from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

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
            EventKind.TOOL_RESULT.value,
            EventKind.NOTE.value,
        }:
            return []
        text = str(event.payload.get("text") or event.payload.get("summary") or "").strip()
        if not text:
            return []
        target_agent = _agent_id(event, fallback=event.actor_id)
        return [
            MemoryCandidate(
                memory_id=_scoped_memory_id(
                    event,
                    legacy=f"episodic:{event.session_id}:{event.event_id}",
                    kind="episodic",
                    parts=(event.session_id, event.event_id),
                ),
                memory_type=MemoryType.EPISODIC.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=MemoryLayer.WORKING.value,
                session_id=event.session_id,
                subject_id=str(event.payload.get("subject_id") or event.actor_id),
                content=text,
                source_event_ids=(event.event_id,),
                rule_id=self.rule_id,
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                agent_id=target_agent,
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
        owner = _agent_id(event, fallback=event.actor_id)
        subject = str(event.payload.get("subject_id") or event.actor_id)
        key = _slug(str(event.payload.get("key") or subject))
        layer = str(event.payload.get("layer", MemoryLayer.CORE.value))
        operation = str(
            event.payload.get("operation")
            or (
                MemoryOperation.REVISE.value
                if event.kind == EventKind.PREFERENCE.value
                else MemoryOperation.CREATE.value
            )
        )
        return [
            MemoryCandidate(
                memory_id=(
                    _profile_memory_id(event, kind="belief", owner=owner, key=key)
                    if layer == MemoryLayer.CORE.value
                    else _scoped_memory_id(
                        event,
                        legacy=f"belief:{event.session_id}:{owner}:{key}",
                        kind="belief",
                        parts=(event.session_id, owner, key),
                    )
                ),
                memory_type=MemoryType.BELIEF.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=layer,
                session_id=event.session_id,
                subject_id=subject,
                content=statement,
                source_event_ids=(event.event_id,),
                rule_id=self.rule_id,
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                agent_id=owner,
                operation=operation,
                owner_id=owner,
                visible_to=tuple(str(item) for item in event.payload.get("visible_to", (owner,))),
                labels=tuple(event.labels),
                tags=_sorted_tags(*event.tags, "belief"),
                salience=float(event.payload.get("salience", 0.75)),
                confidence=float(event.payload.get("confidence", 0.85)),
                metadata={
                    "key": key,
                    "profile_key": _profile_key(event, owner=owner, key=key),
                    "value": event.payload.get("value"),
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
        owner = _agent_id(event, fallback=source)
        sentiment = str(event.payload.get("sentiment") or "noted")
        return [
            MemoryCandidate(
                memory_id=_scoped_memory_id(
                    event,
                    legacy=f"relationship:{event.session_id}:{source}:{target}",
                    kind="relationship",
                    parts=(event.session_id, owner, source, target),
                ),
                memory_type=MemoryType.RELATIONSHIP.value,
                scope=str(event.payload.get("scope", MemoryScope.PRIVATE.value)),
                layer=MemoryLayer.WORKING.value,
                session_id=event.session_id,
                subject_id=target,
                content=f"{source} relationship signal toward {target}: {sentiment}",
                source_event_ids=(event.event_id,),
                rule_id=self.rule_id,
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                agent_id=owner,
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
        agent_id = _agent_id(event, fallback=event.actor_id)
        task = str(event.payload.get("task") or event.payload.get("subject_id") or "task")
        outcome = str(event.payload.get("outcome") or "").strip()
        if not outcome:
            return []
        result = str(event.payload.get("result", "unknown"))
        return [
            MemoryCandidate(
                memory_id=_profile_memory_id(
                    event,
                    kind="strategy",
                    owner=agent_id,
                    key=_slug(task),
                ),
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
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                agent_id=agent_id,
                operation=str(
                    event.payload.get("operation") or MemoryOperation.REVISE.value
                ),
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
                metadata={
                    "task": task,
                    "result": result,
                    "profile_key": _profile_key(
                        event,
                        owner=agent_id,
                        key=_slug(task),
                    ),
                },
            )
        ]


def builtin_rules() -> list[object]:
    return [EpisodicRule(), BeliefRule(), RelationshipRule(), StrategyRule()]


def _agent_id(event: Event, *, fallback: str) -> str:
    return str(event.agent_id or event.payload.get("agent_id") or fallback)


def _scoped_memory_id(
    event: Event,
    *,
    legacy: str,
    kind: str,
    parts: tuple[str, ...],
) -> str:
    """Keep default-tenant IDs stable and namespace every non-default tenant."""
    tenant_id = event.tenant_id or "default"
    if tenant_id == "default":
        return legacy
    encoded = ":".join(quote(str(part), safe="") for part in parts)
    return f"v2:{kind}:{quote(tenant_id, safe='')}:{encoded}"


def _profile_memory_id(
    event: Event,
    *,
    kind: str,
    owner: str,
    key: str,
) -> str:
    identity = _profile_identity(event)
    encoded = ":".join(
        quote(str(part), safe="")
        for part in (event.tenant_id or "default", identity, owner, key)
    )
    return f"v3:{kind}:{encoded}"


def _profile_key(event: Event, *, owner: str, key: str) -> str:
    return "|".join((event.tenant_id or "default", _profile_identity(event), owner, key))


def _profile_identity(event: Event) -> str:
    return str(
        event.user_id
        or event.payload.get("user_id")
        or event.payload.get("subject_id")
        or event.actor_id
    )


def _slug(value: str) -> str:
    slug = "_".join(part for part in value.casefold().replace("/", " ").split() if part)
    return slug[:80] or "item"


def _sorted_tags(*values: str) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))
