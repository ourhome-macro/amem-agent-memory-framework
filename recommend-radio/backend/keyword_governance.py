from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from database import get_connection
from models import Track


DEFAULT_KEYWORD_LIMIT = 3
DEFAULT_VARIANT_COOLDOWN_HOURS = 6
LOW_NOVELTY_COOLDOWN_HOURS = 24
LOW_NOVELTY_THRESHOLD = 0.20


class KeywordGovernance:
    """Govern semantic keyword families, concrete variants and their marginal yield."""

    def __init__(self, db_path: str, *, user_id: str) -> None:
        self.db_path = db_path
        self.user_id = user_id

    def prepare(
        self,
        queries: list[str],
        *,
        source: str,
        preserve_order: bool,
        family_specs: dict[str, dict[str, object]] | None = None,
        limit: int = DEFAULT_KEYWORD_LIMIT,
    ) -> list[dict[str, Any]]:
        now = _utc_now()
        specs = family_specs or {}
        prepared: list[dict[str, Any]] = []
        with get_connection(self.db_path) as conn:
            for order, query in enumerate(dict.fromkeys(item.strip() for item in queries if item.strip())):
                canonical_spec, exploration_axis = _canonical_spec(specs.get(query), query=query, source=source)
                canonical_json = json.dumps(canonical_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                family_id = _family_id(self.user_id, canonical_json)
                keyword_id = _keyword_id(self.user_id, query)
                conn.execute(
                    """
                    INSERT INTO discovery_keyword_families (
                        family_id, user_id, canonical_spec_json, exploration_axis,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, platform, canonical_spec_json) DO UPDATE SET
                        exploration_axis=excluded.exploration_axis,
                        updated_at=excluded.updated_at
                    """,
                    (family_id, self.user_id, canonical_json, exploration_axis, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO discovery_keywords (
                        keyword_id, user_id, keyword, source, family_id,
                        canonical_spec_json, exploration_axis, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, platform, keyword) DO UPDATE SET
                        source=excluded.source,
                        family_id=excluded.family_id,
                        canonical_spec_json=excluded.canonical_spec_json,
                        exploration_axis=excluded.exploration_axis
                    """,
                    (
                        keyword_id,
                        self.user_id,
                        query,
                        source,
                        family_id,
                        canonical_json,
                        exploration_axis,
                        now,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT k.*, f.quality_score AS family_quality,
                           f.status AS family_status, f.cooldown_until AS family_cooldown_until
                    FROM discovery_keywords k
                    JOIN discovery_keyword_families f ON f.family_id=k.family_id
                    WHERE k.keyword_id=?
                    """,
                    (keyword_id,),
                ).fetchone()
                if row is None or row["status"] == "retired" or row["family_status"] == "retired":
                    continue
                cooldown_active = _is_future(row["cooldown_until"], now) or _is_future(
                    row["family_cooldown_until"], now
                )
                prepared.append(
                    {
                        "keywordId": row["keyword_id"],
                        "familyId": row["family_id"],
                        "query": row["keyword"],
                        "quality": float(row["quality_score"] or 0.0),
                        "familyQuality": float(row["family_quality"] or 0.0),
                        "searchCount": int(row["search_count"] or 0),
                        "marginalYield": float(row["last_marginal_yield"] or 0.0),
                        "resultOverlap": float(row["last_result_overlap"] or 0.0),
                        "cooldownActive": cooldown_active,
                        "status": row["status"],
                        "canonicalSpec": canonical_spec,
                        "explorationAxis": exploration_axis,
                        "order": order,
                    }
                )
        return self._select(prepared, limit=max(int(limit), 0), preserve_order=preserve_order)

    def record_discovery(self, keyword_id: str, *, tracks: list[Track], admitted_count: int) -> None:
        now = _utc_now()
        current_ids = list(dict.fromkeys(track.track_id for track in tracks if track.track_id))
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM discovery_keywords WHERE keyword_id=? AND user_id=?",
                (keyword_id, self.user_id),
            ).fetchone()
            if row is None:
                return
            family_id = str(row["family_id"] or "")
            previous_family_ids = {
                str(item["track_id"])
                for item in conn.execute(
                    """
                    SELECT DISTINCT c.track_id
                    FROM discovery_keyword_candidates c
                    JOIN discovery_keywords k ON k.keyword_id=c.keyword_id
                    WHERE c.user_id=? AND k.family_id=?
                    """,
                    (self.user_id, family_id),
                ).fetchall()
            }
            last_ids = set(_json_strings(row["last_result_track_ids_json"]))
            new_ids = [track_id for track_id in current_ids if track_id not in previous_family_ids]
            duplicate_count = max(len(current_ids) - len(new_ids), 0)
            overlap = _jaccard(set(current_ids), last_ids)
            marginal_yield = len(new_ids) / max(len(current_ids), 1)
            low_novelty_count = (
                int(row["consecutive_low_novelty"] or 0) + 1
                if marginal_yield < LOW_NOVELTY_THRESHOLD
                else 0
            )
            searches = int(row["search_count"] or 0) + 1
            total_new = int(row["new_candidate_count"] or 0) + len(new_ids)
            if searches >= 5 and total_new == 0:
                status = "retired"
            elif low_novelty_count >= 2:
                status = "cooldown"
            else:
                status = "active"
            cooldown_hours = (
                LOW_NOVELTY_COOLDOWN_HOURS
                if low_novelty_count >= 2
                else DEFAULT_VARIANT_COOLDOWN_HOURS
            )
            cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).isoformat()
            conn.execute(
                """
                UPDATE discovery_keywords
                SET search_count=search_count+1,
                    candidate_count=candidate_count+?, admitted_count=admitted_count+?,
                    new_candidate_count=new_candidate_count+?,
                    duplicate_candidate_count=duplicate_candidate_count+?,
                    last_marginal_yield=?, last_result_overlap=?,
                    consecutive_low_novelty=?, last_result_track_ids_json=?,
                    status=?, cooldown_until=?, last_used_at=?, last_evaluated_at=?
                WHERE keyword_id=? AND user_id=?
                """,
                (
                    len(tracks),
                    admitted_count,
                    len(new_ids),
                    duplicate_count,
                    round(marginal_yield, 4),
                    round(overlap, 4),
                    low_novelty_count,
                    json.dumps(current_ids, ensure_ascii=False),
                    status,
                    cooldown_until,
                    now,
                    now,
                    keyword_id,
                    self.user_id,
                ),
            )
            for track_id in current_ids:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO discovery_keyword_candidates (
                        keyword_id, user_id, track_id, discovered_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (keyword_id, self.user_id, track_id, now),
                )
            conn.execute(
                """
                UPDATE discovery_keyword_families
                SET search_count=search_count+1,
                    candidate_count=candidate_count+?,
                    new_candidate_count=new_candidate_count+?,
                    last_used_at=?, updated_at=?
                WHERE family_id=? AND user_id=?
                """,
                (len(tracks), len(new_ids), now, now, family_id, self.user_id),
            )
            self._recalculate(conn, keyword_id)
            self._recalculate_family(conn, family_id)

    def record_feedback(self, track_id: str, event: str) -> None:
        if event not in {"shown", "played", "accepted", "completed", "liked", "skipped", "dismissed", "dislike"}:
            return
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c.keyword_id, k.family_id
                FROM discovery_keyword_candidates c
                JOIN discovery_keywords k ON k.keyword_id=c.keyword_id
                WHERE c.user_id=? AND c.track_id=?
                ORDER BY c.discovered_at DESC LIMIT 3
                """,
                (self.user_id, track_id),
            ).fetchall()
            families: set[str] = set()
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
                assignments = ", ".join(
                    f"{field}={field}+{amount}" for field, amount in increments.items()
                )
                conn.execute(
                    f"UPDATE discovery_keywords SET {assignments}, last_evaluated_at=? WHERE keyword_id=?",
                    (_utc_now(), row["keyword_id"]),
                )
                self._recalculate(conn, row["keyword_id"])
                families.add(str(row["family_id"] or ""))
            for family_id in families:
                self._recalculate_family(conn, family_id)

    def _select(
        self,
        candidates: list[dict[str, Any]],
        *,
        limit: int,
        preserve_order: bool,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or not candidates:
            return []
        vectors = _embed_queries([item["query"] for item in candidates])
        pending = list(range(len(candidates)))
        selected: list[int] = []
        family_counts: dict[str, int] = {}
        max_per_family = 2 if preserve_order else 1

        def base_score(index: int) -> float:
            item = candidates[index]
            quality = max(float(item["quality"]), float(item["familyQuality"]))
            novelty = float(item["marginalYield"])
            unseen_bonus = 0.30 if int(item["searchCount"]) == 0 else 0.0
            exploration_bonus = 0.08 if item["explorationAxis"] == "adjacent_genre" else 0.0
            cooldown_penalty = 0.55 if item["cooldownActive"] else 0.0
            order_bonus = max(0.0, 0.08 - 0.01 * int(item["order"])) if preserve_order else 0.0
            return (
                0.42 * quality
                + 0.28 * novelty
                + unseen_bonus
                + exploration_bonus
                + order_bonus
                - 0.22 * float(item["resultOverlap"])
                - cooldown_penalty
            )

        while pending and len(selected) < limit:
            eligible = [
                index
                for index in pending
                if family_counts.get(str(candidates[index]["familyId"]), 0) < max_per_family
                and (
                    preserve_order
                    or candidates[index]["explorationAxis"] != "adjacent_genre"
                    or not any(
                        candidates[item]["explorationAxis"] == "adjacent_genre"
                        for item in selected
                    )
                )
            ]
            if not eligible:
                break

            def mmr_score(index: int) -> float:
                similarity = max(
                    (
                        _keyword_similarity(
                            candidates[index],
                            candidates[other],
                            vectors=vectors,
                            left_index=index,
                            right_index=other,
                        )
                        for other in selected
                    ),
                    default=0.0,
                )
                return base_score(index) - 0.38 * similarity

            best = max(eligible, key=mmr_score)
            pending.remove(best)
            selected.append(best)
            family_id = str(candidates[best]["familyId"])
            family_counts[family_id] = family_counts.get(family_id, 0) + 1

        if not preserve_order and len(selected) >= 3:
            has_exploration = any(candidates[index]["explorationAxis"] == "adjacent_genre" for index in selected)
            exploration_candidates = [
                index
                for index in pending
                if candidates[index]["explorationAxis"] == "adjacent_genre"
                and str(candidates[index]["familyId"])
                not in {str(candidates[item]["familyId"]) for item in selected[:-1]}
            ]
            if not has_exploration and exploration_candidates:
                selected[-1] = max(exploration_candidates, key=base_score)
        mode = "bge_m3_mmr" if vectors is not None else "structured_mmr"
        return [
            {
                **{key: value for key, value in candidates[index].items() if key != "order"},
                "selectionMode": mode,
                "governanceScore": round(base_score(index), 4),
            }
            for index in selected
        ]

    @staticmethod
    def _recalculate(conn: Any, keyword_id: str) -> None:
        row = conn.execute("SELECT * FROM discovery_keywords WHERE keyword_id=?", (keyword_id,)).fetchone()
        if row is None:
            return
        candidates = max(int(row["candidate_count"]), 1)
        shown = int(row["shown_count"])
        dismissed = int(row["dismissed_count"])
        completed = int(row["completed_count"])
        likes = int(row["liked_count"])
        clicks = int(row["clicked_count"])
        acceptance = min((0.25 * clicks + completed + 1.2 * likes) / max(shown, 1), 1.0)
        dismiss_rate = dismissed / max(shown, 1)
        novelty = int(row["new_candidate_count"]) / candidates
        admission = int(row["admitted_count"]) / candidates
        quality = max(
            0.0,
            min(
                1.0,
                0.25 * admission
                + 0.35 * acceptance
                + 0.25 * novelty
                + 0.15 * (1.0 - dismiss_rate)
                - 0.15 * float(row["last_result_overlap"] or 0.0),
            ),
        )
        status = str(row["status"])
        if int(row["search_count"]) >= 5 and int(row["new_candidate_count"]) == 0:
            status = "retired"
        elif shown >= 8 and dismiss_rate >= 0.7 and completed == 0 and likes == 0:
            status = "cooldown"
        conn.execute(
            "UPDATE discovery_keywords SET quality_score=?, status=? WHERE keyword_id=?",
            (round(quality, 4), status, keyword_id),
        )

    @staticmethod
    def _recalculate_family(conn: Any, family_id: str) -> None:
        if not family_id:
            return
        rows = conn.execute(
            "SELECT quality_score, status, cooldown_until FROM discovery_keywords WHERE family_id=?",
            (family_id,),
        ).fetchall()
        if not rows:
            return
        active = [row for row in rows if row["status"] == "active"]
        status = "active" if active else "cooldown" if any(row["status"] == "cooldown" for row in rows) else "retired"
        quality = max(float(row["quality_score"] or 0.0) for row in rows)
        cooldowns = [str(row["cooldown_until"]) for row in rows if row["cooldown_until"]]
        conn.execute(
            """
            UPDATE discovery_keyword_families
            SET quality_score=?, status=?, cooldown_until=?, updated_at=?
            WHERE family_id=?
            """,
            (round(quality, 4), status, max(cooldowns) if cooldowns else None, _utc_now(), family_id),
        )


def _canonical_spec(
    value: dict[str, object] | None,
    *,
    query: str,
    source: str,
) -> tuple[dict[str, object], str]:
    raw = dict(value or _infer_query_spec(query))
    exploration_axis = str(raw.pop("exploration_axis", "") or source or "base")[:40]
    canonical: dict[str, object] = {}
    for key, item in sorted(raw.items()):
        if isinstance(item, (list, tuple, set)):
            values = sorted(set(str(entry).strip().casefold() for entry in item if str(entry).strip()))
            if values:
                canonical[key] = values
        else:
            text = str(item or "").strip().casefold()
            if text:
                canonical[key] = text
    if not canonical:
        canonical["topic"] = " ".join(query.casefold().split())[:160]
    return canonical, exploration_axis


def _infer_query_spec(query: str) -> dict[str, object]:
    normalized = " ".join(query.casefold().split())
    subgenres = [
        subgenre
        for subgenre, terms in {
            "neo_soul": ("neo soul",),
            "funk_soul": ("funk soul",),
            "classic_blues": ("classic blues",),
            "blues_rock": ("blues rock", "布鲁斯摇滚", "蓝调摇滚"),
            "soul_rock": ("soul rock",),
            "indie_rock": ("indie rock",),
            "indie_pop": ("indie pop", "独立流行"),
            "synth_pop": ("synth pop",),
            "city_pop": ("city pop", "城市流行"),
            "jazz_rap": ("jazz rap",),
            "melodic_rap": ("melodic rap",),
            "ska": ("ska",),
            "dub_reggae": ("dub reggae",),
        }.items()
        if any(term in normalized for term in terms)
    ]
    genres = [
        genre
        for genre, terms in {
            "rnb": ("r&b", "rnb", "节奏布鲁斯"),
            "rock": ("rock", "摇滚"),
            "rap": ("rap", "hip-hop", "hiphop", "说唱", "嘻哈"),
            "reggae": ("reggae", "雷鬼", "dub", "ska"),
            "pop": ("pop", "流行", "city pop"),
            "soul": ("soul", "灵魂乐"),
            "funk": ("funk",),
        }.items()
        if any(term in normalized for term in terms)
    ]
    languages = [
        language
        for language, terms in {
            "english": ("英文", "english", "western", "欧美"),
            "chinese": ("中文", "华语", "国语"),
            "japanese": ("日语", "日文", "j-pop", "jpop"),
            "korean": ("韩语", "韩文", "k-pop", "kpop"),
        }.items()
        if any(term in normalized for term in terms)
    ]
    vocals = ["female"] if any(term in normalized for term in ("女声", "女歌手", "female vocal", "female")) else []
    moods = [
        mood
        for mood, terms in {
            "calm": ("安静", "轻柔", "calm"),
            "relaxed": ("放松", "chill", "relax"),
            "lyrical": ("抒情", "ballad"),
            "energetic": ("热血", "动感", "energetic"),
        }.items()
        if any(term in normalized for term in terms)
    ]
    if genres or subgenres or languages or vocals or moods:
        return {
            "genres": genres,
            "subgenres": subgenres,
            "languages": languages,
            "vocals": vocals,
            "moods": moods,
        }
    return {"topic": normalized[:160]}


def _embed_queries(queries: list[str]) -> list[list[float]] | None:
    base_url = os.getenv("AMEM_EMBEDDING_BASE_URL", "").rstrip("/")
    if not base_url or len(queries) < 2:
        return None
    try:
        response = requests.post(
            f"{base_url}/embeddings",
            json={"model": os.getenv("AMEM_EMBEDDING_MODEL", "bge-m3"), "input": queries},
            headers={"Authorization": f"Bearer {os.getenv('BGE_M3_API_KEY', 'local-embedding')}"},
            timeout=max(float(os.getenv("AMEM_EMBEDDING_TIMEOUT_SECONDS", "15")), 5.0),
        )
        response.raise_for_status()
        values = [item["embedding"] for item in sorted(response.json()["data"], key=lambda item: item["index"])]
        return values if len(values) == len(queries) else None
    except Exception:
        return None


def _keyword_similarity(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    vectors: list[list[float]] | None,
    left_index: int,
    right_index: int,
) -> float:
    if left["familyId"] == right["familyId"]:
        return 1.0
    if vectors is not None:
        return max(0.0, _cosine(vectors[left_index], vectors[right_index]))
    left_tokens = _spec_tokens(left["canonicalSpec"])
    right_tokens = _spec_tokens(right["canonicalSpec"])
    return _jaccard(left_tokens, right_tokens)


def _spec_tokens(value: dict[str, object]) -> set[str]:
    tokens: set[str] = set()
    for key, item in value.items():
        tokens.add(str(key))
        if isinstance(item, list):
            tokens.update(str(entry) for entry in item)
        else:
            tokens.add(str(item))
    return tokens


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    return dot / max(left_norm * right_norm, 1e-12)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _json_strings(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _is_future(value: Any, now: str) -> bool:
    return bool(value and str(value) > now)


def _keyword_id(user_id: str, query: str) -> str:
    digest = hashlib.sha1(f"{user_id}:bilibili:{query.casefold()}".encode("utf-8")).hexdigest()[:20]
    return f"keyword:{digest}"


def _family_id(user_id: str, canonical_json: str) -> str:
    digest = hashlib.sha1(f"{user_id}:bilibili-family:{canonical_json}".encode("utf-8")).hexdigest()[:20]
    return f"keyword-family:{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
