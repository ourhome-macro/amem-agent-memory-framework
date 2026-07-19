from __future__ import annotations

from typing import Protocol

from agent_memory_runtime.governance.queue.job import DerivationJob


class DerivationQueueStore(Protocol):
    def enqueue(self, job: DerivationJob) -> DerivationJob:
        ...

    def get(self, job_id: str) -> DerivationJob | None:
        ...

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        ...

    def claim_next(self) -> DerivationJob | None:
        ...

    def update(self, job: DerivationJob) -> None:
        ...

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        ...

    def pending_count(self) -> int:
        ...

    def clear(self) -> None:
        ...
