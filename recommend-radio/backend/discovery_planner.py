from __future__ import annotations

import os
import re
from dataclasses import dataclass

from music_profile import MusicProfile
from request_spec import RequestSpec


DEFAULT_SEARCH_BUDGET = 8
TAG_SEARCH_SUFFIX = "音乐"
DEFAULT_FALLBACK_QUERIES = (
    "欧美流行 英文 音乐",
    "R&B 音乐",
    "华语流行 音乐",
    "摇滚音乐",
)
ADJACENT_EXPLORATION_QUERIES = {
    "r&b": ("Neo Soul 音乐", "Funk Soul 音乐"),
    "rnb": ("Neo Soul 音乐", "Funk Soul 音乐"),
    "摇滚": ("Blues Rock 音乐", "Indie Rock 音乐"),
    "布鲁斯摇滚": ("Classic Blues 音乐", "Soul Rock 音乐"),
    "华语流行": ("华语独立流行 音乐", "华语城市流行 City Pop"),
    "欧美流行": ("欧美 Indie Pop 音乐", "欧美 Synth Pop 音乐"),
    "rap": ("Jazz Rap 音乐", "Melodic Rap 音乐"),
    "雷鬼": ("Ska 音乐", "Dub Reggae 音乐"),
    "reggae": ("Ska 音乐", "Dub Reggae 音乐"),
}
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
    negative_queries: list[str]
    keyword_specs: dict[str, dict[str, object]]
    negative_keyword_specs: dict[str, dict[str, object]]
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
        exploration_queries = self._exploration_queries(profile, blocked_terms)
        fallback_queries: list[str] = []
        if not request_queries and not profile_queries:
            fallback_queries = [
                query for query in DEFAULT_FALLBACK_QUERIES if not _contains_blocked(query, blocked_terms)
            ]
        candidate_limit = max(self.search_budget * 8, 16)
        profile_head = max(self.search_budget * 2, 4)
        source_queries = (
            request_queries
            if request_spec.constrained
            else [
                *profile_queries[:profile_head],
                *exploration_queries,
                *profile_queries[profile_head:],
                *fallback_queries,
            ]
        )
        queries = list(dict.fromkeys(source_queries))[:candidate_limit]
        trace = f"discovery:{scene}:{abs(hash((tuple(queries), request_spec.raw_text))) % 1000000}"
        negative_queries = [] if request_spec.constrained else [f"{topic} {TAG_SEARCH_SUFFIX}" for topic, _ in sorted(profile.negative_topics.items(), key=lambda item: item[1], reverse=True)[:1]]
        request_spec_family = _request_family_spec(request_spec)
        keyword_specs = {
            query: (
                dict(
                    request_spec_family,
                    exploration_axis="request",
                    keyword_kind="probe",
                    origin="request",
                )
                if query in request_queries
                else dict(
                    _infer_family_spec(
                        query,
                        exploration_axis=(
                            "adjacent_genre"
                            if query in exploration_queries
                            else "cold_start" if query in fallback_queries else "profile"
                        ),
                    ),
                    keyword_kind=("anchor" if query in profile_queries else "probe"),
                    origin=(
                        "l3_profile"
                        if query in profile_queries
                        else "adjacent_genre" if query in exploration_queries else "cold_start"
                    ),
                )
            )
            for query in queries
        }
        negative_keyword_specs = {
            query: dict(
                _infer_family_spec(query, exploration_axis="negative_probe"),
                keyword_kind="probe",
                origin="negative_probe",
            )
            for query in negative_queries
        }
        return DiscoveryPlan(
            search_queries=queries,
            negative_queries=negative_queries,
            keyword_specs=keyword_specs,
            negative_keyword_specs=negative_keyword_specs,
            trace_id=trace,
            request_first=bool(request_queries),
        )

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
        queries = [f"{' '.join(labels)} {mood}".strip()]
        if spec.required_genres:
            genre = spec.required_genres[0]
            genre_label = _GENRE_QUERY_LABELS.get(genre, genre)
            if "english" in spec.required_languages or "western" in spec.required_regions:
                queries.extend(
                    [
                        f"英文 {genre_label} 歌曲",
                        f"English {genre} music playlist",
                    ]
                )
            else:
                queries.append(f"{genre_label} 歌曲 playlist")
        return list(dict.fromkeys(query for query in queries if query))

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

    @staticmethod
    def _exploration_queries(profile: MusicProfile, blocked_terms: set[str]) -> list[str]:
        result: list[str] = []
        for topic, _weight in sorted(profile.positive_topics.items(), key=lambda item: item[1], reverse=True)[:4]:
            for query in ADJACENT_EXPLORATION_QUERIES.get(topic.strip().casefold(), ()):
                if not _contains_blocked(query, blocked_terms):
                    result.append(query)
        return list(dict.fromkeys(result))


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


def _request_family_spec(spec: RequestSpec) -> dict[str, object]:
    return {
        "genres": sorted(spec.required_genres),
        "languages": sorted(spec.required_languages),
        "regions": sorted(spec.required_regions),
        "vocals": sorted(spec.required_vocals),
        "moods": sorted(spec.moods),
        "excluded_languages": sorted(spec.excluded_languages),
        "excluded_topics": sorted(spec.excluded_topics),
    }


def _infer_family_spec(query: str, *, exploration_axis: str) -> dict[str, object]:
    normalized = query.casefold()
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
    genres = []
    for genre, terms in {
        "rnb": ("r&b", "rnb", "节奏布鲁斯"),
        "rock": ("rock", "摇滚"),
        "rap": ("rap", "hip-hop", "hiphop", "说唱"),
        "reggae": ("reggae", "雷鬼", "dub", "ska"),
        "pop": ("pop", "流行", "city pop"),
        "soul": ("soul", "灵魂乐"),
        "funk": ("funk",),
    }.items():
        if any(term in normalized for term in terms):
            genres.append(genre)
    languages = []
    for language, terms in {
        "english": ("英文", "english", "欧美"),
        "chinese": ("中文", "华语", "国语"),
        "japanese": ("日语", "日文", "j-pop", "jpop"),
        "korean": ("韩语", "韩文", "k-pop", "kpop"),
    }.items():
        if any(term in normalized for term in terms):
            languages.append(language)
    moods = [
        mood
        for mood in ("安静", "抒情", "治愈", "放松", "热血", "轻柔", "calm", "chill")
        if mood in normalized
    ]
    known = bool(genres or subgenres or languages or moods)
    return {
        "genres": sorted(set(genres)),
        "subgenres": sorted(set(subgenres)),
        "languages": sorted(set(languages)),
        "moods": sorted(set(moods)),
        "topic": "" if known else re.sub(r"\s+", " ", normalized).strip()[:120],
        "exploration_axis": exploration_axis,
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
