from __future__ import annotations

import json
from typing import Any

from music_profile import MusicProfile
from profile_projector import _default_llm_client, _parse_json_object


DEFAULT_STATEMENT_CONFIDENCE = 0.86


class ProfileStatementService:
    def __init__(self, amem_bridge: Any, *, llm_client: Any | None = None, enabled: bool = True) -> None:
        self.amem_bridge = amem_bridge
        self.llm_client = llm_client
        self.enabled = enabled

    def submit(self, *, user_id: str, description: str) -> dict[str, Any]:
        normalized = (description or "").strip()
        if len(normalized) < 4:
            raise ValueError("profile description is too short")
        if len(normalized) > 2000:
            raise ValueError("profile description is too long")

        profile, source = self._extract(normalized)
        result = self.amem_bridge.record_profile_statement(
            user_id=user_id,
            description=normalized,
            profile=profile,
            source=source,
        )
        return {
            "description": normalized,
            "profile": profile.to_dict(),
            "source": source,
            "eventId": result.get("eventId"),
            "memoryIds": result.get("memoryIds") or [],
        }

    def _extract(self, description: str) -> tuple[MusicProfile, str]:
        rule_profile = _extract_with_rules(description)
        if self.enabled:
            try:
                profile = self._extract_with_llm(description)
                _apply_rule_guardrails(profile, rule_profile)
                return profile, "llm+rules"
            except Exception:
                pass
        return rule_profile, "rules"

    def _extract_with_llm(self, description: str) -> MusicProfile:
        client = self.llm_client or _default_llm_client()
        system_prompt = (
            "Extract a structured music profile from a user's free-form self description. "
            "Return exactly one JSON object. No markdown. "
            "Use artist names, genres, language scenes, and music cultures as positive_topics. "
            "Use disliked genres/topics as negative_topics. "
            "Do not invent songs. Do not put negative items in positive_topics."
        )
        user_prompt = json.dumps(
            {
                "description": description,
                "output_schema": {
                    "positive_topics": {"topic_or_artist": 0.0},
                    "negative_topics": {"topic_or_genre": 0.0},
                    "preferred_uploaders": {},
                    "avoid_uploaders": {},
                    "blocked_uploaders": {},
                    "mood_weights": {"mood": 0.0},
                    "recent_intents": ["search intent"],
                    "same_uploader_limit": 0,
                    "exploration_ratio": 0.0,
                    "evidence_memory_ids": [],
                    "confidence": 0.0,
                },
                "examples": {
                    "华语流行音乐忠实粉丝，喜欢 RnB，不喜欢 Rap": {
                        "positive_topics": {"华语流行": 0.9, "R&B": 0.85},
                        "negative_topics": {"Rap": 0.85},
                    }
                },
            },
            ensure_ascii=False,
        )
        parsed = _parse_json_object(client.complete(system_prompt=system_prompt, user_prompt=user_prompt).content)
        profile = MusicProfile.from_dict(parsed, source="statement")
        _normalize_statement_profile(profile)
        if not _has_statement_profile_signals(profile):
            raise ValueError("empty profile statement extraction")
        return profile


