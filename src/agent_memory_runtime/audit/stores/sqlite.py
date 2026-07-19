from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.llm_trace import LLMCallTrace
from agent_memory_runtime.audit.stores.serialization import (
    envelope_from_dict_or_legacy,
    envelope_from_llm_trace,
    llm_trace_from_envelope,
)


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
        with self._manager.connection() as connection:
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

    def clear(self) -> None:
        with self._manager.connection() as connection:
            connection.execute("DELETE FROM audit_envelopes")
            connection.execute("DELETE FROM llm_call_traces")

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


def _manager(path_or_manager: object) -> Any:
    if hasattr(path_or_manager, "connection") and hasattr(path_or_manager, "path"):
        return path_or_manager
    from agent_memory_runtime.memory.stores.sqlite import SQLiteTransactionManager

    return SQLiteTransactionManager(Path(str(path_or_manager)))


def _serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
