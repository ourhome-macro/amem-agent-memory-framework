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
        }
