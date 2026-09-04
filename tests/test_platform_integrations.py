from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.integrations.langchain import AgentMemoryLangChainRetriever
from agent_memory_runtime.memory.embeddings import (
    CallableEmbeddingProvider,
    EmbeddingSpec,
    QdrantVectorIndex,
    VectorRecord,
)
from agent_memory_runtime.memory.stores import SQLiteStoreBundle


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


def test_qdrant_vector_index_creates_acl_payload_index_before_upsert() -> None:
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

    indexed_fields = {item["field_name"] for item in client.payload_indexes}
    assert "acl_principals" in indexed_fields
    assert "tenant_id" in indexed_fields
    assert "user_id" in indexed_fields


def test_qdrant_shared_memory_without_visible_to_indexes_global_acl() -> None:
    client = _FakeQdrantClient()
    spec = EmbeddingSpec(provider="test", model_id="bge-m3", dimensions=3)
    index = QdrantVectorIndex(collection_name="memories", client=client)
    memory = replace(
        _memory(),
        visibility="shared",
        labels=("public",),
        visible_to=(),
    )

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
    assert point.payload["acl_principals"] == ["*", "assistant"]


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


def test_qdrant_vector_index_creates_collection_on_first_upsert() -> None:
    client = _FakeQdrantClient(collection_exists=False)
    spec = EmbeddingSpec(provider="test", model_id="bge-m3", dimensions=3)
    index = QdrantVectorIndex(collection_name="memories", client=client)

    index.upsert(
        VectorRecord(
            memory_id="memory-1",
            spec=spec,
            content_hash="hash-1",
            source_sequence=7,
            vector=(1.0, 0.0, 0.0),
        ),
        memory=_memory(),
    )

    assert client.created_collections == ["memories"]
    assert client.upserts


def test_sqlite_bundle_can_publish_embedding_outbox_to_qdrant_projection(tmp_path) -> None:
    client = _FakeQdrantClient(collection_exists=True)
    provider = CallableEmbeddingProvider(
        EmbeddingSpec(provider="test", model_id="bundle-qdrant", dimensions=3),
        query_embedder=lambda _text: [1.0, 0.0, 0.0],
        document_embedder=lambda _texts: [[1.0, 0.0, 0.0]],
    )
    index = QdrantVectorIndex(collection_name="memories", client=client)
    staging = SQLiteStoreBundle(tmp_path / "qdrant-runtime.sqlite")
    staging.embedding_generations.register(provider.spec, status="backfill")
    staging.activate_embedding_generation(provider.spec.generation)
    stores = SQLiteStoreBundle(
        tmp_path / "qdrant-runtime.sqlite",
        embedding_provider=provider,
        vector_index=index,
    )

    stores.memory_store.upsert(_memory())
    report = stores.embedding_worker(provider).run_until_idle()

    assert report.succeeded == 1
    assert client.upserts[0]["collection_name"] == "memories"
    assert client.upserts[0]["points"][0].payload["memory_id"] == "memory-1"


def test_langchain_adapter_reports_missing_optional_dependency() -> None:
    if AgentMemoryLangChainRetriever.__mro__[1] is not object:
        pytest.skip("langchain-core is installed in this environment")
    with pytest.raises(ImportError, match="langchain extra"):
        AgentMemoryLangChainRetriever()


class _FakeQdrantClient:
    def __init__(
        self,
        *,
        search_rows: list[object] | None = None,
        count: int = 0,
        collection_exists: bool = True,
    ) -> None:
        self.search_rows = search_rows or []
        self.count_value = count
        self.collection_exists_value = collection_exists
        self.upserts: list[dict[str, object]] = []
        self.searches: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []
        self.created_collections: list[str] = []
        self.payload_indexes: list[dict[str, object]] = []

    def upsert(self, **kwargs: object) -> None:
        self.upserts.append(kwargs)

    def search(self, **kwargs: object) -> list[object]:
        self.searches.append(kwargs)
        return self.search_rows

    def delete(self, **kwargs: object) -> None:
        self.deletes.append(kwargs)

    def count(self, **_: object) -> object:
        return SimpleNamespace(count=self.count_value)

    def collection_exists(self, **kwargs: object) -> bool:
        return self.collection_exists_value

    def create_collection(self, **kwargs: object) -> None:
        self.created_collections.append(str(kwargs["collection_name"]))
        self.collection_exists_value = True

    def create_payload_index(self, **kwargs: object) -> None:
        self.payload_indexes.append(kwargs)


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
            level="L1",
            visibility="private",
            priority=0.5,
        ),
        labels=("private",),
    )
