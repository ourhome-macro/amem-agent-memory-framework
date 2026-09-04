from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


_REGION_ALIASES = {
    "western": ("欧美", "欧美流行", "西洋", "western pop", "english pop"),
    "chinese": ("华语", "中文", "国语", "华语流行", "mandopop"),
    "japanese": ("日语", "日文", "j-pop", "jpop", "日系"),
    "korean": ("韩语", "韩文", "k-pop", "kpop", "韩流"),
}
_LANGUAGE_ALIASES = {
    "english": ("英文", "英语", "english"),
    "chinese": ("中文", "华语", "国语"),
    "japanese": ("日语", "日文"),
    "korean": ("韩语", "韩文"),
}
_MOOD_ALIASES = {
    "安静": ("安静", "轻一点", "别太吵", "低打扰"),
    "放松": ("放松", "舒缓", "chill", "relax"),
    "温柔": ("温柔", "治愈", "慰藉"),
}
_VOCAL_ALIASES = {"female": ("女声", "女歌手", "女生唱")}
_GENRE_ALIASES = {
    "pop": ("流行", "pop"),
    "rock": ("摇滚", "rock"),
    "rap": ("rap", "说唱", "嘻哈", "hiphop", "hip-hop"),
    "reggae": ("雷鬼", "reggae"),
    "rnb": ("rnb", "r&b", "节奏布鲁斯"),
}
_NEGATION_PREFIX = r"(?:不要|不想|别|避开|少来点|不听)"


@dataclass(frozen=True)
class RequestSpec:
    """Ephemeral, user-authored constraints for exactly one recommendation request."""

    raw_text: str = ""
    required_regions: tuple[str, ...] = ()
    required_languages: tuple[str, ...] = ()
    excluded_languages: tuple[str, ...] = ()
    required_vocals: tuple[str, ...] = ()
    required_genres: tuple[str, ...] = ()
    moods: tuple[str, ...] = ()
    excluded_topics: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def constrained(self) -> bool:
        return bool(
            self.required_regions
            or self.required_languages
            or self.excluded_languages
            or self.required_vocals
            or self.required_genres
            or self.excluded_topics
        )

    @property
    def primary_label(self) -> str:
        labels = {
            "western": "欧美流行",
            "chinese": "华语流行",
            "japanese": "J-Pop",
            "korean": "K-Pop",
            "english": "英文",
            "female": "女声",
        }
        values = [
            *(labels.get(value, value) for value in self.required_regions),
            *(labels.get(value, value) for value in self.required_languages),
            *(labels.get(value, value) for value in self.required_vocals),
            *(labels.get(value, value) for value in self.required_genres),
            *self.moods,
        ]
        return "、".join(_dedupe(values)[:3]) or "这轮"

    def to_dict(self) -> dict[str, object]:
        return {
            "rawText": self.raw_text,
            "requiredRegions": list(self.required_regions),
            "requiredLanguages": list(self.required_languages),
            "excludedLanguages": list(self.excluded_languages),
            "requiredVocals": list(self.required_vocals),
            "requiredGenres": list(self.required_genres),
            "moods": list(self.moods),
            "excludedTopics": list(self.excluded_topics),
            "evidence": list(self.evidence),
            "scope": "request",
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RequestSpec":
        def strings(key: str) -> tuple[str, ...]:
            raw = value.get(key) or []
            return tuple(str(item) for item in raw if str(item).strip()) if isinstance(raw, list) else ()
        return cls(
            raw_text=str(value.get("rawText") or "")[:240],
            required_regions=strings("requiredRegions"),
            required_languages=strings("requiredLanguages"),
            excluded_languages=strings("excludedLanguages"),
            required_vocals=strings("requiredVocals"),
            required_genres=strings("requiredGenres"),
            moods=strings("moods"),
            excluded_topics=strings("excludedTopics"),
            evidence=strings("evidence"),
        )

    def matches_facets(self, facets: dict[str, object]) -> bool:
        values = {
            key: {str(item) for item in (facets.get(key) or [])}
            for key in ("regions", "languages", "vocals", "topics", "genres")
        }
        if self.required_regions and not set(self.required_regions).issubset(values["regions"]):
            return False
        if self.required_languages and not set(self.required_languages).issubset(values["languages"]):
            return False
        if set(self.excluded_languages) & values["languages"]:
            return False
        if self.required_vocals and not set(self.required_vocals).issubset(values["vocals"]):
            return False
        if self.required_genres and not set(self.required_genres).issubset(values["genres"]):
            return False
        if set(self.excluded_topics) & values["topics"]:
            return False
        return True


class RequestInterpreter:
    """Rule-based interpreter for executable, non-persistent request constraints."""

    def interpret(self, message: str) -> RequestSpec:
        text = " ".join((message or "").strip().split())
        normalized = text.casefold()
        required_regions = _positive_matches(normalized, _REGION_ALIASES)
        required_languages = _positive_matches(normalized, _LANGUAGE_ALIASES)
        excluded_languages = _negative_matches(normalized, _LANGUAGE_ALIASES)
        required_vocals = _positive_matches(normalized, _VOCAL_ALIASES)
        required_genres = _positive_matches(normalized, _GENRE_ALIASES)
        if "western" in required_regions and "english" not in required_languages:
            required_languages.append("english")
        moods = _positive_matches(normalized, _MOOD_ALIASES)
        excluded_topics = _negative_topics(normalized)
        evidence = [
            *[f"required_region:{value}" for value in required_regions],
            *[f"required_language:{value}" for value in required_languages],
            *[f"excluded_language:{value}" for value in excluded_languages],
            *[f"required_vocal:{value}" for value in required_vocals],
            *[f"required_genre:{value}" for value in required_genres],
            *[f"mood:{value}" for value in moods],
            *[f"excluded_topic:{value}" for value in excluded_topics],
        ]
        return RequestSpec(
            raw_text=text[:240],
            required_regions=tuple(required_regions),
            required_languages=tuple(required_languages),
            excluded_languages=tuple(excluded_languages),
            required_vocals=tuple(required_vocals),
            required_genres=tuple(required_genres),
            moods=tuple(moods),
            excluded_topics=tuple(excluded_topics),
            evidence=tuple(evidence),
        )


def _positive_matches(text: str, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        name
        for name, terms in aliases.items()
        if any(term in text and not re.search(_NEGATION_PREFIX + r"\s*" + re.escape(term), text) for term in terms)
    ]


def _negative_matches(text: str, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        name
        for name, terms in aliases.items()
        if any(re.search(_NEGATION_PREFIX + r"\s*" + re.escape(term), text) for term in terms)
    ]


def _negative_topics(text: str) -> list[str]:
    values = []
    for topic in ("rap", "说唱", "摇滚", "电音"):
        if re.search(_NEGATION_PREFIX + r"\s*" + re.escape(topic), text):
            values.append(topic)
    return values


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
