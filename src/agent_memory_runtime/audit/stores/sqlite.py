from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace
from agent_memory_runtime.audit.stores.serialization import (
    envelope_from_dict_or_legacy,
    envelope_from_llm_trace,
    llm_trace_from_envelope,
)

if TYPE_CHECKING:
    from agent_memory_runtime.memory.intake.models import MemoryAuditLog


class SQLiteAuditStore:
    def __init__(self, path_or_manager: object) -> None:
        self._manager = _manager(path_or_manager)
        self.path = self._manager.path
        self._init_schema()

    def append_envelope(self, envelope: AuditEnvelope) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                "INSERT INTO audit_envelopes(trace_id, audit_type, payload) VALUES (?, ?, ?)",
                (envelope.trace_id, envelope.audit_type, _serialize(envelope.to_dict())),
            )

    def list_envelopes(self) -> list[AuditEnvelope]:
        envelopes: list[AuditEnvelope] = []
        with self._manager.read_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM audit_envelopes ORDER BY id"
            ).fetchall()
            legacy_rows = connection.execute(
                "SELECT payload FROM llm_call_traces ORDER BY id"
            ).fetchall()
        for row in rows:
            envelopes.append(AuditEnvelope.from_dict(json.loads(row[0])))
        for row in legacy_rows:
            envelopes.append(envelope_from_dict_or_legacy(json.loads(row[0])))
        return envelopes

    def append_trace(self, trace: LLMCallTrace) -> None:
        self.append_envelope(envelope_from_llm_trace(trace))

    def list_traces(self) -> list[LLMCallTrace]:
        traces: list[LLMCallTrace] = []
        for envelope in self.list_envelopes():
            trace = llm_trace_from_envelope(envelope)
            if trace is not None:
                traces.append(trace)
        return traces

    def append_memory_log(self, log: MemoryAuditLog) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO memory_audit_logs(
                    audit_id, memory_id, proposal_id, action, tenant_id, user_id,
                    agent_id, created_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log.audit_id,
                    log.memory_id,
                    log.proposal_id,
                    log.action,
                    log.tenant_id,
                    log.user_id,
                    log.agent_id,
                    log.created_at,
                    _serialize(log.to_dict()),
                ),
            )

    def list_memory_logs(self) -> list[MemoryAuditLog]:
        with self._manager.read_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM memory_audit_logs ORDER BY id"
            ).fetchall()
        return [_memory_log_from_dict(json.loads(row[0])) for row in rows]

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM audit_envelopes")
            connection.execute("DELETE FROM llm_call_traces")
            connection.execute("DELETE FROM memory_audit_logs")

    def _init_schema(self) -> None:
        with self._manager.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_envelopes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT UNIQUE NOT NULL,
                    audit_type TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_id TEXT UNIQUE NOT NULL,
                    memory_id TEXT,
                    proposal_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT,
                    agent_id TEXT,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )


def _manager(path_or_manager: object) -> Any:
    if hasattr(path_or_manager, "connection") and hasattr(path_or_manager, "path"):
        return path_or_manager
    from agent_memory_runtime.memory.stores.sqlite import SQLiteTransactionManager

    return SQLiteTransactionManager(Path(str(path_or_manager)))


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _memory_log_from_dict(value: dict[str, object]) -> MemoryAuditLog:
    from agent_memory_runtime.domain.memory import MemoryRecord
    from agent_memory_runtime.memory.intake.models import MemoryAuditLog

    before = value.get("before_record")
    after = value.get("after_record")
    return MemoryAuditLog(
        audit_id=str(value["audit_id"]),
        memory_id=None if value.get("memory_id") is None else str(value["memory_id"]),
        proposal_id=str(value["proposal_id"]),
        action=str(value["action"]),
        actor_id=str(value["actor_id"]),
        agent_id=None if value.get("agent_id") is None else str(value["agent_id"]),
        tenant_id=str(value.get("tenant_id") or "default"),
        user_id=None if value.get("user_id") is None else str(value["user_id"]),
        before_record=(
            None if before is None else MemoryRecord.from_dict(dict(before))  # type: ignore[arg-type]
        ),
        after_record=(
            None if after is None else MemoryRecord.from_dict(dict(after))  # type: ignore[arg-type]
        ),
        source_message_ids=tuple(str(item) for item in value.get("source_message_ids", ())),
        source_memory_ids=tuple(str(item) for item in value.get("source_memory_ids", ())),
        evidence_text=str(value.get("evidence_text") or ""),
        confidence=float(value.get("confidence") or 0.0),
        reason=str(value.get("reason") or ""),
        created_at=str(value.get("created_at") or ""),
    )
