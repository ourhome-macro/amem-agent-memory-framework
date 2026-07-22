from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agent_memory_runtime.config import HybridRetrievalConfig, RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    SemanticCircuitOpenError,
    StoreError,
)
from agent_memory_runtime.governance.retention import (
    RetentionAction,
    RetentionExecutor,
    RetentionPlan,
)
from agent_memory_runtime.memory.embeddings import (
    CallableEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingSpec,
    canonical_memory_text,
    embedding_content_hash,
    load_embedding_environment,
)
from agent_memory_runtime.memory.embeddings.base import validate_vector
from agent_memory_runtime.memory.retrieval import SemanticRetriever
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.memory.stores.sqlite_manager import _MIGRATIONS
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_hybrid_retrieval_finds_zero_lexical_overlap_with_sqlite_vec(tmp_path) -> None:
    provider = _provider(
        model="semantic-zero-overlap",
        vectors={
            "north star workshop": [1.0, 0.0, 0.0],
            "mend my vehicle": [1.0, 0.0, 0.0],
            "evening playlist": [0.0, 1.0, 0.0],
        },
    )
    path = tmp_path / "hybrid.sqlite"
    stores = _stores_with_provider(path, provider)
    stores.memory_store.upsert(_record("garage", "North Star workshop is preferred."))
    stores.memory_store.upsert(_record("music", "The evening playlist uses instrumental tracks."))
    assert stores.embedding_worker().run_until_idle().succeeded == 2
    runtime = _runtime(stores)

    selected, trace = runtime.retrieve(_query("Where should I mend my vehicle?"))

    assert [record.memory_id for record in selected] == ["garage"]
    assert trace.retrieval_legs == ("lexical", "semantic")
    assert trace.lexical_candidate_count == 0
    assert trace.semantic_candidate_count == 1
    assert trace.embedding_coverage == 1.0
    assert trace.score_breakdown["garage"]["lexical"] == 0.0
    assert trace.score_breakdown["garage"]["semantic"] > 0
    assert trace.score_breakdown["garage"]["fusion"] > 0
    with sqlite3.connect(path) as connection:
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'memory_fts'"
        ).fetchone()[0]
    assert "USING fts5" in definition
    runtime.close()


def test_vector_acl_filter_runs_before_semantic_top_k(tmp_path) -> None:
    provider = _provider(
        model="acl-prefilter",
        vectors={
            "permission filtered request": [1.0, 0.0, 0.0],
            "forbidden perfect match": [1.0, 0.0, 0.0],
            "allowed useful match": [0.8, 0.6, 0.0],
        },
    )
    stores = _stores_with_provider(tmp_path / "acl.sqlite", provider)
    stores.memory_store.upsert(
        _record(
            "forbidden",
            "Forbidden perfect match.",
            agent_id="other-agent",
        )
    )
    stores.memory_store.upsert(_record("allowed", "Allowed useful match."))
    stores.embedding_worker().run_until_idle()
    runtime = _runtime(
        stores,
        hybrid=HybridRetrievalConfig(
            semantic_candidate_limit=1,
            min_semantic_similarity=0.2,
        ),
    )

    selected, trace = runtime.retrieve(_query("Permission filtered request"))

    assert [record.memory_id for record in selected] == ["allowed"]
    assert trace.semantic_candidate_count == 1
    assert "forbidden" not in trace.score_breakdown
    runtime.close()


def test_sensitive_memory_is_not_copied_into_search_indexes(tmp_path) -> None:
    provider = _provider(model="sensitive-minimization", vectors={})
    path = tmp_path / "sensitive.sqlite"
    stores = _stores_with_provider(path, provider)
    sensitive = replace(
        _record("sensitive", "Private credential material."),
        labels=("private", "sensitive"),
        tags=("credential",),
    )

    stores.memory_store.upsert(sensitive)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_id = 'sensitive'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE memory_id = 'sensitive'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_acl WHERE memory_id = 'sensitive'"
        ).fetchone()[0] == 0
    assert stores.embedding_jobs.list_jobs(generation=provider.spec.generation) == []


