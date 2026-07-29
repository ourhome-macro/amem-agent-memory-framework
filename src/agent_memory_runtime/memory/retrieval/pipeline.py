from __future__ import annotations

from agent_memory_runtime.access.checker import AccessChecker
from agent_memory_runtime.access.principal import Principal
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, RetrievalResult, RetrievalTrace
from agent_memory_runtime.memory.retrieval.candidate_budget import apply_candidate_budget
from agent_memory_runtime.memory.retrieval.candidates import CandidateBatch
from agent_memory_runtime.memory.retrieval.filters import hard_filter
from agent_memory_runtime.memory.retrieval.final_filter import apply_final_filter
from agent_memory_runtime.memory.retrieval.planner import normalize_query
from agent_memory_runtime.memory.retrieval.reranker import rerank
from agent_memory_runtime.memory.retrieval.scoring import score_record


class RetrievalPipeline:
    def __init__(
        self,
        config: RuntimeConfig | None = None,
        access_checker: AccessChecker | None = None,
    ) -> None:
        self.config = config or RuntimeConfig()
        self.access_checker = access_checker or AccessChecker()

    def retrieve(
        self,
        records: list[MemoryRecord],
        query: MemoryQuery,
        *,
        candidate_batch: CandidateBatch | None = None,
    ) -> tuple[list[MemoryRecord], RetrievalTrace]:
        planned = normalize_query(query)
        principal = Principal(
            agent_id=planned.agent_id,
            tenant_id=planned.tenant_id,
            user_id=planned.user_id,
        )
        filtered = [record for record in records if hard_filter(record, planned)]
        blocked_count = 0
        scored: list[RetrievalResult] = []
        record_by_id = {record.memory_id: record for record in filtered}
        for record in filtered:
            candidate = (
                candidate_batch.get(record.memory_id) if candidate_batch is not None else None
            )
            decision = self.access_checker.check(principal, record)
            if not decision.allowed:
                blocked_count += 1
                scored.append(
                    RetrievalResult(
                        memory_id=record.memory_id,
                        score=score_record(
                            record,
                            planned,
                            self.config,
                            candidate=candidate,
                        ),
                        blocked=True,
                        blocked_reason=decision.reason,
                    )
                )
                continue
            score = score_record(
                record,
                planned,
                self.config,
                candidate=candidate,
            )
            if score.total > 0:
                scored.append(RetrievalResult(memory_id=record.memory_id, score=score))
        ranked_results = rerank(scored)
        filtered_results = apply_final_filter(
            ranked_results,
            records_by_id=record_by_id,
            query=planned,
            config=self.config,
            candidate_batch=candidate_batch,
        )
        selected_results = apply_candidate_budget(
            [item for item in filtered_results if not item.blocked],
            planned.limit or self.config.max_retrieval_results,
        )
        selected = [record_by_id[item.memory_id] for item in selected_results]
        trace = RetrievalTrace(
            query=planned,
            candidate_count=len(filtered),
            blocked_count=blocked_count,
            selected_memory_ids=tuple(item.memory_id for item in selected_results),
            results=tuple(ranked_results),
            retrieval_legs=(candidate_batch.retrieval_legs if candidate_batch is not None else ()),
            lexical_candidate_count=(
                candidate_batch.lexical_candidate_count if candidate_batch is not None else 0
            ),
            semantic_candidate_count=(
                candidate_batch.semantic_candidate_count if candidate_batch is not None else 0
            ),
            semantic_generation=(
                candidate_batch.semantic_generation if candidate_batch is not None else None
            ),
            embedding_ms=(candidate_batch.embedding_ms if candidate_batch is not None else 0.0),
            vector_search_ms=(
                candidate_batch.vector_search_ms if candidate_batch is not None else 0.0
            ),
            fusion_ms=(candidate_batch.fusion_ms if candidate_batch is not None else 0.0),
            semantic_timed_out=(
                candidate_batch.semantic_timed_out if candidate_batch is not None else False
            ),
            semantic_error_type=(
                candidate_batch.semantic_error_type if candidate_batch is not None else None
            ),
            embedding_coverage=(
                candidate_batch.embedding_coverage if candidate_batch is not None else None
            ),
            candidate_details=(
                {hit.memory_id: hit.to_dict() for hit in candidate_batch.hits}
                if candidate_batch is not None
                else {}
            ),
        )
        return selected, trace
