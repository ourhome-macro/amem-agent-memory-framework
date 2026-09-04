from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from error_code import APIError
from library_service import utc_now


class AnalysisService:
    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        user_id: str = LEGACY_OWNER_USER_ID,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)

    def record_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = str(payload.get("event") or "").strip()
        if not event:
            raise APIError.validation_error("event is required")

        track_id = payload.get("trackId") or payload.get("track_id")
        session_id = payload.get("sessionId") or payload.get("session_id")
        event_payload = payload.get("payload")
        if event_payload is None:
            event_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"event", "trackId", "track_id", "sessionId", "session_id"}
            }

        now = utc_now()
        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO analysis_events (
                    user_id, event, track_id, session_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.user_id,
                    event,
                    str(track_id) if track_id else None,
                    str(session_id) if session_id else None,
                    json.dumps(event_payload, ensure_ascii=False),
                    now,
                ),
            )
            event_id = cursor.lastrowid

        return {
            "id": event_id,
            "event": event,
            "trackId": str(track_id) if track_id else None,
            "sessionId": str(session_id) if session_id else None,
            "createdAt": now,
        }
