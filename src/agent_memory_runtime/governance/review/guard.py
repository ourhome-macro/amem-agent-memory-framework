from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryCandidate
from agent_memory_runtime.governance.review.queue import InMemoryReviewQueue, ReviewItem
from agent_memory_runtime.governance.review.risk import CandidateRiskScorer


class ReviewGuard:
    def __init__(
        self,
        *,
        review_queue: InMemoryReviewQueue | None = None,
        scorer: CandidateRiskScorer | None = None,
        risk_threshold: float = 0.9,
    ) -> None:
        self.review_queue = review_queue or InMemoryReviewQueue()
        self.scorer = scorer or CandidateRiskScorer()
        self.risk_threshold = risk_threshold

    def route_if_required(self, candidate: MemoryCandidate) -> ReviewItem | None:
        risk = self.scorer.assess(candidate)
        if risk.score < self.risk_threshold:
            return None
        return self.review_queue.enqueue(candidate, risk)
