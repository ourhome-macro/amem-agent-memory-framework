from __future__ import annotations

import re
from datetime import UTC, datetime

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.enums import MemoryType
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, ScoreBreakdown
from agent_memory_runtime.memory.lifecycle.reinforcement import reinforcement_boost

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")


def score_record(record: MemoryRecord, query: MemoryQuery, config: RuntimeConfig) -> ScoreBreakdown:
    query_tokens = _tokens(query.text)
    haystack_tokens = _tokens(_haystack(record))
    overlap = query_tokens & haystack_tokens
    keyword = min(1.0, len(overlap) / max(1, len(query_tokens))) * config.retrieval_weights.keyword
    recency = _recency(record) * config.retrieval_weights.recency
    salience = record.salience * config.retrieval_weights.salience
    confidence = record.confidence * config.retrieval_weights.confidence
    type_boost = _type_boost(record, query) * config.retrieval_weights.type_boost
    source_link = _source_link(record, query) * config.retrieval_weights.source_link
    return ScoreBreakdown(
        keyword=round(keyword, 4),
        recency=round(recency, 4),
        salience=round(salience + reinforcement_boost(record), 4),
        confidence=round(confidence, 4),
        type_boost=round(type_boost, 4),
        source_link=round(source_link, 4),
    )


def _tokens(text: str) -> set[str]:
    return {item.casefold() for item in TOKEN_PATTERN.findall(text)}


def _haystack(record: MemoryRecord) -> str:
    metadata_values = " ".join(str(item) for item in record.metadata.values())
    return " ".join(
        [
            record.memory_id,
            record.memory_type,
            record.scope,
            record.layer,
            record.subject_id,
            record.content,
            *record.tags,
            *record.source_event_ids,
            *record.source_memory_ids,
            metadata_values,
        ]
    )


def _recency(record: MemoryRecord) -> float:
    try:
        updated_at = datetime.fromisoformat(record.updated_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    age_seconds = max(0.0, (datetime.now(UTC) - updated_at.astimezone(UTC)).total_seconds())
    if age_seconds <= 3600:
        return 1.0
    if age_seconds <= 86400:
        return 0.7
    if age_seconds <= 7 * 86400:
        return 0.35
    return 0.0


def _type_boost(record: MemoryRecord, query: MemoryQuery) -> float:
    if record.memory_type in set(query.memory_types):
        return 1.0
    if "how" in query.text.casefold() and record.memory_type == MemoryType.STRATEGY.value:
        return 0.8
    return 0.0


def _source_link(record: MemoryRecord, query: MemoryQuery) -> float:
    if not query.source_memory_ids:
        return 0.0
    return 1.0 if set(query.source_memory_ids) & set(record.source_memory_ids) else 0.0

