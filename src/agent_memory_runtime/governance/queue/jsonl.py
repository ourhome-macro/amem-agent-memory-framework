from __future__ import annotations

import json
from pathlib import Path

from agent_memory_runtime.governance.queue.in_memory import InMemoryDerivationQueueStore
from agent_memory_runtime.governance.queue.job import DerivationJob


class JsonlDerivationQueueStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def enqueue(self, job: DerivationJob) -> DerivationJob:
        store = self._load()
        stored = store.enqueue(job)
        self._save(store.list_jobs())
        return stored

    def get(self, job_id: str) -> DerivationJob | None:
        return self._load().get(job_id)

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        return self._load().find_by_event_id(event_id)

    def claim_next(self) -> DerivationJob | None:
        store = self._load()
        job = store.claim_next()
        self._save(store.list_jobs())
        return job

    def update(self, job: DerivationJob) -> None:
        store = self._load()
        store.update(job)
        self._save(store.list_jobs())

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        return self._load().list_jobs(status=status)

    def pending_count(self) -> int:
        return self._load().pending_count()

    def clear(self) -> None:
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
        self.path.write_text(payload, encoding="utf-8")