def _extract_with_rules(description: str) -> MusicProfile:
    text = description.casefold()
    positive: dict[str, float] = {}
    negative: dict[str, float] = {}
    moods: dict[str, float] = {}

    for topic, patterns in {
        "华语流行": ("华语流行", "华语", "中文歌", "国语"),
        "粤语": ("粤语", "港乐"),
        "R&B": ("rnb", "r&b", "节奏布鲁斯"),
        "欧美流行": ("欧美流行", "欧美", "western pop", "english pop"),
        "摇滚": ("摇滚", "rock"),
        "J-Pop": ("j-pop", "jpop", "日语"),
        "K-Pop": ("k-pop", "kpop", "韩语"),
        "Vocaloid": ("vocaloid", "初音未来", "术力口"),
    }.items():
        if any(pattern in text for pattern in patterns):
            positive[topic] = max(positive.get(topic, 0.0), DEFAULT_STATEMENT_CONFIDENCE)

    for artist in _extract_artist_names(description):
        positive[artist] = max(positive.get(artist, 0.0), 0.84)

    for topic, patterns in {
        "Rap": ("不喜欢rap", "不喜欢 rap", "讨厌rap", "讨厌 rap", "不爱rap", "不爱 rap", "说唱不喜欢"),
        "摇滚": ("不喜欢摇滚", "讨厌摇滚"),
        "电子": ("不喜欢电子", "讨厌电子"),
    }.items():
        if any(pattern in text for pattern in patterns):
            negative[topic] = max(negative.get(topic, 0.0), 0.86)
            positive.pop(topic, None)

    for mood, patterns in {
        "温柔": ("温柔",),
        "抒情": ("抒情", "慢歌", "情歌"),
        "治愈": ("治愈", "低打扰", "少干扰"),
        "热血": ("热血",),
        "放松": ("放松", "舒缓", "累", "疲惫", "压力", "高负荷", "缓一缓"),
        "安静": ("安静", "轻一点", "别太吵"),
        "专注": ("专注", "面试", "考试", "复习", "刷题", "写代码", "工作", "加班"),
        "轻律动": ("夜跑", "跑步", "健身", "运动", "散步"),
    }.items():
        if any(pattern in text for pattern in patterns):
            moods[mood] = 0.74

    return MusicProfile(
        positive_topics=positive,
        negative_topics=negative,
        mood_weights=moods,
        confidence=0.78 if positive or negative or moods else 0.0,
        source="statement",
    )


def _extract_artist_names(description: str) -> list[str]:
    known_artists = [
        "周杰伦",
        "王力宏",
        "陶喆",
        "林俊杰",
        "张惠妹",
        "孙燕姿",
        "陈奕迅",
        "蔡依林",
        "五月天",
        "王菲",
    ]
    result = [artist for artist in known_artists if artist in description]
    if "周王陶林" in description:
        result.extend(["周杰伦", "王力宏", "陶喆", "林俊杰"])
    return list(dict.fromkeys(result))


def _normalize_statement_profile(profile: MusicProfile) -> None:
    normalized_positive: dict[str, float] = {}
    for topic, weight in profile.positive_topics.items():
        key = _normalize_topic_name(topic)
        if key:
            normalized_positive[key] = max(normalized_positive.get(key, 0.0), weight)
    normalized_negative: dict[str, float] = {}
    for topic, weight in profile.negative_topics.items():
        key = _normalize_topic_name(topic)
        if key:
            normalized_negative[key] = max(normalized_negative.get(key, 0.0), weight)
            normalized_positive.pop(key, None)
    profile.positive_topics = normalized_positive
    profile.negative_topics = normalized_negative
    profile.confidence = max(profile.confidence, 0.78 if _has_statement_profile_signals(profile) else 0.0)


def _apply_rule_guardrails(profile: MusicProfile, rule_profile: MusicProfile) -> None:
    for topic, weight in rule_profile.positive_topics.items():
        if topic not in profile.negative_topics:
            profile.positive_topics[topic] = max(profile.positive_topics.get(topic, 0.0), weight)

    for topic, weight in rule_profile.negative_topics.items():
        profile.negative_topics[topic] = max(profile.negative_topics.get(topic, 0.0), weight)
        profile.positive_topics.pop(topic, None)

    for mood, weight in rule_profile.mood_weights.items():
        profile.mood_weights[mood] = max(profile.mood_weights.get(mood, 0.0), weight)

    profile.confidence = max(profile.confidence, rule_profile.confidence)


def _normalize_topic_name(value: str) -> str:
    raw = str(value).strip()
    lowered = raw.casefold()
    aliases = {
        "rnb": "R&B",
        "r&b": "R&B",
        "rap": "Rap",
        "hip-hop": "Rap",
        "hiphop": "Rap",
        "欧美": "欧美流行",
        "欧美pop": "欧美流行",
    }
    return aliases.get(lowered, raw[:80])


def _has_statement_profile_signals(profile: MusicProfile) -> bool:
    return bool(profile.positive_topics or profile.negative_topics or profile.mood_weights)
