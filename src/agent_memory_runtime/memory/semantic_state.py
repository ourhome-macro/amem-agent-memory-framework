from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from agent_memory_runtime.domain.memory import MemoryRecord


@dataclass(frozen=True)
class StateFact:
    entity_tokens: tuple[str, ...]
    attribute: str
    value: str
    temporal_scope: str

    @property
    def entity_key(self) -> str:
        return " ".join(self.entity_tokens)


_STATE_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "enabled": {
        "on": (
            "\u5f00\u542f",
            "\u6253\u5f00",
            "\u542f\u7528",
            "\u4ecd\u7136\u5f00\u542f",
            "\u5df2\u7ecf\u5f00\u542f",
            "\u5df2\u7ecf\u6253\u5f00",
            "enabled",
            "turned on",
            "still on",
            "active",
        ),
        "off": (
            "\u5173\u95ed",
            "\u5173\u6389",
            "\u7981\u7528",
            "\u505c\u7528",
            "\u53d6\u6d88",
            "\u5df2\u7ecf\u5173\u95ed",
            "\u5df2\u7ecf\u7981\u7528",
            "disabled",
            "turned off",
            "inactive",
            "cancelled",
            "canceled",
        ),
    },
    "allowed": {
        "yes": (
            "\u5141\u8bb8",
            "\u51c6\u8bb8",
            "\u53ef\u4ee5",
            "\u53ef\u7528",
            "\u80fd\u7528",
            "allowed",
            "permitted",
            "approved",
        ),
        "no": (
            "\u7981\u6b62",
            "\u4e0d\u5141\u8bb8",
            "\u4e0d\u8981",
            "\u4e0d\u8981\u7528",
            "\u4e0d\u80fd",
            "\u4e0d\u53ef",
            "\u62d2\u7edd",
            "forbidden",
            "blocked",
            "denied",
            "rejected",
        ),
    },
    "success": {
        "yes": (
            "\u6210\u529f",
            "\u6210\u529f\u5b8c\u6210",
            "\u5df2\u7ecf\u5b8c\u6210",
            "\u901a\u8fc7",
            "\u5b8c\u6210",
            "succeeded",
            "successful",
            "passed",
            "completed",
        ),
        "no": (
            "\u5931\u8d25",
            "\u672a\u5b8c\u6210",
            "\u672a\u901a\u8fc7",
            "\u6ca1\u901a\u8fc7",
            "failed",
            "unsuccessful",
            "did not pass",
        ),
    },
    "resolved": {
        "yes": (
            "\u5df2\u89e3\u51b3",
            "\u5df2\u7ecf\u89e3\u51b3",
            "\u89e3\u51b3\u4e86",
            "\u5df2\u5173\u95ed",
            "resolved",
            "closed",
            "fixed",
        ),
        "no": (
            "\u672a\u89e3\u51b3",
            "\u4ecd\u672a\u89e3\u51b3",
            "unresolved",
            "still open",
            "open",
        ),
    },
    "paid": {
        "yes": (
            "\u5df2\u652f\u4ed8",
            "\u5df2\u7ecf\u652f\u4ed8",
            "\u652f\u4ed8\u5b8c\u6210",
            "\u5df2\u4ed8\u6b3e",
            "paid",
            "payment completed",
        ),
        "no": (
            "\u672a\u652f\u4ed8",
            "\u4ecd\u7136\u672a\u652f\u4ed8",
            "\u672a\u4ed8\u6b3e",
            "unpaid",
            "payment pending",
        ),
    },
}

_TEMPORAL_MARKERS: dict[str, tuple[str, ...]] = {
    "current": (
        "\u5f53\u524d",
        "\u73b0\u5728",
        "\u5982\u4eca",
        "\u76ee\u524d",
        "\u4ecd\u7136",
        "\u5df2\u7ecf",
        "current",
        "currently",
        "still",
        "already",
    ),
    "past": (
        "\u8fc7\u53bb",
        "\u66fe\u7ecf",
        "\u4ee5\u524d",
        "\u4e4b\u524d",
        "\u6628\u5929",
        "\u6628\u665a",
        "\u4e0a\u4e2a\u6708",
        "previously",
        "historical",
        "last month",
    ),
    "future": (
        "\u672a\u6765",
        "\u4ee5\u540e",
        "\u8ba1\u5212",
        "\u660e\u5e74",
        "\u540e\u7eed",
        "future",
        "planned",
        "will",
    ),
}

_STOPWORDS = {
    "is",
    "are",
    "was",
    "were",
    "the",
    "a",
    "an",
    "has",
    "have",
    "been",
    "still",
    "now",
    "already",
    "currently",
    "not",
    "no",
    "longer",
    "\u7528\u6237",
    "\u5df2\u7ecf",
    "\u4ecd\u7136",
    "\u73b0\u5728",
    "\u5f53\u524d",
    "\u662f\u5426",
    "\u4ec0\u4e48",
}


