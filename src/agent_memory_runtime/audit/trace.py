from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuntimeTrace:
    selected_memory_ids: tuple[str, ...] = ()
    blocked_memory_count: int = 0
    score_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)
    rule_version: str = ""
    config_hash: str = ""
    last_event_sequence: int = 0
    state_hash: str = ""
    context_source: str = "retrieval"
    retrieval_timed_out: bool = False
    first_token_ms: int | None = None
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
    query_route: dict[str, object] = field(default_factory=dict)
    candidate_details: dict[str, dict[str, object]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_memory_ids": list(self.selected_memory_ids),
            "blocked_memory_count": self.blocked_memory_count,
            "score_breakdown": self.score_breakdown,
            "rule_version": self.rule_version,
            "config_hash": self.config_hash,
            "last_event_sequence": self.last_event_sequence,
            "state_hash": self.state_hash,
            "context_source": self.context_source,
            "retrieval_timed_out": self.retrieval_timed_out,
            "first_token_ms": self.first_token_ms,
            "retrieval_legs": list(self.retrieval_legs),
            "lexical_candidate_count": self.lexical_candidate_count,
            "semantic_candidate_count": self.semantic_candidate_count,
            "semantic_generation": self.semantic_generation,
            "embedding_ms": self.embedding_ms,
            "vector_search_ms": self.vector_search_ms,
            "fusion_ms": self.fusion_ms,
            "semantic_timed_out": self.semantic_timed_out,
            "semantic_error_type": self.semantic_error_type,
            "embedding_coverage": self.embedding_coverage,
            "query_route": self.query_route,
            "candidate_details": self.candidate_details,
        }
