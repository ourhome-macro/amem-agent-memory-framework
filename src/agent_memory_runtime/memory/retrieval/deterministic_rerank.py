from __future__ import annotations

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.enums import MemoryLayer, MemoryStatus
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, RetrievalResult
from agent_memory_runtime.memory.retrieval.candidates import CandidateBatch
from agent_memory_runtime.memory.retrieval.lexical import lexical_tokens, searchable_record_text
from agent_memory_runtime.memory.semantic_state import (
    QueryStateIntent,
    extract_query_state_intent,
    record_temporal_scope,
    state_fact_from_record,
    text_conflicts_record_state,
    token_overlap,
    topic_tokens,
)


def apply_deterministic_rerank(
    results: list[RetrievalResult],
    *,
    records_by_id: dict[str, MemoryRecord],
    query: MemoryQuery,
    config: RuntimeConfig,
    candidate_batch: CandidateBatch | None = None,
) -> list[RetrievalResult]:
    rerank_config = config.deterministic_rerank
    if not rerank_config.enabled:
        return results

    intent = extract_query_state_intent(query.text)
    window = _filter_window(results, rerank_config.max_candidates)
    kept: list[RetrievalResult] = []
    for result in window:
        if result.blocked:
            kept.append(result)
            continue
        record = records_by_id.get(result.memory_id)
        if record is None:
            continue
        if _drop_record(record, result, query=query, intent=intent, config=config):
            continue
        kept.append(result)

    allowed = [result for result in kept if not result.blocked]
    if rerank_config.no_answer_enabled and not _has_reliable_answer(
        allowed,
        records_by_id=records_by_id,
        intent=intent,
        config=config,
        candidate_batch=candidate_batch,
    ):
        return [result for result in kept if result.blocked]

    return sorted(
        kept,
        key=lambda result: _rerank_key(
            result,
            records_by_id=records_by_id,
            intent=intent,
            candidate_batch=candidate_batch,
        ),
        reverse=True,
    )


def _filter_window(results: list[RetrievalResult], max_candidates: int) -> list[RetrievalResult]:
    window: list[RetrievalResult] = []
    allowed_count = 0
    for result in results:
        window.append(result)
        if not result.blocked:
            allowed_count += 1
        if allowed_count >= max_candidates:
            break
    return window


def _drop_record(
    record: MemoryRecord,
    result: RetrievalResult,
    *,
    query: MemoryQuery,
    intent: QueryStateIntent,
    config: RuntimeConfig,
) -> bool:
    rerank_config = config.deterministic_rerank
    if record.status == MemoryStatus.CONFLICTED.value:
        return True
    if record.status == MemoryStatus.ARCHIVED.value and MemoryLayer.ARCHIVAL.value not in set(
        query.layers
    ):
        return True
    if rerank_config.drop_state_conflicts and text_conflicts_record_state(query.text, record):
        return True
    if rerank_config.drop_state_conflicts and _intent_state_conflict(record, intent):
        return True
    if rerank_config.prefer_temporal_scope and _temporal_mismatch(record, intent):
        return True
    if (
        rerank_config.drop_entity_mismatch
        and _should_check_entity(intent)
        and _entity_overlap(intent, record) < rerank_config.min_entity_overlap
        and result.score.total < config.final_retrieval_filter.min_rank_score
    ):
        return True
    return False


def _temporal_mismatch(record: MemoryRecord, intent: QueryStateIntent) -> bool:
    if intent.temporal_scope is None:
        return False
    record_scope = record_temporal_scope(record)
    if record_scope == intent.temporal_scope:
        return False
    if record_scope in {"past", "future"}:
        return True
    if _entity_overlap(intent, record) <= 0.0:
        return False
    return True


def _intent_state_conflict(record: MemoryRecord, intent: QueryStateIntent) -> bool:
    if intent.attribute is None or intent.expected_value is None:
        return False
    record_fact = state_fact_from_record(record)
    if record_fact is None:
        return False
    if not _attributes_comparable(intent.attribute, record_fact.attribute):
        return False
    if record_fact.value == intent.expected_value:
        return False
    if intent.temporal_scope is not None and record_fact.temporal_scope != intent.temporal_scope:
        return False
    return _entity_overlap(intent, record) > 0.0


def _attributes_comparable(intent_attribute: str, record_attribute: str) -> bool:
    if intent_attribute == record_attribute:
        return True
    if intent_attribute == "allowed" and record_attribute in {"success", "resolved"}:
        return True
    if intent_attribute == "success" and record_attribute == "resolved":
        return True
    return False


def _has_reliable_answer(
    results: list[RetrievalResult],
    *,
    records_by_id: dict[str, MemoryRecord],
    intent: QueryStateIntent,
    config: RuntimeConfig,
    candidate_batch: CandidateBatch | None,
) -> bool:
    if not results:
        return False
    best = results[0]
    if best.score.total >= config.deterministic_rerank.no_answer_min_score:
        return True
    record = records_by_id.get(best.memory_id)
    if record is None:
        return False
    if (
        intent.temporal_scope is not None
        and record_temporal_scope(record) == intent.temporal_scope
        and _entity_overlap(intent, record) > 0.0
    ):
        return True
    candidate = None if candidate_batch is None else candidate_batch.get(best.memory_id)
    if candidate is not None and float(candidate.semantic_similarity or 0.0) >= 0.5:
        return True
    if _entity_overlap(intent, record) >= config.deterministic_rerank.no_answer_min_entity_overlap:
        return True
    return _lexical_coverage(intent, record) >= config.final_retrieval_filter.min_lexical_coverage


def _rerank_key(
    result: RetrievalResult,
    *,
    records_by_id: dict[str, MemoryRecord],
    intent: QueryStateIntent,
    candidate_batch: CandidateBatch | None,
) -> tuple[float, float, float, str]:
    record = records_by_id.get(result.memory_id)
    if result.blocked or record is None:
        return (-1000.0, 0.0, result.score.total, result.memory_id)
    candidate = None if candidate_batch is None else candidate_batch.get(result.memory_id)
    semantic = 0.0 if candidate is None else float(candidate.semantic_similarity or 0.0)
    temporal_bonus = (
        0.25
        if intent.temporal_scope is not None
        and record_temporal_scope(record) == intent.temporal_scope
        else 0.0
    )
    entity_bonus = min(0.25, _entity_overlap(intent, record))
    return (
        result.score.total + temporal_bonus + entity_bonus,
        semantic,
        result.score.total,
        result.memory_id,
    )


def _should_check_entity(intent: QueryStateIntent) -> bool:
    return len(intent.entity_tokens) >= 2


def _entity_overlap(intent: QueryStateIntent, record: MemoryRecord) -> float:
    if not intent.entity_tokens:
        return 0.0
    return token_overlap(intent.entity_tokens, topic_tokens(searchable_record_text(record)))


def _lexical_coverage(intent: QueryStateIntent, record: MemoryRecord) -> float:
    if not intent.entity_tokens:
        return 0.0
    record_tokens = lexical_tokens(searchable_record_text(record))
    return len(set(intent.entity_tokens) & record_tokens) / len(intent.entity_tokens)
