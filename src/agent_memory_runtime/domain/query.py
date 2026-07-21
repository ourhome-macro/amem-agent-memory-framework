from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemoryQuery:
    agent_id: str
    text: str
    session_id: str | None = None
    scopes: tuple[str, ...] = ()
    memory_types: tuple[str, ...] = ()
    layers: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source_memory_ids: tuple[str, ...] = ()
    limit: int | None = None
    # Appended to preserve the pre-v0.2 positional constructor contract.
    tenant_id: str = "default"
    user_id: str | None = None


@dataclass(frozen=True)
class ScoreBreakdown:
    keyword: float = 0.0
    recency: float = 0.0
    salience: float = 0.0
    confidence: float = 0.0
    type_boost: float = 0.0
    source_link: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.keyword
            + self.recency
            + self.salience
            + self.confidence
            + self.type_boost
            + self.source_link,
            4,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "keyword": self.keyword,
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
