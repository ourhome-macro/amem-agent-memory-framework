from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Protocol

from agent_memory_runtime.governance.queue.job import DerivationJob


class DerivationRuntime(Protocol):
    def run_derivation_once(self) -> DerivationJob | None:
        ...


@dataclass(frozen=True)
class WorkerRunReport:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    dead_lettered: int = 0


class DerivationWorker:
    def __init__(self, runtime: DerivationRuntime, *, poll_interval_seconds: float = 1.0) -> None:
        self.runtime = runtime
        self.poll_interval_seconds = poll_interval_seconds

    def run_once(self) -> DerivationJob | None:
        return self.runtime.run_derivation_once()

    def run_until_idle(self, *, max_jobs: int | None = None) -> WorkerRunReport:
        processed = 0
        succeeded = 0
        failed = 0
        dead_lettered = 0
        while max_jobs is None or processed < max_jobs:
            job = self.run_once()
            if job is None:
                break
            processed += 1
            if job.status == "succeeded":
                succeeded += 1
            elif job.status == "dead_letter":
                failed += 1
                dead_lettered += 1
            else:
                failed += 1
        return WorkerRunReport(
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            dead_lettered=dead_lettered,
        )

    def run_forever(self, *, stop_after_jobs: int | None = None) -> WorkerRunReport:
        processed = 0
        succeeded = 0
        failed = 0
        dead_lettered = 0
        while stop_after_jobs is None or processed < stop_after_jobs:
            job = self.run_once()
            if job is None:
                sleep(self.poll_interval_seconds)
                continue
            processed += 1
            if job.status == "succeeded":
                succeeded += 1
            elif job.status == "dead_letter":
                failed += 1
                dead_lettered += 1
            else:
                failed += 1
        return WorkerRunReport(
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            dead_lettered=dead_lettered,
        )
