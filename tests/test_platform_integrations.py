from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.integrations.langchain import AgentMemoryLangChainRetriever
from agent_memory_runtime.memory.embeddings import EmbeddingSpec, QdrantVectorIndex, VectorRecord


def test_qdrant_vector_index_writes_memory_filter_payload() -> None:
    client = _FakeQdrantClient()
    spec = EmbeddingSpec(provider="test", model_id="bge-m3", dimensions=3)
    index = QdrantVectorIndex(collection_name="memories", client=client)
    memory = _memory()

    index.upsert(
        VectorRecord(
            memory_id=memory.memory_id,
            spec=spec,
            content_hash="hash-1",
            source_sequence=7,
            vector=(1.0, 0.0, 0.0),
        ),
        memory=memory,
    )

    point = client.upserts[0]["points"][0]
    assert point.payload["memory_id"] == "memory-1"
    assert point.payload["tenant_id"] == "tenant-1"
    assert point.payload["user_id"] == "user-1"
    assert point.payload["session_id"] == "session-1"
    assert point.payload["memory_status"] == "active"
    assert point.payload["acl_principals"] == ["assistant"]


def test_qdrant_vector_index_search_applies_identity_acl_and_generation_filters() -> None:
    client = _FakeQdrantClient(
        search_rows=[
            SimpleNamespace(score=0.91, payload={"memory_id": "memory-1"}),
        ]
    )
    spec = EmbeddingSpec(provider="test", model_id="bge-m3", dimensions=3)
    index = QdrantVectorIndex(collection_name="memories", client=client)

    hits = index.search(
        [1.0, 0.0, 0.0],
        MemoryQuery(
            agent_id="assistant",
            text="query",
            tenant_id="tenant-1",
            user_id="user-1",
            session_id="session-1",
        ),
        spec=spec,
        limit=5,
    )

    qfilter = client.searches[0]["query_filter"]
    assert hits[0].memory_id == "memory-1"
    assert hits[0].similarity == 0.91
    conditions = _conditions(qfilter)
    assert ("generation", spec.generation) in conditions
    assert ("tenant_id", "tenant-1") in conditions
    assert ("session_id", "session-1") in conditions
    nested_should = [item for item in qfilter.must if getattr(item, "should", None)]
    assert any(
        any(getattr(condition, "is_empty", None) for condition in item.should or ())
        and ("user_id", "user-1") in _conditions(item)
        for item in nested_should
    )


def test_qdrant_coverage_uses_expected_count_provider() -> None:
    client = _FakeQdrantClient(count=9)
    index = QdrantVectorIndex(
        collection_name="memories",
        client=client,
        expected_count=lambda _generation: 10,
    )

    assert index.coverage(generation="embedding-generation") == 0.9


def test_langchain_adapter_reports_missing_optional_dependency() -> None:
    if AgentMemoryLangChainRetriever.__mro__[1] is not object:
        pytest.skip("langchain-core is installed in this environment")
    with pytest.raises(ImportError, match="langchain extra"):
        AgentMemoryLangChainRetriever()


class _FakeQdrantClient:
    def __init__(self, *, search_rows: list[object] | None = None, count: int = 0) -> None:
        self.search_rows = search_rows or []
        self.count_value = count
        self.upserts: list[dict[str, object]] = []
        self.searches: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []

    def upsert(self, **kwargs: object) -> None:
        self.upserts.append(kwargs)

    def search(self, **kwargs: object) -> list[object]:
        self.searches.append(kwargs)
        return self.search_rows

    def delete(self, **kwargs: object) -> None:
        self.deletes.append(kwargs)

    def count(self, **_: object) -> object:
        return SimpleNamespace(count=self.count_value)


def _conditions(qfilter: object) -> set[tuple[str, object]]:
    values = set()
    for condition in getattr(qfilter, "must", None) or []:
        key = getattr(condition, "key", None)
        match = getattr(condition, "match", None)
        if key is not None and match is not None:
            values.add((key, getattr(match, "value", None)))
        values.update(_conditions(condition))
    for condition in getattr(qfilter, "should", None) or []:
        key = getattr(condition, "key", None)
        match = getattr(condition, "match", None)
        if key is not None and match is not None:
            values.add((key, getattr(match, "value", None)))
        values.update(_conditions(condition))
    return values


def _memory() -> MemoryRecord:
    return replace(
        MemoryRecord(
            memory_id="memory-1",
            memory_type="episodic",
            scope="private",
            layer="working",
            session_id="session-1",
            subject_id="user-1",
            content="Preferred workshop is North Star.",
            source_event_ids=("event-1",),
            rule_id="test",
            owner_id="assistant",
            tenant_id="tenant-1",
            user_id="user-1",
            agent_id="assistant",
            status="active",
            last_event_sequence=7,
        ),
        labels=("private",),
    )
