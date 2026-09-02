from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.domain.enums import MemoryLabel, MemoryVisibility
from agent_memory_runtime.domain.memory import MemoryCandidate


@dataclass(frozen=True)
class RiskAssessment:
    score: float
    reasons: tuple[str, ...]


class CandidateRiskScorer:
    def assess(self, candidate: MemoryCandidate) -> RiskAssessment:
        score = 0.0
        reasons: list[str] = []
        labels = set(candidate.labels)
        tags = {tag.casefold() for tag in candidate.tags}
        if MemoryLabel.SENSITIVE.value in labels:
            score += 0.8
            reasons.append("sensitive_label")
        if candidate.visibility == MemoryVisibility.PUBLIC.value:
            score += 0.4
            reasons.append("public_visibility")
        if (
            candidate.visibility == MemoryVisibility.SHARED.value
            and MemoryLabel.SENSITIVE.value in labels
        ):
            score += 0.2
            reasons.append("shared_sensitive")
        if {"health", "medical", "credential", "payment", "legal"} & tags:
            score += 0.2
            reasons.append("high_risk_tag")
        return RiskAssessment(score=round(min(score, 1.0), 4), reasons=tuple(reasons))
