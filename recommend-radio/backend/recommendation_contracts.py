from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from models import Track
from music_keyword_pool import matched_artist_names
from music_profile import MusicProfile


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

MEMORY_EVIDENCE_EVENTS = {
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

PROFILE_LIFECYCLE_EVENTS = {
    "played",
    "accepted",
    "dismissed",
    "dislike",
    "skipped",
    "completed",
    "liked",
    "collection_added",
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
    recently_heard_recording_ids: set[str] = field(default_factory=set)
    recently_recommended_work_ids: set[str] = field(default_factory=set)
    skipped_recording_ids: set[str] = field(default_factory=set)


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
    source_keyword_ids: set[str] = field(default_factory=set)
    source_keyword_family_ids: set[str] = field(default_factory=set)
    source_discovery_job_ids: set[str] = field(default_factory=set)
    text_embedding: list[float] = field(default_factory=list)
    audio_embedding: list[float] = field(default_factory=list)


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
    source_keyword_ids: list[str] = field(default_factory=list)
    source_keyword_family_ids: list[str] = field(default_factory=list)
    source_discovery_job_ids: list[str] = field(default_factory=list)
    recommendation_trace_id: str = ""
    text_embedding: list[float] = field(default_factory=list, repr=False)
    audio_embedding: list[float] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "track": self.track,
            "score": round(self.score, 2),
            "source": self.source,
            "reason": self.reason,
            "sourceKeywordIds": self.source_keyword_ids,
            "sourceKeywordFamilyIds": self.source_keyword_family_ids,
            "sourceDiscoveryJobIds": self.source_discovery_job_ids,
            "recommendationTraceId": self.recommendation_trace_id,
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
        "workId": track.get("workId"),
        "recordingId": track.get("recordingId"),
        "canonicalTitle": track.get("canonicalTitle"),
        "canonicalArtist": track.get("canonicalArtist"),
        "versionType": track.get("versionType"),
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
        "sourceKeywordIds": candidate.source_keyword_ids,
        "sourceKeywordFamilyIds": candidate.source_keyword_family_ids,
        "sourceDiscoveryJobIds": candidate.source_discovery_job_ids,
        "recommendationTraceId": candidate.recommendation_trace_id,
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


def _payload_string_list(payload: dict[str, Any], *keys: str, limit: int) -> list[str]:
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        return [str(item) for item in value if str(item).strip()][:limit]
    return []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}
