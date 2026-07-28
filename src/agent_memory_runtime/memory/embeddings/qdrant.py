from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Any
from uuid import UUID

from agent_memory_runtime.domain.enums import MemoryLayer, MemorySessionPolicy, MemoryStatus
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.embeddings.base import validate_vector
from agent_memory_runtime.memory.embeddings.models import EmbeddingSpec, VectorHit, VectorRecord


class QdrantVectorIndex:
    """Qdrant implementation of the VectorIndex protocol.

    SQLite remains the truth store. This class only owns the semantic vector
    projection and must be fed by the existing embedding outbox/worker path.
    """

    def __init__(
        self,
        *,
        collection_name: str,
        client: Any | None = None,
        url: str | None = None,
        api_key: str | None = None,
        expected_count: Callable[[str], int] | None = None,
    ) -> None:
        if client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as error:  # pragma: no cover - optional dependency
                raise ImportError(
                    "QdrantVectorIndex requires the optional qdrant extra: "
                    'pip install "agent-memory-runtime[qdrant]"'
                ) from error
            client = QdrantClient(url=url, api_key=api_key)
        self.client = client
        self.collection_name = collection_name
        self.expected_count = expected_count

    def upsert(self, record: VectorRecord, *, memory: MemoryRecord | None = None) -> None:
        from qdrant_client import models

        validate_vector(record.vector, record.spec)
        payload = {
            "memory_id": record.memory_id,
            "generation": record.spec.generation,
            "model_id": record.spec.model_id,
            "model_revision": record.spec.model_revision,
            "dimensions": record.spec.dimensions,
            "content_hash": record.content_hash,
            "source_sequence": record.source_sequence,
            "status": "ready",
            "embedded_at": record.embedded_at,
        }
        if memory is not None:
            payload.update(qdrant_payload_from_memory(memory))
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=_point_id(record.memory_id, record.spec.generation),
                    vector=list(record.vector),
                    payload=payload,
                )
            ],
        )

    def search(
        self,
        vector: list[float],
        query: MemoryQuery,
        *,
        spec: EmbeddingSpec,
        limit: int,
    ) -> list[VectorHit]:
        from qdrant_client import models

        if limit <= 0:
            return []
        validate_vector(vector, spec)
        qfilter = models.Filter.model_validate(_query_filter(query, generation=spec.generation))
        if hasattr(self.client, "search"):
            rows = self.client.search(
                collection_name=self.collection_name,
                query_vector=vector,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
        else:
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
            rows = response.points
        hits: list[VectorHit] = []
        for row in rows:
            payload = _payload(row)
            memory_id = payload.get("memory_id")
            if memory_id is None:
                continue
            score = float(getattr(row, "score", 0.0))
            hits.append(
                VectorHit(
                    memory_id=str(memory_id),
                    distance=max(0.0, 1.0 - score),
                    similarity=max(-1.0, min(1.0, score)),
                )
            )
        return hits

    def delete_memory(self, memory_id: str, *, through_sequence: int | None = None) -> None:
        if through_sequence is None:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector={
                    "filter": {
                        "must": [
                            _match("memory_id", memory_id),
                        ]
                    }
                },
            )
            return
        self.client.delete(
            collection_name=self.collection_name,
            points_selector={
                "filter": {
                    "must": [
                        _match("memory_id", memory_id),
                        {"key": "source_sequence", "range": {"lte": through_sequence}},
                    ]
                }
            },
        )

    def coverage(self, *, generation: str) -> float:
        from qdrant_client import models

        count_result = self.client.count(
            collection_name=self.collection_name,
            count_filter=models.Filter.model_validate(
                {"must": [_match("generation", generation), _match("status", "ready")]}
            ),
            exact=True,
        )
        ready = int(getattr(count_result, "count", count_result if count_result is not None else 0))
        if self.expected_count is None:
            return 1.0 if ready > 0 else 0.0
        expected = max(0, int(self.expected_count(generation)))
        return 1.0 if expected == 0 else round(ready / expected, 6)


def _query_filter(query: MemoryQuery, *, generation: str) -> dict[str, object]:
    must: list[dict[str, object]] = [
        _match("generation", generation),
        _match("status", "ready"),
        _match("tenant_id", query.tenant_id),
        _match("memory_status", MemoryStatus.ACTIVE.value),
    ]
    should: list[dict[str, object]] = []
    if query.user_id is None:
        must.append(_missing("user_id"))
    else:
        must.append({"should": [_missing("user_id"), _match("user_id", query.user_id)]})

    if query.session_id is not None:
        policy = MemorySessionPolicy(query.session_policy)
        if policy is MemorySessionPolicy.EXACT:
            must.append(_match("session_id", query.session_id))
        elif policy is MemorySessionPolicy.PROFILE:
            must.append(
                {
                    "should": [
                        _match("session_id", query.session_id),
                        {"must_not": [_match("layer", MemoryLayer.WORKING.value)]},
                    ]
                }
            )

    for key, values in (
        ("scope", query.scopes),
        ("memory_type", query.memory_types),
        ("layer", query.layers),
    ):
        if values:
            must.append(_any(key, values))
    if query.tags:
        must.append(_any("tags", query.tags))
    should.extend([_match("acl_principals", "*"), _match("acl_principals", query.agent_id)])
    must.append({"should": should})
    return {"must": must}


def qdrant_payload_for_memory(
    *,
    tenant_id: str,
    user_id: str | None,
    agent_id: str | None,
    session_id: str,
    layer: str,
    status: str,
    memory_type: str,
    scope: str,
    tags: tuple[str, ...],
    acl_principals: tuple[str, ...],
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "layer": layer,
        "memory_status": status,
        "memory_type": memory_type,
        "scope": scope,
        "tags": list(tags),
        "acl_principals": list(acl_principals),
    }


def qdrant_payload_from_memory(record: MemoryRecord) -> dict[str, object]:
    return qdrant_payload_for_memory(
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        session_id=record.session_id,
        layer=record.layer,
        status=record.status,
        memory_type=record.memory_type,
        scope=record.scope,
        tags=tuple(record.tags),
        acl_principals=_acl_principals(record),
    )


def _acl_principals(record: MemoryRecord) -> tuple[str, ...]:
    if "sensitive" in set(record.labels):
        return ()
    if record.scope == "global":
        return ("*",)
    principals = set(record.visible_to)
    if record.agent_id:
        principals.add(record.agent_id)
    if record.owner_id:
        principals.add(record.owner_id)
    return tuple(sorted(principals))


def _point_id(memory_id: str, generation: str) -> str:
    digest = sha256(f"{generation}\0{memory_id}".encode()).hexdigest()
    return str(UUID(digest[:32]))


def _payload(row: object) -> dict[str, object]:
    payload = getattr(row, "payload", None)
    if isinstance(payload, dict):
        return payload
    if isinstance(row, dict):
        value = row.get("payload")
        return value if isinstance(value, dict) else {}
    return {}


def _match(key: str, value: object) -> dict[str, object]:
    return {"key": key, "match": {"value": value}}


def _any(key: str, values: tuple[str, ...]) -> dict[str, object]:
    return {"key": key, "match": {"any": list(values)}}


def _missing(key: str) -> dict[str, object]:
    return {"is_empty": {"key": key}}
