from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from amem_bridge import NoopAmemBridge, record_music_behavior
from bili_client import BiliClient
from candidate_pool import CandidatePool
from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from discovery_planner import DiscoveryPlanner
from discovery_service import DiscoveryService
from library_service import LibraryService
from memory_lifecycle import SceneMemoryService
from models import Track
from music_keyword_pool import (
    has_gossip_exclusion,
    has_music_relevance_signal,
    has_non_music_context,
    is_music_relevant,
    matched_artist_names,
)
from music_profile import MusicProfile
from profile_projector import ProfileProjector
from profile_statement_service import ProfileStatementService
from profile_update import MusicProfileUpdatePipeline
from recommendation_engine import RecommendationEngine, RecommendationRequest
from request_spec import RequestSpec

RECENT_LISTEN_DAYS = 7
RECENT_RECOMMEND_DAYS = 7
DEFAULT_RECOMMENDATION_LIMIT = 8
MAX_RECOMMENDATION_LIMIT = 8
DEFAULT_POOL_TARGET = 32
EXPLORE_SLOT_COUNT = 5
HIGH_SCORE_SLOT_COUNT = MAX_RECOMMENDATION_LIMIT - EXPLORE_SLOT_COUNT
POPULAR_MUSIC_QUERY = "音乐"
TAG_SEARCH_SUFFIX = "音乐"
NEGATIVE_OWNER_SUPPRESSION_THRESHOLD = 3
NEGATIVE_OWNER_SCORE_PENALTY = 6
SERVICE_DEFAULT_SAME_UPLOADER_LIMIT = 2
SERVICE_DEFAULT_SAME_ARTIST_LIMIT = 2
SEARCH_BACKED_SOURCES = {"discovery_search", "tag_search", "popular_music"}
EXPLORE_SOURCES = {
    "frequent_up",
    "liked_up",
    "tag_search",
    "popular_music",
    "discovery_search",
}


@dataclass
class UserProfile:
    frequent_owner_mids: set[int] = field(default_factory=set)
    liked_owner_mids: set[int] = field(default_factory=set)
    common_tags: set[str] = field(default_factory=set)
    repeated_owner_mids: set[int] = field(default_factory=set)
    completed_owner_mids: set[int] = field(default_factory=set)
    negative_owner_mids: set[int] = field(default_factory=set)
    recently_heard_track_ids: set[str] = field(default_factory=set)
    recently_recommended_track_ids: set[str] = field(default_factory=set)
    skipped_track_ids: set[str] = field(default_factory=set)


@dataclass
class CandidateDraft:
    track: Track
    sources: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    llm_reason: str = ""
    profile_signals: list[str] = field(default_factory=list)
    facets: dict[str, list[str]] = field(default_factory=dict)
    scope_evidence: list[str] = field(default_factory=list)
    scope_kind: str = "default"


@dataclass
class RecommendationCandidate:
    track: dict[str, Any]
    score: float
    source: str
    reason: str
    llm_reason: str = ""
    profile_signals: list[str] = field(default_factory=list)
    agent_trace_id: str | None = None
    tags: list[str] = field(default_factory=list)
    score_signals: dict[str, float] = field(default_factory=dict)
    matched_preferences: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)
    facets: dict[str, list[str]] = field(default_factory=dict)
    scope_evidence: list[str] = field(default_factory=list)
    scope_kind: str = "default"

    def to_dict(self) -> dict[str, Any]:
        value = {
            "track": self.track,
            "score": round(self.score, 2),
            "source": self.source,
            "reason": self.reason,
        }
        if self.llm_reason:
            value["llmReason"] = self.llm_reason
        if self.profile_signals:
            value["profileSignals"] = self.profile_signals
        if self.agent_trace_id:
            value["agentTraceId"] = self.agent_trace_id
        if self.score_signals:
            value["scoreSignals"] = self.score_signals
        if self.matched_preferences:
            value["matchedPreferences"] = self.matched_preferences
        if self.evidence:
            value["evidence"] = self.evidence
        if self.penalties:
            value["penalties"] = self.penalties
        if self.scope_evidence:
            value["scopeEvidence"] = self.scope_evidence
        return value


