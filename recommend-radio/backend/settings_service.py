from __future__ import annotations

from pathlib import Path
from typing import Optional

from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from error_code import APIError
from library_service import utc_now


AUDIO_QUALITY_VALUES = {"auto", "64k", "132k", "192k", "dolby", "hires"}
AUDIO_QUALITY_ALIASES = {"standard": "132k", "high": "192k"}
AUDIO_QUALITY_KEY = "audio_quality_preference"
PLAYBACK_SPEED_KEY = "playback_speed"


class SettingsService:
    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        user_id: str = LEGACY_OWNER_USER_ID,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)

    def get_audio_quality_preference(self) -> str:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE user_id = ? AND key = ?",
                (self.user_id, AUDIO_QUALITY_KEY),
            ).fetchone()
        if not row:
            return "auto"
        value = AUDIO_QUALITY_ALIASES.get(row["value"], row["value"])
        return value if value in AUDIO_QUALITY_VALUES else "auto"

    def set_audio_quality_preference(self, value: str) -> str:
        normalized = (value or "").strip().lower()
        normalized = AUDIO_QUALITY_ALIASES.get(normalized, normalized)
        if normalized not in AUDIO_QUALITY_VALUES:
            raise APIError.validation_error(
                "audioQualityPreference must be one of: auto, 64k, 132k, 192k, dolby, hires"
            )

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO settings (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (self.user_id, AUDIO_QUALITY_KEY, normalized, utc_now()),
            )
        return normalized

    def get_playback_speed(self) -> float:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE user_id = ? AND key = ?",
                (self.user_id, PLAYBACK_SPEED_KEY),
            ).fetchone()
        if not row:
            return 1.0
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            return 1.0
        return value if value in PLAYBACK_SPEED_VALUES else 1.0

    def set_playback_speed(self, value: float) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            raise APIError.validation_error("playbackSpeed is invalid")
        if normalized not in PLAYBACK_SPEED_VALUES:
            raise APIError.validation_error("playbackSpeed is unsupported")

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO settings (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (self.user_id, PLAYBACK_SPEED_KEY, str(normalized), utc_now()),
            )
        return normalized

    def to_dict(self) -> dict[str, str | float]:
        return {
            "audioQualityPreference": self.get_audio_quality_preference(),
            "playbackSpeed": self.get_playback_speed(),
        }


PLAYBACK_SPEED_VALUES = {0.5, 0.75, 1.0, 1.25, 1.5, 2.0}
