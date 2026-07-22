from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlite_vec

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import LeaseLostError, StoreError
from agent_memory_runtime.memory.embeddings.base import validate_vector
from agent_memory_runtime.memory.embeddings.models import (
    EmbeddingJob,
    EmbeddingSpec,
    VectorHit,
    VectorRecord,
    embedding_content_hash,
    optional_str,
    utc_now_iso,
)
from agent_memory_runtime.memory.stores.sqlite_filters import structured_memory_where


class SQLiteEmbeddingGenerationStore:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def register(self, spec: EmbeddingSpec, *, status: str = "backfill") -> EmbeddingSpec:
        if status not in {"active", "backfill", "retired"}:
            raise ValueError(f"unsupported embedding generation status: {status}")
        payload = _serialize(spec.to_dict())
        with self._manager.connection() as connection:
            row = connection.execute(
                """
                SELECT spec_payload, status
                FROM embedding_generations WHERE generation = ?
                """,
                (spec.generation,),
            ).fetchone()
            if row is not None:
                if str(row[0]) != payload:
                    raise StoreError(f"embedding generation collision for {spec.generation!r}")
                current_status = str(row[1])
                if current_status == "retired" and status == "backfill":
                    connection.execute(
                        """
                        UPDATE embedding_generations
                        SET status = 'backfill', activated_at = NULL
                        WHERE generation = ?
                        """,
                        (spec.generation,),
                    )
                elif current_status != "active" and status == "active":
                    other_active = connection.execute(
                        """
                        SELECT generation FROM embedding_generations
                        WHERE status = 'active' AND generation <> ? LIMIT 1
                        """,
                        (spec.generation,),
                    ).fetchone()
                    if other_active is not None:
                        raise StoreError("another embedding generation is already active")
                    connection.execute(
                        """
                        UPDATE embedding_generations
                        SET status = 'active', activated_at = ?
                        WHERE generation = ?
                        """,
                        (utc_now_iso(), spec.generation),
                    )
                return spec
            now = utc_now_iso()
            connection.execute(
                """
                INSERT INTO embedding_generations(
                    generation, provider, model_id, model_revision, dimensions,
                    distance_metric, status, spec_payload, created_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.generation,
                    spec.provider,
                    spec.model_id,
                    spec.model_revision,
                    spec.dimensions,
                    spec.distance_metric,
                    status,
                    payload,
                    now,
                    now if status == "active" else None,
                ),
            )
        return spec

    def ensure_active(self, spec: EmbeddingSpec) -> None:
        active = self.active()
        if active is None:
            self.register(spec, status="backfill")
            raise StoreError(
                "embedding generation is not active; run embedding backfill and worker, "
                "then activate it after the coverage gate passes"
            )
        if active.generation != spec.generation:
            self.register(spec, status="backfill")
            raise StoreError(
                "a different embedding generation is active; backfill and explicitly "
                "activate the new generation before serving it"
            )

    def activate(self, generation: str) -> EmbeddingSpec:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT spec_payload FROM embedding_generations WHERE generation = ?",
                (generation,),
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown embedding generation: {generation}")
            connection.execute(
                "UPDATE embedding_generations SET status = 'retired' WHERE status = 'active'"
            )
            connection.execute(
                """
                UPDATE embedding_generations
                SET status = 'active', activated_at = ?
                WHERE generation = ?
                """,
                (utc_now_iso(), generation),
            )
        return EmbeddingSpec.from_dict(json.loads(row[0]))

    def active(self) -> EmbeddingSpec | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                """
                SELECT spec_payload FROM embedding_generations
                WHERE status = 'active' LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return EmbeddingSpec.from_dict(json.loads(row[0]))

    def get(self, generation: str) -> EmbeddingSpec | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT spec_payload FROM embedding_generations WHERE generation = ?",
                (generation,),
            ).fetchone()
        if row is None:
            return None
        return EmbeddingSpec.from_dict(json.loads(row[0]))

    def indexing_specs(self) -> tuple[EmbeddingSpec, ...]:
        with self._manager.read_connection() as connection:
            rows = connection.execute(
                """
                SELECT spec_payload FROM embedding_generations
                WHERE status IN ('active', 'backfill')
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, generation
                """
            ).fetchall()
        return tuple(EmbeddingSpec.from_dict(json.loads(row[0])) for row in rows)

    def list_generations(self) -> list[dict[str, object]]:
        with self._manager.read_connection() as connection:
            rows = connection.execute(
                """
                SELECT generation, provider, model_id, model_revision, dimensions,
                       distance_metric, status, created_at, activated_at
                FROM embedding_generations
                ORDER BY created_at, generation
                """
            ).fetchall()
        return [
            {
                "generation": str(row[0]),
                "provider": str(row[1]),
                "model_id": str(row[2]),
                "model_revision": str(row[3]),
                "dimensions": int(row[4]),
                "distance_metric": str(row[5]),
                "status": str(row[6]),
                "created_at": str(row[7]),
                "activated_at": optional_str(row[8]),
            }
            for row in rows
        ]

    def delete_retired(self, generation: str) -> None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT status FROM embedding_generations WHERE generation = ?",
                (generation,),
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown embedding generation: {generation}")
            if str(row[0]) != "retired":
                raise StoreError(
                    "only a retired embedding generation can be deleted"
                )
            connection.execute(
                "DELETE FROM embedding_generations WHERE generation = ?",
                (generation,),
            )


