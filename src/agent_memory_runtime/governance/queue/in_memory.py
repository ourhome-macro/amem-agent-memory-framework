from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock

from agent_memory_runtime.governance.queue.job import DerivationJob


class InMemoryDerivationQueueStore:
    def __init__(self) -> None:
        self._jobs: dict[str, DerivationJob] = {}
        self._lock = RLock()

    def enqueue(self, job: DerivationJob) -> DerivationJob:
        with self._lock:
            existing = self.find_by_event_id(job.event_id)
            if existing is not None:
                return existing
            self._jobs[job.job_id] = job
            return job

    def get(self, job_id: str) -> DerivationJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        with self._lock:
            for job in self.list_jobs():
                if job.event_id == event_id:
                    return job
            return None

    def claim_next(
        self,
        *,
        worker_id: str = "worker",
        lease_seconds: float = 30.0,
    ) -> DerivationJob | None:
        with self._lock:
            now = datetime.now(UTC)
            self._recover_expired(now=now)
            pending = [job for job in self.list_jobs(status="pending") if job.is_available(now=now)]
            if not pending:
                return None
            job = pending[0].claim(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )
            self._jobs[job.job_id] = job
            return job

    def update(self, job: DerivationJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> DerivationJob | None:
        with self._lock:
            now = datetime.now(UTC)
            job = self._owned_running_job(
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            if job is None:
                return None
            completed = job.succeed(now=now)
            self._jobs[job_id] = completed
            return completed

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
        with self._lock:
            now = datetime.now(UTC)
            job = self._owned_running_job(
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            if job is None:
                return None
            failed = job.fail(
                error,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
                now=now,
            )
            self._jobs[job_id] = failed
            return failed

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> DerivationJob | None:
        with self._lock:
            now = datetime.now(UTC)
            job = self._owned_running_job(
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            if job is None:
                return None
            renewed = job.renew(lease_seconds=lease_seconds, now=now)
            self._jobs[job_id] = renewed
            return renewed

    def redrive(self, job_id: str) -> DerivationJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "dead_letter":
                return None
            redriven = job.redrive()
            self._jobs[job_id] = redriven
            return redriven

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: (item.created_at, item.job_id))
            if status is None:
                return jobs
            return [job for job in jobs if job.status == status]

    def pending_count(self) -> int:
        with self._lock:
            return len(self.list_jobs(status="pending"))

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _owned_running_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> DerivationJob | None:
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return None
        if job.is_lease_expired(now=now):
            self._jobs[job_id] = job.recover_expired_lease(now=now)
            return None
        if (
            job.lease_owner != worker_id
            or not lease_token
            or job.lease_token != lease_token
        ):
            return None
        return job

    def _recover_expired(self, *, now: datetime) -> None:
        for job_id, job in tuple(self._jobs.items()):
            if job.is_lease_expired(now=now):
                self._jobs[job_id] = job.recover_expired_lease(now=now)
