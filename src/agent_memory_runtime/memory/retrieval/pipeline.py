from __future__ import annotations

from agent_memory_runtime.access.checker import AccessChecker
from agent_memory_runtime.access.principal import Principal
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery, RetrievalResult, RetrievalTrace
from agent_memory_runtime.memory.retrieval.candidate_budget import apply_candidate_budget
from agent_memory_runtime.memory.retrieval.filters import hard_filter
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
    ) -> tuple[list[MemoryRecord], RetrievalTrace]:
        planned = normalize_query(query)
        principal = Principal(agent_id=planned.agent_id)
        filtered = [record for record in records if hard_filter(record, planned)]
        blocked_count = 0
        scored: list[RetrievalResult] = []
        record_by_id = {record.memory_id: record for record in filtered}
        for record in filtered:
            decision = self.access_checker.check(principal, record)
            if not decision.allowed:
                blocked_count += 1
                scored.append(
                    RetrievalResult(
                        memory_id=record.memory_id,
                        score=score_record(record, planned, self.config),
                        blocked=True,
                        blocked_reason=decision.reason,
                    )
                )
                continue
            score = score_record(record, planned, self.config)
            if score.total > 0:
                scored.append(RetrievalResult(memory_id=record.memory_id, score=score))
        ranked_results = rerank(scored)
        selected_results = apply_candidate_budget(
            [item for item in ranked_results if not item.blocked],
            planned.limit or self.config.max_retrieval_results,
        )
        selected = [record_by_id[item.memory_id] for item in selected_results]
        trace = RetrievalTrace(
            query=planned,
            candidate_count=len(filtered),
            blocked_count=blocked_count,
            selected_memory_ids=tuple(item.memory_id for item in selected_results),
            results=tuple(ranked_results),
        )
        return selected, trace
