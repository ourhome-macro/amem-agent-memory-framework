from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection
from library_service import LibraryService
from music_keyword_pool import match_topics
from music_profile import MusicProfile


POSITIVE_EVENTS = {"played", "accepted", "completed", "liked", "collection_added"}
NEGATIVE_EVENTS = {"skipped", "dismissed", "dislike"}
DEFAULT_MIN_EVENTS = 3
DEFAULT_L3_MIN_EVENTS = 6
DEFAULT_L3_MIN_AGE_DAYS = 7


class MusicProfileUpdatePipeline:
    """Thresholded event-to-profile updater; a single behavior cannot mutate AMEM preferences."""

    def __init__(
        self,
        db_path: str,
        *,
        user_id: str,
        amem_bridge: Any,
        min_events: int = DEFAULT_MIN_EVENTS,
        l3_min_events: int | None = None,
        l3_min_age_days: int | None = None,
    ) -> None:
        self.db_path = db_path
        self.user_id = user_id
        self.amem_bridge = amem_bridge
        self.min_events = max(int(min_events), 2)
        self.l3_min_events = max(int(l3_min_events or _env_int("MUSIC_PROFILE_L3_MIN_EVENTS", DEFAULT_L3_MIN_EVENTS)), self.min_events)
        self.l3_min_age_days = max(int(l3_min_age_days if l3_min_age_days is not None else _env_int("MUSIC_PROFILE_L3_MIN_AGE_DAYS", DEFAULT_L3_MIN_AGE_DAYS)), 0)
        self.library = LibraryService(db_path, user_id=user_id)

    def process(self) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            state = conn.execute(
                "SELECT last_event_id FROM music_profile_update_state WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()
            cursor = int(state["last_event_id"] or 0) if state else 0
            rows = conn.execute(
                """
                SELECT id, track_id, event
                FROM recommendation_events
                WHERE user_id = ? AND id > ?
                  AND event IN ('played', 'accepted', 'completed', 'liked', 'collection_added', 'skipped', 'dismissed', 'dislike')
                ORDER BY id ASC
                """,
                (self.user_id, cursor),
            ).fetchall()
        positive: Counter[str] = Counter()
        negative: Counter[str] = Counter()
        last_id = cursor
        for row in rows:
            last_id = int(row["id"])
            track = self.library.get_track(str(row["track_id"]))
            if track is None:
                continue
            topics = match_topics(f"{track.title} {track.owner}")
            target = positive if str(row["event"]) in POSITIVE_EVENTS else negative
            target.update(topics)
        now = _utc_now()
        self._increment_lifecycle(positive, polarity="positive", now=now)
        self._increment_lifecycle(negative, polarity="negative", now=now)
        l1_rows = self._eligible_l1()
        l1_profile = _profile_from_rows(l1_rows, source="event_l1")
        l1_result = {"memoryIds": []}
        if l1_profile is not None:
            l1_result = self.amem_bridge.record_profile_statement(
                user_id=self.user_id,
                description="Aggregated music behavior signals passed the L1 preference threshold.",
                profile=l1_profile,
                source="event_l1",
            )
            self._mark_l1_written(l1_rows)
        l3_rows = self._eligible_l3()
        l3_profile = _profile_from_rows(l3_rows, source="event_l3")
        l3_result = {"memoryIds": []}
        if l3_profile is not None:
            l3_result = self.amem_bridge.promote_music_profile(
                user_id=self.user_id,
                profile=l3_profile,
                support_counts={row["topic"]: int(row["support_count"]) for row in l3_rows},
            )
            self._mark_l3_promoted(l3_rows, now=now)
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO music_profile_update_state (user_id, last_event_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_event_id = excluded.last_event_id, updated_at = excluded.updated_at
                """,
                (self.user_id, last_id, now),
            )
        return {
            "processed": bool(rows),
            "pendingEventCount": len(rows),
            "cursor": last_id,
            "l1MemoryIds": l1_result.get("memoryIds") or [],
            "l3MemoryIds": l3_result.get("memoryIds") or [],
        }

    def _increment_lifecycle(self, values: Counter[str], *, polarity: str, now: str) -> None:
        if not values:
            return
        with get_connection(self.db_path) as conn:
            for topic, count in values.items():
                conn.execute(
                    """
                    INSERT INTO music_preference_lifecycle (
                        user_id, polarity, topic, support_count, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, polarity, topic) DO UPDATE SET
                        support_count = music_preference_lifecycle.support_count + excluded.support_count,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (self.user_id, polarity, topic, int(count), now, now),
                )

    def _eligible_l1(self) -> list[Any]:
        with get_connection(self.db_path) as conn:
            return conn.execute(
                """
                SELECT * FROM music_preference_lifecycle
                WHERE user_id = ? AND support_count >= ?
                  AND support_count > l1_written_count
                ORDER BY last_seen_at DESC
                """,
                (self.user_id, self.min_events),
            ).fetchall()

    def _eligible_l3(self) -> list[Any]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.l3_min_age_days)).isoformat()
        with get_connection(self.db_path) as conn:
            return conn.execute(
                """
                SELECT * FROM music_preference_lifecycle
                WHERE user_id = ? AND support_count >= ?
                  AND first_seen_at <= ? AND l3_promoted_at IS NULL
                ORDER BY last_seen_at DESC
                """,
                (self.user_id, self.l3_min_events, cutoff),
            ).fetchall()

    def _mark_l1_written(self, rows: list[Any]) -> None:
        with get_connection(self.db_path) as conn:
            for row in rows:
                conn.execute(
                    """
                    UPDATE music_preference_lifecycle SET l1_written_count = support_count
                    WHERE user_id = ? AND polarity = ? AND topic = ?
                    """,
                    (self.user_id, row["polarity"], row["topic"]),
                )

    def _mark_l3_promoted(self, rows: list[Any], *, now: str) -> None:
        with get_connection(self.db_path) as conn:
            for row in rows:
                conn.execute(
                    """
                    UPDATE music_preference_lifecycle SET l3_promoted_at = ?
                    WHERE user_id = ? AND polarity = ? AND topic = ?
                    """,
                    (now, self.user_id, row["polarity"], row["topic"]),
                )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _profile_from_rows(rows: list[Any], *, source: str) -> MusicProfile | None:
    if not rows:
        return None
    positive = {row["topic"]: 0.72 for row in rows if row["polarity"] == "positive"}
    negative = {row["topic"]: 0.72 for row in rows if row["polarity"] == "negative"}
    if not positive and not negative:
        return None
    return MusicProfile(positive_topics=positive, negative_topics=negative, confidence=0.72, source=source)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
