from __future__ import annotations

import os
import re
from dataclasses import dataclass

from music_profile import MusicProfile
from request_spec import RequestSpec


DEFAULT_SEARCH_BUDGET = 3
TAG_SEARCH_SUFFIX = "音乐"
MOOD_QUERY_MODIFIERS = {
    "calm": ["calm", "chill", "治愈", "轻柔"],
    "chill": ["chill", "calm", "治愈", "轻柔"],
    "relax": ["relax", "chill", "治愈", "轻柔"],
    "relaxed": ["relaxed", "chill", "治愈", "轻柔"],
    "治愈": ["治愈", "轻柔", "calm", "chill"],
    "轻柔": ["轻柔", "治愈", "calm", "chill"],
}
TOPIC_QUERY_ALIASES = {
    "miku": ["初音未来", "Miku"],
    "初音未来": ["初音未来", "Miku"],
    "vocaloid": ["Vocaloid"],
}
_REGION_QUERY_LABELS = {
    "western": "欧美流行 英文",
    "chinese": "华语流行",
    "japanese": "J-Pop 日语",
    "korean": "K-Pop 韩语",
}
_LANGUAGE_QUERY_LABELS = {
    "english": "英文歌",
    "chinese": "中文歌",
    "japanese": "日语歌",
    "korean": "韩语歌",
}
_GENRE_QUERY_LABELS = {"pop": "流行音乐", "rock": "摇滚音乐", "rap": "Rap 说唱音乐", "reggae": "雷鬼 Reggae 音乐", "rnb": "R&B 音乐"}


@dataclass(frozen=True)
class DiscoveryPlan:
    search_queries: list[str]
    trace_id: str
    request_first: bool


class DiscoveryPlanner:
    """Builds bounded search plans. It has no network or recommendation-serving responsibility."""

    def __init__(self, *, search_budget: int | None = None) -> None:
        self.search_budget = search_budget if search_budget is not None else _env_int(
            "RECOMMEND_DISCOVERY_SEARCH_BUDGET", DEFAULT_SEARCH_BUDGET
        )

    def plan(self, *, profile: MusicProfile, request_spec: RequestSpec, scene: str) -> DiscoveryPlan:
        blocked_terms = {topic.casefold() for topic in profile.negative_topics} | {
            topic.casefold() for topic in request_spec.excluded_topics
        }
        request_queries = self._request_queries(request_spec)
        profile_queries = self._profile_queries(profile, blocked_terms)
        queries = list(dict.fromkeys([*request_queries, *profile_queries]))[: max(self.search_budget, 0)]
        trace = f"discovery:{scene}:{abs(hash((tuple(queries), request_spec.raw_text))) % 1000000}"
        return DiscoveryPlan(search_queries=queries, trace_id=trace, request_first=bool(request_queries))

    def _request_queries(self, spec: RequestSpec) -> list[str]:
        labels = [*(_REGION_QUERY_LABELS.get(value, value) for value in spec.required_regions)]
        labels.extend(
            _LANGUAGE_QUERY_LABELS.get(value, value)
            for value in spec.required_languages
            if not (value == "english" and "western" in spec.required_regions)
        )
        labels.extend(
            _GENRE_QUERY_LABELS.get(value, value)
            for value in spec.required_genres
            if not (value == "pop" and "western" in spec.required_regions)
        )
        if spec.required_vocals:
            labels.append("女声")
        if not labels:
            return []
        mood = spec.moods[0] if spec.moods else ""
        return [f"{' '.join(labels)} {mood}".strip()]

    def _profile_queries(self, profile: MusicProfile, blocked_terms: set[str]) -> list[str]:
        intents = [intent for intent in profile.recent_intents if not _contains_blocked(intent, blocked_terms)]
        topics = [
            topic
            for topic, _weight in sorted(profile.positive_topics.items(), key=lambda item: item[1], reverse=True)
            if topic.casefold() not in blocked_terms
        ]
        modifiers = _mood_modifiers(profile)
        for topic in topics:
            for query in _topic_queries(topic, modifiers):
                if not _contains_blocked(query, blocked_terms):
                    intents.append(query)
        if not topics:
            intents.extend(f"{mood} {TAG_SEARCH_SUFFIX}" for mood in profile.mood_weights if mood.casefold() not in blocked_terms)
        return list(dict.fromkeys(item.strip() for item in intents if item.strip()))


def _mood_modifiers(profile: MusicProfile) -> list[str]:
    values = []
    for mood, _weight in sorted(profile.mood_weights.items(), key=lambda item: item[1], reverse=True):
        values.extend(MOOD_QUERY_MODIFIERS.get(mood.strip().casefold(), [mood.strip()]))
    return list(dict.fromkeys(value for value in values if value))


def _topic_queries(topic: str, modifiers: list[str]) -> list[str]:
    labels = TOPIC_QUERY_ALIASES.get(topic.strip().casefold(), [topic.strip()])
    if not modifiers:
        return [f"{labels[0]} {TAG_SEARCH_SUFFIX}"]
    queries = [f"{labels[0]} {modifier}" for modifier in modifiers[:2]]
    if len(labels) > 1:
        queries.append(f"{labels[1]} {modifiers[0]}")
    return list(dict.fromkeys(query for query in queries if query.strip()))


def _contains_blocked(value: str, blocked_terms: set[str]) -> bool:
    normalized = value.casefold()
    return any(_contains_term(normalized, term) for term in blocked_terms if term)


def _contains_term(value: str, term: str) -> bool:
    if term.replace(" ", "").isascii() and term.replace(" ", "").isalnum():
        escaped = r"\s+".join(re.escape(part) for part in term.split())
        return re.search(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", value) is not None
    return term in value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
