from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

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
    ) -> tuple[list[Any], list[Any]]:
        eligible = [
            item
            for item in candidates
            if not hard_filtered(item) and request.request_spec.matches_facets(getattr(item, "facets", {}))
        ]
        eligible.sort(key=lambda item: float(getattr(item, "score", 0)), reverse=True)
        reranked = eligible[: min(len(eligible), max(request.limit * 4, 16))]
        selected = diversity(select(eligible))
        if len(selected) < request.limit:
            selected_ids = {
                str((getattr(item, "track", {}) or {}).get("trackId") or "")
                for item in selected
            }
            selected = diversity(
                [
                    *selected,
                    *[
                        item
                        for item in eligible
                        if str((getattr(item, "track", {}) or {}).get("trackId") or "") not in selected_ids
                    ],
                ]
            )
        return reranked, self._lexical_mmr(selected, request.limit)

    @staticmethod
    def _lexical_mmr(candidates: list[Any], limit: int) -> list[Any]:
        """Deterministic diversity pass until an embedding-based MMR is introduced."""
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