class SQLiteEmbeddingJobStore:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    @property
    def transaction_manager(self) -> Any:
        return self._manager

    def schedule(self, record: MemoryRecord, spec: EmbeddingSpec) -> EmbeddingJob | None:
        content_hash = embedding_content_hash(record, spec)
        with self._manager.connection() as connection:
            vector = connection.execute(
                """
                SELECT content_hash, status FROM memory_embeddings
                WHERE memory_id = ? AND generation = ?
                """,
                (record.memory_id, spec.generation),
            ).fetchone()
            if vector is not None and vector[0] == content_hash and vector[1] == "ready":
                connection.execute(
                    """
                    UPDATE memory_embeddings SET source_sequence = ?
                    WHERE memory_id = ? AND generation = ?
                    """,
                    (record.last_event_sequence, record.memory_id, spec.generation),
                )
                return None

            connection.execute(
                """
                UPDATE memory_embeddings SET status = 'stale'
                WHERE memory_id = ? AND generation = ?
                """,
                (record.memory_id, spec.generation),
            )
            connection.execute(
                """
                UPDATE embedding_jobs
                SET status = 'superseded', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE memory_id = ? AND generation = ?
                  AND status IN ('pending', 'running')
                  AND content_hash <> ?
                """,
                (utc_now_iso(), record.memory_id, spec.generation, content_hash),
            )
            row = connection.execute(
                """
                SELECT * FROM embedding_jobs
                WHERE memory_id = ? AND generation = ? AND content_hash = ?
                """,
                (record.memory_id, spec.generation, content_hash),
            ).fetchone()
            if row is not None:
                existing = _job_from_row(row)
                if existing.status in {"pending", "running"}:
                    return existing
                reset = EmbeddingJob.new(
                    memory_id=record.memory_id,
                    generation=spec.generation,
                    content_hash=content_hash,
                    source_sequence=record.last_event_sequence,
                    max_attempts=existing.max_attempts,
                )
                connection.execute(
                    "DELETE FROM embedding_jobs WHERE job_id = ?",
                    (existing.job_id,),
                )
                self._insert(connection, reset)
                return reset

            job = EmbeddingJob.new(
                memory_id=record.memory_id,
                generation=spec.generation,
                content_hash=content_hash,
                source_sequence=record.last_event_sequence,
            )
            self._insert(connection, job)
            return job

    def cancel_memory(self, memory_id: str) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                UPDATE embedding_jobs
                SET status = 'superseded', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE memory_id = ? AND status IN ('pending', 'running')
                """,
                (utc_now_iso(), memory_id),
            )

    def get(self, job_id: str) -> EmbeddingJob | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM embedding_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def claim_next(
        self,
        *,
        generation: str,
        worker_id: str,
        lease_seconds: float,
    ) -> EmbeddingJob | None:
        with self._manager.connection() as connection:
            now = datetime.now(UTC)
            now_iso = now.isoformat()
            while True:
                row = connection.execute(
                    """
                    SELECT * FROM embedding_jobs
                    WHERE generation = ?
                      AND (
                        (status = 'pending' AND available_at <= ?)
                        OR (
                            status = 'running'
                            AND lease_expires_at IS NOT NULL
                            AND lease_expires_at <= ?
                        )
                      )
                    ORDER BY
                        CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                        COALESCE(lease_expires_at, available_at),
                        created_at,
                        job_id
                    LIMIT 1
                    """,
                    (generation, now_iso, now_iso),
                ).fetchone()
                if row is None:
                    return None
                job = _job_from_row(row)
                if job.is_lease_expired(now=now):
                    job = job.recover_expired_lease(now=now)
                    self._write(connection, job)
                if not job.is_available(now=now):
                    continue
                claimed = job.claim(
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                    now=now,
                )
                self._write(connection, claimed)
                return claimed

    def owned_running(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> EmbeddingJob | None:
        with self._manager.connection() as connection:
            return self._owned_running(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> EmbeddingJob | None:
        with self._manager.connection() as connection:
            job = self._owned_running(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            if job is None:
                return None
            completed = job.succeed()
            self._write(connection, completed)
            return completed

    def supersede(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> EmbeddingJob | None:
        with self._manager.connection() as connection:
            job = self._owned_running(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            if job is None:
                return None
            superseded = job.supersede()
            self._write(connection, superseded)
            return superseded

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        error: Exception,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> EmbeddingJob | None:
        with self._manager.connection() as connection:
            job = self._owned_running(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            if job is None:
                return None
            failed = job.fail(
                error,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
            self._write(connection, failed)
            return failed

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> EmbeddingJob | None:
        with self._manager.connection() as connection:
            job = self._owned_running(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
            )
            if job is None:
                return None
            renewed = job.renew(lease_seconds=lease_seconds)
            self._write(connection, renewed)
            return renewed

    def list_jobs(
        self,
        *,
        generation: str | None = None,
        status: str | None = None,
    ) -> list[EmbeddingJob]:
        where: list[str] = []
        parameters: list[object] = []
        if generation is not None:
            where.append("generation = ?")
            parameters.append(generation)
        if status is not None:
            where.append("status = ?")
            parameters.append(status)
        sql = "SELECT * FROM embedding_jobs"
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY created_at, job_id"
        with self._manager.read_connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [_job_from_row(row) for row in rows]

    def pending_count(self, *, generation: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM embedding_jobs WHERE status = 'pending'"
        parameters: tuple[object, ...] = ()
        if generation is not None:
            sql += " AND generation = ?"
            parameters = (generation,)
        with self._manager.read_connection() as connection:
            return int(connection.execute(sql, parameters).fetchone()[0])

    def outstanding_count(self, *, generation: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM embedding_jobs WHERE status IN ('pending', 'running')"
        parameters: tuple[object, ...] = ()
        if generation is not None:
            sql += " AND generation = ?"
            parameters = (generation,)
        with self._manager.read_connection() as connection:
            return int(connection.execute(sql, parameters).fetchone()[0])

    def backlog_lag_seconds(self, *, generation: str | None = None) -> float:
        sql = (
            "SELECT MIN(created_at) FROM embedding_jobs "
            "WHERE status IN ('pending', 'running')"
        )
        parameters: tuple[object, ...] = ()
        if generation is not None:
            sql += " AND generation = ?"
            parameters = (generation,)
        with self._manager.read_connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
        if row is None or row[0] is None:
            return 0.0
        created_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return round(
            max(0.0, (datetime.now(UTC) - created_at.astimezone(UTC)).total_seconds()),
            3,
        )

    def _owned_running(
        self,
        connection: Any,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> EmbeddingJob | None:
        row = connection.execute(
            "SELECT * FROM embedding_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        job = _job_from_row(row)
        now = datetime.now(UTC)
        if job.status != "running":
            return None
        if job.is_lease_expired(now=now):
            self._write(connection, job.recover_expired_lease(now=now))
            return None
        if job.lease_owner != worker_id or job.lease_token != lease_token:
            return None
        return job

    @staticmethod
    def _insert(connection: Any, job: EmbeddingJob) -> None:
        connection.execute(
            """
            INSERT INTO embedding_jobs(
                job_id, memory_id, generation, content_hash, source_sequence,
                status, attempts, max_attempts, available_at, lease_owner,
                lease_token, lease_expires_at, error_type, error_hash,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _job_row(job),
        )

    @staticmethod
    def _write(connection: Any, job: EmbeddingJob) -> None:
        connection.execute(
            """
            UPDATE embedding_jobs SET
                memory_id = ?, generation = ?, content_hash = ?, source_sequence = ?,
                status = ?, attempts = ?, max_attempts = ?, available_at = ?,
                lease_owner = ?, lease_token = ?, lease_expires_at = ?,
                error_type = ?, error_hash = ?, created_at = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (*_job_row(job)[1:], job.job_id),
        )


class SQLiteVectorIndex:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def upsert(self, record: VectorRecord) -> None:
        validate_vector(record.vector, record.spec)
        with self._manager.connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_embeddings(
                    memory_id, generation, model_id, model_revision, dimensions,
                    content_hash, source_sequence, embedding, status, embedded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                ON CONFLICT(memory_id, generation) DO UPDATE SET
                    model_id = excluded.model_id,
                    model_revision = excluded.model_revision,
                    dimensions = excluded.dimensions,
                    content_hash = excluded.content_hash,
                    source_sequence = excluded.source_sequence,
                    embedding = excluded.embedding,
                    status = 'ready',
                    embedded_at = excluded.embedded_at
                """,
                (
                    record.memory_id,
                    record.spec.generation,
                    record.spec.model_id,
                    record.spec.model_revision,
                    record.spec.dimensions,
                    record.content_hash,
                    record.source_sequence,
                    sqlite_vec.serialize_float32(record.vector),
                    record.embedded_at,
                ),
            )

    def search(
        self,
        vector: list[float],
        query: MemoryQuery,
        *,
        spec: EmbeddingSpec,
        limit: int,
    ) -> list[VectorHit]:
        if limit <= 0:
            return []
        validate_vector(vector, spec)
        where, parameters = structured_memory_where(query, alias="m")
        sql = f"""
            SELECT
                e.memory_id,
                vec_distance_cosine(e.embedding, ?) AS distance
            FROM memory_embeddings AS e
            JOIN memories AS m ON m.memory_id = e.memory_id
            WHERE e.generation = ? AND e.status = 'ready'
              AND {" AND ".join(where)}
            ORDER BY distance ASC, e.memory_id ASC
            LIMIT ?
        """
        values: list[object] = [
            sqlite_vec.serialize_float32(vector),
            spec.generation,
            *parameters,
            limit,
        ]
        with self._manager.read_connection() as connection:
            rows = connection.execute(sql, tuple(values)).fetchall()
        return [
            VectorHit(
                memory_id=str(memory_id),
                distance=float(distance),
                similarity=max(-1.0, min(1.0, 1.0 - float(distance))),
            )
            for memory_id, distance in rows
        ]

    def delete_memory(self, memory_id: str, *, through_sequence: int | None = None) -> None:
        with self._manager.connection() as connection:
            if through_sequence is None:
                connection.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id = ?",
                    (memory_id,),
                )
            else:
                connection.execute(
                    """
                    DELETE FROM memory_embeddings
                    WHERE memory_id = ? AND source_sequence <= ?
                    """,
                    (memory_id, through_sequence),
                )

    def mark_retired_stale(self, memory_id: str) -> None:
        """Prevent an old generation from serving content changed after retirement."""

        with self._manager.connection() as connection:
            connection.execute(
                """
                UPDATE memory_embeddings
                SET status = 'stale'
                WHERE memory_id = ?
                  AND status = 'ready'
                  AND generation IN (
                      SELECT generation FROM embedding_generations
                      WHERE status = 'retired'
                  )
                """,
                (memory_id,),
            )

    def coverage(self, *, generation: str) -> float:
        with self._manager.read_connection() as connection:
            total = int(
                connection.execute("SELECT COUNT(DISTINCT memory_id) FROM memory_acl").fetchone()[0]
            )
            ready = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT e.memory_id)
                    FROM memory_embeddings AS e
                    JOIN memory_acl AS a ON a.memory_id = e.memory_id
                    WHERE e.generation = ? AND e.status = 'ready'
                    """,
                    (generation,),
                ).fetchone()[0]
            )
        return 1.0 if total == 0 else round(ready / total, 6)

    def ready_count(self, *, generation: str) -> int:
        with self._manager.read_connection() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM memory_embeddings
                    WHERE generation = ? AND status = 'ready'
                    """,
                    (generation,),
                ).fetchone()[0]
            )