class RecommendationService:
    def __init__(
        self,
        db_path: Path | str | None = None,
        user_id: str = LEGACY_OWNER_USER_ID,
        bili_client: Any | None = None,
        amem_bridge: Any | None = None,
        profile_projector: ProfileProjector | None = None,
        discovery_planner: DiscoveryPlanner | None = None,
        auto_discovery: bool | None = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)
        self.library = LibraryService(self.db_path, user_id=self.user_id)
        self.bili_client = bili_client if bili_client is not None else BiliClient()
        self.amem_bridge = amem_bridge if amem_bridge is not None else NoopAmemBridge()
        self.profile_projector = profile_projector or ProfileProjector(self.amem_bridge)
        self.candidate_pool = CandidatePool(str(self.db_path), user_id=self.user_id)
        self.discovery_service = DiscoveryService(
            str(self.db_path),
            user_id=self.user_id,
            bili_client=self.bili_client,
            planner=discovery_planner,
        )
        self.recommendation_engine = RecommendationEngine()
        self.profile_statement_service = ProfileStatementService(self.amem_bridge)
        self.profile_update_pipeline = MusicProfileUpdatePipeline(
            str(self.db_path),
            user_id=self.user_id,
            amem_bridge=self.amem_bridge,
        )
        self.scene_memory_service = SceneMemoryService(str(self.db_path), user_id=self.user_id)
        self.auto_discovery = _env_bool("RECOMMEND_AUTO_DISCOVERY_ENABLED", False) if auto_discovery is None else auto_discovery

    def list_recommendations(
        self,
        scene: str = "home",
        limit: int = DEFAULT_RECOMMENDATION_LIMIT,
        request_spec: RequestSpec | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        timings: dict[str, Any] = {}
        normalized_scene = self._normalize_scene(scene)
        span_started = time.perf_counter()
        scene_memories = self.scene_memory_service.active(scene=normalized_scene)
        timings["l2SceneMemoryMs"] = round((time.perf_counter() - span_started) * 1000, 3)
        timings["l2SceneMemorySource"] = scene_memories[0].get("source") if scene_memories else "sqlite_empty"
        resolved_request_spec = request_spec or (
            RequestSpec.from_dict(scene_memories[0]["requestSpec"])
            if scene_memories and isinstance(scene_memories[0].get("requestSpec"), dict)
            else RequestSpec()
        )
        bounded_limit = min(
            max(int(limit or DEFAULT_RECOMMENDATION_LIMIT), 1),
            MAX_RECOMMENDATION_LIMIT,
        )
        legacy_profile = self._load_user_profile()
        fallback_profile = self._fallback_music_profile(legacy_profile)
        span_started = time.perf_counter()
        projection = self.profile_projector.project(
            user_id=self.user_id,
            scene=normalized_scene,
            fallback_profile=fallback_profile,
        )
        timings["profileProjectionMs"] = round((time.perf_counter() - span_started) * 1000, 2)
        timings["profileCacheHit"] = bool(getattr(projection, "cache_hit", False))
        if getattr(projection, "llm_latency_ms", 0):
            timings["profileLlmApiMs"] = round(float(projection.llm_latency_ms), 2)
        music_profile = projection.profile
        recommendation_request = RecommendationRequest(
            scene=normalized_scene,
            limit=bounded_limit,
            request_spec=resolved_request_spec,
            profile=music_profile,
            exclude_track_ids=(legacy_profile.recently_heard_track_ids | legacy_profile.recently_recommended_track_ids | legacy_profile.skipped_track_ids),
            recent_context={"sceneMemories": scene_memories, "l1": music_profile.to_dict()},
        )
        profile_version = _profile_version(projection.trace_id, music_profile)

        context_specs = []
        if normalized_scene == "conversation" and not resolved_request_spec.constrained:
            context_specs = [
                RequestSpec.from_dict(item["requestSpec"])
                for item in scene_memories[:1]
                if isinstance(item.get("requestSpec"), dict)
            ]
        span_started = time.perf_counter()
        drafts = self._generate_candidates(legacy_profile, resolved_request_spec, context_specs=context_specs)
        timings["candidatePoolReadMs"] = round((time.perf_counter() - span_started) * 1000, 2)
        discovery_plan = self.discovery_service.planner.plan(
            profile=music_profile,
            request_spec=resolved_request_spec,
            scene=normalized_scene,
        )
        discovery_job_id = None
        if self.auto_discovery and normalized_scene == "home" and not resolved_request_spec.constrained and len(drafts) < DEFAULT_POOL_TARGET:
            discovery_job_id = self.discovery_service.enqueue(
                profile=music_profile,
                request_spec=resolved_request_spec,
                scene=normalized_scene,
                limit=bounded_limit,
            )

        span_started = time.perf_counter()
        candidates = [
            self._score_candidate(
                draft,
                legacy_profile,
                music_profile,
                discovery_plan.trace_id,
                request_spec=resolved_request_spec,
            )
            for draft in drafts.values()
        ]
        timings["candidateScoringMs"] = round((time.perf_counter() - span_started) * 1000, 2)
        span_started = time.perf_counter()
        reranked, selected = self.recommendation_engine.rank_and_select(
            candidates,
            request=recommendation_request,
            hard_filtered=lambda item: (
                self._is_hard_filtered(item, music_profile, legacy_profile)
                or item.track["trackId"] in legacy_profile.recently_recommended_track_ids
            ),
            select=lambda values: self._select_epsilon_greedy(
                values,
                bounded_limit,
                normalized_scene,
                legacy_profile,
                music_profile,
            ),
            diversity=lambda values: self._apply_diversity_limits(
                values,
                music_profile.same_uploader_limit,
                bounded_limit,
            ),
        )
        timings["selectionMmrMs"] = round((time.perf_counter() - span_started) * 1000, 2)

        self._upsert_candidate_tracks(selected)
        trace_id = self._store_recommendation_trace(
            scene=normalized_scene,
            profile_trace_id=projection.trace_id,
            agent_trace_id=discovery_plan.trace_id,
            memories=projection.memories,
            music_profile=music_profile,
            profile_version=profile_version,
            agent_queries=discovery_plan.search_queries,
            local_candidate_count=len(drafts),
            agent_candidates=[],
            reranked=reranked,
            selected=selected,
            request_spec=resolved_request_spec,
            timing=timings,
        )
        self.record_events(
            [
                {
                    "trackId": item.track["trackId"],
                    "event": "shown",
                    "scene": normalized_scene,
                    "source": item.source,
                    "reason": item.reason,
                    "score": item.score,
                }
                for item in selected
            ]
        )
        timings["totalMs"] = round((time.perf_counter() - started_at) * 1000, 2)
        return {
            "scene": normalized_scene,
            "items": [item.to_dict() for item in selected],
            "profile": music_profile.to_dict(),
            "profileTraceId": projection.trace_id,
            "profileVersion": profile_version,
            "discoveryTraceId": discovery_plan.trace_id,
            "discoveryJobId": discovery_job_id,
            "requestSpec": resolved_request_spec.to_dict(),
            "debugTraceId": trace_id,
            "timing": timings,
        }

    def latest_debug_trace(self, scene: str = "home") -> dict[str, Any]:
        normalized_scene = self._normalize_scene(scene)
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT trace_id, scene, profile_trace_id, agent_trace_id, payload_json, created_at
                FROM recommendation_traces
                WHERE user_id = ? AND scene = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (self.user_id, normalized_scene),
            ).fetchone()
        if row is None:
            return {
                "traceId": None,
                "scene": normalized_scene,
                "available": False,
                "message": "No recommendation trace has been recorded for this scene.",
            }
        payload = _json_loads(row["payload_json"])
        payload.update(
            {
                "traceId": row["trace_id"],
                "scene": row["scene"],
                "profileTraceId": row["profile_trace_id"],
                "agentTraceId": row["agent_trace_id"],
                "createdAt": row["created_at"],
                "available": True,
            }
        )
        return payload

    def music_profile_analysis(self, scene: str = "home") -> dict[str, Any]:
        normalized_scene = self._normalize_scene(scene)
        legacy_profile = self._load_user_profile()
        fallback_profile = self._fallback_music_profile(legacy_profile)
        projection = self.profile_projector.project(
            user_id=self.user_id,
            scene=normalized_scene,
            fallback_profile=fallback_profile,
        )
        profile = projection.profile
        return {
            "scene": normalized_scene,
            "profile": profile.to_dict(),
            "profileTraceId": projection.trace_id,
            "memories": [memory.to_prompt_dict() for memory in projection.memories],
            "summary": _profile_summary(profile),
            "sceneMemories": self.scene_memory_service.active(scene=normalized_scene),
        }

    def backfill_music_memories(self, limit: int = 80) -> dict[str, Any]:
        bounded_limit = min(max(int(limit or 80), 1), 200)
        events: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for payload in self.library.list_recent(limit=bounded_limit):
            track = Track.from_dict(payload)
            event = "completed" if payload.get("completed") else "played"
            key = (event, track.track_id or "")
            if key in seen:
                continue
            seen.add(key)
            events.append({"event": event, "track": track, "scene": "backfill"})

        for payload in self.library.list_likes()[:bounded_limit]:
            track = Track.from_dict(payload)
            key = ("liked", track.track_id or "")
            if key in seen:
                continue
            seen.add(key)
            events.append({"event": "liked", "track": track, "scene": "backfill"})

        for item in self._review_backfill_items(bounded_limit):
            track = item["track"]
            key = ("track_reviewed", track.track_id or "")
            if key in seen:
                continue
            seen.add(key)
            events.append(
                {
                    "event": "track_reviewed",
                    "track": track,
                    "scene": "backfill",
                    "payload": {
                        "rating": item["rating"],
                        "mood": item["mood"],
                        "note": item["note"],
                    },
                }
            )

        memory_ids: list[str] = []
        recorded = 0
        for item in events[: bounded_limit * 3]:
            result = record_music_behavior(
                self.amem_bridge,
                user_id=self.user_id,
                event=item["event"],
                track=item["track"],
                scene=item["scene"],
                payload=item.get("payload") or {},
            )
            ids = result.get("memoryIds") or []
            if result.get("eventId"):
                recorded += 1
            memory_ids.extend(str(memory_id) for memory_id in ids)

        return {
            "userId": self.user_id,
            "eventsConsidered": len(events),
            "eventsRecorded": recorded,
            "memoryIds": memory_ids,
            "memoryCount": len(memory_ids),
        }

    def submit_profile_statement(self, description: str) -> dict[str, Any]:
        result = self.profile_statement_service.submit(
            user_id=self.user_id,
            description=description,
        )
        if hasattr(self.profile_projector, "clear_cache"):
            self.profile_projector.clear_cache(user_id=self.user_id)
        try:
            submitted_profile = MusicProfile.from_dict(result.get("profile") or {}, source="profile_statement")
            result["discovery"] = self.discovery_service.discover_now(
                profile=submitted_profile,
                request_spec=RequestSpec(),
                scene="home",
                limit=MAX_RECOMMENDATION_LIMIT,
            )
        except Exception:
            result["discovery"] = {"available": self.candidate_pool.availability(RequestSpec()), "queries": []}
        result["analysis"] = self.music_profile_analysis(scene="home")
        return result

    def enqueue_discovery(
        self,
        *,
        scene: str = "home",
        limit: int = DEFAULT_RECOMMENDATION_LIMIT,
        request_spec: RequestSpec | None = None,
    ) -> str | None:
        normalized_scene = self._normalize_scene(scene)
        legacy_profile = self._load_user_profile()
        projection = self.profile_projector.project(
            user_id=self.user_id,
            scene=normalized_scene,
            fallback_profile=self._fallback_music_profile(legacy_profile),
        )
        return self.discovery_service.enqueue(
            profile=projection.profile,
            request_spec=request_spec or RequestSpec(),
            scene=normalized_scene,
            limit=min(max(int(limit or DEFAULT_RECOMMENDATION_LIMIT), 1), MAX_RECOMMENDATION_LIMIT),
        )

    def bootstrap_discovery(
        self,
        *,
        scene: str = "conversation",
        limit: int = DEFAULT_RECOMMENDATION_LIMIT,
        request_spec: RequestSpec | None = None,
    ) -> dict[str, Any]:
        normalized_scene = self._normalize_scene(scene)
        legacy_profile = self._load_user_profile()
        projection = self.profile_projector.project(
            user_id=self.user_id,
            scene=normalized_scene,
            fallback_profile=self._fallback_music_profile(legacy_profile),
        )
        return self.discovery_service.discover_now(
            profile=projection.profile,
            request_spec=request_spec or RequestSpec(),
            scene=normalized_scene,
            limit=min(max(int(limit or DEFAULT_RECOMMENDATION_LIMIT), 1), MAX_RECOMMENDATION_LIMIT),
        )

    def discovery_status(self, job_id: str) -> dict[str, Any]:
        return self.discovery_service.job_status(job_id)

    def remember_request(self, *, scene: str, request_spec: RequestSpec) -> str | None:
        return self.scene_memory_service.remember_request(
            scene=self._normalize_scene(scene),
            request_spec=request_spec,
        )

    def validate_and_finalize(
        self,
        candidates: list[RecommendationCandidate],
        *,
        profile: MusicProfile,
        legacy_profile: UserProfile,
        limit: int,
        scene: str,
        request_spec: RequestSpec | None = None,
    ) -> list[RecommendationCandidate]:
        resolved_request_spec = request_spec or RequestSpec()
        filtered = self._filter_candidates(
            candidates,
            profile=profile,
            legacy_profile=legacy_profile,
        )
        filtered = [item for item in filtered if resolved_request_spec.matches_facets(item.facets)]
        selected = self._select_epsilon_greedy(filtered, limit, scene, legacy_profile, profile)
        return self._apply_diversity_limits(selected, profile.same_uploader_limit, limit)

    def record_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        events = self.record_events([payload])
        return events[0] if events else {}

    def record_events(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = _utc_now()
        normalized = []
        for payload in payloads:
            track_id = str(payload.get("trackId") or payload.get("track_id") or "").strip()
            track = self.library.get_track(track_id) if track_id else None
            if not track_id or track is None:
                continue
            event = self._normalize_event(payload.get("event"))
            normalized.append(
                {
                    "trackId": track_id,
                    "track": track,
                    "event": event,
                    "scene": self._normalize_scene(str(payload.get("scene") or "home")),
                    "source": str(payload.get("source") or "")[:64],
                    "reason": str(payload.get("reason") or "")[:240],
                    "score": float(payload.get("score") or 0),
                    "playedSeconds": max(
                        int(payload.get("playedSeconds") or payload.get("played_seconds") or 0),
                        0,
                    ),
                    "completed": bool(payload.get("completed")) or event == "completed",
                    "liked": event == "liked",
                    "skipped": bool(payload.get("skipped"))
                    or event in {"skipped", "dismissed", "dislike"},
                    "createdAt": now,
                    "behaviorPayload": dict(payload),
                }
            )

        if normalized:
            with get_connection(self.db_path) as conn:
                conn.executemany(
                    """
                    INSERT INTO recommendation_events (
                        user_id, track_id, event, scene, source, reason, score, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            self.user_id,
                            item["trackId"],
                            item["event"],
                            item["scene"],
                            item["source"],
                            item["reason"],
                            item["score"],
                            item["createdAt"],
                        )
                        for item in normalized
                    ],
                )
                for item in normalized:
                    self._write_history(conn, item)

        for item in normalized:
            behavior_payload = dict(item.get("behaviorPayload") or {})
            behavior_payload.update(
                {
                    "source": item["source"],
                    "reason": item["reason"],
                    "score": item["score"],
                    "playedSeconds": item["playedSeconds"],
                    "completed": item["completed"],
                    "skipped": item["skipped"],
                }
            )
            record_music_behavior(
                self.amem_bridge,
                user_id=self.user_id,
                event=item["event"],
                track=item["track"],
                scene=item["scene"],
                payload=behavior_payload,
            )
        if normalized:
            self.profile_update_pipeline.process()
        return [
            {key: value for key, value in item.items() if key not in {"track", "behaviorPayload"}}
            for item in normalized
        ]

    def _generate_candidates(
        self,
        profile: UserProfile,
        request_spec: RequestSpec | None = None,
        *,
        context_specs: list[RequestSpec] | None = None,
    ) -> dict[str, CandidateDraft]:
        """Load only previously admitted content. This serving path never calls Bilibili search."""
        candidates: dict[str, CandidateDraft] = {}
        for candidate in self.candidate_pool.list_ready(request_spec or RequestSpec(), context_specs=context_specs):
            self._add_candidate(candidates, candidate.track, candidate.source)
            draft = candidates[candidate.track.track_id]
            draft.facets = candidate.facets
            draft.scope_evidence = candidate.evidence
            draft.scope_kind = candidate.scope_kind
            draft.tags.update(
                evidence.removeprefix("discovery_query:")
                for evidence in candidate.evidence
                if evidence.startswith("discovery_query:")
            )
        return candidates

    def _score_candidate(
        self,
        draft: CandidateDraft,
        profile: UserProfile,
        music_profile: MusicProfile,
        agent_trace_id: str,
        request_spec: RequestSpec | None = None,
    ) -> RecommendationCandidate:
        track = draft.track
        score = 0.0
        score_signals: dict[str, float] = {}
        matched_preferences: list[str] = []
        evidence: list[str] = []
        penalties: list[str] = []
        text = f"{track.title} {track.owner} {' '.join(draft.tags)} {' '.join(draft.profile_signals)}"

        if track.owner_mid and track.owner_mid in profile.frequent_owner_mids:
            score += _add_score_signal(score_signals, "frequent_owner", 3)
            evidence.append("来自你最近常听的 UP 或相近来源")

        matched_tags = sorted(draft.tags & profile.common_tags)
        if matched_tags:
            score += _add_score_signal(score_signals, "tag_match", 3)
            matched_preferences.extend(matched_tags[:2])
            evidence.append(f"命中你近期标过的标签：{'、'.join(matched_tags[:2])}")

        if track.owner_mid and track.owner_mid in profile.repeated_owner_mids:
            score += _add_score_signal(score_signals, "recent_owner_repeat", 2)
            evidence.append("最近重复听过相近来源")

        if track.owner_mid and track.owner_mid in profile.completed_owner_mids:
            score += _add_score_signal(score_signals, "recent_completion", 2)
            evidence.append("最近完整听完过相近来源")

        positive_weight = music_profile.topic_weight(text, positive=True)
        if positive_weight:
            score += _add_score_signal(score_signals, "profile_match", 4 * positive_weight)
            profile_topics = _matched_profile_topics(text, music_profile.positive_topics)
            matched_preferences.extend(profile_topics)
            if profile_topics:
                evidence.append(f"贴近你稳定偏好的 {'、'.join(profile_topics[:3])}")
            else:
                evidence.append("贴近你稳定偏好的听感")

        negative_weight = music_profile.topic_weight(text, positive=False)
        if set((request_spec or RequestSpec()).required_genres) & set(draft.facets.get("genres") or []):
            negative_weight = 0.0
        if negative_weight:
            score += _add_score_signal(score_signals, "negative_preference_penalty", -3 * negative_weight)
            negative_topics = _matched_profile_topics(text, music_profile.negative_topics)
            if negative_topics:
                penalties.append(f"命中你回避的 {'、'.join(negative_topics[:2])}")
            else:
                penalties.append("命中近期负反馈方向")

        uploader_key = _uploader_key(track)
        uploader_weight = music_profile.uploader_weight(uploader_key)
        if uploader_weight:
            score += _add_score_signal(score_signals, "preferred_uploader", 3 * uploader_weight)
            evidence.append("来自你更容易接受的 UP 或来源")

        if track.track_id in profile.recently_heard_track_ids:
            score += _add_score_signal(score_signals, "recently_heard_penalty", -3)
            penalties.append("最近已经听过")
        if track.track_id in profile.recently_recommended_track_ids:
            score += _add_score_signal(score_signals, "fatigue_penalty", -4)
            penalties.append("近期已经推荐过")
        if track.track_id in profile.skipped_track_ids:
            score += _add_score_signal(score_signals, "skip_penalty", -5)
            penalties.append("你之前跳过或点过不感兴趣")
        if track.owner_mid and track.owner_mid in profile.negative_owner_mids:
            score += _add_score_signal(
                score_signals,
                "negative_owner_penalty",
                -NEGATIVE_OWNER_SCORE_PENALTY,
            )
            penalties.append("该 UP 近期负反馈较多")

        if (request_spec or RequestSpec()).constrained and draft.scope_evidence:
            evidence.append("满足本轮范围约束")
        if "discovery_search" in draft.sources and draft.llm_reason:
            matched_preferences.extend(_profile_signal_names(draft.profile_signals))
            evidence.append(f"搜索计划命中：{_clean_agent_reason(draft.llm_reason)}")
        elif "tag_search" in draft.sources and matched_tags:
            evidence.append(f"按 {matched_tags[0]} 扩展探索")
        elif "popular_music" in draft.sources and score <= 0:
            evidence.append("来自最近热门音乐候选")

        if not evidence:
            evidence.append(self._source_reason(draft.sources))

        return RecommendationCandidate(
            track=track.to_dict(),
            score=score,
            source=self._primary_source(draft.sources),
            reason=_candidate_reason(evidence, penalties),
            llm_reason=draft.llm_reason,
            profile_signals=draft.profile_signals,
            agent_trace_id=agent_trace_id if "discovery_search" in draft.sources else None,
            tags=sorted(draft.tags),
            score_signals=score_signals,
            matched_preferences=list(dict.fromkeys(matched_preferences)),
            evidence=list(dict.fromkeys(evidence)),
            penalties=list(dict.fromkeys(penalties)),
            facets=draft.facets,
            scope_evidence=draft.scope_evidence,
            scope_kind=draft.scope_kind,
        )

    def _select_epsilon_greedy(
        self,
        candidates: list[RecommendationCandidate],
        limit: int,
        scene: str,
        profile: UserProfile,
        music_profile: MusicProfile,
    ) -> list[RecommendationCandidate]:
        if not candidates:
            return []

        if music_profile.exploration_ratio > 0:
            explore_count = min(limit, max(0, round(limit * music_profile.exploration_ratio)))
        else:
            explore_count = min(EXPLORE_SLOT_COUNT, max(limit - HIGH_SCORE_SLOT_COUNT, 0))
        high_count = limit - explore_count
        selected_ids: set[str] = set()
        explore_selected: list[RecommendationCandidate] = []
        high_selected: list[RecommendationCandidate] = []

        explore_pool = [
            item for item in candidates
            if self._is_unfamiliar(item, profile)
            and item.source in EXPLORE_SOURCES
        ][:50]

        seed = f"{self.user_id}:{scene}:{datetime.now(timezone.utc).date().isoformat()}"
        rng = random.Random(seed)
        rng.shuffle(explore_pool)

        for item in explore_pool[:explore_count]:
            item.source = "explore"
            if not item.reason.startswith("探索"):
                item.reason = f"探索：{item.reason}"
            explore_selected.append(item)
            selected_ids.add(item.track["trackId"])

        high_pool = [
            item for item in candidates
            if item.track["trackId"] not in selected_ids
            and not self._is_skipped(item, profile)
            and item.track["trackId"] not in profile.recently_recommended_track_ids
        ]
        for item in high_pool[:high_count]:
            high_selected.append(item)
            selected_ids.add(item.track["trackId"])

        if len(high_selected) < high_count:
            for item in candidates:
                track_id = item.track["trackId"]
                if track_id in selected_ids or self._is_skipped(item, profile) or track_id in profile.recently_recommended_track_ids:
                    continue
                high_selected.append(item)
                selected_ids.add(track_id)
                if len(high_selected) >= high_count:
                    break

        return (high_selected + explore_selected)[:limit]

    @staticmethod
    def _apply_diversity_limits(
        candidates: list[RecommendationCandidate],
        same_uploader_limit: int,
        limit: int,
    ) -> list[RecommendationCandidate]:
        uploader_limit = (
            same_uploader_limit
            if same_uploader_limit > 0
            else SERVICE_DEFAULT_SAME_UPLOADER_LIMIT
        )
        uploader_counts: dict[str, int] = {}
        artist_counts: dict[str, int] = {}
        contextual_count = 0
        selected: list[RecommendationCandidate] = []
        for item in candidates:
            uploader = str(item.track.get("ownerMid") or item.track.get("owner") or "")
            artist_keys = _candidate_artist_keys(item)
            if uploader and uploader_counts.get(uploader, 0) >= uploader_limit:
                continue
            if any(
                artist_counts.get(artist, 0) >= SERVICE_DEFAULT_SAME_ARTIST_LIMIT
                for artist in artist_keys
            ):
                continue
            if item.scope_kind == "request" and contextual_count >= 2:
                continue
            selected.append(item)
            if uploader:
                uploader_counts[uploader] = uploader_counts.get(uploader, 0) + 1
            for artist in artist_keys:
                artist_counts[artist] = artist_counts.get(artist, 0) + 1
            if item.scope_kind == "request":
                contextual_count += 1
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _filter_candidates(
        candidates: list[RecommendationCandidate],
        *,
        profile: MusicProfile,
        legacy_profile: UserProfile,
    ) -> list[RecommendationCandidate]:
        return [
            item for item in candidates
            if not RecommendationService._is_hard_filtered(item, profile, legacy_profile)
        ]

    @staticmethod
    def _is_hard_filtered(
        item: RecommendationCandidate,
        profile: MusicProfile,
        legacy_profile: UserProfile,
    ) -> bool:
        track_id = str(item.track.get("trackId") or "")
        if track_id in legacy_profile.skipped_track_ids:
            return True
        if not RecommendationService._is_music_candidate(item, legacy_profile):
            return True
        owner_mid = _candidate_owner_mid(item)
        trusted_owner_mids = (
            set(getattr(legacy_profile, "frequent_owner_mids", set()))
            | set(getattr(legacy_profile, "liked_owner_mids", set()))
            | set(getattr(legacy_profile, "completed_owner_mids", set()))
        )
        if owner_mid in set(getattr(legacy_profile, "negative_owner_mids", set())) and owner_mid not in trusted_owner_mids:
            return True
        uploader = str(item.track.get("ownerMid") or item.track.get("owner") or "")
        return profile.hard_blocked_uploader(uploader) or profile.avoided_uploader(uploader)

    @staticmethod
    def _is_music_candidate(item: RecommendationCandidate, profile: UserProfile) -> bool:
        text = _candidate_text(item)
        if has_gossip_exclusion(text):
            return False
        if item.source in SEARCH_BACKED_SOURCES:
            return has_music_relevance_signal(text)
        if has_non_music_context(text) and not has_music_relevance_signal(text):
            return False
        owner_mid = _candidate_owner_mid(item)
        trusted_owner_mids = (
            set(getattr(profile, "frequent_owner_mids", set()))
            | set(getattr(profile, "liked_owner_mids", set()))
            | set(getattr(profile, "completed_owner_mids", set()))
        )
        if owner_mid in trusted_owner_mids:
            return True
        return is_music_relevant(text)

    @staticmethod
    def _is_unfamiliar(item: RecommendationCandidate, profile: UserProfile) -> bool:
        track_id = item.track["trackId"]
        return (
            track_id not in profile.recently_heard_track_ids
            and track_id not in profile.recently_recommended_track_ids
            and track_id not in profile.skipped_track_ids
        )

    @staticmethod
    def _is_skipped(item: RecommendationCandidate, profile: UserProfile) -> bool:
        return item.track["trackId"] in profile.skipped_track_ids

    def _load_user_profile(self) -> UserProfile:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_LISTEN_DAYS)).isoformat()
        recommend_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=RECENT_RECOMMEND_DAYS)
        ).isoformat()
        profile = UserProfile()

        with get_connection(self.db_path) as conn:
            profile.frequent_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT t.owner_mid, COUNT(*) + COALESCE(SUM(r.play_count), 0) AS weight
                    FROM tracks t
                    LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                    LEFT JOIN playback_recent pr ON pr.user_id = ? AND pr.track_id = t.track_id
                    WHERE t.owner_mid IS NOT NULL
                      AND (r.track_id IS NOT NULL OR pr.track_id IS NOT NULL)
                    GROUP BY t.owner_mid
                    ORDER BY weight DESC
                    LIMIT 10
                    """,
                    (self.user_id, self.user_id),
                ).fetchall()
                if row["owner_mid"]
            }
            profile.liked_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT t.owner_mid
                    FROM likes l
                    JOIN tracks t ON t.track_id = l.track_id
                    WHERE l.user_id = ? AND t.owner_mid IS NOT NULL
                    LIMIT 20
                    """,
                    (self.user_id,),
                ).fetchall()
                if row["owner_mid"]
            }
            profile.common_tags = {
                str(row["mood"]).strip()
                for row in conn.execute(
                    """
                    SELECT mood, COUNT(*) AS weight
                    FROM track_reviews
                    WHERE user_id = ? AND TRIM(mood) <> ''
                    GROUP BY mood
                    ORDER BY weight DESC, MAX(updated_at) DESC
                    LIMIT 10
                    """,
                    (self.user_id,),
                ).fetchall()
                if str(row["mood"]).strip()
            }
            profile.repeated_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT t.owner_mid
                    FROM recent r
                    JOIN tracks t ON t.track_id = r.track_id
                    WHERE r.user_id = ? AND r.play_count >= 2 AND t.owner_mid IS NOT NULL
                    GROUP BY t.owner_mid
                    LIMIT 10
                    """,
                    (self.user_id,),
                ).fetchall()
                if row["owner_mid"]
            }
            profile.completed_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT t.owner_mid
                    FROM tracks t
                    LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                    LEFT JOIN playback_recent pr ON pr.user_id = ? AND pr.track_id = t.track_id
                    WHERE t.owner_mid IS NOT NULL
                      AND (COALESCE(r.completed, 0) = 1 OR COALESCE(pr.completed, 0) = 1)
                    LIMIT 20
                    """,
                    (self.user_id, self.user_id),
                ).fetchall()
                if row["owner_mid"]
            }
            profile.recently_heard_track_ids = {
                str(row["track_id"])
                for row in conn.execute(
                    """
                    SELECT track_id FROM recent
                    WHERE user_id = ? AND last_played_at >= ?
                    UNION
                    SELECT track_id FROM playback_recent
                    WHERE user_id = ? AND last_played_at >= ?
                    """,
                    (self.user_id, cutoff, self.user_id, cutoff),
                ).fetchall()
            }
            profile.recently_recommended_track_ids = {
                str(row["track_id"])
                for row in conn.execute(
                    """
                    SELECT track_id FROM recommendation_history
                    WHERE user_id = ? AND recommended_at >= ?
                    UNION
                    SELECT track_id FROM recommendation_events
                    WHERE user_id = ? AND event = 'shown' AND created_at >= ?
                    """,
                    (self.user_id, recommend_cutoff, self.user_id, recommend_cutoff),
                ).fetchall()
            }
            profile.skipped_track_ids = {
                str(row["track_id"])
                for row in conn.execute(
                    """
                    SELECT track_id FROM playback_recent
                    WHERE user_id = ? AND skipped = 1
                    UNION
                    SELECT track_id FROM recommendation_history
                    WHERE user_id = ? AND skipped = 1
                    UNION
                    SELECT track_id FROM recommendation_events
                    WHERE user_id = ? AND event IN ('skipped', 'dismissed', 'dislike')
                    """,
                    (self.user_id, self.user_id, self.user_id),
                ).fetchall()
            }
            profile.negative_owner_mids = {
                int(row["owner_mid"])
                for row in conn.execute(
                    """
                    SELECT owner_mid
                    FROM (
                        SELECT t.owner_mid AS owner_mid
                        FROM recommendation_events e
                        JOIN tracks t ON t.track_id = e.track_id
                        WHERE e.user_id = ?
                          AND e.event IN ('skipped', 'dismissed', 'dislike')
                          AND t.owner_mid IS NOT NULL
                        UNION ALL
                        SELECT t.owner_mid AS owner_mid
                        FROM recommendation_history h
                        JOIN tracks t ON t.track_id = h.track_id
                        WHERE h.user_id = ?
                          AND h.skipped = 1
                          AND t.owner_mid IS NOT NULL
                    )
                    GROUP BY owner_mid
                    HAVING COUNT(*) >= ?
                    LIMIT 50
                    """,
                    (
                        self.user_id,
                        self.user_id,
                        NEGATIVE_OWNER_SUPPRESSION_THRESHOLD,
                    ),
                ).fetchall()
                if row["owner_mid"]
            }

        return profile

    @staticmethod
    def _fallback_music_profile(profile: UserProfile) -> MusicProfile:
        return MusicProfile(
            positive_topics={tag: 0.72 for tag in profile.common_tags},
            preferred_uploaders={
                str(mid): 0.75 for mid in (profile.liked_owner_mids | profile.frequent_owner_mids)
            },
            confidence=0.45 if profile.common_tags or profile.liked_owner_mids else 0.0,
            source="fallback",
        )

    def _safe_list_user_tracks(self, mid: int, order: str, page_size: int) -> list[Track]:
        try:
            payload = self.bili_client.list_user_tracks(
                mid,
                page=1,
                page_size=page_size,
                order=order,
            )
        except Exception:
            return []
        tracks = payload.get("tracks") if isinstance(payload, dict) else []
        return _coerce_tracks(tracks or [])

    def _safe_search_tracks(self, keyword: str, page_size: int) -> list[Track]:
        try:
            tracks = self.bili_client.search(keyword, page=1, page_size=page_size)
        except Exception:
            return []
        return _coerce_tracks(tracks or [])

    def _local_tracks_with_common_tags(self, tags: set[str]) -> list[tuple[Track, str]]:
        if not tags:
            return []
        placeholders = ",".join("?" for _ in tags)
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, tr.mood
                FROM track_reviews tr
                JOIN tracks t ON t.track_id = tr.track_id
                WHERE tr.user_id = ?
                  AND tr.mood IN ({placeholders})
                ORDER BY tr.updated_at DESC
                LIMIT 50
                """,
                (self.user_id, *tags),
            ).fetchall()
        return [(self.library._track_from_row(row), str(row["mood"])) for row in rows]

    def _local_fallback_tracks(self) -> list[Track]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.*
                FROM tracks t
                LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                LEFT JOIN likes l ON l.user_id = ? AND l.track_id = t.track_id
                LEFT JOIN track_reviews tr ON tr.user_id = ? AND tr.track_id = t.track_id
                LEFT JOIN playlist_items pi ON pi.user_id = ? AND pi.track_id = t.track_id
                WHERE r.track_id IS NOT NULL
                   OR l.track_id IS NOT NULL
                   OR tr.track_id IS NOT NULL
                   OR pi.track_id IS NOT NULL
                ORDER BY COALESCE(r.last_played_at, l.created_at, tr.updated_at, t.updated_at) DESC
                LIMIT 50
                """,
                (self.user_id, self.user_id, self.user_id, self.user_id),
            ).fetchall()
        return [self.library._track_from_row(row) for row in rows]

    def _review_backfill_items(self, limit: int) -> list[dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT tr.rating, tr.mood, tr.note, t.*
                FROM track_reviews tr
                JOIN tracks t ON t.track_id = tr.track_id
                WHERE tr.user_id = ?
                ORDER BY tr.updated_at DESC
                LIMIT ?
                """,
                (self.user_id, limit),
            ).fetchall()
        return [
            {
                "track": self.library._track_from_row(row),
                "rating": int(row["rating"]),
                "mood": str(row["mood"] or ""),
                "note": str(row["note"] or ""),
            }
            for row in rows
        ]

    @staticmethod
    def _add_candidate(
        candidates: dict[str, CandidateDraft],
        track: Track,
        source: str,
        tag: str | None = None,
    ) -> None:
        if not track.track_id:
            return
        draft = candidates.get(track.track_id)
        if not draft:
            draft = CandidateDraft(track=track)
            candidates[track.track_id] = draft
        draft.sources.add(source)
        if tag:
            draft.tags.add(tag)

    def _upsert_draft_tracks(self, drafts: Any) -> None:
        tracks = [draft.track for draft in drafts]
        if tracks:
            self.library.upsert_tracks(tracks)

    def _upsert_candidate_tracks(self, candidates: list[RecommendationCandidate]) -> None:
        tracks = []
        for candidate in candidates:
            try:
                tracks.append(Track.from_dict(candidate.track))
            except Exception:
                continue
        if tracks:
            self.library.upsert_tracks(tracks)

    def _store_recommendation_trace(
        self,
        *,
        scene: str,
        profile_trace_id: str,
        agent_trace_id: str,
        memories: list[Any],
        music_profile: MusicProfile,
        profile_version: str,
        agent_queries: list[str],
        local_candidate_count: int,
        agent_candidates: list[Any],
        reranked: list[RecommendationCandidate],
        selected: list[RecommendationCandidate],
        request_spec: RequestSpec,
        timing: dict[str, Any],
    ) -> str:
        created_at = _utc_now()
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        trace_id = f"recommend:{self.user_id}:{scene}:{timestamp_ms}"
        payload = {
            "memoryRetrieval": {
                "count": len(memories),
                "memories": [memory.to_prompt_dict() for memory in memories],
            },
            "profileVersion": profile_version,
            "profileSnapshot": {
                "traceId": profile_trace_id,
                "version": profile_version,
                "profile": music_profile.to_dict(),
            },
            "musicProfile": music_profile.to_dict(),
            "requestSpec": request_spec.to_dict(),
            "timing": timing,
            "candidatePool": {
                "searchQueries": agent_queries,
                "availableCandidateCount": max(local_candidate_count, 0),
                "newlyDiscoveredCandidateCount": len(agent_candidates),
            },
            "agent": {
                "searchQueries": agent_queries,
                "localCandidateCount": max(local_candidate_count, 0),
                "agentCandidateCount": 0,
                "agentCandidates": [],
            },
            "rerankedCandidates": [
                _candidate_to_trace(candidate)
                for candidate in reranked[:40]
            ],
            "finalResults": [
                _candidate_to_trace(candidate)
                for candidate in selected
            ],
        }
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO recommendation_traces (
                    trace_id, user_id, scene, profile_trace_id, agent_trace_id,
                    payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    self.user_id,
                    scene,
                    profile_trace_id,
                    agent_trace_id,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )
        return trace_id

    def _write_history(self, conn: Any, item: dict[str, Any]) -> None:
        if item["event"] == "shown":
            conn.execute(
                """
                INSERT INTO recommendation_history (
                    user_id, track_id, recommended_at, clicked, played_seconds,
                    completed, liked, skipped, scene, source, score, reason
                )
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, ?, ?, ?)
                """,
                (
                    self.user_id,
                    item["trackId"],
                    item["createdAt"],
                    item["scene"],
                    item["source"],
                    item["score"],
                    item["reason"],
                ),
            )
            return

        latest = conn.execute(
            """
            SELECT id FROM recommendation_history
            WHERE user_id = ? AND track_id = ?
            ORDER BY recommended_at DESC
            LIMIT 1
            """,
            (self.user_id, item["trackId"]),
        ).fetchone()
        if latest:
            conn.execute(
                """
                UPDATE recommendation_history
                SET clicked = MAX(clicked, ?),
                    played_seconds = MAX(played_seconds, ?),
                    completed = MAX(completed, ?),
                    liked = MAX(liked, ?),
                    skipped = MAX(skipped, ?)
                WHERE id = ?
                """,
                (
                    int(item["event"] in {"played", "accepted", "completed"}),
                    item["playedSeconds"],
                    int(item["completed"]),
                    int(item["liked"]),
                    int(item["skipped"]),
                    latest["id"],
                ),
            )
            return

        conn.execute(
            """
            INSERT INTO recommendation_history (
                user_id, track_id, recommended_at, clicked, played_seconds,
                completed, liked, skipped, scene, source, score, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.user_id,
                item["trackId"],
                item["createdAt"],
                int(item["event"] in {"played", "accepted", "completed"}),
                item["playedSeconds"],
                int(item["completed"]),
                int(item["liked"]),
                int(item["skipped"]),
                item["scene"],
                item["source"],
                item["score"],
                item["reason"],
            ),
        )

    @staticmethod
    def _primary_source(sources: set[str]) -> str:
        for source in [
            "discovery_search",
            "frequent_up",
            "liked_up",
            "tag_search",
            "tag_match",
            "popular_music",
            "library",
        ]:
            if source in sources:
                return source
        return next(iter(sources), "library")

    @staticmethod
    def _source_reason(sources: set[str]) -> str:
        source = RecommendationService._primary_source(sources)
        return {
            "discovery_search": "候选池中的发现结果",
            "frequent_up": "常听 UP 的其他稿件",
            "liked_up": "喜欢歌曲 UP 的其他稿件",
            "tag_search": "同标签搜索结果",
            "tag_match": "标签相同的歌曲",
            "popular_music": "最近热门音乐稿件",
        }.get(source, "来自你的播放和收藏记录")

    @staticmethod
    def _normalize_scene(scene: str) -> str:
        value = (scene or "home").strip().lower()
        return value[:32] or "home"

    @staticmethod
    def _normalize_event(event: Any) -> str:
        value = str(event or "shown").strip().lower()
        allowed = {
            "shown",
            "played",
            "accepted",
            "dismissed",
            "dislike",
            "skipped",
            "completed",
            "liked",
            "unliked",
            "collection_added",
            "track_reviewed",
        }
        return value if value in allowed else "shown"


def _coerce_tracks(values: list[Any]) -> list[Track]:
    result = []
    for item in values:
        try:
            result.append(item if isinstance(item, Track) else Track.from_dict(item))
        except Exception:
            continue
    return result


def _uploader_key(track: Track) -> str:
    return str(track.owner_mid or track.owner or "")


def _candidate_owner_mid(candidate: RecommendationCandidate) -> int | None:
    value = candidate.track.get("ownerMid")
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _candidate_text(candidate: RecommendationCandidate) -> str:
    track = candidate.track
    values = [
        track.get("title"),
        track.get("pageTitle"),
        track.get("owner"),
        candidate.llm_reason,
        " ".join(candidate.profile_signals),
        " ".join(candidate.tags),
    ]
    if candidate.source != "popular_music":
        values.append(candidate.reason)
    return " ".join(str(value) for value in values if value)


def _candidate_artist_keys(candidate: RecommendationCandidate) -> list[str]:
    return matched_artist_names(_candidate_text(candidate))


def _add_score_signal(signals: dict[str, float], name: str, delta: float) -> float:
    value = round(float(delta), 4)
    signals[name] = round(signals.get(name, 0.0) + value, 4)
    return value


def _matched_profile_topics(text: str, values: dict[str, float], *, limit: int = 3) -> list[str]:
    normalized = text.casefold()
    result = [
        topic
        for topic, _weight in sorted(values.items(), key=lambda item: item[1], reverse=True)
        if topic and topic.casefold() in normalized
    ]
    return result[:limit]


def _profile_signal_names(signals: list[str]) -> list[str]:
    result: list[str] = []
    for signal in signals:
        if ":" not in signal:
            continue
        _kind, value = signal.split(":", 1)
        value = value.strip()
        if value:
            result.append(value)
    return list(dict.fromkeys(result))


def _clean_agent_reason(value: str) -> str:
    text = re.sub(r"^search\s+intent\s*:\s*", "", value, flags=re.IGNORECASE).strip()
    return text[:80] or "主动搜索候选"


def _candidate_reason(evidence: list[str], penalties: list[str]) -> str:
    clean_evidence = [item for item in evidence if item]
    if clean_evidence:
        return "；".join(clean_evidence[:2])
    clean_penalties = [item for item in penalties if item]
    if clean_penalties:
        return f"已降权后仍保留少量探索：{clean_penalties[0]}"
    return "来自你的播放和收藏记录"


def _candidate_to_trace(candidate: RecommendationCandidate) -> dict[str, Any]:
    track = candidate.track
    return {
        "trackId": track.get("trackId"),
        "bvid": track.get("bvid"),
        "cid": track.get("cid"),
        "title": track.get("title"),
        "owner": track.get("owner"),
        "ownerMid": track.get("ownerMid"),
        "score": round(candidate.score, 4),
        "source": candidate.source,
        "reason": candidate.reason,
        "tags": candidate.tags,
        "llmReason": candidate.llm_reason,
        "profileSignals": candidate.profile_signals,
        "agentTraceId": candidate.agent_trace_id,
        "scoreSignals": candidate.score_signals,
        "matchedPreferences": candidate.matched_preferences,
        "evidence": candidate.evidence,
        "penalties": candidate.penalties,
        "facets": candidate.facets,
        "scopeEvidence": candidate.scope_evidence,
    }


def _profile_version(profile_trace_id: str, profile: MusicProfile) -> str:
    payload = json.dumps(profile.to_dict(), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(f"{profile_trace_id}:{payload}".encode("utf-8")).hexdigest()[:12]
    return f"{profile_trace_id}:{digest}"


def _profile_summary(profile: MusicProfile) -> dict[str, Any]:
    return {
        "topPositiveTopics": _top_score_items(profile.positive_topics),
        "topNegativeTopics": _top_score_items(profile.negative_topics),
        "topUploaders": _top_score_items(profile.preferred_uploaders),
        "topMoods": _top_score_items(profile.mood_weights),
        "strategy": {
            "sameUploaderLimit": profile.same_uploader_limit,
            "explorationRatio": profile.exploration_ratio,
            "confidence": profile.confidence,
            "source": profile.source,
        },
        "evidenceMemoryCount": len(profile.evidence_memory_ids),
    }


def _top_score_items(values: dict[str, float], *, limit: int = 6) -> list[dict[str, Any]]:
    return [
        {"name": key, "weight": value}
        for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _json_loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}