def test_semantic_timeout_degrades_to_completed_fts5_leg(tmp_path) -> None:
    provider = _provider(
        model="slow-query",
        vectors={"refund status": [1.0, 0.0, 0.0]},
        query_delay_seconds=0.05,
    )
    stores = _stores_with_provider(tmp_path / "timeout.sqlite", provider)
    stores.memory_store.upsert(_record("refund", "Refund status awaits bank approval."))
    stores.embedding_worker().run_until_idle()
    runtime = _runtime(
        stores,
        hybrid=HybridRetrievalConfig(
            semantic_timeout_ms=5,
            min_semantic_similarity=0.2,
            semantic_max_concurrency=1,
        ),
    )

    selected, trace = runtime.retrieve(_query("refund status"))

    assert [record.memory_id for record in selected] == ["refund"]
    assert trace.retrieval_legs == ("lexical",)
    assert trace.lexical_candidate_count == 1
    assert trace.semantic_timed_out is True
    selected_again, second_trace = runtime.retrieve(_query("refund status"))
    assert [record.memory_id for record in selected_again] == ["refund"]
    assert second_trace.semantic_error_type == "SemanticBulkheadRejected"
    time.sleep(0.08)
    runtime.close()


def test_revise_supersedes_old_job_and_safe_upsert_preserves_ready_vector(tmp_path) -> None:
    provider = _provider(
        model="outbox-fencing",
        vectors={
            "old preference": [1.0, 0.0, 0.0],
            "new preference": [0.0, 1.0, 0.0],
        },
    )
    path = tmp_path / "outbox.sqlite"
    stores = _stores_with_provider(path, provider)
    old = _record("preference", "Old preference.", sequence=1)
    revised = replace(old, content="New preference.", last_event_sequence=2)
    stores.memory_store.upsert(old)
    stores.memory_store.upsert(revised)

    jobs = stores.embedding_jobs.list_jobs(generation=provider.spec.generation)
    assert {job.status for job in jobs} == {"pending", "superseded"}
    assert stores.embedding_worker().run_until_idle().succeeded == 1
    with sqlite3.connect(path) as connection:
        before = connection.execute(
            """
            SELECT vector_id, content_hash, source_sequence
            FROM memory_embeddings WHERE memory_id = 'preference'
            """
        ).fetchone()
    assert before[1:] == (
        embedding_content_hash(revised, provider.spec),
        2,
    )

    same_content_new_event = replace(revised, last_event_sequence=3)
    stores.memory_store.upsert(same_content_new_event)
    with sqlite3.connect(path) as connection:
        after = connection.execute(
            """
            SELECT vector_id, content_hash, source_sequence
            FROM memory_embeddings WHERE memory_id = 'preference'
            """
        ).fetchone()
    assert after[0] == before[0]
    assert after[1] == before[1]
    assert after[2] == 3


def test_tombstone_replay_cannot_resurrect_memory_or_vector(tmp_path) -> None:
    provider = _provider(
        model="tombstone",
        vectors={"erased memory": [1.0, 0.0, 0.0]},
    )
    stores = _stores_with_provider(tmp_path / "tombstone.sqlite", provider)
    runtime = _runtime(stores)
    record = runtime.ingest(
        Event(
            event_id="erase-event",
            kind="message.created",
            actor_id="user-1",
            session_id="s1",
            tenant_id="tenant-1",
            user_id="user-1",
            agent_id="assistant",
            labels=("private",),
            payload={
                "agent_id": "assistant",
                "subject_id": "user-1",
                "text": "Erased memory.",
            },
        )
    ).records[0]
    stores.embedding_worker().run_until_idle()
    assert stores.vector_index.ready_count(generation=provider.spec.generation) == 1

    RetentionExecutor(
        memory_store=stores.memory_store,
        audit_store=stores.audit_store,
        tombstone_store=stores.tombstone_store,
        transaction_manager=stores,
    ).apply(
        RetentionPlan(
            actions=(RetentionAction(record.memory_id, "delete", "user_erasure"),),
            current_sequence=1,
        ),
        snapshot=runtime.snapshot(),
    )
    runtime.replay()

    assert stores.memory_store.get(record.memory_id) is None
    assert stores.vector_index.ready_count(generation=provider.spec.generation) == 0
    assert stores.embedding_jobs.list_jobs(generation=provider.spec.generation) == []
    runtime.close()


