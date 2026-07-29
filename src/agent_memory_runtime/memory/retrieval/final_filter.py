from __future__ import annotations

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, RetrievalResult
from agent_memory_runtime.memory.retrieval.candidates import CandidateBatch, CandidateHit
from agent_memory_runtime.memory.retrieval.contradiction import has_state_conflict
from agent_memory_runtime.memory.retrieval.lexical import lexical_tokens, searchable_record_text


def apply_final_filter(
    ranked_results: list[RetrievalResult],
    *,
    records_by_id: dict[str, MemoryRecord],
    query: MemoryQuery,
    config: RuntimeConfig,
    candidate_batch: CandidateBatch | None = None,
) -> list[RetrievalResult]:
    filter_config = config.final_retrieval_filter
    if not filter_config.enabled:
        return ranked_results

    candidates = candidate_batch if candidate_batch is not None else _empty_candidates()
    ranked_results = _filter_window(ranked_results, filter_config.max_filter_candidates)
    filtered = [
        result
        for result in ranked_results
        if result.blocked
        or _allowed_by_forbidden_filter(result, records_by_id, filter_config.filter_hard_negative)
    ]
    if filter_config.filter_pairwise_conflicts:
        filtered = _drop_pairwise_conflicts(filtered, records_by_id, query, candidates)
    allowed = [result for result in filtered if not result.blocked]
    if filter_config.abstain_enabled and candidates.hits and not _has_strong_evidence(
        allowed,
        records_by_id,
        query,
        config,
        candidates,
    ):
        return [result for result in filtered if result.blocked]
    return filtered


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


def _allowed_by_forbidden_filter(
    result: RetrievalResult,
    records_by_id: dict[str, MemoryRecord],
    enabled: bool,
) -> bool:
    if not enabled:
        return True
    if result.score.hard_negative < 0:
        return False
    record = records_by_id.get(result.memory_id)
    return record is not None


def _drop_pairwise_conflicts(
    results: list[RetrievalResult],
    records_by_id: dict[str, MemoryRecord],
    query: MemoryQuery,
    candidates: CandidateBatch,
) -> list[RetrievalResult]:
    kept: list[RetrievalResult] = []
    for result in results:
        if result.blocked:
            kept.append(result)
            continue
        record = records_by_id.get(result.memory_id)
        if record is None:
            continue
        conflicting_index = _first_conflict_index(record, kept, records_by_id)
        if conflicting_index is None:
            kept.append(result)
            continue
        incumbent = kept[conflicting_index]
        incumbent_record = records_by_id.get(incumbent.memory_id)
        if incumbent_record is None:
            kept[conflicting_index] = result
            continue
        if _query_alignment(result, record, query, candidates) > _query_alignment(
            incumbent,
            incumbent_record,
            query,
            candidates,
        ):
            kept[conflicting_index] = result
    return kept


def _first_conflict_index(
    record: MemoryRecord,
    kept: list[RetrievalResult],
    records_by_id: dict[str, MemoryRecord],
) -> int | None:
    for index, other in enumerate(kept):
        if other.blocked:
            continue
        other_record = records_by_id.get(other.memory_id)
        if other_record is None:
            continue
        if has_state_conflict(record.content, other_record.content):
            return index
    return None


def _has_strong_evidence(
    results: list[RetrievalResult],
    records_by_id: dict[str, MemoryRecord],
    query: MemoryQuery,
    config: RuntimeConfig,
    candidates: CandidateBatch,
) -> bool:
    if not results:
        return False
    for result in results[: max(1, query.limit or config.max_retrieval_results)]:
        record = records_by_id.get(result.memory_id)
        if record is None:
            continue
        candidate = candidates.get(result.memory_id)
        if candidate is None:
            continue
        if result.score.total >= config.final_retrieval_filter.min_rank_score and (
            _semantic_similarity(candidate)
            >= config.final_retrieval_filter.min_semantic_similarity
            or _lexical_coverage(query.text, record)
            >= config.final_retrieval_filter.min_lexical_coverage
        ):
            return True
    return False


def _query_alignment(
    result: RetrievalResult,
    record: MemoryRecord,
    query: MemoryQuery,
    candidates: CandidateBatch,
) -> float:
    candidate = candidates.get(result.memory_id)
    semantic = _semantic_similarity(candidate) if candidate is not None else 0.0
    lexical = _lexical_coverage(query.text, record)
    return round(result.score.total + semantic + lexical, 4)


def _semantic_similarity(candidate: CandidateHit) -> float:
    return float(candidate.semantic_similarity or 0.0)


def _lexical_coverage(query_text: str, record: MemoryRecord) -> float:
    query_tokens = lexical_tokens(query_text)
    if not query_tokens:
        return 0.0
    record_tokens = lexical_tokens(searchable_record_text(record))
    return len(query_tokens & record_tokens) / len(query_tokens)


def _empty_candidates() -> CandidateBatch:
    return CandidateBatch(hits=())
