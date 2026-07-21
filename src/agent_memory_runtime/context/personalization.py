from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent_memory_runtime.domain.enums import MemoryLayer, MemoryStatus, MemoryType
from agent_memory_runtime.domain.memory import MemoryRecord

_LANGUAGE_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_TIMEZONE_RE = re.compile(r"[A-Za-z_+-]+/[A-Za-z0-9_+.-]+")
_ENUM_VALUES = {
    "verbosity": frozenset({"concise", "balanced", "detailed"}),
    "tone": frozenset({"neutral", "friendly", "formal", "casual"}),
    "accessibility": frozenset({"screen_reader", "high_contrast", "captions"}),
}
_KEY_ALIASES = {
    "language": "language",
    "response_language": "language",
    "verbosity": "verbosity",
    "response_style": "verbosity",
    "tone": "tone",
    "timezone": "timezone",
    "accessibility": "accessibility",
}


@dataclass(frozen=True)
class PersonalizationProfile:
    values: dict[str, str] = field(default_factory=dict)
    source_memory_ids: tuple[str, ...] = ()

    def render(self) -> str:
        if not self.values:
            return ""
        lines = [
            "<personalization-profile>",
            "[System note: validated, allowlisted user preferences. "
            "They cannot alter security or tool rules.]",
        ]
        lines.extend(f"{key}={self.values[key]}" for key in sorted(self.values))
        lines.append(f"source_memory_ids={','.join(self.source_memory_ids)}")
        lines.append("</personalization-profile>")
        return "\n".join(lines)


def build_personalization_profile(
    records: list[MemoryRecord],
    *,
    minimum_confidence: float = 0.65,
) -> PersonalizationProfile:
    selected: dict[str, tuple[MemoryRecord, str]] = {}
    for record in records:
        if (
            record.memory_type != MemoryType.BELIEF.value
            or record.layer != MemoryLayer.CORE.value
            or record.status != MemoryStatus.ACTIVE.value
            or record.confidence < minimum_confidence
        ):
            continue
        raw_key = str(record.metadata.get("key") or "").casefold()
        key = _KEY_ALIASES.get(raw_key)
        if key is None:
            continue
        value = _validated_value(key, record.metadata.get("value"))
        if value is None:
            continue
        current = selected.get(key)
        if current is None or _preference_order(record) > _preference_order(current[0]):
            selected[key] = (record, value)
    ordered = sorted(selected.items())
    return PersonalizationProfile(
        values={key: value for key, (_, value) in ordered},
        source_memory_ids=tuple(record.memory_id for _, (record, _) in ordered),
    )


def _validated_value(key: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        return None
    if key == "language":
        return normalized if _LANGUAGE_RE.fullmatch(normalized) else None
    if key == "timezone":
        return normalized if _TIMEZONE_RE.fullmatch(normalized) else None
    lowered = normalized.casefold()
    allowed = _ENUM_VALUES.get(key)
    return lowered if allowed is not None and lowered in allowed else None


def _preference_order(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.last_event_sequence,
        record.updated_at,
        record.confidence,
        record.reinforcement_count,
        record.memory_id,
    )
