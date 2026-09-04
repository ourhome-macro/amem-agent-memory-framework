from __future__ import annotations

from dataclasses import dataclass, field

from agent_memory_runtime.domain.enums import MemorySessionPolicy, MemoryTemperature


@dataclass(frozen=True)
class MemoryQuery:
    agent_id: str
    text: str
    session_id: str | None = None
    memory_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    limit: int | None = None
    # Appended to preserve the pre-v0.2 positional constructor contract.
    tenant_id: str = "default"
    user_id: str | None = None
    session_policy: str = MemorySessionPolicy.EXACT.value
    retrieval_mode: str | None = None
    levels: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    visibilities: tuple[str, ...] = ()
    temperatures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            MemorySessionPolicy(self.session_policy)
        except ValueError as error:
            raise ValueError(f"unsupported memory session policy: {self.session_policy}") from error
        invalid_temperatures = [
            value
            for value in self.temperatures
            if value not in {item.value for item in MemoryTemperature}
        ]
        if invalid_temperatures:
            joined = ", ".join(sorted(set(invalid_temperatures)))
            raise ValueError(f"unsupported memory temperatures: {joined}")


@dataclass(frozen=True)
class ScoreBreakdown:
    # ``keyword`` is retained for JSON/API compatibility with pre-v0.6 callers.
    # Hybrid SQLite retrieval writes the rank-normalized lexical contribution
    # into ``lexical`` instead of disguising it as a keyword-overlap score.
    keyword: float = 0.0
    lexical: float = 0.0
    semantic: float = 0.0
    fusion: float = 0.0
    hard_negative: float = 0.0
    recency: float = 0.0
    salience: float = 0.0
    confidence: float = 0.0
    type_boost: float = 0.0
    source_link: float = 0.0

    @property
    def total(self) -> float:
        retrieval_relevance = (
            self.fusion if self.fusion > 0 else self.keyword + self.lexical + self.semantic
        )
        return round(
            retrieval_relevance
            + self.recency
            + self.salience
            + self.confidence
            + self.type_boost
            + self.source_link
            + self.hard_negative,
            4,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "keyword": self.keyword,
            "lexical": self.lexical,
            "semantic": self.semantic,
            "fusion": self.fusion,
            "hard_negative": self.hard_negative,
            "recency": self.recency,
            "salience": self.salience,
            "confidence": self.confidence,
            "type_boost": self.type_boost,
            "source_link": self.source_link,
            "total": self.total,
        }


@dataclass(frozen=True)
class RetrievalResult:
    memory_id: str
    score: ScoreBreakdown
    blocked: bool = False
    blocked_reason: str | None = None


@dataclass(frozen=True)
class RetrievalTrace:
    query: MemoryQuery
    candidate_count: int
    blocked_count: int
    selected_memory_ids: tuple[str, ...]
    results: tuple[RetrievalResult, ...] = field(default_factory=tuple)
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
