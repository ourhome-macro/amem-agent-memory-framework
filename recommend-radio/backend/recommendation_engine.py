from __future__ import annotations

import re
import os
import math
import time
from dataclasses import dataclass
from typing import Any, Callable
import requests

from request_spec import RequestSpec


@dataclass(frozen=True)
class RecommendationRequest:
    scene: str
    limit: int
    request_spec: RequestSpec
    profile: Any
    exclude_track_ids: set[str]
    recent_context: dict[str, Any]


class RecommendationEngine:
    """Serving-only engine: scope filtering, score ordering, diversity and final selection."""

    def rank_and_select(
        self,
        candidates: list[Any],
        *,
        request: RecommendationRequest,
        hard_filtered: Callable[[Any], bool],
        select: Callable[[list[Any]], list[Any]],
        diversity: Callable[[list[Any]], list[Any]],
    ) -> tuple[list[Any], list[Any], dict[str, Any]]:
        eligible = [
            item
            for item in candidates
            if not hard_filtered(item) and request.request_spec.matches_facets(getattr(item, "facets", {}))
        ]
        eligible.sort(key=lambda item: float(getattr(item, "score", 0)), reverse=True)
        reranked = eligible[: min(len(eligible), max(request.limit * 4, 16))]
        mmr_pool = eligible[: min(len(eligible), max(request.limit * 2, 16))]
        mmr_ranked, diagnostics = self._vector_mmr(
            mmr_pool,
            request,
            output_limit=min(len(mmr_pool), max(request.limit * 4, 16)),
        )
        selected = diversity(select(mmr_ranked))
        if len(selected) < request.limit:
            selected_ids = {
                str((getattr(item, "track", {}) or {}).get("trackId") or "")
                for item in selected
            }
            fill_pool = list(selected)
            for item in [*mmr_ranked, *eligible]:
                track_id = str((getattr(item, "track", {}) or {}).get("trackId") or "")
                if not track_id or track_id in selected_ids:
                    continue
                selected_ids.add(track_id)
                fill_pool.append(item)
            selected = diversity(fill_pool)
        return reranked, selected[: request.limit], diagnostics

    def _vector_mmr(
        self,
        candidates: list[Any],
        request: RecommendationRequest,
        *,
        output_limit: int,
    ) -> tuple[list[Any], dict[str, Any]]:
        started = time.perf_counter()
        if len(candidates) <= 1:
            return candidates[:output_limit], {"mode": "none", "candidateCount": len(candidates), "embeddingMs": 0.0}
        base_url = os.getenv("AMEM_EMBEDDING_BASE_URL", "").rstrip("/")
        model = os.getenv("AMEM_EMBEDDING_MODEL", "bge-m3")
        timeout_seconds = _env_float("AMEM_EMBEDDING_TIMEOUT_SECONDS", 15.0)
        if not base_url:
            return self._lexical_mmr(candidates, output_limit), {
                "mode": "lexical_fallback",
                "candidateCount": len(candidates),
                "embeddingMs": 0.0,
                "reason": "embedding_base_url_missing",
            }
        profile = request.profile
        query = " ".join(
            item
            for item in [
                request.request_spec.raw_text,
                " ".join(getattr(profile, "positive_topics", {}).keys()),
                " ".join(getattr(profile, "mood_weights", {}).keys()),
                getattr(profile, "music_persona", ""),
                getattr(profile, "current_music_phase", ""),
                " ".join(getattr(profile, "core_traits", [])),
            ]
            if item
        ) or "personalized music recommendation"
        negative_query = " ".join(
            [
                " ".join(getattr(profile, "negative_topics", {}).keys()),
                " ".join(getattr(profile, "negative_interest_texts", [])),
                " ".join(str(item) for item in request.recent_context.get("negativeSamples", [])[:12]),
            ]
        ).strip()
        texts = [
            f"{str(item.track.get('title', ''))[:180]} {str(item.track.get('owner', ''))[:80]}"
            for item in candidates
        ]
        embedding_inputs = [query, *([negative_query] if negative_query else []), *texts]
        try:
            response = requests.post(
                f"{base_url}/embeddings",
                json={"model": model, "input": embedding_inputs},
                headers={"Authorization": f"Bearer {os.getenv('BGE_M3_API_KEY', 'local-embedding')}"},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            vectors = [item["embedding"] for item in sorted(response.json()["data"], key=lambda item: item["index"])]
            if len(vectors) != len(embedding_inputs):
                raise ValueError("embedding count mismatch")
        except Exception as exc:
            return self._lexical_mmr(candidates, output_limit), {
                "mode": "lexical_fallback",
                "candidateCount": len(candidates),
                "negativeSampleCount": len(request.recent_context.get("negativeSamples", [])),
                "embeddingMs": round((time.perf_counter() - started) * 1000, 2),
                "reason": type(exc).__name__,
            }
        query_vector = vectors[0]
        negative_vector = vectors[1] if negative_query else None
        document_vectors = vectors[2:] if negative_query else vectors[1:]
        pending = list(range(len(candidates)))
        selected: list[int] = []
        scores = [float(getattr(item, "score", 0.0)) for item in candidates]
        low, high = min(scores), max(scores)
        while pending and len(selected) < output_limit:
            def mmr(index: int) -> float:
                relevance = 0.5 if high == low else (scores[index] - low) / (high - low)
                query_similarity = _cosine(query_vector, document_vectors[index])
                negative_similarity = (
                    max(_cosine(negative_vector, document_vectors[index]), 0.0)
                    if negative_vector is not None
                    else 0.0
                )
                diversity = max((_cosine(document_vectors[index], document_vectors[other]) for other in selected), default=0.0)
                return 0.45 * relevance + 0.35 * query_similarity - 0.20 * diversity - 0.20 * negative_similarity
            best = max(pending, key=mmr)
            pending.remove(best)
            selected.append(best)
        return [candidates[index] for index in selected], {
            "mode": "bge_m3_vector",
            "model": model,
            "candidateCount": len(candidates),
            "negativeSampleCount": len(request.recent_context.get("negativeSamples", [])),
            "embeddingMs": round((time.perf_counter() - started) * 1000, 2),
        }

    @staticmethod
    def _lexical_mmr(candidates: list[Any], limit: int) -> list[Any]:
        """Deterministic fallback used only when bge-m3 is unavailable."""
        pending = list(candidates)
        selected: list[Any] = []
        while pending and len(selected) < limit:
            best = max(pending, key=lambda item: RecommendationEngine._mmr_score(item, selected))
            pending.remove(best)
            selected.append(best)
        return selected

    @staticmethod
    def _mmr_score(candidate: Any, selected: list[Any]) -> float:
        relevance = float(getattr(candidate, "score", 0.0))
        if not selected:
            return relevance
        similarity = max(_jaccard(_tokens(candidate), _tokens(other)) for other in selected)
        return relevance - 0.35 * similarity


def _tokens(candidate: Any) -> set[str]:
    track = getattr(candidate, "track", {}) or {}
    text = " ".join(
        str(value or "")
        for value in (track.get("title"), track.get("owner"), " ".join(getattr(candidate, "tags", [])))
    ).casefold()
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{1,3}", text))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    return dot / max(left_norm * right_norm, 1e-12)


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, str(default))), 0.1)
    except ValueError:
        return default
