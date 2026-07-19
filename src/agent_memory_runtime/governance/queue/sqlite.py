from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_memory_runtime.governance.queue.job import DerivationJob


class SQLiteDerivationQueueStore:
    def __init__(self, path_or_manager: object) -> None:
        self._manager = _manager(path_or_manager)
        self.path = self._manager.path
        self._init_schema()

    def enqueue(self, job: DerivationJob) -> DerivationJob:
        existing = self.find_by_event_id(job.event_id)
        if existing is not None and existing.status in {"pending", "running", "succeeded"}:
            return existing
        with self._manager.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO derivation_jobs(job_id, event_id, status, payload)
                VALUES (?, ?, ?, ?)
                """,
                (job.job_id, job.event_id, job.status, _serialize(job.to_dict())),
            )
        return job

    def get(self, job_id: str) -> DerivationJob | None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM derivation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return DerivationJob.from_dict(json.loads(row[0]))

    def find_by_event_id(self, event_id: str) -> DerivationJob | None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM derivation_jobs WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return DerivationJob.from_dict(json.loads(row[0]))

    def claim_next(self) -> DerivationJob | None:
        with self._manager.connection() as connection:
            row = connection.execute(
                """
                SELECT payload FROM derivation_jobs
                WHERE status = 'pending'
                ORDER BY json_extract(payload, '$.created_at'), job_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            job = DerivationJob.from_dict(json.loads(row[0])).claim()
            connection.execute(
                "UPDATE derivation_jobs SET status = ?, payload = ? WHERE job_id = ?",
                (job.status, _serialize(job.to_dict()), job.job_id),
            )
        return job

    def update(self, job: DerivationJob) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO derivation_jobs(job_id, event_id, status, payload)
                VALUES (?, ?, ?, ?)
                """,
                (job.job_id, job.event_id, job.status, _serialize(job.to_dict())),
            )

    def list_jobs(self, *, status: str | None = None) -> list[DerivationJob]:
        query = "SELECT payload FROM derivation_jobs"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY json_extract(payload, '$.created_at'), job_id"
        with self._manager.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DerivationJob.from_dict(json.loads(row[0])) for row in rows]

    def pending_count(self) -> int:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM derivation_jobs WHERE status = 'pending'"
            ).fetchone()
        return int(row[0])

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM derivation_jobs")

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
