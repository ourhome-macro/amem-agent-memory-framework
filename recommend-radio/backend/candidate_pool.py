from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from database import get_connection
from library_service import LibraryService
from models import Track
from music_keyword_pool import has_gossip_exclusion, has_music_relevance_signal, has_non_music_context
from request_spec import RequestSpec


READY_STATUS = "ready"


@dataclass(frozen=True)
class PoolCandidate:
    track: Track
    source: str
    facets: dict[str, list[str]]
    evidence: list[str]
    scope_kind: str = "default"


class CandidatePool:
    """Durable, user-scoped inventory of candidates eligible for recommendation serving."""

    def __init__(self, db_path: str, *, user_id: str) -> None:
        self.db_path = db_path
        self.user_id = user_id
        self.library = LibraryService(db_path, user_id=user_id)

    def admit(
        self,
        tracks: Iterable[Track],
        *,
        source: str,
        request_spec: RequestSpec,
        query: str,
    ) -> dict[str, int]:
        values = [track for track in tracks if track.track_id]
        scope_kind = "request" if request_spec.constrained else "default"
        scope_key = _scope_key(request_spec) if scope_kind == "request" else ""
        if values:
            self.library.upsert_tracks(values)
        enqueued = 0
        admitted = 0
        with get_connection(self.db_path) as conn:
            for track in values:
                facets, evidence = infer_facets(track, query=query)
                status = READY_STATUS if is_admissible(track, facets) else "rejected"
                now = _utc_now()
                conn.execute(
                    """
                    INSERT INTO discovery_candidates (
                        user_id, track_id, source, query_text, request_spec_json,
                        facets_json, evidence_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, track_id, query_text) DO UPDATE SET
                        source = excluded.source,
                        request_spec_json = excluded.request_spec_json,
                        facets_json = excluded.facets_json,
                        evidence_json = excluded.evidence_json,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.user_id,
                        track.track_id,
                        source,
                        query[:240],
                        json.dumps(request_spec.to_dict(), ensure_ascii=False),
                        json.dumps(facets, ensure_ascii=False),
                        json.dumps(evidence, ensure_ascii=False),
                        status,
                        now,
                        now,
                    ),
                )
                enqueued += 1
                if status != READY_STATUS:
                    continue
                conn.execute(
                    """
                    INSERT INTO content_cache (
                        user_id, track_id, source, facets_json, evidence_json, status, scope_kind, scope_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, track_id) DO UPDATE SET
                        source = excluded.source,
                        facets_json = excluded.facets_json,
                        evidence_json = excluded.evidence_json,
                        status = excluded.status,
                        scope_kind = excluded.scope_kind,
                        scope_key = excluded.scope_key,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.user_id,
                        track.track_id,
                        source,
                        json.dumps(facets, ensure_ascii=False),
                        json.dumps(evidence, ensure_ascii=False),
                        READY_STATUS,
                        scope_kind,
                        scope_key,
                        now,
                        now,
                    ),
                )
                admitted += 1
        return {"enqueued": enqueued, "admitted": admitted}

    def record_negative_samples(self, tracks: Iterable[Track], *, query: str) -> int:
        values = [track for track in tracks if track.track_id]
        if values:
            self.library.upsert_tracks(values)
        now = _utc_now()
        with get_connection(self.db_path) as conn:
            for track in values:
                facets, evidence = infer_facets(track, query=query)
                conn.execute(
                    """
                    INSERT INTO discovery_candidates (
                        user_id, track_id, source, query_text, request_spec_json,
                        facets_json, evidence_json, status, created_at, updated_at
                    ) VALUES (?, ?, 'negative_probe', ?, '{}', ?, ?, 'negative_sample', ?, ?)
                    ON CONFLICT(user_id, track_id, query_text) DO UPDATE SET
                        source='negative_probe', facets_json=excluded.facets_json,
                        evidence_json=excluded.evidence_json, status='negative_sample', updated_at=excluded.updated_at
                    """,
                    (self.user_id, track.track_id, query[:240], json.dumps(facets, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False), now, now),
                )
        return len(values)

    def list_ready(
        self,
        request_spec: RequestSpec,
        *,
        context_specs: list[RequestSpec] | None = None,
    ) -> list[PoolCandidate]:
        scoped = request_spec.constrained
        scope_key = _scope_key(request_spec) if scoped else ""
        context_keys = [_scope_key(spec) for spec in (context_specs or []) if spec.constrained]
        context_sql = ",".join("?" for _ in context_keys) or "''"
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, c.source AS cache_source, c.facets_json, c.evidence_json
                       , c.scope_kind
                FROM content_cache c
                JOIN tracks t ON t.track_id = c.track_id
                WHERE c.user_id = ? AND c.status = ?
                  AND (
                    c.scope_kind = 'default'
                    OR (? = 1 AND c.scope_kind = 'request' AND c.scope_key = ?)
                    OR (? = 1 AND c.scope_kind = 'request' AND c.scope_key IN ({context_sql}))
                  )
                ORDER BY c.updated_at DESC
                LIMIT 300
                """,
                (self.user_id, READY_STATUS, int(scoped), scope_key, int(bool(context_keys)), *context_keys),
            ).fetchall()
        result = []
        for row in rows:
            facets = _json_object(row["facets_json"])
            if not request_spec.matches_facets(facets):
                continue
            result.append(
                PoolCandidate(
                    track=self.library._track_from_row(row),
                    source=str(row["cache_source"] or "candidate_pool"),
                    facets={key: [str(item) for item in value] for key, value in facets.items() if isinstance(value, list)},
                    evidence=_json_strings(row["evidence_json"]),
                    scope_kind=str(row["scope_kind"] or "default"),
                )
            )
        return result

    def availability(self, request_spec: RequestSpec) -> int:
        return len(self.list_ready(request_spec))

    def list_negative_sample_texts(self, *, limit: int = 12) -> list[str]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.title, t.owner, d.query_text
                FROM discovery_candidates d
                JOIN tracks t ON t.track_id=d.track_id
                WHERE d.user_id=? AND d.status='negative_sample'
                ORDER BY d.updated_at DESC
                LIMIT ?
                """,
                (self.user_id, max(1, min(int(limit), 40))),
            ).fetchall()
        return [
            " ".join(
                part for part in (str(row["title"] or ""), str(row["owner"] or ""), str(row["query_text"] or "")) if part
            )[:320]
            for row in rows
        ]