def extract_state_fact(text: str) -> StateFact | None:
    normalized = _normalize(text)
    signals = _state_signals(normalized)
    if not signals:
        return None
    if _has_internal_conflict(signals):
        return None
    attribute, value = sorted(signals, key=lambda item: item[2])[0][:2]
    entity_tokens = _topic_tokens(normalized)
    if not entity_tokens:
        return None
    return StateFact(
        entity_tokens=entity_tokens,
        attribute=attribute,
        value=value,
        temporal_scope=_temporal_scope(normalized),
    )


def state_fact_metadata(text: str, *, source: str) -> dict[str, Any]:
    fact = extract_state_fact(text)
    if fact is None:
        return {}
    return {
        "semantic_state_schema": "current_state.v1",
        "semantic_state_source": source,
        "semantic_state_entity": fact.entity_key,
        "semantic_state_entity_tokens": list(fact.entity_tokens),
        "semantic_state_attribute": fact.attribute,
        "semantic_state_value": fact.value,
        "semantic_state_temporal_scope": fact.temporal_scope,
    }


def state_fact_from_record(record: MemoryRecord) -> StateFact | None:
    metadata = record.metadata or {}
    tokens = metadata.get("semantic_state_entity_tokens")
    attribute = metadata.get("semantic_state_attribute")
    value = metadata.get("semantic_state_value")
    temporal_scope = metadata.get("semantic_state_temporal_scope")
    if isinstance(tokens, list) and attribute and value and temporal_scope:
        entity_tokens = tuple(str(token) for token in tokens if str(token))
        if entity_tokens:
            return StateFact(
                entity_tokens=entity_tokens,
                attribute=str(attribute),
                value=str(value),
                temporal_scope=str(temporal_scope),
            )
    return extract_state_fact(record.content)


def current_state_group_key(
    record: MemoryRecord,
) -> tuple[str, str | None, str | None, str, str, str] | None:
    fact = state_fact_from_record(record)
    if fact is None or fact.temporal_scope != "current":
        return None
    return (
        record.tenant_id,
        record.user_id,
        record.agent_id,
        record.subject_id,
        fact.entity_key,
        fact.attribute,
    )


def state_values_conflict(left: MemoryRecord, right: MemoryRecord) -> bool:
    left_fact = state_fact_from_record(left)
    right_fact = state_fact_from_record(right)
    if left_fact is None or right_fact is None:
        return False
    if left_fact.attribute != right_fact.attribute or left_fact.value == right_fact.value:
        return False
    if left_fact.temporal_scope != right_fact.temporal_scope:
        return False
    return _jaccard(set(left_fact.entity_tokens), set(right_fact.entity_tokens)) >= 0.25


def text_conflicts_record_state(text: str, record: MemoryRecord) -> bool:
    query_fact = extract_state_fact(text)
    record_fact = state_fact_from_record(record)
    if query_fact is None or record_fact is None:
        return False
    if query_fact.attribute != record_fact.attribute or query_fact.value == record_fact.value:
        return False
    if query_fact.temporal_scope != record_fact.temporal_scope:
        return False
    return _jaccard(set(query_fact.entity_tokens), set(record_fact.entity_tokens)) >= 0.25


def _state_signals(text: str) -> list[tuple[str, str, int]]:
    signals: list[tuple[str, str, int]] = []
    for family, values in _STATE_MARKERS.items():
        for value, markers in values.items():
            for marker in markers:
                index = text.find(marker)
                if index >= 0:
                    signals.append((family, value, index))
    return signals


def _has_internal_conflict(signals: list[tuple[str, str, int]]) -> bool:
    by_family: dict[str, set[str]] = {}
    for family, value, _index in signals:
        by_family.setdefault(family, set()).add(value)
    return any(len(values) > 1 for values in by_family.values())


def _temporal_scope(text: str) -> str:
    for scope, markers in _TEMPORAL_MARKERS.items():
        if any(marker in text for marker in markers):
            return scope
    return "current"


def _topic_tokens(text: str) -> tuple[str, ...]:
    cleaned = text
    for markers in _TEMPORAL_MARKERS.values():
        for marker in markers:
            cleaned = cleaned.replace(marker, " ")
    for values in _STATE_MARKERS.values():
        for markers in values.values():
            for marker in markers:
                cleaned = cleaned.replace(marker, " ")
    tokens = {
        token
        for token in re.findall(r"[a-z0-9_]{2,}", cleaned)
        if token not in _STOPWORDS
    }
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", cleaned))
    tokens.update(
        token
        for token in (cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
        if token and token not in _STOPWORDS
    )
    return tuple(sorted(tokens))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()
