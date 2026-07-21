from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from agent_memory_runtime.governance.queue.in_memory import InMemoryDerivationQueueStore
from agent_memory_runtime.governance.queue.job import DerivationJob


class JsonlDerivationQueueStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.RLock()

    def enqueue(self, job: DerivationJob) -> DerivationJob:
        with self._lock:
            store = self._load()
            stored = store.enqueue(job)
            self._save(store.list_jobs())
            return stored

    def get(self, job_id: str) -> DerivationJob | None:
        with self._lock:
            return self._load().get(job_id)

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        with self._lock:
            return self._load().find_by_event_id(event_id)

    def claim_next(
        self,
        *,
        worker_id: str = "worker",
        lease_seconds: float = 30.0,
    ) -> DerivationJob | None:
        with self._lock:
            store = self._load()
            job = store.claim_next(worker_id=worker_id, lease_seconds=lease_seconds)
            self._save(store.list_jobs())
            return job

    def update(self, job: DerivationJob) -> None:
        with self._lock:
            store = self._load()
            store.update(job)
            self._save(store.list_jobs())

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> DerivationJob | None:
        with self._lock:
            store = self._load()
            job = store.complete(
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            self._save(store.list_jobs())
            return job

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
            store = self._load()
            job = store.fail(
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                error=error,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
            self._save(store.list_jobs())
            return job

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> DerivationJob | None:
        with self._lock:
            store = self._load()
            job = store.renew_lease(
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_seconds=lease_seconds,
            )
            self._save(store.list_jobs())
            return job

    def redrive(self, job_id: str) -> DerivationJob | None:
        with self._lock:
            store = self._load()
            job = store.redrive(job_id)
            self._save(store.list_jobs())
            return job

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        with self._lock:
            return self._load().list_jobs(status=status)

    def pending_count(self) -> int:
        with self._lock:
            return self._load().pending_count()

    def clear(self) -> None:
        with self._lock:
            self.path.write_text("", encoding="utf-8")

    def _load(self) -> InMemoryDerivationQueueStore:
        store = InMemoryDerivationQueueStore()
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    store.update(DerivationJob.from_dict(json.loads(line)))
        return store

    def _save(self, jobs: list[DerivationJob]) -> None:
        payload = "".join(
            json.dumps(job.to_dict(), ensure_ascii=True, sort_keys=True) + "\n"
            for job in jobs
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