class SQLiteEmbeddingScheduler:
    def __init__(
        self,
        *,
        generations: SQLiteEmbeddingGenerationStore,
        jobs: SQLiteEmbeddingJobStore,
        vectors: SQLiteVectorIndex,
    ) -> None:
        self.generations = generations
        self.jobs = jobs
        self.vectors = vectors

    def schedule(self, record: MemoryRecord, *, retrievable: bool) -> tuple[EmbeddingJob, ...]:
        if not retrievable:
            self.jobs.cancel_memory(record.memory_id)
            self.vectors.delete_memory(record.memory_id)
            return ()
        scheduled = []
        for spec in self.generations.indexing_specs():
            job = self.jobs.schedule(record, spec)
            if job is not None:
                scheduled.append(job)
        return tuple(scheduled)

    def schedule_many(
        self,
        records: list[tuple[MemoryRecord, bool]],
    ) -> tuple[EmbeddingJob, ...]:
        specs = self.generations.indexing_specs()
        scheduled = []
        for record, retrievable in records:
            if not retrievable:
                self.jobs.cancel_memory(record.memory_id)
                self.vectors.delete_memory(record.memory_id)
                continue
            for spec in specs:
                job = self.jobs.schedule(record, spec)
                if job is not None:
                    scheduled.append(job)
        return tuple(scheduled)


