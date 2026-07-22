from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agent_memory_runtime.domain.query import MemoryQuery


@dataclass(frozen=True)
class CandidateHit:
    memory_id: str
    sources: tuple[str, ...] = ()
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    lexical_raw_score: float | None = None
    semantic_similarity: float | None = None
    lexical_relevance: float = 0.0
    semantic_relevance: float = 0.0
    fusion_score: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "sources": list(self.sources),
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "lexical_raw_score": self.lexical_raw_score,
            "semantic_similarity": self.semantic_similarity,
            "lexical_relevance": self.lexical_relevance,
            "semantic_relevance": self.semantic_relevance,
            "fusion_score": self.fusion_score,
        }


@dataclass(frozen=True)
class CandidateBatch:
    hits: tuple[CandidateHit, ...]
    retrieval_legs: tuple[str, ...] = ()
    lexical_candidate_count: int = 0
    semantic_candidate_count: int = 0
    semantic_generation: str | None = None
    embedding_ms: float = 0.0
    vector_search_ms: float = 0.0
    fusion_ms: float = 0.0
    semantic_timed_out: bool = False
    semantic_error_type: str | None = None
    embedding_coverage: float | None = None
    _by_id: dict[str, CandidateHit] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {hit.memory_id: hit for hit in self.hits})

    def get(self, memory_id: str) -> CandidateHit | None:
        return self._by_id.get(memory_id)


class CandidateRetriever(Protocol):
    def retrieve(self, query: MemoryQuery, *, limit: int) -> CandidateBatch: ...

    def close(self) -> None: ...
