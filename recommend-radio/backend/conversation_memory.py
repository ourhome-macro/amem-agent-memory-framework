from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection


HOT_TURN_LIMIT = 8
WARM_TOPIC_TTL_HOURS = 24
_HOT: dict[tuple[str, str], deque[dict[str, str]]] = {}


class ConversationMemoryService:
    """Hot ring buffer plus SQLite-backed warm topic summaries for one local user."""

    def __init__(self, db_path: str, *, user_id: str) -> None:
        self.db_path = db_path
        self.user_id = user_id

    def append(self, *, session_id: str, role: str, content: str) -> None:
        key = (self.user_id, session_id)
        buffer = _HOT.setdefault(key, deque(maxlen=HOT_TURN_LIMIT))
        buffer.append({"role": role, "content": content[:500]})

    def hot(self, *, session_id: str) -> list[dict[str, str]]:
        key = (self.user_id, session_id)
        buffer = _HOT.get(key)
        if buffer is None:
            buffer = deque(maxlen=HOT_TURN_LIMIT)
            with get_connection(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT role, content FROM agent_dialogue_turns WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, HOT_TURN_LIMIT),
                ).fetchall()
            for row in reversed(rows):
                buffer.append({"role": str(row["role"]), "content": str(row["content"])[:500]})
            _HOT[key] = buffer
        return list(buffer)

    def refresh_warm(self, *, session_id: str, topic: str) -> None:
        normalized_topic = _topic_key(topic)
        if not normalized_topic:
            return
        hot = self.hot(session_id=session_id)
        user_lines = [item["content"] for item in hot if item["role"] == "user"][-4:]
        if not user_lines:
            return
        summary = "；".join(user_lines)[-900:]
        keywords = _keywords(" ".join(user_lines))
        now = _utc_now()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=WARM_TOPIC_TTL_HOURS)).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE conversation_warm_topics SET status = 'inactive' WHERE session_id = ? AND topic_key <> ? AND status = 'active'",
                (session_id, normalized_topic),
            )
            conn.execute(
                """
                INSERT INTO conversation_warm_topics (
                    session_id, user_id, topic_key, summary, keywords_json, status, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(session_id, topic_key) DO UPDATE SET
                    summary = excluded.summary, keywords_json = excluded.keywords_json,
                    status = 'active', updated_at = excluded.updated_at, expires_at = excluded.expires_at
                """,
                (session_id, self.user_id, normalized_topic, summary, _json(keywords), now, expires_at),
            )

    def warm(self, *, session_id: str) -> dict[str, Any] | None:
        now = _utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute("UPDATE conversation_warm_topics SET status = 'expired' WHERE session_id = ? AND expires_at <= ?", (session_id, now))
            row = conn.execute(
                "SELECT topic_key, summary, keywords_json, updated_at, expires_at FROM conversation_warm_topics WHERE session_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"topic": row["topic_key"], "summary": row["summary"], "keywords": _json_load(row["keywords_json"]), "updatedAt": row["updated_at"], "expiresAt": row["expires_at"]}


def _topic_key(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return text[:80]


def _keywords(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,4}", value.casefold())
    return list(dict.fromkeys(token for token in tokens if len(token) >= 2))[:12]


def _json(value: list[str]) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _json_load(value: str) -> list[str]:
    import json
    try: data = json.loads(value)
    except (TypeError, ValueError): return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
