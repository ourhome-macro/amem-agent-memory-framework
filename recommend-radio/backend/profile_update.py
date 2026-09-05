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
        l3_demotions = self._apply_conflict_and_decay(now=now)
        l3_demote_result = self._flush_l3_demotions(now=now)
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
            "l3DemotedMemoryIds": l3_demote_result.get("memoryIds") or [],
            "l3Demotions": l3_demotions,
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

    def _apply_conflict_and_decay(self, *, now: str) -> list[dict[str, str]]:
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        decay_cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        demotions: dict[tuple[str, str], str] = {}
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE music_preference_lifecycle
                SET support_count = MAX(support_count - 1, 0), last_decay_at = ?
                WHERE user_id = ? AND last_seen_at <= ?
                  AND (last_decay_at IS NULL OR last_decay_at <= ?)
                """,
                (now, self.user_id, stale_cutoff, decay_cutoff),
            )
            stale_promotions = conn.execute(
                """
                SELECT topic, polarity
                FROM music_preference_lifecycle
                WHERE user_id=? AND l3_promoted_at IS NOT NULL AND support_count<?
                """,
                (self.user_id, self.l3_min_events),
            ).fetchall()
            for row in stale_promotions:
                demotions[(row["polarity"], row["topic"])] = "l3_time_decay_below_threshold"
            conflicts = conn.execute(
                """
                SELECT a.topic, a.polarity, b.support_count AS opposing_support
                FROM music_preference_lifecycle a
                JOIN music_preference_lifecycle b
                  ON a.user_id=b.user_id AND a.topic=b.topic AND a.polarity<>b.polarity
                WHERE a.user_id=? AND a.l3_promoted_at IS NOT NULL AND b.support_count>=?
                """,
                (self.user_id, self.min_events),
            ).fetchall()
            for row in conflicts:
                demotions[(row["polarity"], row["topic"])] = "l3_reverse_evidence_threshold_reached"
            for (polarity, topic), _reason in demotions.items():
                conn.execute(
                    """
                    UPDATE music_preference_lifecycle
                    SET support_count=0, l1_written_count=0, l3_promoted_at=NULL, last_decay_at=?
                    WHERE user_id=? AND topic=? AND polarity=?
                    """,
                    (now, self.user_id, topic, polarity),
                )
                conn.execute(
                    """
                    INSERT INTO music_l3_demotion_outbox (
                        user_id, polarity, topic, reason, status, attempt_count,
                        last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', 0, '', ?, ?)
                    ON CONFLICT(user_id, polarity, topic) DO UPDATE SET
                        reason=excluded.reason, status='pending', attempt_count=0,
                        last_error='', updated_at=excluded.updated_at
                    """,
                    (self.user_id, polarity, topic, _reason, now, now),
                )
        return [
            {"polarity": polarity, "topic": topic, "reason": reason}
            for (polarity, topic), reason in demotions.items()
        ]

    def _flush_l3_demotions(self, *, now: str) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT polarity, topic, reason
                FROM music_l3_demotion_outbox
                WHERE user_id=? AND status='pending'
                ORDER BY updated_at
                """,
                (self.user_id,),
            ).fetchall()
        values = [dict(row) for row in rows]
        profile = _profile_from_demotions(values)
        demote = getattr(self.amem_bridge, "demote_music_profile", None)
        if profile is None or not callable(demote):
            return {"memoryIds": [], "pending": len(values)}
        try:
            result = demote(
                user_id=self.user_id,
                profile=profile,
                reasons={f"{item['polarity']}:{item['topic']}": item["reason"] for item in values},
            )
            if not result.get("enabled", True):
                return {"memoryIds": [], "pending": len(values)}
        except Exception as exc:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE music_l3_demotion_outbox
                    SET attempt_count=attempt_count+1, last_error=?, updated_at=?
                    WHERE user_id=? AND status='pending'
                    """,
                    (str(exc)[:300], now, self.user_id),
                )
            return {"memoryIds": [], "pending": len(values), "error": str(exc)[:300]}
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE music_l3_demotion_outbox
                SET status='completed', last_error='', updated_at=?
                WHERE user_id=? AND status='pending'
                """,
                (now, self.user_id),
            )
        return {**result, "pending": 0}

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
    evidence = [
        f"{row['polarity']} topic {row['topic']} supported by {int(row['support_count'])} behavior events"
        for row in rows[:12]
    ]
    return MusicProfile(
        positive_topics=positive,
        negative_topics=negative,
        persona_evidence=evidence,
        confidence=0.72,
        persona_confidence=0.55,
        source=source,
    )


def _profile_from_demotions(rows: list[dict[str, str]]) -> MusicProfile | None:
    if not rows:
        return None
    return MusicProfile(
        positive_topics={row["topic"]: 0.72 for row in rows if row["polarity"] == "positive"},
        negative_topics={row["topic"]: 0.72 for row in rows if row["polarity"] == "negative"},
        confidence=0.8,
        source="event_l3_demote",
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
