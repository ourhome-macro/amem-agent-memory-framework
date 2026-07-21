from __future__ import annotations

import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from agent_memory_runtime.audit.stores.sqlite import SQLiteAuditStore
from agent_memory_runtime.domain.enums import MemoryLayer, MemorySessionPolicy, MemoryStatus
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.domain.tombstone import MemoryTombstone
from agent_memory_runtime.exceptions import EventConflictError
from agent_memory_runtime.memory.retrieval.lexical import (
    lexical_tokens,
    searchable_record_text,
)
from agent_memory_runtime.memory.stores.sqlite_manager import (
    SQLiteBackupReport,
    SQLiteTransactionManager,
)

if TYPE_CHECKING:
    from agent_memory_runtime.agent.orchestration.stores import OrchestrationStateStore
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
    def __init__(self, path_or_manager: str | Path | SQLiteTransactionManager) -> None:
        super().__init__(path_or_manager)
        self._backfill_search_index()

    def upsert(self, record: MemoryRecord) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO memories(
                    memory_id, payload, tenant_id, user_id, agent_id, session_id,
                    layer, status, memory_type, scope, updated_at, salience, search_indexed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                _memory_row(record),
            )
            self._replace_search_index(connection, record)

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

    def query_records(
        self,
        query: MemoryQuery,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        if limit <= 0:
            return []
        terms = sorted(lexical_tokens(query.text))
        parameters: list[object] = []
        if terms:
            placeholders = ", ".join("?" for _ in terms)
            match_join = f"""
                LEFT JOIN (
                    SELECT memory_id, COUNT(*) AS lexical_hits
                    FROM memory_terms
                    WHERE term IN ({placeholders})
                    GROUP BY memory_id
                ) AS hits ON hits.memory_id = memories.memory_id
            """
            parameters.extend(terms)
        else:
            match_join = ""

        where = ["memories.tenant_id = ?"]
        parameters.append(query.tenant_id)
        if query.user_id is None:
            where.append("memories.user_id IS NULL")
        else:
            where.append("(memories.user_id IS NULL OR memories.user_id = ?)")
            parameters.append(query.user_id)

        archival_enabled = MemoryLayer.ARCHIVAL.value in set(query.layers)
        if archival_enabled:
            where.append(
                "(memories.status = ? OR "
                "(memories.status = ? AND memories.layer = ?))"
            )
            parameters.extend(
                [
                    MemoryStatus.ACTIVE.value,
                    MemoryStatus.ARCHIVED.value,
                    MemoryLayer.ARCHIVAL.value,
                ]
            )
        else:
            where.append("memories.status = ?")
            parameters.append(MemoryStatus.ACTIVE.value)

        if query.session_id is not None:
            policy = MemorySessionPolicy(query.session_policy)
            if policy is MemorySessionPolicy.EXACT:
                where.append("memories.session_id = ?")
                parameters.append(query.session_id)
            elif policy is MemorySessionPolicy.PROFILE:
                where.append("(memories.session_id = ? OR memories.layer <> ?)")
                parameters.extend([query.session_id, MemoryLayer.WORKING.value])
        _append_in_filter(where, parameters, "memories.scope", query.scopes)
        _append_in_filter(where, parameters, "memories.memory_type", query.memory_types)
        _append_in_filter(where, parameters, "memories.layer", query.layers)
        if query.tags:
            placeholders = ", ".join("?" for _ in query.tags)
            where.append(
                "EXISTS ("
                "SELECT 1 FROM memory_tags AS requested_tags "
                "WHERE requested_tags.memory_id = memories.memory_id "
                f"AND requested_tags.tag IN ({placeholders})"
                ")"
            )
            parameters.extend(query.tags)

        lexical_order = "COALESCE(hits.lexical_hits, 0) DESC," if terms else ""
        sql = f"""
            SELECT memories.payload
            FROM memories
            {match_join}
            WHERE {' AND '.join(where)}
            ORDER BY {lexical_order}
                     memories.salience DESC,
                     memories.updated_at DESC,
                     memories.memory_id ASC
            LIMIT ? OFFSET ?
        """
        parameters.extend([limit, max(0, offset)])
        with self._manager.read_connection() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [MemoryRecord.from_dict(json.loads(row[0])) for row in rows]

    def replace_all(self, records: list[MemoryRecord]) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM memories")
            connection.executemany(
                """
                INSERT INTO memories(
                    memory_id, payload, tenant_id, user_id, agent_id, session_id,
                    layer, status, memory_type, scope, updated_at, salience, search_indexed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                [_memory_row(record) for record in records],
            )
            for record in records:
                self._replace_search_index(connection, record)

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM memories")

    def _backfill_search_index(self) -> None:
        with self._manager.connection() as connection:
            rows = connection.execute(
                "SELECT memory_id, payload FROM memories WHERE search_indexed = 0"
            ).fetchall()
            for memory_id, payload in rows:
                record = MemoryRecord.from_dict(json.loads(payload))
                self._replace_search_index(connection, record)
                connection.execute(
                    "UPDATE memories SET search_indexed = 1 WHERE memory_id = ?",
                    (memory_id,),
                )

    @staticmethod
    def _replace_search_index(connection: object, record: MemoryRecord) -> None:
        connection.execute("DELETE FROM memory_terms WHERE memory_id = ?", (record.memory_id,))
        terms = sorted(lexical_tokens(searchable_record_text(record)))
        if terms:
            connection.executemany(
                "INSERT INTO memory_terms(memory_id, term) VALUES (?, ?)",
                [(record.memory_id, term) for term in terms],
            )
        connection.execute("DELETE FROM memory_tags WHERE memory_id = ?", (record.memory_id,))
        if record.tags:
            connection.executemany(
                "INSERT INTO memory_tags(memory_id, tag) VALUES (?, ?)",
                [(record.memory_id, tag) for tag in sorted(set(record.tags))],
            )


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

    def prune(self, *, keep_last: int) -> int:
        keep = max(0, keep_last)
        with self._manager.connection() as connection:
            before = int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
            if keep:
                connection.execute(
                    """
                    DELETE FROM snapshots
                    WHERE id NOT IN (
                        SELECT id FROM snapshots ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (keep,),
                )
            else:
                connection.execute("DELETE FROM snapshots")
            after = int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
        return before - after


class SQLiteTombstoneStore(SQLiteStore):
    def put(self, tombstone: MemoryTombstone) -> None:
        with self._manager.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM memory_tombstones WHERE memory_id = ?",
                (tombstone.memory_id,),
            ).fetchone()
            if row is not None:
                current = MemoryTombstone.from_dict(json.loads(row[0]))
                if current.deleted_through_sequence > tombstone.deleted_through_sequence:
                    return
            connection.execute(
                """
                INSERT OR REPLACE INTO memory_tombstones(
                    memory_id, tenant_id, deleted_at, payload
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    tombstone.memory_id,
                    tombstone.tenant_id,
                    tombstone.deleted_at,
                    _serialize(tombstone.to_dict()),
                ),
            )

    def get(self, memory_id: str) -> MemoryTombstone | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM memory_tombstones WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return None if row is None else MemoryTombstone.from_dict(json.loads(row[0]))

    def list_tombstones(self) -> list[MemoryTombstone]:
        with self._manager.read_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM memory_tombstones ORDER BY memory_id"
            ).fetchall()
        return [MemoryTombstone.from_dict(json.loads(row[0])) for row in rows]

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM memory_tombstones")


class SQLiteStoreBundle:
    """Creates stores that share one transaction manager and database file."""

    def __init__(
        self,
        path: str | Path,
        *,
        agent_state_codec: StateCodec | None = None,
    ) -> None:
        from agent_memory_runtime.agent.orchestration.stores import (
            SQLiteOrchestrationStore,
        )
        from agent_memory_runtime.agent.stores import SQLiteAgentStateStore
        from agent_memory_runtime.governance.queue import SQLiteDerivationQueueStore

        self._manager = SQLiteTransactionManager(path)
        self.event_store = SQLiteEventStore(self._manager)
        self.memory_store = SQLiteMemoryStore(self._manager)
        self.snapshot_store = SQLiteSnapshotStore(self._manager)
        self.tombstone_store = SQLiteTombstoneStore(self._manager)
        self.audit_store = SQLiteAuditStore(self._manager)
        self.derivation_queue = SQLiteDerivationQueueStore(self._manager)
        self.agent_state_store = SQLiteAgentStateStore(
            self._manager,
            codec=agent_state_codec,
        )
        self.orchestration_store: OrchestrationStateStore = SQLiteOrchestrationStore(
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


def _memory_row(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.memory_id,
        _serialize(record.to_dict()),
        record.tenant_id,
        record.user_id,
        record.agent_id or record.owner_id,
        record.session_id,
        record.layer,
        record.status,
        record.memory_type,
        record.scope,
        record.updated_at,
        record.salience,
    )


def _append_in_filter(
    where: list[str],
    parameters: list[object],
    column: str,
    values: tuple[str, ...],
) -> None:
    if not values:
        return
    placeholders = ", ".join("?" for _ in values)
    where.append(f"{column} IN ({placeholders})")
    parameters.extend(values)
