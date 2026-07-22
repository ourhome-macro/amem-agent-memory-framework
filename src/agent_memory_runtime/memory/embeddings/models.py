from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from agent_memory_runtime.audit.hashing import secure_hash
from agent_memory_runtime.domain.memory import MemoryRecord

_SEMANTIC_TAG_RE = re.compile(r"^[^\W\d_][\w-]{1,31}$", re.UNICODE)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str
    model_id: str
    dimensions: int
    model_revision: str = "default"
    distance_metric: str = "cosine"
    normalized: bool = True
    query_prefix: str = ""
    document_prefix: str = ""
    semantic_tag_allowlist: tuple[str, ...] = ()
    query_template_version: str = "v1"
    document_template_version: str = "v1"
    generation: str = ""

    def __post_init__(self) -> None:
        normalized_tags = tuple(sorted(set(self.semantic_tag_allowlist)))
        if any(not _SEMANTIC_TAG_RE.fullmatch(tag) for tag in normalized_tags):
            raise ValueError("semantic tag allowlist contains an invalid tag")
        object.__setattr__(self, "semantic_tag_allowlist", normalized_tags)
        if not self.provider.strip() or not self.model_id.strip():
            raise ValueError("embedding provider and model_id are required")
        if self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        if self.distance_metric != "cosine":
            raise ValueError("only cosine embedding distance is currently supported")
        if not self.generation:
            payload = json.dumps(
                {
                    "provider": self.provider,
                    "model_id": self.model_id,
                    "model_revision": self.model_revision,
                    "dimensions": self.dimensions,
                    "distance_metric": self.distance_metric,
                    "normalized": self.normalized,
                    "query_prefix": self.query_prefix,
                    "document_prefix": self.document_prefix,
                    "semantic_tag_allowlist": sorted(set(self.semantic_tag_allowlist)),
                    "query_template_version": self.query_template_version,
                    "document_template_version": self.document_template_version,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            digest = sha256(payload.encode("utf-8")).hexdigest()[:16]
            object.__setattr__(self, "generation", f"embedding-{digest}")

    def format_query(self, text: str) -> str:
        return f"{self.query_prefix}{text.strip()}"

    def format_document(self, text: str) -> str:
        return f"{self.document_prefix}{text.strip()}"

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
            "distance_metric": self.distance_metric,
            "normalized": self.normalized,
            "query_prefix": self.query_prefix,
            "document_prefix": self.document_prefix,
            "semantic_tag_allowlist": list(self.semantic_tag_allowlist),
            "query_template_version": self.query_template_version,
            "document_template_version": self.document_template_version,
            "generation": self.generation,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> EmbeddingSpec:
        return cls(
            provider=str(value["provider"]),
            model_id=str(value["model_id"]),
            model_revision=str(value.get("model_revision") or "default"),
            dimensions=int(value["dimensions"]),
            distance_metric=str(value.get("distance_metric") or "cosine"),
            normalized=bool(value.get("normalized", True)),
            query_prefix=str(value.get("query_prefix") or ""),
            document_prefix=str(value.get("document_prefix") or ""),
            semantic_tag_allowlist=tuple(
                str(item) for item in value.get("semantic_tag_allowlist", ())
            ),
            query_template_version=str(value.get("query_template_version") or "v1"),
            document_template_version=str(value.get("document_template_version") or "v1"),
            generation=str(value.get("generation") or ""),
        )


@dataclass(frozen=True)
class EmbeddingJob:
    job_id: str
    memory_id: str
    generation: str
    content_hash: str
    source_sequence: int
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 3
    available_at: str = ""
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: str | None = None
    error_type: str | None = None
    error_hash: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(
        cls,
        *,
        memory_id: str,
        generation: str,
        content_hash: str,
        source_sequence: int,
        max_attempts: int = 3,
    ) -> EmbeddingJob:
        now = utc_now_iso()
        return cls(
            job_id=str(uuid4()),
            memory_id=memory_id,
            generation=generation,
            content_hash=content_hash,
            source_sequence=source_sequence,
            max_attempts=max_attempts,
            available_at=now,
            created_at=now,
            updated_at=now,
        )

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> EmbeddingJob:
        claimed_at = _utc(now)
        return replace(
            self,
            status="running",
            attempts=self.attempts + 1,
            lease_owner=worker_id,
            lease_token=str(uuid4()),
            lease_expires_at=_iso(claimed_at + timedelta(seconds=max(0.001, lease_seconds))),
            updated_at=_iso(claimed_at),
        )

    def succeed(self, *, now: datetime | None = None) -> EmbeddingJob:
        completed_at = _utc(now)
        return replace(
            self,
            status="succeeded",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            error_type=None,
            error_hash=None,
            updated_at=_iso(completed_at),
        )

    def supersede(self, *, now: datetime | None = None) -> EmbeddingJob:
        completed_at = _utc(now)
        return replace(
            self,
            status="superseded",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            updated_at=_iso(completed_at),
        )

    def fail(
        self,
        error: Exception,
        *,
        retry_base_seconds: float,
        retry_max_seconds: float,
        now: datetime | None = None,
    ) -> EmbeddingJob:
        failed_at = _utc(now)
        dead_lettered = self.attempts >= self.max_attempts
        delay = min(
            max(0.0, retry_max_seconds),
            max(0.0, retry_base_seconds) * (2 ** max(0, self.attempts - 1)),
        )
        return replace(
            self,
            status="dead_letter" if dead_lettered else "pending",
            available_at=_iso(failed_at if dead_lettered else failed_at + timedelta(seconds=delay)),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            error_type=type(error).__name__,
            error_hash=secure_hash({"type": type(error).__name__, "message": str(error)}),
            updated_at=_iso(failed_at),
        )

    def renew(
        self,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> EmbeddingJob:
        renewed_at = _utc(now)
        return replace(
            self,
            lease_expires_at=_iso(renewed_at + timedelta(seconds=max(0.001, lease_seconds))),
            updated_at=_iso(renewed_at),
        )

    def recover_expired_lease(self, *, now: datetime | None = None) -> EmbeddingJob:
        recovered_at = _utc(now)
        exhausted = self.attempts >= self.max_attempts
        return replace(
            self,
            status="dead_letter" if exhausted else "pending",
            available_at=_iso(recovered_at),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            error_type="LeaseExpired",
            error_hash=secure_hash({"type": "LeaseExpired", "job_id": self.job_id}),
            updated_at=_iso(recovered_at),
        )

    def is_available(self, *, now: datetime | None = None) -> bool:
        return self.status == "pending" and _parse(self.available_at) <= _utc(now)

    def is_lease_expired(self, *, now: datetime | None = None) -> bool:
        return (
            self.status == "running"
            and self.lease_expires_at is not None
            and _parse(self.lease_expires_at) <= _utc(now)
        )


@dataclass(frozen=True)
class VectorRecord:
    memory_id: str
    spec: EmbeddingSpec
    content_hash: str
    source_sequence: int
    vector: tuple[float, ...]
    embedded_at: str = field(default_factory=utc_now_iso)


@dataclass(frozen=True)
class VectorHit:
    memory_id: str
    distance: float
    similarity: float


def canonical_memory_text(
    record: MemoryRecord,
    *,
    semantic_tag_allowlist: tuple[str, ...] = (),
) -> str:
    allowed_tags = set(semantic_tag_allowlist)
    tags = sorted(
        tag for tag in set(record.tags) if tag in allowed_tags and _SEMANTIC_TAG_RE.fullmatch(tag)
    )
    parts = [f"memory_type: {record.memory_type}", f"content: {record.content.strip()}"]
    if tags:
        parts.append(f"tags: {', '.join(tags)}")
    return "\n".join(parts)


def embedding_content_hash(record: MemoryRecord, spec: EmbeddingSpec) -> str:
    payload = spec.format_document(
        canonical_memory_text(
            record,
            semantic_tag_allowlist=spec.semantic_tag_allowlist,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


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
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