def test_generation_activation_requires_coverage_and_drained_outbox(tmp_path) -> None:
    provider_a = _provider(model="generation-a", vectors={"stable": [1.0, 0.0, 0.0]})
    provider_b = _provider(model="generation-b", vectors={"stable": [0.0, 1.0, 0.0]})
    path = tmp_path / "generation.sqlite"
    stores = _stores_with_provider(path, provider_a)
    stores.memory_store.upsert(_record("stable", "Stable memory."))
    stores.embedding_worker().run_until_idle()
    stores.embedding_generations.register(provider_b.spec, status="backfill")
    stores.enqueue_embedding_backfill()

    generation_status = {
        item["generation"]: item for item in stores.semantic_status()["generations"]
    }
    assert generation_status[provider_b.spec.generation]["embedding_coverage"] == 0.0
    assert generation_status[provider_b.spec.generation]["job_status_counts"] == {
        "pending": 1
    }

    with pytest.raises(StoreError, match="coverage"):
        stores.activate_embedding_generation(provider_b.spec.generation)

    report = stores.embedding_worker(provider_b).run_until_idle()
    assert report.succeeded == 1
    activated = stores.activate_embedding_generation(provider_b.spec.generation)
    assert activated.generation == provider_b.spec.generation
    stores.delete_retired_embedding_generation(provider_a.spec.generation)
    assert stores.embedding_generations.get(provider_a.spec.generation) is None
    reopened = SQLiteStoreBundle(path, embedding_provider=provider_b)
    assert reopened.semantic_status()["active_generation"] == provider_b.spec.generation


def test_initial_embedding_generation_requires_explicit_activation(tmp_path) -> None:
    provider = _provider(model="initial-gate", vectors={})
    path = tmp_path / "initial-gate.sqlite"

    with pytest.raises(StoreError, match="not active"):
        SQLiteStoreBundle(path, embedding_provider=provider)

    staging = SQLiteStoreBundle(path)
    assert staging.embedding_generations.active() is None
    assert staging.embedding_generations.get(provider.spec.generation) == provider.spec
    staging.activate_embedding_generation(provider.spec.generation)
    online = SQLiteStoreBundle(path, embedding_provider=provider)
    assert online.semantic_status()["active_generation"] == provider.spec.generation


def test_retired_generation_is_staled_and_must_be_rebuilt_before_rollback(tmp_path) -> None:
    provider_a = _provider(model="rollback-a", vectors={"original": [1.0, 0.0, 0.0]})
    provider_b = _provider(model="rollback-b", vectors={"revised": [0.0, 1.0, 0.0]})
    stores = _stores_with_provider(tmp_path / "rollback.sqlite", provider_a)
    original = _record("rollback", "Original content.", sequence=1)
    stores.memory_store.upsert(original)
    stores.embedding_worker(provider_a).run_until_idle()
    stores.embedding_generations.register(provider_b.spec, status="backfill")
    stores.enqueue_embedding_backfill()
    stores.embedding_worker(provider_b).run_until_idle()
    stores.activate_embedding_generation(provider_b.spec.generation)

    stores.memory_store.upsert(
        replace(original, content="Revised content.", last_event_sequence=2)
    )
    stores.embedding_worker(provider_b).run_until_idle()

    assert stores.vector_index.coverage(generation=provider_a.spec.generation) == 0.0
    with pytest.raises(StoreError, match="coverage"):
        stores.activate_embedding_generation(provider_a.spec.generation)

    stores.embedding_generations.register(provider_a.spec, status="backfill")
    stores.enqueue_embedding_backfill()
    stores.embedding_worker(provider_a).run_until_idle()
    stores.activate_embedding_generation(provider_a.spec.generation)
    assert stores.embedding_generations.active() == provider_a.spec


