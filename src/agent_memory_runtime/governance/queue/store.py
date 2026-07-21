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

    def claim_next(
        self,
        *,
        worker_id: str = "worker",
        lease_seconds: float = 30.0,
    ) -> DerivationJob | None:
        ...

    def update(self, job: DerivationJob) -> None:
        ...

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> DerivationJob | None:
        ...

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        error: Exception,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 300.0,
    ) -> DerivationJob | None:
        ...

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> DerivationJob | None:
        ...

    def redrive(self, job_id: str) -> DerivationJob | None:
        ...

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        ...

    def pending_count(self) -> int:
        ...

    def clear(self) -> None:
        ...
