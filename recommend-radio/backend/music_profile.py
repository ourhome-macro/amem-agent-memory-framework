from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_SAME_UPLOADER_LIMIT = 0
DEFAULT_EXPLORATION_RATIO = 0.0


@dataclass(frozen=True)
class RelevantMemory:
    memory_id: str
    content: str
    layer: str
    memory_type: str
    tags: tuple[str, ...] = ()
    salience: float = 0.5
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: Any) -> "RelevantMemory":
        return cls(
            memory_id=str(getattr(record, "memory_id", "")),
            content=str(getattr(record, "content", "")),
            layer=str(getattr(record, "layer", "")),
            memory_type=str(getattr(record, "memory_type", "")),
            tags=tuple(str(item) for item in getattr(record, "tags", ()) or ()),
            salience=_clamp_float(getattr(record, "salience", 0.5)),
            confidence=_clamp_float(getattr(record, "confidence", 0.5)),
            metadata=dict(getattr(record, "metadata", {}) or {}),
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "layer": self.layer,
            "memory_type": self.memory_type,
            "tags": list(self.tags),
            "salience": self.salience,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class MusicProfile:
    positive_topics: dict[str, float] = field(default_factory=dict)
    negative_topics: dict[str, float] = field(default_factory=dict)
    preferred_uploaders: dict[str, float] = field(default_factory=dict)
    avoid_uploaders: dict[str, float] = field(default_factory=dict)
    blocked_uploaders: dict[str, float] = field(default_factory=dict)
    mood_weights: dict[str, float] = field(default_factory=dict)
    recent_intents: list[str] = field(default_factory=list)
    positive_interest_texts: list[str] = field(default_factory=list)
    negative_interest_texts: list[str] = field(default_factory=list)
    mbti: str = ""
    music_persona: str = ""
    current_music_phase: str = ""
    core_traits: list[str] = field(default_factory=list)
    psychological_needs: list[str] = field(default_factory=list)
    persona_evidence: list[str] = field(default_factory=list)
    persona_confidence: float = 0.0
    same_uploader_limit: int = DEFAULT_SAME_UPLOADER_LIMIT
    exploration_ratio: float = DEFAULT_EXPLORATION_RATIO
    evidence_memory_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "fallback"

    @classmethod
    def empty(cls) -> "MusicProfile":
        return cls()

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, source: str) -> "MusicProfile":
        return cls(
            positive_topics=_score_map(value.get("positive_topics")),
            negative_topics=_score_map(value.get("negative_topics")),
            preferred_uploaders=_score_map(value.get("preferred_uploaders")),
            avoid_uploaders=_score_map(value.get("avoid_uploaders")),
            blocked_uploaders=_score_map(value.get("blocked_uploaders")),
            mood_weights=_score_map(value.get("mood_weights")),
            recent_intents=_string_list(value.get("recent_intents"), limit=8),
            positive_interest_texts=_string_list(value.get("positive_interest_texts"), limit=12),
            negative_interest_texts=_string_list(value.get("negative_interest_texts"), limit=12),
            mbti=str(value.get("mbti") or "")[:8],
            music_persona=str(value.get("music_persona") or "")[:500],
            current_music_phase=str(value.get("current_music_phase") or "")[:300],
            core_traits=_string_list(value.get("core_traits"), limit=8),
            psychological_needs=_string_list(value.get("psychological_needs"), limit=8),
            persona_evidence=_string_list(value.get("persona_evidence"), limit=12),
            persona_confidence=_clamp_float(value.get("persona_confidence"), 0.0),
            same_uploader_limit=max(int(value.get("same_uploader_limit") or 0), 0),
            exploration_ratio=_clamp_float(value.get("exploration_ratio"), 0.0),
            evidence_memory_ids=_string_list(value.get("evidence_memory_ids"), limit=24),
            confidence=_clamp_float(value.get("confidence"), 0.0),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "positive_topics": self.positive_topics,
            "negative_topics": self.negative_topics,
            "preferred_uploaders": self.preferred_uploaders,
            "avoid_uploaders": self.avoid_uploaders,
            "blocked_uploaders": self.blocked_uploaders,
            "mood_weights": self.mood_weights,
            "recent_intents": self.recent_intents,
            "positive_interest_texts": self.positive_interest_texts,
            "negative_interest_texts": self.negative_interest_texts,
            "mbti": self.mbti,
            "music_persona": self.music_persona,
            "current_music_phase": self.current_music_phase,
            "core_traits": self.core_traits,
            "psychological_needs": self.psychological_needs,
            "persona_evidence": self.persona_evidence,
            "persona_confidence": self.persona_confidence,
            "same_uploader_limit": self.same_uploader_limit,
            "exploration_ratio": self.exploration_ratio,
            "evidence_memory_ids": self.evidence_memory_ids,
            "confidence": self.confidence,
            "source": self.source,
        }

    def topic_weight(self, text: str, *, positive: bool) -> float:
        values = self.positive_topics if positive else self.negative_topics
        normalized = text.casefold()
        score = 0.0
        for topic, weight in values.items():
            if topic and topic.casefold() in normalized:
                score = max(score, weight)
        return score

    def uploader_weight(self, uploader_key: str) -> float:
        return self.preferred_uploaders.get(uploader_key, 0.0)

    def hard_blocked_uploader(self, uploader_key: str) -> bool:
        return uploader_key in self.blocked_uploaders

    def avoided_uploader(self, uploader_key: str) -> bool:
        return uploader_key in self.avoid_uploaders