def test_semantic_provider_circuit_opens_after_bounded_failures(tmp_path) -> None:
    calls = 0
    spec = EmbeddingSpec(provider="test", model_id="broken", dimensions=3)

    def broken_query(_: str) -> list[float]:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider payload must not enter trace")

    provider = CallableEmbeddingProvider(
        spec,
        query_embedder=broken_query,
        document_embedder=lambda texts: [[1.0, 0.0, 0.0] for _ in texts],
    )
    stores = _stores_with_provider(tmp_path / "breaker.sqlite", provider)
    retriever = SemanticRetriever(
        provider=provider,
        vector_index=stores.vector_index,
        config=HybridRetrievalConfig(
            min_semantic_similarity=0.2,
            semantic_failure_threshold=2,
            semantic_cooldown_seconds=60,
        ),
    )

    with pytest.raises(RuntimeError):
        retriever.retrieve(_query("first"), limit=1)
    with pytest.raises(RuntimeError):
        retriever.retrieve(_query("second"), limit=1)
    with pytest.raises(SemanticCircuitOpenError):
        retriever.retrieve(_query("third"), limit=1)
    assert calls == 2


def test_invalid_query_vector_is_not_cached_and_opens_circuit(tmp_path) -> None:
    calls = 0
    spec = EmbeddingSpec(provider="test", model_id="invalid-vector", dimensions=3)

    class InvalidProvider:
        @property
        def spec(self) -> EmbeddingSpec:
            return spec

        def embed_query(self, _text: str) -> list[float]:
            nonlocal calls
            calls += 1
            return [float("nan"), 0.0, 1.0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

    provider = InvalidProvider()
    stores = _stores_with_provider(tmp_path / "invalid-vector.sqlite", provider)
    retriever = SemanticRetriever(
        provider=provider,
        vector_index=stores.vector_index,
        config=HybridRetrievalConfig(
            min_semantic_similarity=0.2,
            semantic_failure_threshold=1,
            semantic_cooldown_seconds=60,
        ),
    )

    with pytest.raises(EmbeddingDimensionError, match="finite"):
        retriever.retrieve(_query("invalid"), limit=1)
    with pytest.raises(SemanticCircuitOpenError):
        retriever.retrieve(_query("invalid"), limit=1)
    assert calls == 1


def test_embedding_worker_batches_documents_and_canonical_text_is_minimal(tmp_path) -> None:
    batch_sizes: list[int] = []
    spec = EmbeddingSpec(
        provider="test",
        model_id="batch",
        dimensions=3,
        semantic_tag_allowlist=("finance",),
    )

    def embed_documents(texts: list[str]) -> list[list[float]]:
        batch_sizes.append(len(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    provider = CallableEmbeddingProvider(
        spec,
        query_embedder=lambda _text: [1.0, 0.0, 0.0],
        document_embedder=embed_documents,
    )
    stores = _stores_with_provider(tmp_path / "batch.sqlite", provider)
    for index in range(3):
        stores.memory_store.upsert(
            replace(
                _record(f"batch-{index}", f"Batch content {index}."),
                tags=("finance", "secret-customer-tag"),
                metadata={"raw_email": "private@example.com"},
            )
        )

    report = stores.embedding_worker(batch_size=3).run_until_idle()
    record = stores.memory_store.get("batch-0")
    assert record is not None
    text = canonical_memory_text(
        record,
        semantic_tag_allowlist=spec.semantic_tag_allowlist,
    )

    assert report.succeeded == 3
    assert batch_sizes == [3]
    assert "finance" in text
    assert "secret-customer-tag" not in text
    assert "private@example.com" not in text
    assert "batch-0" not in text
    with pytest.raises(EmbeddingDimensionError, match="finite"):
        validate_vector([float("nan"), 0.0, 1.0], spec)


def test_v5_database_migrates_and_backfills_fts_acl_projection(tmp_path) -> None:
    path = tmp_path / "migrate-v5.sqlite"
    record = _record("legacy-memory", "Legacy refund status.")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        for migration in _MIGRATIONS[:5]:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, '2026-07-21T00:00:00+00:00')
                """,
                (migration.version, migration.name, migration.checksum),
            )
        connection.execute("PRAGMA user_version=5")
        connection.execute(
            """
            INSERT INTO memories(
                memory_id, payload, tenant_id, user_id, agent_id, session_id,
                layer, status, memory_type, scope, updated_at, salience,
                search_indexed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                record.memory_id,
                json.dumps(record.to_dict(), sort_keys=True),
                record.tenant_id,
                record.user_id,
                record.agent_id,
                record.session_id,
                record.layer,
                record.status,
                record.memory_type,
                record.scope,
                record.updated_at,
                record.salience,
            ),
        )

    stores = SQLiteStoreBundle(path)
    selected = stores.memory_store.query_records(_query("refund status"), limit=5)
    with sqlite3.connect(path) as connection:
        fts_count = connection.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_id = 'legacy-memory'"
        ).fetchone()[0]
        acl_count = connection.execute(
            "SELECT COUNT(*) FROM memory_acl WHERE memory_id = 'legacy-memory'"
        ).fetchone()[0]

    assert stores.schema_version == 6
    assert [item.memory_id for item in selected] == ["legacy-memory"]
    assert fts_count == 1
    assert acl_count == 1


