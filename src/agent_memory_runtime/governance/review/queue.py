from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_memory_runtime.domain.memory import MemoryCandidate
from agent_memory_runtime.governance.review.risk import RiskAssessment


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ReviewItem:
    review_id: str
    candidate: MemoryCandidate
    risk: RiskAssessment
    status: str = "pending"
    reviewer_id: str | None = None
    decision_reason: str | None = None
    created_at: str = ""
    decided_at: str | None = None

    @classmethod
    def new(cls, candidate: MemoryCandidate, risk: RiskAssessment) -> ReviewItem:
        return cls(
            review_id=str(uuid4()),
            candidate=candidate,
            risk=risk,
            created_at=utc_now_iso(),
        )

    def approve(self, *, reviewer_id: str, reason: str | None = None) -> ReviewItem:
        return replace(
            self,
            status="approved",
            reviewer_id=reviewer_id,
            decision_reason=reason,
            decided_at=utc_now_iso(),
        )

    def reject(self, *, reviewer_id: str, reason: str | None = None) -> ReviewItem:
        return replace(
            self,
            status="rejected",
            reviewer_id=reviewer_id,
            decision_reason=reason,
            decided_at=utc_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "candidate": self.candidate.to_dict(),
            "risk": {"score": self.risk.score, "reasons": list(self.risk.reasons)},
            "status": self.status,
            "reviewer_id": self.reviewer_id,
            "decision_reason": self.decision_reason,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


class InMemoryReviewQueue:
    def __init__(self) -> None:
        self._items: dict[str, ReviewItem] = {}

    def enqueue(self, candidate: MemoryCandidate, risk: RiskAssessment) -> ReviewItem:
        existing = self.find_pending(candidate)
        if existing is not None:
            return existing
        item = ReviewItem.new(candidate, risk)
        self._items[item.review_id] = item
        return item

    def find_pending(self, candidate: MemoryCandidate) -> ReviewItem | None:
        for item in self.pending_items():
            if (
                item.candidate.memory_id == candidate.memory_id
                and item.candidate.source_event_ids == candidate.source_event_ids
            ):
                return item
        return None

    def get(self, review_id: str) -> ReviewItem | None:
        return self._items.get(review_id)

    def update(self, item: ReviewItem) -> None:
        self._items[item.review_id] = item

    def pending_items(self) -> list[ReviewItem]:
        return [
            item
            for item in sorted(
                self._items.values(),
                key=lambda value: (value.created_at, value.review_id),
            )
            if item.status == "pending"
        ]

    def list_items(self) -> list[ReviewItem]:
        return sorted(self._items.values(), key=lambda value: (value.created_at, value.review_id))