def infer_facets(track: Track, *, query: str) -> tuple[dict[str, list[str]], list[str]]:
    text = f"{track.title} {track.page_title or ''} {track.owner}".casefold()
    query_text = query.casefold()
    facets: dict[str, list[str]] = {"regions": [], "languages": [], "vocals": [], "topics": [], "genres": []}
    evidence: list[str] = []
    western_query = _matches(query_text, ("欧美", "英文", "english", "western"))
    if _matches(text, ("欧美", "英文", "english", "western", "taylor swift", "adele", "billie eilish", "lady gaga", "bruno mars", "ed sheeran", "the weeknd", "rihanna", "beyoncé", "beyonce", "ariana grande", "maroon 5", "coldplay", "linkin park", "imagine dragons", "lana del rey", "dua lipa")) or (western_query and _looks_english(track.title)):
        facets["regions"].append("western")
        facets["languages"].append("english")
        evidence.append("facet:western_or_english:title_or_artist")
    if _matches(text, ("华语", "中文", "国语", "周杰伦", "林俊杰", "孙燕姿", "陶喆", "陈奕迅")):
        facets["regions"].append("chinese")
        facets["languages"].append("chinese")
        evidence.append("facet:chinese:title_or_artist")
    if _matches(text, ("日语", "日文", "j-pop", "jpop", "米津玄师", "宇多田光")):
        facets["regions"].append("japanese")
        facets["languages"].append("japanese")
        evidence.append("facet:japanese:title_or_artist")
    if _matches(text, ("韩语", "韩文", "k-pop", "kpop", "bts", "blackpink", "newjeans")):
        facets["regions"].append("korean")
        facets["languages"].append("korean")
        evidence.append("facet:korean:title_or_artist")
    if _matches(text, ("女声", "女歌手", "female vocal", "女版")):
        facets["vocals"].append("female")
        evidence.append("facet:female_vocal:title")
    for topic, terms in {
        "pop": ("流行", "pop"),
        "rap": ("rap", "说唱", "hiphop", "hip-hop"),
        "rock": ("摇滚", "rock"),
        "reggae": ("雷鬼", "reggae"),
        "rnb": ("rnb", "r&b", "节奏布鲁斯"),
        "electronic": ("电音", "edm", "electronic"),
    }.items():
        if _matches(text, terms) or _matches(query_text, terms):
            facets["topics"].append(topic)
            facets["genres"].append(topic)
            evidence.append(
                f"facet:topic:{topic}:title" if _matches(text, terms) else f"facet:topic:{topic}:query"
            )
    if query:
        evidence.append(f"discovery_query:{query[:120]}")
    return facets, evidence


def is_admissible(track: Track, facets: dict[str, list[str]]) -> bool:
    text = f"{track.title} {track.page_title or ''} {track.owner}"
    if has_gossip_exclusion(text):
        return False
    if has_non_music_context(text) and not has_music_relevance_signal(text):
        return False
    return bool(track.title.strip())


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _looks_english(value: str) -> bool:
    letters = len(re.findall(r"[A-Za-z]", value or ""))
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value or ""))
    return letters >= 6 and chinese <= 4


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_strings(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _scope_key(spec: RequestSpec) -> str:
    payload = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
