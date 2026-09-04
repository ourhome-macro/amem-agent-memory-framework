from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import grpc
from music_profile import MusicProfile, RelevantMemory
from profile_projector import ProfileProjection
from dataclasses import replace

_GEN_DIR = Path(__file__).resolve().parent / "amem_gen"
if str(_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GEN_DIR))

from amem.v1 import amem_pb2, amem_pb2_grpc  # noqa: E402


class AmemGrpcBridge:
    enabled = True

    def __init__(self, addr: str, *, timeout_seconds: float = 10.0) -> None:
        self.addr = addr.strip() or "127.0.0.1:9090"
        self.timeout_seconds = timeout_seconds if timeout_seconds > 0 else 10.0
        self.channel = grpc.insecure_channel(self.addr)
        self.client = amem_pb2_grpc.AmemServiceStub(self.channel)
        self.last_profile_timing: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "AmemGrpcBridge":
        return cls(
            os.getenv("AMEM_GRPC_ADDR", "127.0.0.1:9090"),
            timeout_seconds=_timeout_seconds(),
        )

    def health(self) -> str:
        response = self.client.Health(
            amem_pb2.HealthRequest(),
            timeout=self.timeout_seconds,
        )
        return str(response.status or "")

    def record_behavior(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = dict(payload or {})
        event_id = str(values.get("event_id") or values.get("eventId") or uuid4())
        response = self.client.RecordBehavior(
            amem_pb2.RecordBehaviorRequest(
                event_id=event_id,
                user_id=_string_from(values, "userId", "user_id") or "legacy-owner",
                event=_string_from(values, "event") or "shown",
                scene=_string_from(values, "scene") or "home",
                track_id=_string_from(values, "trackId", "track_id"),
                payload_json=json.dumps(values, ensure_ascii=False).encode("utf-8"),
            ),
            timeout=self.timeout_seconds,
        )
        return {
            "enabled": True,
            "eventId": response.amem_event_id,
            "memoryIds": list(response.memory_ids),
            "source": "amem-grpc",
        }

    def record_profile_statement(
        self,
        *,
        user_id: str,
        description: str,
        profile: Any,
        source: str,
    ) -> dict[str, Any]:
        profile_dict = profile.to_dict() if hasattr(profile, "to_dict") else dict(profile or {})
        response = self.client.RecordProfileStatement(
            amem_pb2.RecordProfileStatementRequest(
                user_id=str(user_id or "legacy-owner"),
                scene="conversation",
                description=str(description or ""),
                profile_json=json.dumps(profile_dict, ensure_ascii=False).encode("utf-8"),
                source=str(source or "python-backend"),
            ),
            timeout=self.timeout_seconds,
        )
        return {
            "enabled": True,
            "eventId": response.amem_event_id,
            "memoryIds": list(response.memory_ids),
            "source": "amem-grpc",
        }

    def promote_music_profile(
        self,
        *,
        user_id: str,
        profile: Any,
        support_counts: dict[str, int],
    ) -> dict[str, Any]:
        profile_dict = profile.to_dict() if hasattr(profile, "to_dict") else dict(profile or {})
        response = self.client.RecordProfileStatement(
            amem_pb2.RecordProfileStatementRequest(
                user_id=str(user_id or "legacy-owner"),
                scene="conversation",
                description=json.dumps({"supportCounts": support_counts}, ensure_ascii=False),
                profile_json=json.dumps(profile_dict, ensure_ascii=False).encode("utf-8"),
                source="event_l3",
            ),
            timeout=self.timeout_seconds,
        )
        return {
            "enabled": True,
            "eventId": response.amem_event_id,
            "memoryIds": list(response.memory_ids),
            "source": "amem-grpc",
        }

    def get_music_profile(self, *, user_id: str, scene: str) -> MusicProfile:
        response = self.client.GetMusicProfile(
            amem_pb2.GetMusicProfileRequest(
                user_id=str(user_id or "legacy-owner"),
                scene=str(scene or "music_recommendation"),
            ),
            timeout=self.timeout_seconds,
        )
        self.last_profile_timing = {
            "profileLlmApiMs": float(getattr(response, "profile_llm_api_ms", 0.0)),
            "profileGrpcTotalMs": float(getattr(response, "profile_total_ms", 0.0)),
        }
        return _profile_from_response(response)

    def retrieve_memories(self, user_id: str, scene: str, *, limit: int = 12) -> list[RelevantMemory]:
        profile = self.get_music_profile(user_id=user_id, scene=scene)
        return _evidence_memories(profile, limit=limit)


class GrpcProfileProjector:
    def __init__(self, bridge: AmemGrpcBridge, *, ttl_seconds: int | None = None) -> None:
        self.bridge = bridge
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else _env_int(
            "RECOMMEND_PROFILE_TTL_SECONDS",
            600,
        )
        self._cache: dict[tuple[str, str], tuple[float, ProfileProjection]] = {}

    def clear_cache(self, user_id: str | None = None, scene: str | None = None) -> None:
        if user_id is None and scene is None:
            self._cache.clear()
            return
        for key in list(self._cache):
            key_user, key_scene = key
            if user_id is not None and key_user != user_id:
                continue
            if scene is not None and key_scene != scene:
                continue
            self._cache.pop(key, None)

    def project(
        self,
        *,
        user_id: str,
        scene: str,
        fallback_profile: MusicProfile,
    ) -> ProfileProjection:
        cache_key = (user_id, scene)
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < self.ttl_seconds:
            return replace(cached[1], llm_latency_ms=0.0, cache_hit=True)

        try:
            profile = self.bridge.get_music_profile(user_id=user_id, scene=scene)
        except Exception:
            profile = MusicProfile.from_dict(fallback_profile.to_dict(), source="grpc_unavailable")
        memories = _evidence_memories(profile, limit=16)
        projection = ProfileProjection(
            profile=profile,
            memories=memories,
            trace_id=f"profile:{user_id}:{scene}:{int(time.time())}:amem-grpc",
            llm_latency_ms=float(self.bridge.last_profile_timing.get("profileLlmApiMs", 0.0)),
        )
        self._cache[cache_key] = (time.time(), projection)
        return projection


def _profile_from_response(response: Any) -> MusicProfile:
    value = {
        "positive_topics": dict(response.positive_topics),
        "negative_topics": dict(response.negative_topics),
        "preferred_uploaders": dict(response.preferred_uploaders),
        "avoid_uploaders": dict(response.avoid_uploaders),
        "blocked_uploaders": dict(response.blocked_uploaders),
        "mood_weights": dict(response.mood_weights),
        "recent_intents": list(response.recent_intents),
        "positive_interest_texts": list(response.positive_interest_texts),
        "negative_interest_texts": list(response.negative_interest_texts),
        "same_uploader_limit": int(response.same_uploader_limit or 0),
        "exploration_ratio": float(response.exploration_ratio or 0.0),
        "evidence_memory_ids": list(response.evidence_memory_ids),
        "confidence": float(response.confidence or 0.0),
    }
    return MusicProfile.from_dict(value, source=str(response.source or "amem-grpc"))


def _evidence_memories(profile: MusicProfile, *, limit: int) -> list[RelevantMemory]:
    memories: list[RelevantMemory] = []
    for memory_id in profile.evidence_memory_ids[:limit]:
        memories.append(
            RelevantMemory(
                memory_id=memory_id,
                content="AMEM gRPC profile evidence",
                layer="",
                memory_type="evidence",
                tags=("music", "recommend-radio", "amem-grpc"),
                salience=profile.confidence,
                confidence=profile.confidence,
                metadata={"source": "amem-grpc"},
            )
        )
    return memories


def _string_from(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _timeout_seconds() -> float:
    raw = os.getenv("AMEM_GRPC_TIMEOUT_SECONDS") or os.getenv("AMEM_TIMEOUT") or "10"
    value = raw.strip().lower()
    if value.endswith("s"):
        value = value[:-1]
    try:
        return float(value)
    except ValueError:
        return 10.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