def _job_row(job: EmbeddingJob) -> tuple[object, ...]:
    return (
        job.job_id,
        job.memory_id,
        job.generation,
        job.content_hash,
        job.source_sequence,
        job.status,
        job.attempts,
        job.max_attempts,
        job.available_at,
        job.lease_owner,
        job.lease_token,
        job.lease_expires_at,
        job.error_type,
        job.error_hash,
        job.created_at,
        job.updated_at,
    )


def _job_from_row(row: Any) -> EmbeddingJob:
    return EmbeddingJob(
        job_id=str(row[0]),
        memory_id=str(row[1]),
        generation=str(row[2]),
        content_hash=str(row[3]),
        source_sequence=int(row[4]),
        status=str(row[5]),
        attempts=int(row[6]),
        max_attempts=int(row[7]),
        available_at=str(row[8]),
        lease_owner=optional_str(row[9]),
        lease_token=optional_str(row[10]),
        lease_expires_at=optional_str(row[11]),
        error_type=optional_str(row[12]),
        error_hash=optional_str(row[13]),
        created_at=str(row[14]),
        updated_at=str(row[15]),
    )


def require_owned_job(
    jobs: SQLiteEmbeddingJobStore,
    job: EmbeddingJob,
) -> EmbeddingJob:
    if job.lease_token is None:
        raise LeaseLostError(f"embedding job {job.job_id} has no lease token")
    owned = jobs.owned_running(
        job.job_id,
        worker_id=job.lease_owner or "",
        lease_token=job.lease_token,
    )
    if owned is None:
        raise LeaseLostError(f"embedding job {job.job_id} lost its lease")
    return owned


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
