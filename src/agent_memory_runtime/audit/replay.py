from __future__ import annotations

from agent_memory_runtime.audit.consistency import ConsistencyReport, compare_snapshots
from agent_memory_runtime.audit.snapshot import RuntimeSnapshot
from agent_memory_runtime.domain.event import Event


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

