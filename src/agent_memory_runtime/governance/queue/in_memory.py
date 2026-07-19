from __future__ import annotations

from agent_memory_runtime.governance.queue.job import DerivationJob


class InMemoryDerivationQueueStore:
    def __init__(self) -> None:
        self._jobs: dict[str, DerivationJob] = {}

    def enqueue(self, job: DerivationJob) -> DerivationJob:
        existing = self.find_by_event_id(job.event_id)
        if existing is not None and existing.status in {"pending", "running", "succeeded"}:
            return existing
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> DerivationJob | None:
        return self._jobs.get(job_id)

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        for job in self.list_jobs():
            if job.event_id == event_id:
                return job
        return None

    def claim_next(self) -> DerivationJob | None:
        pending = self.list_jobs(status="pending")
        if not pending:
            return None
        job = pending[0].claim()
        self._jobs[job.job_id] = job
        return job

    def update(self, job: DerivationJob) -> None:
        self._jobs[job.job_id] = job

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        jobs = sorted(self._jobs.values(), key=lambda item: (item.created_at, item.job_id))
        if status is None:
            return jobs
        return [job for job in jobs if job.status == status]

    def pending_count(self) -> int:
        return len(self.list_jobs(status="pending"))

    def clear(self) -> None:
        self._jobs.clear()
