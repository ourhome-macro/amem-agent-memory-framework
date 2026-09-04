from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from database import get_connection
from request_spec import RequestSpec


DEFAULT_SCENE_TTL_MINUTES = 120
L2_CACHE_TTL_SECONDS = 30.0
_L2_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, object]]]] = {}


class SceneMemoryService:
    """L2 scene memory: durable enough for a session, explicitly expiring before profile use."""

    def __init__(self, db_path: str, *, user_id: str, ttl_minutes: int = DEFAULT_SCENE_TTL_MINUTES) -> None:
        self.db_path = db_path
        self.user_id = user_id
        self.ttl = timedelta(minutes=max(int(ttl_minutes), 1))

    def remember_request(self, *, scene: str, request_spec: RequestSpec) -> str | None:
        if not request_spec.constrained and not request_spec.moods:
            return None
        now = _utc_now()
        memory_id = f"scene:{uuid4().hex}"
        expires_at = (datetime.now(timezone.utc) + self.ttl).isoformat()
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM music_scene_memories WHERE user_id = ? AND expires_at <= ?", (self.user_id, now))
            conn.execute(
                """
                INSERT INTO music_scene_memories (memory_id, user_id, scene, request_spec_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (memory_id, self.user_id, scene[:32], json.dumps(request_spec.to_dict(), ensure_ascii=False), now, expires_at),
            )
        _L2_CACHE.pop((self.user_id, scene[:32]), None)
        return memory_id

    def active(self, *, scene: str) -> list[dict[str, object]]:
        started = time.perf_counter()
        cache_key = (self.user_id, scene[:32])
        cached = _L2_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < L2_CACHE_TTL_SECONDS:
            return [dict(item, source="memory_cache", lookupMs=round((time.perf_counter() - started) * 1000, 3)) for item in cached[1]]
        now = _utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute("DELETE FROM music_scene_memories WHERE user_id = ? AND expires_at <= ?", (self.user_id, now))
            rows = conn.execute(
                """
                SELECT memory_id, request_spec_json, created_at, expires_at
                FROM music_scene_memories
                WHERE user_id = ? AND scene = ? AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 8
                """,
                (self.user_id, scene[:32], now),
            ).fetchall()
        result = [
            {
                "memoryId": row["memory_id"],
                "requestSpec": _json_object(row["request_spec_json"]),
                "createdAt": row["created_at"],
                "expiresAt": row["expires_at"],
                "level": "L2",
            }
            for row in rows
        ]
        _L2_CACHE[cache_key] = (time.monotonic(), result)
        return [dict(item, source="sqlite", lookupMs=round((time.perf_counter() - started) * 1000, 3)) for item in result]


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