def overlay_profile_snapshot(projected: MusicProfile, snapshot: MusicProfile) -> MusicProfile:
    """Overlay the last explicit profile submission onto a derived projection.

    LLM projection remains useful for behavior-derived traits, but it must not make an
    immediately submitted profile disappear or flip its explicit positive/negative topics.
    """
    if snapshot.source != "profile_snapshot":
        return projected
    result = MusicProfile.from_dict(projected.to_dict(), source=projected.source)
    for topic, weight in snapshot.positive_topics.items():
        result.positive_topics[topic] = max(result.positive_topics.get(topic, 0.0), weight)
        result.negative_topics.pop(topic, None)
    for topic, weight in snapshot.negative_topics.items():
        result.negative_topics[topic] = max(result.negative_topics.get(topic, 0.0), weight)
        result.positive_topics.pop(topic, None)
    for name in ("preferred_uploaders", "avoid_uploaders", "blocked_uploaders", "mood_weights"):
        getattr(result, name).update(getattr(snapshot, name))
    for name in ("mbti", "music_persona", "current_music_phase"):
        value = getattr(snapshot, name)
        if value:
            setattr(result, name, value)
    for name in ("core_traits", "psychological_needs", "persona_evidence"):
        values = getattr(snapshot, name)
        if values:
            setattr(result, name, list(values))
    result.recent_intents = _dedupe_strings([*snapshot.recent_intents, *result.recent_intents], limit=8)
    result.positive_interest_texts = _dedupe_strings(
        [*snapshot.positive_interest_texts, *result.positive_interest_texts], limit=12
    )
    result.negative_interest_texts = _dedupe_strings(
        [*snapshot.negative_interest_texts, *result.negative_interest_texts], limit=12
    )
    result.evidence_memory_ids = _dedupe_strings(
        [*snapshot.evidence_memory_ids, *result.evidence_memory_ids], limit=24
    )
    result.persona_confidence = max(result.persona_confidence, snapshot.persona_confidence)
    result.same_uploader_limit = snapshot.same_uploader_limit
    result.exploration_ratio = snapshot.exploration_ratio
    result.confidence = max(result.confidence, snapshot.confidence)
    result.source = f"{projected.source}+profile_snapshot"
    return result


def _score_map(value: object) -> dict[str, float]:
    result: dict[str, float] = {}
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("topic")
                or item.get("name")
                or item.get("key")
                or item.get("uploader")
                or item.get("owner_mid")
                or item.get("mid")
            )
            score = item.get("score", item.get("weight", item.get("confidence", item.get("value", 0.5))))
            items.append((key, score))
    else:
        return result

    for key, score in items:
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        result[normalized_key[:80]] = _clamp_float(score)
    return result


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text[:120])
        if len(result) >= limit:
            break
    return result


def _dedupe_strings(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _clamp_float(value: object, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(min(max(number, 0.0), 1.0), 4)
