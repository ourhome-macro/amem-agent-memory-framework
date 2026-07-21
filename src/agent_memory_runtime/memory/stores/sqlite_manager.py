from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from agent_memory_runtime.exceptions import StoreError


@dataclass(frozen=True)
class SQLiteBackupReport:
    path: Path
    integrity_check: str
    page_count: int
    schema_version: int


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n".join((str(self.version), self.name, *self.statements))
        return sha256(payload.encode("utf-8")).hexdigest()


_MIGRATIONS = (
    _Migration(
        version=1,
        name="core_event_memory_snapshot",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS llm_call_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL
            )
            """,
        ),
    ),
    _Migration(
        version=2,
        name="audit_queue_and_governance",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS audit_envelopes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT UNIQUE NOT NULL,
                audit_type TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS derivation_jobs (
                job_id TEXT PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_derivation_jobs_status
            ON derivation_jobs(status)
            """,
            """
            CREATE TABLE IF NOT EXISTS review_items (
                review_id TEXT PRIMARY KEY,
                dedupe_key TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_review_items_status
            ON review_items(status)
            """,
            """
            CREATE TABLE IF NOT EXISTS vault_records (
                token_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_vault_records_owner
            ON vault_records(tenant_id, owner_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS memory_tombstones (
                memory_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                deleted_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """,
        ),
    ),
    _Migration(
        version=3,
        name="business_agent_runs",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(tenant_id, request_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_status
            ON agent_runs(tenant_id, status)
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_turns (
                turn_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(run_id, sequence),
                FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_checkpoints (
                run_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                call_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run_status
            ON agent_tool_calls(run_id, status)
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_approvals (
                approval_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                call_id TEXT UNIQUE NOT NULL,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY(call_id) REFERENCES agent_tool_calls(call_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_approvals_tenant_status
            ON agent_approvals(tenant_id, status)
            """,
        ),
    ),
    _Migration(
        version=4,
        name="controlled_agent_orchestrations",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS agent_orchestrations (
                orchestration_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(tenant_id, request_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_orchestrations_tenant_status
            ON agent_orchestrations(tenant_id, status)
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_delegations (
                orchestration_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(orchestration_id, task_id),
                FOREIGN KEY(orchestration_id)
                    REFERENCES agent_orchestrations(orchestration_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_delegations_run_status
            ON agent_delegations(orchestration_id, status)
            """,
        ),
    ),
)

LATEST_SCHEMA_VERSION = _MIGRATIONS[-1].version


class SQLiteTransactionManager:
    """SQLite connection policy, migrations, transactions, and online backup."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 5.0,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = max(0.1, timeout_seconds)
        self.busy_timeout_ms = max(1, busy_timeout_ms)
        self._local = threading.local()
        self.migrate()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._active_connection() is not None:
            try:
                yield
            except BaseException:
                self._local.rollback_only = True
                raise
            return

        connection = self._connect()
        self._local.connection = connection
        self._local.rollback_only = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            if self._local.rollback_only:
                raise StoreError("nested SQLite operation marked the transaction rollback-only")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._local.connection = None
            self._local.rollback_only = False
            connection.close()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection()
        if active is not None:
            yield active
            return
        with self.transaction():
            connection = self._active_connection()
            if connection is None:
                raise StoreError("SQLite transaction did not expose an active connection")
            yield connection

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection()
        if active is not None:
            yield active
            return
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _active_connection(self) -> sqlite3.Connection | None:
        return getattr(self._local, "connection", None)

    @property
    def schema_version(self) -> int:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        return int(row[0])

    def migrate(self) -> int:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            for migration in _MIGRATIONS:
                self._apply_migration(connection, migration)
        finally:
            connection.close()
        return LATEST_SCHEMA_VERSION

    def _apply_migration(
        self,
        connection: sqlite3.Connection,
        migration: _Migration,
    ) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT name, checksum FROM schema_migrations WHERE version = ?",
                (migration.version,),
            ).fetchone()
            if row is not None:
                if row[0] != migration.name or row[1] != migration.checksum:
                    raise StoreError(f"SQLite migration {migration.version} checksum mismatch")
                connection.commit()
                return
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.execute(f"PRAGMA user_version={migration.version}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def integrity_check(self) -> str:
        with self.read_connection() as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0])

    def backup(self, destination: str | Path) -> SQLiteBackupReport:
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise ValueError("SQLite backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        source_connection = self._connect()
        target_connection: sqlite3.Connection | None = None
        try:
            try:
                target_connection = sqlite3.connect(temporary, isolation_level=None)
                source_connection.backup(target_connection)
                integrity = str(target_connection.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity != "ok":
                    raise StoreError(f"SQLite backup integrity check failed: {integrity}")
                page_count = int(target_connection.execute("PRAGMA page_count").fetchone()[0])
                schema_version = int(
                    target_connection.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
            finally:
                if target_connection is not None:
                    target_connection.close()
                source_connection.close()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        try:
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return SQLiteBackupReport(
            path=target,
            integrity_check=integrity,
            page_count=page_count,
            schema_version=schema_version,
        )
