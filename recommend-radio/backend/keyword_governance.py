from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from database import get_connection
from models import Track


class KeywordGovernance:
    """Turns search phrases into user-scoped assets with measurable supply and feedback quality."""

    def __init__(self, db_path: str, *, user_id: str) -> None:
        self.db_path = db_path
        self.user_id = user_id

    def prepare(self, queries: list[str], *, source: str, preserve_order: bool) -> list[dict[str, Any]]:
        now = _utc_now()
        prepared = []
        with get_connection(self.db_path) as conn:
            for query in dict.fromkeys(item.strip() for item in queries if item.strip()):
                keyword_id = _keyword_id(self.user_id, query)
                conn.execute(
                    """
                    INSERT INTO discovery_keywords (keyword_id, user_id, keyword, source, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, platform, keyword) DO UPDATE SET source = excluded.source
                    """,
                    (keyword_id, self.user_id, query, source, now),
                )
                row = conn.execute(
                    "SELECT keyword_id, keyword, quality_score, status, search_count FROM discovery_keywords WHERE keyword_id = ?",
                    (keyword_id,),
                ).fetchone()
                if row and row["status"] in {"active", "cooldown"}:
                    prepared.append(
                        {
                            "keywordId": row["keyword_id"],
                            "query": row["keyword"],
                            "quality": str(row["quality_score"]),
                            "searchCount": int(row["search_count"] or 0),
                        }
                    )
        if not preserve_order:
            prepared.sort(key=lambda item: float(item["quality"]), reverse=True)
        return prepared

    def record_discovery(self, keyword_id: str, *, tracks: list[Track], admitted_count: int) -> None:
        now = _utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE discovery_keywords
                SET search_count = search_count + 1,
                    candidate_count = candidate_count + ?,
                    admitted_count = admitted_count + ?,
                    last_used_at = ?, last_evaluated_at = ?
                WHERE keyword_id = ? AND user_id = ?
                """,
                (len(tracks), admitted_count, now, now, keyword_id, self.user_id),
            )
            for track in tracks:
                if track.track_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO discovery_keyword_candidates (keyword_id, user_id, track_id, discovered_at) VALUES (?, ?, ?, ?)",
                        (keyword_id, self.user_id, track.track_id, now),
                    )
            self._recalculate(conn, keyword_id)

    def record_feedback(self, track_id: str, event: str) -> None:
        if event not in {"shown", "played", "accepted", "completed", "liked", "skipped", "dismissed", "dislike"}:
            return
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT keyword_id FROM discovery_keyword_candidates
                WHERE user_id = ? AND track_id = ?
                ORDER BY discovered_at DESC LIMIT 3
                """,
                (self.user_id, track_id),
            ).fetchall()
            for row in rows:
                increments = {
                    "shown": {"shown_count": 1},
                    "played": {"clicked_count": 1},
                    "accepted": {"clicked_count": 1},
                    "completed": {"clicked_count": 1, "completed_count": 1},
                    "liked": {"liked_count": 1},
                    "skipped": {"dismissed_count": 1},
                    "dismissed": {"dismissed_count": 1},
                    "dislike": {"dismissed_count": 1},
                }[event]
                assignments = ", ".join(f"{field} = {field} + {amount}" for field, amount in increments.items())
                conn.execute(f"UPDATE discovery_keywords SET {assignments}, last_evaluated_at = ? WHERE keyword_id = ?", (_utc_now(), row["keyword_id"]))
                self._recalculate(conn, row["keyword_id"])

    @staticmethod
    def _recalculate(conn: Any, keyword_id: str) -> None:
        row = conn.execute(
            "SELECT search_count, candidate_count, admitted_count, shown_count, dismissed_count, clicked_count, completed_count, liked_count FROM discovery_keywords WHERE keyword_id = ?",
            (keyword_id,),
        ).fetchone()
        if row is None:
            return
        candidates = max(int(row["candidate_count"]), 1)
        admitted = int(row["admitted_count"])
        clicks = int(row["clicked_count"])
        likes = int(row["liked_count"])
        shown = int(row["shown_count"])
        dismissed = int(row["dismissed_count"])
        completed = int(row["completed_count"])
        searches = int(row["search_count"])
        acceptance = min((0.25 * clicks + completed + 1.2 * likes) / max(shown, 1), 1.0)
        dismiss_rate = dismissed / max(shown, 1)
        quality = 0.30 * (admitted / candidates) + 0.50 * acceptance + 0.20 * (1.0 - dismiss_rate)
        status = "retired" if searches >= 5 and admitted == 0 else "cooldown" if shown >= 8 and dismiss_rate >= 0.7 and completed == 0 and likes == 0 else "active"
        conn.execute("UPDATE discovery_keywords SET quality_score = ?, status = ? WHERE keyword_id = ?", (round(quality, 4), status, keyword_id))


def _keyword_id(user_id: str, query: str) -> str:
    digest = hashlib.sha1(f"{user_id}:bilibili:{query.casefold()}".encode("utf-8")).hexdigest()[:20]
    return f"keyword:{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