def test_embedding_environment_requires_dimensions_and_online_threshold(monkeypatch) -> None:
    monkeypatch.setenv("AMEM_EMBEDDING_MODEL", "multilingual-model")
    monkeypatch.setenv("AMEM_EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("AMEM_EMBEDDING_MIN_SIMILARITY", "")

    with pytest.raises(EmbeddingConfigurationError, match="MIN_SIMILARITY"):
        load_embedding_environment(require_online_threshold=True)

    monkeypatch.setenv("AMEM_EMBEDDING_MIN_SIMILARITY", "0.42")
    environment = load_embedding_environment(require_online_threshold=True)

    assert environment.provider is not None
    assert environment.provider.spec.dimensions == 1024
    assert environment.min_similarity == 0.42


def _stores_with_provider(
    path: Path,
    provider: EmbeddingProvider,
) -> SQLiteStoreBundle:
    staging = SQLiteStoreBundle(path)
    staging.embedding_generations.register(provider.spec, status="backfill")
    staging.activate_embedding_generation(provider.spec.generation)
    return SQLiteStoreBundle(path, embedding_provider=provider)


def _runtime(
    stores: SQLiteStoreBundle,
    *,
    hybrid: HybridRetrievalConfig | None = None,
) -> AgentMemoryRuntime:
    return AgentMemoryRuntime(
        config=RuntimeConfig(
            hybrid_retrieval=hybrid or HybridRetrievalConfig(min_semantic_similarity=0.2)
        ),
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        audit_store=stores.audit_store,
        derivation_queue=stores.derivation_queue,
        tombstone_store=stores.tombstone_store,
        transaction_manager=stores,
    )


def _record(
    memory_id: str,
    content: str,
    *,
    agent_id: str = "assistant",
    sequence: int = 1,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        memory_type="belief",
        scope="private",
        layer="working",
        session_id="s1",
        subject_id="user-1",
        content=content,
        source_event_ids=(f"event-{memory_id}",),
        rule_id="test.semantic",
        owner_id=agent_id,
        labels=("private",),
        tenant_id="tenant-1",
        user_id="user-1",
        agent_id=agent_id,
        created_at="2026-07-22T00:00:00+00:00",
        updated_at="2026-07-22T00:00:00+00:00",
        last_event_sequence=sequence,
    )


def _query(text: str) -> MemoryQuery:
    return MemoryQuery(
        agent_id="assistant",
        text=text,
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="s1",
    )


def _provider(
    *,
    model: str,
    vectors: dict[str, list[float]],
    query_delay_seconds: float = 0.0,
) -> CallableEmbeddingProvider:
    spec = EmbeddingSpec(provider="test", model_id=model, dimensions=3)

    def vector(text: str) -> list[float]:
        lowered = text.casefold()
        for marker, value in vectors.items():
            if marker in lowered:
                return list(value)
        return [0.0, 0.0, 1.0]

    def query_embedder(text: str) -> list[float]:
        if query_delay_seconds:
            time.sleep(query_delay_seconds)
        return vector(text)

    return CallableEmbeddingProvider(
        spec,
        query_embedder=query_embedder,
        document_embedder=lambda texts: [vector(text) for text in texts],
    )
