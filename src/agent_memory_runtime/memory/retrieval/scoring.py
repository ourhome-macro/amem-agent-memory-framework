from __future__ import annotations

from datetime import UTC, datetime

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, ScoreBreakdown
from agent_memory_runtime.memory.retrieval.candidates import CandidateHit
from agent_memory_runtime.memory.retrieval.contradiction import has_state_conflict
from agent_memory_runtime.memory.retrieval.lexical import (
    lexical_tokens,
    searchable_record_text,
)
from agent_memory_runtime.memory.semantic_state import text_conflicts_record_state


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
    # Keep RRF/keyword as the primary signal. Priority and freshness are small
    # deterministic adjustments, not a hand-written learning-to-rank formula.
    recency = _recency(record) * config.retrieval_weights.recency
    priority = record.priority * config.retrieval_weights.salience
    hard_negative = (
        config.retrieval_weights.hard_negative
        if text_conflicts_record_state(query.text, record)
        or has_state_conflict(query.text, record.content)
        else 0.0
    )
    return ScoreBreakdown(
        keyword=round(keyword, 4),
        lexical=round(lexical, 4),
        semantic=round(semantic, 4),
        fusion=round(fusion, 4),
        recency=round(recency, 4),
        salience=round(priority, 4),
        confidence=0.0,
        type_boost=0.0,
        source_link=0.0,
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

