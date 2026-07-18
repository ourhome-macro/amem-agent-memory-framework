from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agent_memory_runtime.audit.hashing import stable_hash


@dataclass(frozen=True)
class RetrievalWeights:
    keyword: float = 1.0
    recency: float = 0.2
    salience: float = 0.8
    confidence: float = 0.3
    type_boost: float = 0.4
    source_link: float = 0.6


@dataclass(frozen=True)
class RuntimeConfig:
    rule_version: str = "builtin-v1"
    max_retrieval_results: int = 8
    context_token_budget: int = 900
    low_salience_archive_threshold: float = 0.12
    retrieval_weights: RetrievalWeights = field(default_factory=RetrievalWeights)

    @property
    def config_hash(self) -> str:
        return stable_hash(asdict(self))

