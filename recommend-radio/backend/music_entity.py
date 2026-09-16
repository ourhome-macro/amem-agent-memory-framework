from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from models import Track

RESOLVER_VERSION = "music-entity-v1"
_BRACKETED_NOISE = re.compile(
    r"[\[【（(](?:official|mv|pv|完整版|高清|无损|字幕|动态歌词|音乐|歌曲|合集|单曲)[^\]】）)]*[\]】）)]",
    re.IGNORECASE,
)
_WORK_TITLE = re.compile(r"《([^》]{1,120})》")
_SEPARATOR = re.compile(r"\s+(?:-|–|—|｜|\|)\s+")
_VERSION_ANNOTATION = re.compile(
    r"[\[【（(](?:live|concert|cover|翻唱|现场|演唱会|remix|mix|混音|伴奏|instrumental|acoustic|不插电)[^\]】）)]*[\]】）)]",
    re.IGNORECASE,
)
_VERSION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("live", re.compile(r"\b(?:live|concert)\b|现场|演唱会", re.IGNORECASE)),
    ("cover", re.compile(r"\bcover\b|翻唱|试唱", re.IGNORECASE)),
    ("remix", re.compile(r"\b(?:remix|mix)\b|混音", re.IGNORECASE)),
    ("instrumental", re.compile(r"\b(?:instrumental|伴奏|纯音乐)\b", re.IGNORECASE)),
    ("acoustic", re.compile(r"\bacoustic\b|不插电", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ResolvedMusicEntity:
    work_id: str
    recording_id: str
    canonical_title: str
    canonical_artist: str
    performer: str
    version_type: str
    duration_bucket: int
    confidence: float
    evidence: dict[str, Any]


def resolve_track_entity(track: Track) -> ResolvedMusicEntity:
    source_title = track.page_title or track.title
    version_type = _version_type(f"{track.title} {track.page_title or ''}")
    title, artist, extraction = _extract_title_artist(source_title)
    normalized_title = normalize_entity_text(title)
    normalized_artist = normalize_entity_text(artist)
    duration_bucket = max(int(track.duration or 0) // 10 * 10, 0)

    if normalized_title and normalized_artist:
        work_key = f"{normalized_title}|{normalized_artist}"
        confidence = 0.82 if extraction != "separator" else 0.7
    else:
        # Do not merge same-title videos when the performer is unknown. A false
        # merge is more damaging than a duplicate because dislikes would leak
        # across covers, live recordings and unrelated works.
        normalized_title = normalized_title or normalize_entity_text(track.title)
        work_key = f"unresolved|{normalized_title}|{track.track_id}"
        confidence = 0.38

    work_id = _stable_id("work", work_key)
    performer = artist.strip()
    recording_key = "|".join(
        (
            work_id,
            normalize_entity_text(performer),
            version_type,
            str(duration_bucket),
        )
    )
    recording_id = _stable_id("recording", recording_key)
    return ResolvedMusicEntity(
        work_id=work_id,
        recording_id=recording_id,
        canonical_title=title.strip() or track.title.strip(),
        canonical_artist=artist.strip(),
        performer=performer,
        version_type=version_type,
        duration_bucket=duration_bucket,
        confidence=confidence,
        evidence={
            "resolverVersion": RESOLVER_VERSION,
            "sourceTitle": source_title,
            "extraction": extraction,
            "assetId": track.track_id,
        },
    )


def persist_track_entity(conn: Any, track: Track, *, updated_at: str) -> ResolvedMusicEntity:
    resolved = resolve_track_entity(track)
    conn.execute(
        """
        INSERT INTO song_works (
            work_id, canonical_title, canonical_artist, normalized_title,
            normalized_artist, resolver_version, confidence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(work_id) DO UPDATE SET
            canonical_title=excluded.canonical_title,
            canonical_artist=excluded.canonical_artist,
            resolver_version=excluded.resolver_version,
            confidence=MAX(song_works.confidence, excluded.confidence),
            updated_at=excluded.updated_at
        """,
        (
            resolved.work_id,
            resolved.canonical_title,
            resolved.canonical_artist,
            normalize_entity_text(resolved.canonical_title),
            normalize_entity_text(resolved.canonical_artist),
            RESOLVER_VERSION,
            resolved.confidence,
            updated_at,
            updated_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO song_recordings (
            recording_id, work_id, performer, version_type, duration_bucket,
            resolver_version, confidence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(recording_id) DO UPDATE SET
            performer=excluded.performer,
            version_type=excluded.version_type,
            duration_bucket=excluded.duration_bucket,
            resolver_version=excluded.resolver_version,
            confidence=MAX(song_recordings.confidence, excluded.confidence),
            updated_at=excluded.updated_at
        """,
        (
            resolved.recording_id,
            resolved.work_id,
            resolved.performer,
            resolved.version_type,
            resolved.duration_bucket,
            RESOLVER_VERSION,
            resolved.confidence,
            updated_at,
            updated_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO track_entity_links (
            track_id, recording_id, resolver_version, confidence,
            evidence_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            recording_id=excluded.recording_id,
            resolver_version=excluded.resolver_version,
            confidence=excluded.confidence,
            evidence_json=excluded.evidence_json,
            updated_at=excluded.updated_at
        """,
        (
            track.track_id,
            resolved.recording_id,
            RESOLVER_VERSION,
            resolved.confidence,
            json.dumps(resolved.evidence, ensure_ascii=False, sort_keys=True),
            updated_at,
        ),
    )
    conn.execute(
        """
        UPDATE tracks
        SET work_id=?, recording_id=?, canonical_title=?, canonical_artist=?,
            version_type=?, entity_confidence=?
        WHERE track_id=?
        """,
        (
            resolved.work_id,
            resolved.recording_id,
            resolved.canonical_title,
            resolved.canonical_artist,
            resolved.version_type,
            resolved.confidence,
            track.track_id,
        ),
    )
    track.work_id = resolved.work_id
    track.recording_id = resolved.recording_id
    track.canonical_title = resolved.canonical_title
    track.canonical_artist = resolved.canonical_artist
    track.version_type = resolved.version_type
    track.entity_confidence = resolved.confidence
    return resolved


def backfill_track_entities(conn: Any, *, limit: int | None = None) -> int:
    sql = """
        SELECT * FROM tracks
        WHERE recording_id IS NULL OR recording_id = ''
        ORDER BY updated_at DESC
    """
    parameters: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        parameters = (max(int(limit), 1),)
    rows = conn.execute(sql, parameters).fetchall()
    for row in rows:
        raw = _json_object(row["raw_json"] if "raw_json" in row.keys() else "")
        track = Track.from_dict(
            {
                **raw,
                "trackId": row["track_id"],
                "bvid": row["bvid"],
                "cid": row["cid"],
                "title": row["title"],
                "owner": row["owner"],
                "ownerMid": row["owner_mid"],
                "duration": row["duration"],
                "pageTitle": row["page_title"],
                "description": row["description"],
                "tags": _json_list(row["tags_json"]),
                "typeName": row["type_name"],
                "hitColumns": _json_list(row["hit_columns_json"]),
            }
        )
        persist_track_entity(conn, track, updated_at=str(row["updated_at"]))
    return len(rows)


def normalize_entity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = _BRACKETED_NOISE.sub(" ", normalized)
    normalized = _VERSION_ANNOTATION.sub(" ", normalized)
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", " ", normalized)
    return " ".join(normalized.split())[:160]


def _extract_title_artist(value: str) -> tuple[str, str, str]:
    cleaned = _VERSION_ANNOTATION.sub(" ", _BRACKETED_NOISE.sub(" ", str(value or ""))).strip()
    quoted = _WORK_TITLE.search(cleaned)
    if quoted:
        title = quoted.group(1).strip()
        outside = f"{cleaned[: quoted.start()]} {cleaned[quoted.end() :]}".strip(" -–—|｜")
        artist = outside if outside and len(outside) <= 80 else ""
        return title, artist, "book-title"
    parts = _SEPARATOR.split(cleaned, maxsplit=1)
    if len(parts) == 2 and all(parts):
        left, right = (part.strip() for part in parts)
        # Bilibili music uploads most often use "artist - title". Keep the
        # confidence below explicit 《title》 extraction because this is not a
        # universally reliable convention.
        return right, left, "separator"
    return cleaned, "", "title-only"


def _version_type(value: str) -> str:
    for name, pattern in _VERSION_PATTERNS:
        if pattern.search(value):
            return name
    return "studio_or_unknown"


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
