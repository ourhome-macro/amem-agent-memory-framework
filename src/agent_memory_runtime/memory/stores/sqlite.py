from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from agent_memory_runtime.audit.stores.sqlite import SQLiteAuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord


class SQLiteTransactionManager:
    """Shares one SQLite write transaction across the runtime stores in a single operation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._active_connection() is not None:
            # 运行时编排和单个 Store 可以嵌套，但不能拆分同一个写入单元。
            yield
            return

        connection = self._connect()
        self._local.connection = connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._local.connection = None
            connection.close()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection()
        if active is not None:
            yield active
            return
        # Store 被直接使用时，也为该单次操作创建短事务。
        with self.transaction():
            connection = self._active_connection()
            if connection is None:
                raise RuntimeError("SQLite transaction did not expose an active connection.")
            yield connection

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _active_connection(self) -> sqlite3.Connection | None:
        return getattr(self._local, "connection", None)

    def _init_schema(self) -> None:
        with self.transaction():
            connection = self._active_connection()
            if connection is None:
                raise RuntimeError("SQLite schema initialization requires an active connection.")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_call_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )


class SQLiteStore:
    def __init__(self, path_or_manager: str | Path | SQLiteTransactionManager) -> None:
        self._manager = (
            path_or_manager
            if isinstance(path_or_manager, SQLiteTransactionManager)
            else SQLiteTransactionManager(path_or_manager)
        )
        self.path = self._manager.path


class SQLiteEventStore(SQLiteStore):
    def append(self, event: Event) -> Event:
        with self._manager.connection() as connection:
            current = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()
            sequence = event.sequence or int(current[0]) + 1
            stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
            connection.execute(
                "INSERT INTO events(sequence, event_id, payload) VALUES (?, ?, ?)",
                (stored.sequence, stored.event_id, _serialize(stored.to_dict())),
            )
        return stored

    def list_events(self) -> list[Event]:
        with self._manager.connection() as connection:
            rows = connection.execute("SELECT payload FROM events ORDER BY sequence").fetchall()
        return [Event.from_dict(json.loads(row[0])) for row in rows]

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM events")


class SQLiteMemoryStore(SQLiteStore):
    def upsert(self, record: MemoryRecord) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO memories(memory_id, payload) VALUES (?, ?)",
                (record.memory_id, _serialize(record.to_dict())),
            )

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        return MemoryRecord.from_dict(json.loads(row[0]))

    def list_records(self) -> list[MemoryRecord]:
        with self._manager.connection() as connection:
            rows = connection.execute("SELECT payload FROM memories ORDER BY memory_id").fetchall()
        return [MemoryRecord.from_dict(json.loads(row[0])) for row in rows]

    def replace_all(self, records: list[MemoryRecord]) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM memories")
            connection.executemany(
                "INSERT INTO memories(memory_id, payload) VALUES (?, ?)",
                [(record.memory_id, _serialize(record.to_dict())) for record in records],
            )

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM memories")


class SQLiteSnapshotStore(SQLiteStore):
    def save(self, snapshot: dict[str, object]) -> None:
        with self._manager.connection() as connection:
            connection.execute("INSERT INTO snapshots(payload) VALUES (?)", (_serialize(snapshot),))

    def latest(self) -> dict[str, object] | None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(json.loads(row[0]))

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM snapshots")


class SQLiteStoreBundle:
    """Creates stores that share one transaction manager and database file."""

    def __init__(self, path: str | Path) -> None:
        from agent_memory_runtime.governance.queue import SQLiteDerivationQueueStore

        self._manager = SQLiteTransactionManager(path)
        self.event_store = SQLiteEventStore(self._manager)
        self.memory_store = SQLiteMemoryStore(self._manager)
        self.snapshot_store = SQLiteSnapshotStore(self._manager)
        self.audit_store = SQLiteAuditStore(self._manager)
        self.derivation_queue = SQLiteDerivationQueueStore(self._manager)

    def transaction(self) -> AbstractContextManager[None]:
        return self._manager.transaction()


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
