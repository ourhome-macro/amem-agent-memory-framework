from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_memory_runtime.audit.hashing import secure_hash


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class DerivationJob:
    job_id: str
    event_id: str
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 3
    error_type: str | None = None
    error_hash: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(cls, event_id: str, *, max_attempts: int = 3) -> DerivationJob:
        now = utc_now_iso()
        return cls(
            job_id=str(uuid4()),
            event_id=event_id,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DerivationJob:
        return cls(
            job_id=str(value["job_id"]),
            event_id=str(value["event_id"]),
            status=str(value.get("status", "pending")),
            attempts=int(value.get("attempts", 0)),
            max_attempts=int(value.get("max_attempts", 3)),
            error_type=_optional_str(value.get("error_type")),
            error_hash=_optional_str(value.get("error_hash")),
            created_at=str(value.get("created_at") or utc_now_iso()),
            updated_at=str(value.get("updated_at") or utc_now_iso()),
        )

    def claim(self) -> DerivationJob:
        return replace(self, status="running", updated_at=utc_now_iso())

    def succeed(self) -> DerivationJob:
        return replace(
            self,
            status="succeeded",
            error_type=None,
            error_hash=None,
            updated_at=utc_now_iso(),
        )

    def fail(self, error: Exception) -> DerivationJob:
        attempts = self.attempts + 1
        return replace(
            self,
            status="dead_letter" if attempts >= self.max_attempts else "pending",
            attempts=attempts,
            error_type=type(error).__name__,
            error_hash=secure_hash({"type": type(error).__name__, "message": str(error)}),
            updated_at=utc_now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "event_id": self.event_id,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "error_type": self.error_type,
            "error_hash": self.error_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
