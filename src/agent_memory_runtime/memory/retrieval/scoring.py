from __future__ import annotations

from datetime import UTC, datetime

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.enums import MemoryLayer, MemoryType
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, ScoreBreakdown
from agent_memory_runtime.memory.retrieval.candidates import CandidateHit
from agent_memory_runtime.memory.retrieval.contradiction import has_state_conflict
from agent_memory_runtime.memory.retrieval.lexical import (
    lexical_tokens,
    searchable_record_text,
)
from agent_memory_runtime.memory.retrieval.planner import requests_archival_recall


def score_record(
    record: MemoryRecord,
    query: MemoryQuery,
    config: RuntimeConfig,
    *,
    candidate: CandidateHit | None = None,
) -> ScoreBreakdown:
    keyword = 0.0
    lexical = 0.0
    semantic = 0.0
    fusion = 0.0
    if candidate is None:
        query_tokens = lexical_tokens(query.text)
        haystack_tokens = lexical_tokens(searchable_record_text(record))
        overlap = query_tokens & haystack_tokens
        keyword = (
            min(1.0, len(overlap) / max(1, len(query_tokens))) * config.retrieval_weights.keyword
        )
    else:
        lexical = candidate.lexical_relevance * config.retrieval_weights.keyword
        semantic = candidate.semantic_relevance * config.retrieval_weights.semantic
        fusion = candidate.fusion_score * config.retrieval_weights.fusion
    recency = _recency(record) * config.retrieval_weights.recency
    salience = (record.salience + _reinforcement_boost(record)) * config.retrieval_weights.salience
    confidence = record.confidence * config.retrieval_weights.confidence
    type_boost = _type_boost(record, query) * config.retrieval_weights.type_boost
    source_link = _source_link(record, query) * config.retrieval_weights.source_link
    hard_negative = (
        config.retrieval_weights.hard_negative
        if has_state_conflict(query.text, record.content)
        else 0.0
    )
    return ScoreBreakdown(
        keyword=round(keyword, 4),
        lexical=round(lexical, 4),
        semantic=round(semantic, 4),
        fusion=round(fusion, 4),
        recency=round(recency, 4),
        salience=round(salience, 4),
        confidence=round(confidence, 4),
        type_boost=round(type_boost, 4),
        source_link=round(source_link, 4),
        hard_negative=round(hard_negative, 4),
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


def _reinforcement_boost(record: MemoryRecord) -> float:
    if record.reinforcement_count <= 1:
        return 0.0
    return min(0.35, (record.reinforcement_count - 1) * 0.08)


def _type_boost(record: MemoryRecord, query: MemoryQuery) -> float:
    if record.memory_type in set(query.memory_types):
        return 1.0
    if record.layer == MemoryLayer.ARCHIVAL.value and requests_archival_recall(query.text):
        return 1.0
    if "how" in query.text.casefold() and record.memory_type == MemoryType.STRATEGY.value:
        return 0.8
    return 0.0


def _source_link(record: MemoryRecord, query: MemoryQuery) -> float:
    if not query.source_memory_ids:
        return 0.0
    return 1.0 if set(query.source_memory_ids) & set(record.source_memory_ids) else 0.0
