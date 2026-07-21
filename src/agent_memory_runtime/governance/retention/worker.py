from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from threading import Event as ThreadEvent
from typing import TYPE_CHECKING

from agent_memory_runtime.audit.snapshot import RuntimeSnapshot
from agent_memory_runtime.governance.retention.executor import RetentionExecutor
from agent_memory_runtime.governance.retention.planner import RetentionPlanner
from agent_memory_runtime.governance.retention.policy import (
    RetentionPlan,
    RetentionPolicy,
    RetentionReport,
)

if TYPE_CHECKING:
    from agent_memory_runtime.runtime import AgentMemoryRuntime


@dataclass(frozen=True)
class RetentionCycle:
    plan: RetentionPlan
    report: RetentionReport
    snapshot: RuntimeSnapshot


@dataclass(frozen=True)
class RetentionWorkerReport:
    cycles: int
    archived: int
    deleted: int
    last_cycle: RetentionCycle | None = None


class RetentionWorker:
    """Periodic retention worker with an interruptible wait and atomic deletions."""

    def __init__(
        self,
        runtime: AgentMemoryRuntime,
        *,
        policy: RetentionPolicy | None = None,
        interval_seconds: float = 300.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("retention worker interval_seconds must be positive")
        self.runtime = runtime
        self.policy = policy or RetentionPolicy()
        self.interval_seconds = interval_seconds

    def run_once(self) -> RetentionCycle:
        manager = self.runtime.transaction_manager
        context = manager.transaction() if manager is not None else nullcontext()
        with context:
            before = self.runtime.snapshot()
            plan = RetentionPlanner(self.policy).plan(
                self.runtime.memory_store.list_records(),
                current_sequence=before.last_event_sequence,
            )
            report = RetentionExecutor(
                memory_store=self.runtime.memory_store,
                audit_store=self.runtime.audit_store,
                tombstone_store=self.runtime.tombstone_store,
                transaction_manager=manager,
            ).apply(plan, snapshot=before)
            after = self.runtime.refresh_snapshot()
        return RetentionCycle(plan=plan, report=report, snapshot=after)

    def run_forever(
        self,
        *,
        stop_event: ThreadEvent,
        max_cycles: int | None = None,
        on_cycle: Callable[[RetentionCycle], None] | None = None,
    ) -> RetentionWorkerReport:
        if max_cycles is not None and max_cycles <= 0:
            raise ValueError("retention worker max_cycles must be positive")
        cycle_count = 0
        archived = 0
        deleted = 0
        last_cycle: RetentionCycle | None = None
        while not stop_event.is_set():
            last_cycle = self.run_once()
            cycle_count += 1
            archived += len(last_cycle.report.archived_memory_ids)
            deleted += len(last_cycle.report.deleted_memory_ids)
            if on_cycle is not None:
                on_cycle(last_cycle)
            if max_cycles is not None and cycle_count >= max_cycles:
                break
            stop_event.wait(self.interval_seconds)
        return RetentionWorkerReport(
            cycles=cycle_count,
            archived=archived,
            deleted=deleted,
            last_cycle=last_cycle,
        )
