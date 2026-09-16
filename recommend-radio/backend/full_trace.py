from __future__ import annotations

import contextvars
import hashlib
import json
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from database import get_connection

_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "recommend_radio_trace_id",
    default=None,
)
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
}


def current_trace_id() -> str | None:
    return _CURRENT_TRACE_ID.get()


class FullTrace:
    """Best-effort persisted trace for dialogue, recommendation and discovery."""

    def __init__(
        self,
        db_path: str,
        *,
        trace_type: str,
        user_id: str,
        session_id: str | None = None,
        request_id: str | None = None,
        parent_trace_id: str | None = None,
        trace_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        initial_status: str = "running",
    ) -> None:
        self.db_path = db_path
        self.trace_id = trace_id or f"trace:{trace_type}:{uuid4().hex}"
        self.trace_type = trace_type
        self.user_id = user_id
        self.session_id = session_id
        self.request_id = request_id
        self.parent_trace_id = parent_trace_id
        self.started_at = _utc_now()
        self._started_perf = perf_counter()
        self._sequence = 0
        self._token: contextvars.Token[str | None] | None = None
        self._write_trace(initial_status=initial_status, attributes=attributes or {})

    @classmethod
    def resume(cls, db_path: str, trace_id: str) -> FullTrace:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM evaluation_traces WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM evaluation_trace_events WHERE trace_id=?",
                (trace_id,),
            ).fetchone()[0]
        if row is None:
            raise KeyError(trace_id)
        value = cls.__new__(cls)
        value.db_path = db_path
        value.trace_id = trace_id
        value.trace_type = str(row["trace_type"])
        value.user_id = str(row["user_id"] or "")
        value.session_id = row["session_id"]
        value.request_id = row["request_id"]
        value.parent_trace_id = row["parent_trace_id"]
        value.started_at = str(row["started_at"])
        value._started_perf = perf_counter()
        value._sequence = int(sequence or 0)
        value._token = None
        with get_connection(db_path) as conn:
            conn.execute(
                "UPDATE evaluation_traces SET status='running', error_type=NULL WHERE trace_id=?",
                (trace_id,),
            )
        return value

    def __enter__(self) -> FullTrace:
        self._token = _CURRENT_TRACE_ID.set(self.trace_id)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_value is None:
            self.finish("completed")
        else:
            self.finish("failed", error_type=type(exc_value).__name__)
        if self._token is not None:
            _CURRENT_TRACE_ID.reset(self._token)
            self._token = None
        return False

    def record_span(
        self,
        name: str,
        duration_ms: float,
        *,
        kind: str = "internal",
        status: str = "completed",
        parent_span_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> str:
        span_id = f"span:{uuid4().hex}"
        ended_at = datetime.now(timezone.utc)
        started_at = ended_at - timedelta(milliseconds=max(float(duration_ms), 0.0))
        try:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO evaluation_trace_spans (
                        span_id, trace_id, parent_span_id, sequence, name, kind,
                        status, started_at, ended_at, duration_ms, input_json,
                        output_json, metrics_json, error_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        span_id,
                        self.trace_id,
                        parent_span_id,
                        self._next_sequence(),
                        name[:120],
                        kind[:40],
                        status[:32],
                        started_at.isoformat(),
                        ended_at.isoformat(),
                        round(max(float(duration_ms), 0.0), 4),
                        _safe_json(inputs or {}),
                        _safe_json(outputs or {}),
                        _safe_json(metrics or {}),
                        error_type,
                    ),
                )
        except Exception:
            return span_id
        return span_id

    def event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        span_id: str | None = None,
    ) -> None:
        try:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO evaluation_trace_events (
                        event_id, trace_id, span_id, sequence, event_type,
                        occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"trace-event:{uuid4().hex}",
                        self.trace_id,
                        span_id,
                        self._next_sequence(),
                        event_type[:120],
                        _utc_now(),
                        _safe_json(payload or {}),
                    ),
                )
        except Exception:
            return

    def finish(
        self,
        status: str,
        *,
        attributes: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> None:
        ended_at = _utc_now()
        duration_ms = self.elapsed_ms(ended_at=ended_at)
        try:
            with get_connection(self.db_path) as conn:
                current = conn.execute(
                    "SELECT attributes_json FROM evaluation_traces WHERE trace_id=?",
                    (self.trace_id,),
                ).fetchone()
                current_attributes = _json_object(
                    None if current is None else current["attributes_json"]
                )
                conn.execute(
                    """
                    UPDATE evaluation_traces
                    SET status=?, ended_at=?, duration_ms=?, attributes_json=?, error_type=?
                    WHERE trace_id=?
                    """,
                    (
                        status[:32],
                        ended_at,
                        round(duration_ms, 4),
                        _safe_json({**current_attributes, **(attributes or {})}),
                        error_type,
                        self.trace_id,
                    ),
                )
        except Exception:
            return

    def elapsed_ms(self, *, ended_at: str | None = None) -> float:
        try:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat((ended_at or _utc_now()).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            return max((ended - started).total_seconds() * 1000, 0.0)
        except ValueError:
            return (perf_counter() - self._started_perf) * 1000

    def _write_trace(self, *, initial_status: str, attributes: dict[str, Any]) -> None:
        root_trace_id = self.trace_id
        if self.parent_trace_id:
            try:
                with get_connection(self.db_path) as conn:
                    parent = conn.execute(
                        "SELECT root_trace_id FROM evaluation_traces WHERE trace_id=?",
                        (self.parent_trace_id,),
                    ).fetchone()
                if parent is not None:
                    root_trace_id = str(parent["root_trace_id"] or self.parent_trace_id)
            except Exception:
                root_trace_id = self.parent_trace_id
        try:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO evaluation_traces (
                        trace_id, root_trace_id, parent_trace_id, trace_type,
                        user_id, session_id, request_id, status, started_at,
                        attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.trace_id,
                        root_trace_id,
                        self.parent_trace_id,
                        self.trace_type,
                        self.user_id,
                        self.session_id,
                        self.request_id,
                        initial_status,
                        self.started_at,
                        _safe_json(attributes),
                    ),
                )
        except Exception:
            return

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence


def hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def load_trace_tree(db_path: str, trace_id: str) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        root = conn.execute(
            "SELECT root_trace_id FROM evaluation_traces WHERE trace_id=?",
            (trace_id,),
        ).fetchone()
        if root is None:
            return {"available": False, "traceId": trace_id}
        root_trace_id = str(root["root_trace_id"])
        trace_rows = conn.execute(
            """
            SELECT * FROM evaluation_traces
            WHERE root_trace_id=? ORDER BY started_at, trace_id
            """,
            (root_trace_id,),
        ).fetchall()
        trace_ids = [str(row["trace_id"]) for row in trace_rows]
        placeholders = ",".join("?" for _ in trace_ids)
        spans = conn.execute(
            f"""
            SELECT * FROM evaluation_trace_spans
            WHERE trace_id IN ({placeholders}) ORDER BY started_at, sequence
            """,
            tuple(trace_ids),
        ).fetchall()
        events = conn.execute(
            f"""
            SELECT * FROM evaluation_trace_events
            WHERE trace_id IN ({placeholders}) ORDER BY occurred_at, sequence
            """,
            tuple(trace_ids),
        ).fetchall()
    return {
        "available": True,
        "rootTraceId": root_trace_id,
        "traces": [_trace_payload(row) for row in trace_rows],
        "spans": [_span_payload(row) for row in spans],
        "events": [_event_payload(row) for row in events],
    }


def _safe_json(value: Any) -> str:
    rendered = json.dumps(
        _sanitize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(rendered.encode("utf-8")) <= 64 * 1024:
        return rendered
    return json.dumps(
        {
            "_truncated": True,
            "_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "_preview": rendered[:16000],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if key.casefold() in _SENSITIVE_KEYS or any(
        marker in key.casefold() for marker in ("password", "secret", "token", "cookie")
    ):
        return "[REDACTED]"
    if depth >= 6:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        items = list(value.items())[:80]
        return {
            str(item_key)[:120]: _sanitize(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in items
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:80]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _trace_payload(row: Any) -> dict[str, Any]:
    return {
        "traceId": row["trace_id"],
        "rootTraceId": row["root_trace_id"],
        "parentTraceId": row["parent_trace_id"],
        "traceType": row["trace_type"],
        "userId": row["user_id"],
        "sessionId": row["session_id"],
        "requestId": row["request_id"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "durationMs": row["duration_ms"],
        "attributes": _json_object(row["attributes_json"]),
        "errorType": row["error_type"],
    }


def _span_payload(row: Any) -> dict[str, Any]:
    return {
        "spanId": row["span_id"],
        "traceId": row["trace_id"],
        "parentSpanId": row["parent_span_id"],
        "sequence": row["sequence"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "durationMs": row["duration_ms"],
        "input": _json_object(row["input_json"]),
        "output": _json_object(row["output_json"]),
        "metrics": _json_object(row["metrics_json"]),
        "errorType": row["error_type"],
    }


def _event_payload(row: Any) -> dict[str, Any]:
    return {
        "eventId": row["event_id"],
        "traceId": row["trace_id"],
        "spanId": row["span_id"],
        "sequence": row["sequence"],
        "eventType": row["event_type"],
        "occurredAt": row["occurred_at"],
        "payload": _json_object(row["payload_json"]),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
