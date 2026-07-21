from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_memory_runtime.governance.queue.job import DerivationJob


class SQLiteDerivationQueueStore:
    def __init__(self, path_or_manager: object) -> None:
        self._manager = _manager(path_or_manager)
        self.path = self._manager.path
        self._init_schema()

    def enqueue(self, job: DerivationJob) -> DerivationJob:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM derivation_jobs WHERE event_id = ?",
                (job.event_id,),
            ).fetchone()
            if row is not None:
                return DerivationJob.from_dict(json.loads(row[0]))
            connection.execute(
                """
                INSERT INTO derivation_jobs(job_id, event_id, status, payload)
                VALUES (?, ?, ?, ?)
                """,
                (job.job_id, job.event_id, job.status, _serialize(job.to_dict())),
            )
        return job

    def get(self, job_id: str) -> DerivationJob | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM derivation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return DerivationJob.from_dict(json.loads(row[0]))

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM derivation_jobs WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return DerivationJob.from_dict(json.loads(row[0]))

    def claim_next(
        self,
        *,
        worker_id: str = "worker",
        lease_seconds: float = 30.0,
    ) -> DerivationJob | None:
        with self._manager.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM derivation_jobs
                WHERE status IN ('pending', 'running')
                ORDER BY json_extract(payload, '$.created_at'), job_id
                """
            ).fetchall()
            now = datetime.now(UTC)
            for row in rows:
                job = DerivationJob.from_dict(json.loads(row[0]))
                if job.is_lease_expired(now=now):
                    job = job.recover_expired_lease(now=now)
                    self._write_job(connection, job)
                if not job.is_available(now=now):
                    continue
                claimed = job.claim(
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                    now=now,
                )
                self._write_job(connection, claimed)
                return claimed
        return None

    def update(self, job: DerivationJob) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO derivation_jobs(job_id, event_id, status, payload)
                VALUES (?, ?, ?, ?)
                """,
                (job.job_id, job.event_id, job.status, _serialize(job.to_dict())),
            )

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> DerivationJob | None:
        with self._manager.connection() as connection:
            now = datetime.now(UTC)
            job = self._owned_running_job(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            if job is None:
                return None
            completed = job.succeed(now=now)
            self._write_job(connection, completed)
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
        with self._manager.connection() as connection:
            now = datetime.now(UTC)
            job = self._owned_running_job(
                connection,
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
            self._write_job(connection, failed)
            return failed

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> DerivationJob | None:
        with self._manager.connection() as connection:
            now = datetime.now(UTC)
            job = self._owned_running_job(
                connection,
                job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            if job is None:
                return None
            renewed = job.renew(lease_seconds=lease_seconds, now=now)
            self._write_job(connection, renewed)
            return renewed

    def redrive(self, job_id: str) -> DerivationJob | None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM derivation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            job = DerivationJob.from_dict(json.loads(row[0]))
            if job.status != "dead_letter":
                return None
            redriven = job.redrive()
            self._write_job(connection, redriven)
            return redriven

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        query = "SELECT payload FROM derivation_jobs"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY json_extract(payload, '$.created_at'), job_id"
        with self._manager.read_connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DerivationJob.from_dict(json.loads(row[0])) for row in rows]

    def pending_count(self) -> int:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM derivation_jobs WHERE status = 'pending'"
            ).fetchone()
        return int(row[0])

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM derivation_jobs")

    def _owned_running_job(
        self,
        connection: Any,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> DerivationJob | None:
        row = connection.execute(
            "SELECT payload FROM derivation_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        job = DerivationJob.from_dict(json.loads(row[0]))
        if job.status != "running":
            return None
        if job.is_lease_expired(now=now):
            self._write_job(connection, job.recover_expired_lease(now=now))
            return None
        if (
            job.lease_owner != worker_id
            or not lease_token
            or job.lease_token != lease_token
        ):
            return None
        return job

    def _write_job(self, connection: Any, job: DerivationJob) -> None:
        connection.execute(
            "UPDATE derivation_jobs SET status = ?, payload = ? WHERE job_id = ?",
            (job.status, _serialize(job.to_dict()), job.job_id),
        )

    def _init_schema(self) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS derivation_jobs (
                    job_id TEXT PRIMARY KEY,
                    event_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_derivation_jobs_status
                ON derivation_jobs(status)
                """
            )


def _manager(path_or_manager: object) -> Any:
    if hasattr(path_or_manager, "connection") and hasattr(path_or_manager, "path"):
        return path_or_manager
    from agent_memory_runtime.memory.stores.sqlite import SQLiteTransactionManager

    return SQLiteTransactionManager(Path(str(path_or_manager)))


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
