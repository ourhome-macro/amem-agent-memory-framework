from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
    available_at: str = ""
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: str | None = None
    claimed_at: str | None = None
    last_error_at: str | None = None
    redrive_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(cls, event_id: str, *, max_attempts: int = 3) -> DerivationJob:
        now = utc_now_iso()
        return cls(
            job_id=str(uuid4()),
            event_id=event_id,
            max_attempts=max_attempts,
            available_at=now,
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
            available_at=str(
                value.get("available_at")
                or value.get("created_at")
                or utc_now_iso()
            ),
            lease_owner=_optional_str(value.get("lease_owner")),
            lease_token=_optional_str(value.get("lease_token")),
            lease_expires_at=_optional_str(value.get("lease_expires_at")),
            claimed_at=_optional_str(value.get("claimed_at")),
            last_error_at=_optional_str(value.get("last_error_at")),
            redrive_count=int(value.get("redrive_count", 0)),
            created_at=str(value.get("created_at") or utc_now_iso()),
            updated_at=str(value.get("updated_at") or utc_now_iso()),
        )

    def claim(
        self,
        *,
        worker_id: str = "worker",
        lease_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DerivationJob:
        claimed_at = _utc(now)
        return replace(
            self,
            status="running",
            attempts=self.attempts + 1,
            lease_owner=worker_id,
            lease_token=str(uuid4()),
            lease_expires_at=_iso(claimed_at + timedelta(seconds=max(0.001, lease_seconds))),
            claimed_at=_iso(claimed_at),
            updated_at=_iso(claimed_at),
        )

    def succeed(self, *, now: datetime | None = None) -> DerivationJob:
        completed_at = _utc(now)
        return replace(
            self,
            status="succeeded",
            error_type=None,
            error_hash=None,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            updated_at=_iso(completed_at),
        )

    def fail(
        self,
        error: Exception,
        *,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 300.0,
        now: datetime | None = None,
    ) -> DerivationJob:
        failed_at = _utc(now)
        dead_lettered = self.attempts >= self.max_attempts
        delay = min(
            max(0.0, retry_max_seconds),
            max(0.0, retry_base_seconds) * (2 ** max(0, self.attempts - 1)),
        )
        return replace(
            self,
            status="dead_letter" if dead_lettered else "pending",
            error_type=type(error).__name__,
            error_hash=secure_hash({"type": type(error).__name__, "message": str(error)}),
            available_at=_iso(failed_at if dead_lettered else failed_at + timedelta(seconds=delay)),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            last_error_at=_iso(failed_at),
            updated_at=_iso(failed_at),
        )

    def renew(
        self,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> DerivationJob:
        renewed_at = _utc(now)
        return replace(
            self,
            lease_expires_at=_iso(renewed_at + timedelta(seconds=max(0.001, lease_seconds))),
            updated_at=_iso(renewed_at),
        )

    def recover_expired_lease(self, *, now: datetime | None = None) -> DerivationJob:
        recovered_at = _utc(now)
        exhausted = self.attempts >= self.max_attempts
        return replace(
            self,
            status="dead_letter" if exhausted else "pending",
            error_type="LeaseExpired",
            error_hash=secure_hash({"type": "LeaseExpired", "job_id": self.job_id}),
            available_at=_iso(recovered_at),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            last_error_at=_iso(recovered_at),
            updated_at=_iso(recovered_at),
        )

    def redrive(self, *, now: datetime | None = None) -> DerivationJob:
        redriven_at = _utc(now)
        return replace(
            self,
            status="pending",
            attempts=0,
            error_type=None,
            error_hash=None,
            available_at=_iso(redriven_at),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            claimed_at=None,
            last_error_at=None,
            redrive_count=self.redrive_count + 1,
            updated_at=_iso(redriven_at),
        )

    def is_available(self, *, now: datetime | None = None) -> bool:
        return self.status == "pending" and _parse(self.available_at) <= _utc(now)

    def is_lease_expired(self, *, now: datetime | None = None) -> bool:
        return (
            self.status == "running"
            and self.lease_expires_at is not None
            and _parse(self.lease_expires_at) <= _utc(now)
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
            "available_at": self.available_at,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "claimed_at": self.claimed_at,
            "last_error_at": self.last_error_at,
            "redrive_count": self.redrive_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)
