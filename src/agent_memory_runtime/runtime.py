from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.access.write_guard import WriteGuard
from agent_memory_runtime.audit.snapshot import RuntimeSnapshot, build_snapshot
from agent_memory_runtime.audit.trace import RuntimeTrace
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.context import AgentContext, ContextBuilder
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate, MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.derivation import DerivationEngine
from agent_memory_runtime.memory.lifecycle import LifecycleReducer
from agent_memory_runtime.memory.retrieval import RetrievalPipeline
from agent_memory_runtime.memory.stores import (
    InMemoryEventStore,
    InMemoryMemoryStore,
    InMemorySnapshotStore,
)
from agent_memory_runtime.memory.stores.base import EventStore, MemoryStore, SnapshotStore


@dataclass(frozen=True)
class IngestResult:
    event: Event
    candidates: tuple[MemoryCandidate, ...]
    records: tuple[MemoryRecord, ...]


class AgentMemoryRuntime:
    def __init__(
        self,
        *,
        config: RuntimeConfig | None = None,
        event_store: EventStore | None = None,
        memory_store: MemoryStore | None = None,
        snapshot_store: SnapshotStore | None = None,
        derivation_engine: DerivationEngine | None = None,
        lifecycle: LifecycleReducer | None = None,
        retrieval: RetrievalPipeline | None = None,
        context_builder: ContextBuilder | None = None,
        write_guard: WriteGuard | None = None,
    ) -> None:
        self.config = config or RuntimeConfig()
        self.event_store = event_store or InMemoryEventStore()
        self.memory_store = memory_store or InMemoryMemoryStore()
        self.snapshot_store = snapshot_store or InMemorySnapshotStore()
        self.derivation_engine = derivation_engine or DerivationEngine()
        self.lifecycle = lifecycle or LifecycleReducer(self.config)
        self.retrieval = retrieval or RetrievalPipeline(self.config)
        self.context_builder = context_builder or ContextBuilder(self.config)
        self.write_guard = write_guard or WriteGuard()
        self.last_trace = RuntimeTrace()

    def ingest(self, event: Event | dict[str, object]) -> IngestResult:
        source_event = Event.from_dict(event) if isinstance(event, dict) else event
        stored_event = self.event_store.append(source_event)
        records = self.apply_event(stored_event)
        self._save_snapshot()
        return IngestResult(
            event=stored_event,
            candidates=tuple(self.derivation_engine.derive(stored_event)),
            records=tuple(records),
        )

    def apply_event(self, event: Event) -> list[MemoryRecord]:
        candidates = self.derivation_engine.derive(event)
        records: list[MemoryRecord] = []
        event_ids = {item.event_id for item in self.event_store.list_events()}
        if event.event_id not in event_ids:
            event_ids.add(event.event_id)
        for candidate in candidates:
            current = self.memory_store.get(candidate.memory_id)
            self.write_guard.validate(
                candidate,
                source_event_exists=all(item in event_ids for item in candidate.source_event_ids),
                current=current,
            )
            record = self.lifecycle.reduce(current, candidate, event)
            self.memory_store.upsert(record)
            records.append(record)
        return records

    def retrieve(
        self,
        query: MemoryQuery | dict[str, object],
    ) -> tuple[list[MemoryRecord], RuntimeTrace]:
        memory_query = _query_from_dict(query) if isinstance(query, dict) else query
        selected, trace = self.retrieval.retrieve(self.memory_store.list_records(), memory_query)
        snapshot = self.snapshot()
        self.last_trace = RuntimeTrace(
            selected_memory_ids=trace.selected_memory_ids,
            blocked_memory_count=trace.blocked_count,
            score_breakdown={
                result.memory_id: result.score.to_dict()
                for result in trace.results
                if not result.blocked
            },
            rule_version=snapshot.rule_version,
            config_hash=snapshot.config_hash,
            last_event_sequence=snapshot.last_event_sequence,
            state_hash=snapshot.state_hash,
        )
        return selected, self.last_trace

    def project(self, query: MemoryQuery | dict[str, object]) -> AgentContext:
        memory_query = _query_from_dict(query) if isinstance(query, dict) else query
        selected, trace = self.retrieval.retrieve(self.memory_store.list_records(), memory_query)
        context = self.context_builder.build(
            agent_id=memory_query.agent_id,
            records=selected,
            trace=trace,
        )
        snapshot = self.snapshot()
        self.last_trace = RuntimeTrace(
            selected_memory_ids=context.selected_memory_ids,
            blocked_memory_count=context.blocked_memory_count,
            score_breakdown={
                result.memory_id: result.score.to_dict()
                for result in trace.results
                if not result.blocked
            },
            rule_version=snapshot.rule_version,
            config_hash=snapshot.config_hash,
            last_event_sequence=snapshot.last_event_sequence,
            state_hash=snapshot.state_hash,
        )
        return context

    def replay(self, events: list[Event] | None = None) -> RuntimeSnapshot:
        source_events = events if events is not None else self.event_store.list_events()
        self.memory_store.clear()
        for event in source_events:
            self.apply_event(event)
        return self._save_snapshot()

    def snapshot(self) -> RuntimeSnapshot:
        return build_snapshot(
            config=self.config,
            events=self.event_store.list_events(),
            records=self.memory_store.list_records(),
        )

    def _save_snapshot(self) -> RuntimeSnapshot:
        snapshot = self.snapshot()
        self.snapshot_store.save(snapshot.to_dict())
        return snapshot


def _query_from_dict(value: dict[str, object]) -> MemoryQuery:
    return MemoryQuery(
        agent_id=str(value.get("agent_id") or value.get("agent") or "agent"),
        text=str(value.get("text") or value.get("query") or ""),
        session_id=_optional_str(value.get("session_id")),
        scopes=tuple(str(item) for item in value.get("scopes", ())),
        memory_types=tuple(str(item) for item in value.get("memory_types", ())),
        layers=tuple(str(item) for item in value.get("layers", ())),
        tags=tuple(str(item) for item in value.get("tags", ())),
        source_memory_ids=tuple(str(item) for item in value.get("source_memory_ids", ())),
        limit=int(value["limit"]) if value.get("limit") is not None else None,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
