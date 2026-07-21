from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.audit.consistency import ConsistencyReport, compare_snapshots
from agent_memory_runtime.audit.snapshot import RuntimeSnapshot
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.memory.derivation import DerivationEngine


@dataclass(frozen=True)
class ShadowReplayReport:
    ok: bool
    event_count: int
    expected: dict[str, object] | None
    actual: dict[str, object]
    reason: str | None = None


def replay_events(runtime: object, events: list[Event]) -> RuntimeSnapshot:
    runtime.memory_store.clear()
    for event in events:
        runtime.apply_event(event)
    return runtime.snapshot()


def consistency_check(
    runtime: object,
    events: list[Event],
    expected: RuntimeSnapshot | dict[str, object] | None,
) -> ConsistencyReport:
    actual = replay_events(runtime, events)
    return compare_snapshots(expected, actual)


def shadow_replay_events(
    events: list[Event],
    expected: RuntimeSnapshot | dict[str, object] | None,
    *,
    config: RuntimeConfig | None = None,
    derivation_engine: DerivationEngine | None = None,
) -> ShadowReplayReport:
    """Rebuild state in isolated stores without mutating live runtime data."""
    from agent_memory_runtime.runtime import AgentMemoryRuntime

    shadow = AgentMemoryRuntime(
        config=config,
        derivation_engine=derivation_engine,
    )
    try:
        for event in events:
            shadow.ingest(event)
        result = compare_snapshots(expected, shadow.snapshot())
        return ShadowReplayReport(
            ok=result.ok,
            event_count=len(events),
            expected=result.expected,
            actual=result.actual,
            reason=result.reason,
        )
    finally:
        shadow.close()
