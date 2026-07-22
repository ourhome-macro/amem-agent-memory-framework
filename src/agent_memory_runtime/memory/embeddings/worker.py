from __future__ import annotations

import threading
from dataclasses import dataclass
from time import sleep
from uuid import uuid4

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.exceptions import LeaseLostError
from agent_memory_runtime.memory.embeddings.base import EmbeddingProvider, validate_vector
from agent_memory_runtime.memory.embeddings.models import (
    EmbeddingJob,
    VectorRecord,
    canonical_memory_text,
    embedding_content_hash,
)
from agent_memory_runtime.memory.embeddings.sqlite import (
    SQLiteEmbeddingJobStore,
    SQLiteVectorIndex,
)
from agent_memory_runtime.memory.stores.base import MemoryStore


@dataclass(frozen=True)
class EmbeddingWorkerReport:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    dead_lettered: int = 0
    superseded: int = 0


class EmbeddingWorker:
    def __init__(
        self,
        *,
        provider: EmbeddingProvider,
        jobs: SQLiteEmbeddingJobStore,
        vectors: SQLiteVectorIndex,
        memories: MemoryStore,
        worker_id: str | None = None,
        lease_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 300.0,
        poll_interval_seconds: float = 1.0,
        batch_size: int = 32,
    ) -> None:
        self.provider = provider
        self.jobs = jobs
        self.vectors = vectors
        self.memories = memories
        self.worker_id = worker_id or f"embedding-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.poll_interval_seconds = poll_interval_seconds
        if batch_size <= 0:
            raise ValueError("embedding worker batch_size must be positive")
        self.batch_size = batch_size

    def run_once(self) -> EmbeddingJob | None:
        job = self.jobs.claim_next(
            generation=self.provider.spec.generation,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        if job.lease_token is None:
            raise LeaseLostError(f"embedding job {job.job_id} was claimed without a token")
        try:
            record = self.memories.get(job.memory_id)
            if record is None or not self._matches(job, record):
                return self._supersede(job)
            with _LeaseHeartbeat(
                jobs=self.jobs,
                job=job,
                lease_seconds=self.lease_seconds,
                interval_seconds=self.heartbeat_interval_seconds,
            ) as heartbeat:
                vector = self.provider.embed_documents(
                    [
                        canonical_memory_text(
                            record,
                            semantic_tag_allowlist=(self.provider.spec.semantic_tag_allowlist),
                        )
                    ]
                )[0]
                validate_vector(vector, self.provider.spec)
            if heartbeat.lease_lost:
                raise LeaseLostError(f"embedding job {job.job_id} lost its lease")

            with self.jobs.transaction_manager.transaction():
                current = self.memories.get(job.memory_id)
                if current is None or not self._matches(job, current):
                    superseded = self.jobs.supersede(
                        job.job_id,
                        worker_id=self.worker_id,
                        lease_token=job.lease_token,
                    )
                    if superseded is None:
                        raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
                    return superseded
                owned = self.jobs.owned_running(
                    job.job_id,
                    worker_id=self.worker_id,
                    lease_token=job.lease_token,
                )
                if owned is None:
                    raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
                self.vectors.upsert(
                    VectorRecord(
                        memory_id=current.memory_id,
                        spec=self.provider.spec,
                        content_hash=job.content_hash,
                        source_sequence=current.last_event_sequence,
                        vector=tuple(vector),
                    )
                )
                completed = self.jobs.complete(
                    job.job_id,
                    worker_id=self.worker_id,
                    lease_token=job.lease_token,
                )
                if completed is None:
                    raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
                return completed
        except LeaseLostError:
            raise
        except Exception as error:
            failed = self.jobs.fail(
                job.job_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                error=error,
                retry_base_seconds=self.retry_base_seconds,
                retry_max_seconds=self.retry_max_seconds,
            )
            return failed or job

    def run_until_idle(self, *, max_jobs: int | None = None) -> EmbeddingWorkerReport:
        jobs = []
        while max_jobs is None or len(jobs) < max_jobs:
            remaining = None if max_jobs is None else max_jobs - len(jobs)
            batch = self.run_batch_once(max_jobs=remaining)
            if not batch:
                break
            jobs.extend(batch)
        return _report(jobs)

    def run_forever(self, *, stop_after_jobs: int | None = None) -> EmbeddingWorkerReport:
        jobs = []
        while stop_after_jobs is None or len(jobs) < stop_after_jobs:
            remaining = None if stop_after_jobs is None else stop_after_jobs - len(jobs)
            batch = self.run_batch_once(max_jobs=remaining)
            if not batch:
                sleep(self.poll_interval_seconds)
                continue
            jobs.extend(batch)
        return _report(jobs)

    def run_batch_once(self, *, max_jobs: int | None = None) -> list[EmbeddingJob]:
        limit = self.batch_size if max_jobs is None else min(self.batch_size, max_jobs)
        if limit <= 0:
            return []
        claimed = []
        for _ in range(limit):
            job = self.jobs.claim_next(
                generation=self.provider.spec.generation,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if job is None:
                break
            if job.lease_token is None:
                raise LeaseLostError(f"embedding job {job.job_id} was claimed without a token")
            claimed.append(job)
        if not claimed:
            return []
        return self._process_batch(claimed)

    def _process_batch(self, claimed: list[EmbeddingJob]) -> list[EmbeddingJob]:
        result_by_id: dict[str, EmbeddingJob] = {}
        prepared: list[tuple[EmbeddingJob, MemoryRecord]] = []
        for job in claimed:
            record = self.memories.get(job.memory_id)
            if record is None or not self._matches(job, record):
                result_by_id[job.job_id] = self._supersede(job)
            else:
                prepared.append((job, record))
        if not prepared:
            return [result_by_id[job.job_id] for job in claimed]

        heartbeat = _BatchLeaseHeartbeat(
            jobs=self.jobs,
            claimed=tuple(job for job, _ in prepared),
            lease_seconds=self.lease_seconds,
            interval_seconds=self.heartbeat_interval_seconds,
        )
        try:
            documents = [
                canonical_memory_text(
                    record,
                    semantic_tag_allowlist=self.provider.spec.semantic_tag_allowlist,
                )
                for _, record in prepared
            ]
            with heartbeat:
                vectors = self.provider.embed_documents(documents)
                if len(vectors) != len(prepared):
                    raise ValueError("embedding provider returned the wrong batch size")
                for vector in vectors:
                    validate_vector(vector, self.provider.spec)
        except Exception as error:
            for job, _ in prepared:
                if heartbeat.lost(job.job_id):
                    result_by_id[job.job_id] = self.jobs.get(job.job_id) or job
                    continue
                failed = self.jobs.fail(
                    job.job_id,
                    worker_id=self.worker_id,
                    lease_token=job.lease_token or "",
                    error=error,
                    retry_base_seconds=self.retry_base_seconds,
                    retry_max_seconds=self.retry_max_seconds,
                )
                result_by_id[job.job_id] = failed or self.jobs.get(job.job_id) or job
            return [result_by_id[job.job_id] for job in claimed]

        for (job, _), vector in zip(prepared, vectors, strict=True):
            if heartbeat.lost(job.job_id):
                result_by_id[job.job_id] = self.jobs.get(job.job_id) or job
                continue
            try:
                result_by_id[job.job_id] = self._publish_vector(job, vector)
            except LeaseLostError:
                result_by_id[job.job_id] = self.jobs.get(job.job_id) or job
        return [result_by_id[job.job_id] for job in claimed]

    def _publish_vector(
        self,
        job: EmbeddingJob,
        vector: list[float],
    ) -> EmbeddingJob:
        with self.jobs.transaction_manager.transaction():
            current = self.memories.get(job.memory_id)
            if current is None or not self._matches(job, current):
                superseded = self.jobs.supersede(
                    job.job_id,
                    worker_id=self.worker_id,
                    lease_token=job.lease_token or "",
                )
                if superseded is None:
                    raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
                return superseded
            owned = self.jobs.owned_running(
                job.job_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token or "",
            )
            if owned is None:
                raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
            self.vectors.upsert(
                VectorRecord(
                    memory_id=current.memory_id,
                    spec=self.provider.spec,
                    content_hash=job.content_hash,
                    source_sequence=current.last_event_sequence,
                    vector=tuple(vector),
                )
            )
            completed = self.jobs.complete(
                job.job_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token or "",
            )
            if completed is None:
                raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
            return completed

    def _matches(self, job: EmbeddingJob, record: MemoryRecord) -> bool:
        return (
            job.generation == self.provider.spec.generation
            and job.content_hash == embedding_content_hash(record, self.provider.spec)
            and job.source_sequence == record.last_event_sequence
        )

    def _supersede(self, job: EmbeddingJob) -> EmbeddingJob:
        superseded = self.jobs.supersede(
            job.job_id,
            worker_id=self.worker_id,
            lease_token=job.lease_token or "",
        )
        if superseded is None:
            raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
        return superseded


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        jobs: SQLiteEmbeddingJobStore,
        job: EmbeddingJob,
        lease_seconds: float,
        interval_seconds: float | None,
    ) -> None:
        self.jobs = jobs
        self.job = job
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds or lease_seconds / 3
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"amem-embedding-lease-{job.job_id}",
            daemon=True,
        )

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def __enter__(self) -> _LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=min(max(self.lease_seconds, 0.1), 5.0))
        if self._thread.is_alive():
            self._lost.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                renewed = self.jobs.renew_lease(
                    self.job.job_id,
                    worker_id=self.job.lease_owner or "",
                    lease_token=self.job.lease_token or "",
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                self._lost.set()
                return
            if renewed is None:
                self._lost.set()
                return


class _BatchLeaseHeartbeat:
    def __init__(
        self,
        *,
        jobs: SQLiteEmbeddingJobStore,
        claimed: tuple[EmbeddingJob, ...],
        lease_seconds: float,
        interval_seconds: float | None,
    ) -> None:
        self.jobs = jobs
        self.claimed = claimed
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds or lease_seconds / 3
        self._stop = threading.Event()
        self._lost_job_ids: set[str] = set()
        self._lock = threading.RLock()
        self._thread = threading.Thread(
            target=self._run,
            name=f"amem-embedding-batch-{uuid4()}",
            daemon=True,
        )

    def lost(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._lost_job_ids

    def __enter__(self) -> _BatchLeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=min(max(self.lease_seconds, 0.1), 5.0))
        if self._thread.is_alive():
            with self._lock:
                self._lost_job_ids.update(job.job_id for job in self.claimed)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            for job in self.claimed:
                if self.lost(job.job_id):
                    continue
                try:
                    renewed = self.jobs.renew_lease(
                        job.job_id,
                        worker_id=job.lease_owner or "",
                        lease_token=job.lease_token or "",
                        lease_seconds=self.lease_seconds,
                    )
                except Exception:
                    renewed = None
                if renewed is None:
                    with self._lock:
                        self._lost_job_ids.add(job.job_id)


def _report(jobs: list[EmbeddingJob]) -> EmbeddingWorkerReport:
    return EmbeddingWorkerReport(
        processed=len(jobs),
        succeeded=sum(job.status == "succeeded" for job in jobs),
        failed=sum(job.status in {"pending", "dead_letter"} for job in jobs),
        dead_lettered=sum(job.status == "dead_letter" for job in jobs),
        superseded=sum(job.status == "superseded" for job in jobs),
    )
