from __future__ import annotations

import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from agent_memory_runtime.audit.stores.sqlite import SQLiteAuditStore
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.exceptions import EventConflictError
from agent_memory_runtime.memory.stores.sqlite_manager import (
    SQLiteBackupReport,
    SQLiteTransactionManager,
)

if TYPE_CHECKING:
    from agent_memory_runtime.agent.stores import StateCodec


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
            row = connection.execute(
                "SELECT payload FROM events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if row is not None:
                existing = Event.from_dict(json.loads(row[0]))
                if not existing.is_retry_of(event):
                    raise EventConflictError(
                        f"event_id {event.event_id!r} is already bound to a different event"
                    )
                return existing
            current = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()
            sequence = event.sequence or int(current[0]) + 1
            stored = Event.from_dict({**event.to_dict(), "sequence": sequence})
            connection.execute(
                "INSERT INTO events(sequence, event_id, payload) VALUES (?, ?, ?)",
                (stored.sequence, stored.event_id, _serialize(stored.to_dict())),
            )
        return stored

    def get(self, event_id: str) -> Event | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return Event.from_dict(json.loads(row[0]))

    def list_events(self) -> list[Event]:
        with self._manager.read_connection() as connection:
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
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        return MemoryRecord.from_dict(json.loads(row[0]))

    def list_records(self) -> list[MemoryRecord]:
        with self._manager.read_connection() as connection:
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
        with self._manager.read_connection() as connection:
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

    def __init__(
        self,
        path: str | Path,
        *,
        agent_state_codec: StateCodec | None = None,
    ) -> None:
        from agent_memory_runtime.agent.stores import SQLiteAgentStateStore
        from agent_memory_runtime.governance.queue import SQLiteDerivationQueueStore

        self._manager = SQLiteTransactionManager(path)
        self.event_store = SQLiteEventStore(self._manager)
        self.memory_store = SQLiteMemoryStore(self._manager)
        self.snapshot_store = SQLiteSnapshotStore(self._manager)
        self.audit_store = SQLiteAuditStore(self._manager)
        self.derivation_queue = SQLiteDerivationQueueStore(self._manager)
        self.agent_state_store = SQLiteAgentStateStore(
            self._manager,
            codec=agent_state_codec,
        )

    def transaction(self) -> AbstractContextManager[None]:
        return self._manager.transaction()

    @property
    def schema_version(self) -> int:
        return self._manager.schema_version

    def integrity_check(self) -> str:
        return self._manager.integrity_check()

    def backup(self, destination: str | Path) -> SQLiteBackupReport:
        return self._manager.backup(destination)

    def shadow_replay(
        self,
        *,
        config: object | None = None,
        derivation_engine: object | None = None,
    ) -> object:
        from agent_memory_runtime.audit.replay import shadow_replay_events

        return shadow_replay_events(
            self.event_store.list_events(),
            self.snapshot_store.latest(),
            config=config,
            derivation_engine=derivation_engine,
        )


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
