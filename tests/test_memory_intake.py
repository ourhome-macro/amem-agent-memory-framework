from __future__ import annotations

import asyncio

from agent_memory_runtime.agent import AgentRequest, ToolExecutionContext
from agent_memory_runtime.config import HybridRetrievalConfig, RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.embeddings import CallableEmbeddingProvider, EmbeddingSpec
from agent_memory_runtime.memory.intake import (
    AutoDreamAnalyzer,
    DreamCheckpoint,
    MemoryIntakeService,
    MemoryProposal,
    MemoryToolIdentity,
    build_memory_intake_tools,
)
from agent_memory_runtime.memory.intake.worker import AutoDreamWorker
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_save_memory_tool_applies_proposal_and_writes_audit_log() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)

    result = service.save_memory(
        {
            "kind": "preference.updated",
            "key": "java_style",
            "content": "Use explicit loops in Java examples.",
            "salience": 0.95,
            "confidence": 0.96,
        },
        identity=_identity(),
        idempotency_key="save-java-style",
    )

    assert result.status == "succeeded"
    assert result.event is None
    assert result.proposal_id == "memory-intake:save_memory:save-java-style"
    assert runtime.event_store.list_events() == []
    assert result.memory_ids == ("v3:belief:tenant-a:user-a:assistant:java_style",)
    record = runtime.memory_store.get(result.memory_ids[0])
    assert record is not None
    assert record.content == "Use explicit loops in Java examples."
    assert record.layer == "core"
    assert record.metadata["key"] == "java_style"
    assert record.metadata["proposal_id"] == "memory-intake:save_memory:save-java-style"
    assert record.rule_id == "proposal.direct.v1"
    assert record.version == 1
    audit_logs = runtime.audit_store.list_memory_logs()
    assert len(audit_logs) == 1
    assert audit_logs[0].proposal_id == result.proposal_id
    assert audit_logs[0].before_record is None
    assert audit_logs[0].after_record == record


def test_revise_memory_updates_same_profile_memory_and_preserves_sources() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)
    original = service.save_memory(
        {
            "kind": "belief.stated",
            "key": "database",
            "content": "The user uses MySQL.",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="save-db",
    )

    revised = service.revise_memory(
        {
            "kind": "belief.stated",
            "key": "database",
            "content": "The user uses PostgreSQL.",
            "target_memory_id": original.memory_ids[0],
            "operation": "supersede",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="revise-db",
    )

    assert revised.memory_ids == original.memory_ids
    record = runtime.memory_store.get(original.memory_ids[0])
    assert record is not None
    assert record.content == "The user uses PostgreSQL."
    assert record.last_operation == "revise"
    assert record.source_memory_ids == (original.memory_ids[0],)
    assert record.version == 2
    audit_logs = runtime.audit_store.list_memory_logs()
    assert len(audit_logs) == 2
    assert audit_logs[-1].before_record is not None
    assert audit_logs[-1].before_record.content == "The user uses MySQL."
    assert audit_logs[-1].after_record is not None
    assert audit_logs[-1].after_record.content == "The user uses PostgreSQL."


def test_forget_memory_tombstones_and_hides_authorized_memory() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)
    saved = service.save_memory(
        {
            "kind": "belief.stated",
            "key": "phone",
            "content": "The user's phone is 15500001111.",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="save-phone",
    )

    forgotten = service.forget_memory(
        {"memory_id": saved.memory_ids[0], "reason": "user_requested_erasure"},
        identity=_identity(),
        idempotency_key="forget-phone",
    )

    assert forgotten.status == "succeeded"
    assert forgotten.tombstoned_memory_ids == saved.memory_ids
    assert runtime.memory_store.get(saved.memory_ids[0]) is None
    assert runtime.tombstone_store.get(saved.memory_ids[0]) is not None
    audit_logs = runtime.audit_store.list_memory_logs()
    assert audit_logs[-1].action == "delete"
    assert audit_logs[-1].before_record is not None
    assert audit_logs[-1].after_record is None
    context = runtime.project(
        MemoryQuery(
            agent_id="assistant",
            text="phone",
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="s1",
            session_policy="profile",
        )
    )
    assert context.selected_memory_ids == ()


def test_memory_intake_agent_tools_execute_with_run_identity() -> None:
    async def scenario() -> None:
        runtime = AgentMemoryRuntime()
        tools = {tool.name: tool for tool in build_memory_intake_tools(runtime)}
        request = AgentRequest(
            agent_id="assistant",
            message="remember",
            actor_id="user-a",
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="s1",
            request_id="r1",
        )
        output = await tools["save_memory"].execute(
            {
                "kind": "preference.updated",
                "key": "answer_language",
                "content": "Answer in Chinese.",
            },
            ToolExecutionContext(call_id="tool-save", run_id="run-1", request=request, attempt=1),
        )

        assert output["status"] == "succeeded"
        assert output["event_id"] == "memory-intake:save_memory:tool-save"
        assert output["memory_ids"] == [
            "v3:belief:tenant-a:user-a:assistant:answer_language"
        ]

    asyncio.run(scenario())


def test_auto_dream_analyzes_incremental_messages_and_advances_checkpoint() -> None:
    events = [
        _message(
            "evt-1",
            1,
            "\u4ee5\u540e Java \u793a\u4f8b\u4e0d\u8981\u7528 Lambda",
        ),
        _message("evt-2", 2, "\u666e\u901a\u804a\u5929\u4e0d\u5e94\u8be5\u5019\u9009"),
        _message("evt-3", 3, "\u5fd8\u6389\u6211\u7684\u624b\u673a\u53f7"),
    ]

    report = AutoDreamAnalyzer().analyze(
        events=events,
        records=[],
        checkpoint=DreamCheckpoint(last_processed_sequence=1),
    )

    assert report.source_sequence_range == (2, 3)
    assert report.checkpoint.last_processed_sequence == 3
    assert [proposal.action for proposal in report.proposals] == ["needs_review"]
    assert report.proposals[0].reason == "explicit_forget_marker"


def test_auto_dream_reports_typed_event_without_derived_memory() -> None:
    event = Event(
        event_id="typed-pref",
        sequence=4,
        kind="preference.updated",
        actor_id="user-a",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="assistant",
        payload={
            "agent_id": "assistant",
            "subject_id": "user-a",
            "key": "answer_style",
            "preference": "Use direct answers.",
        },
    )

    report = AutoDreamAnalyzer().analyze(events=[event], records=[])

    assert len(report.proposals) == 1
    assert report.proposals[0].action == "create"
    assert report.proposals[0].reason == "typed_event_without_derived_memory"


def test_auto_dream_structures_current_state_metadata_and_service_persists() -> None:
    event = Event(
        event_id="typed-renewal-state",
        sequence=5,
        kind="belief.stated",
        actor_id="user-a",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="assistant",
        payload={
            "agent_id": "assistant",
            "subject_id": "user-a",
            "key": "billing_auto_renewal",
            "belief": "\u8d26\u5355\u81ea\u52a8\u7eed\u8d39\u5df2\u7ecf\u5173\u95ed\u3002",
        },
    )

    proposal = AutoDreamAnalyzer().analyze(events=[event], records=[]).proposals[0]

    assert proposal.action == "create"
    assert proposal.metadata["semantic_state_schema"] == "current_state.v1"
    assert proposal.metadata["semantic_state_attribute"] == "enabled"
    assert proposal.metadata["semantic_state_value"] == "off"
    assert proposal.metadata["semantic_state_temporal_scope"] == "current"

    runtime = AgentMemoryRuntime()
    result = runtime.apply_memory_proposal(proposal)

    assert result.status == "succeeded"
    record = runtime.memory_store.get(result.memory_ids[0])
    assert record is not None
    assert record.metadata["semantic_state_value"] == "off"
    audit_log = runtime.audit_store.list_memory_logs()[0]
    assert audit_log.after_record is not None
    assert audit_log.after_record.metadata["semantic_state_attribute"] == "enabled"


def test_auto_dream_detects_duplicate_active_memories() -> None:
    records = [
        _record("m1", "Repeated memory", ("e1",)),
        _record("m2", "Repeated memory", ("e2",)),
    ]

    report = AutoDreamAnalyzer().analyze(events=[], records=records)

    assert [proposal.action for proposal in report.proposals] == ["reinforce", "archive"]
    assert report.proposals[0].action == "reinforce"
    assert report.proposals[0].target_memory_id == "m1"
    assert report.proposals[0].source_memory_ids == ("m2",)
    assert report.proposals[0].reason == "semantic_duplicate_of:m1"


def test_auto_dream_routes_current_state_conflict_to_review_across_keys() -> None:
    records = [
        _record(
            "renewal-off",
            "\u8d26\u5355\u81ea\u52a8\u7eed\u8d39\u5df2\u7ecf\u5173\u95ed\u3002",
            ("e1",),
            key="billing_auto_renewal_closed",
        ),
        _record(
            "renewal-on",
            "\u8d26\u5355\u81ea\u52a8\u7eed\u8d39\u4ecd\u7136\u5f00\u542f\u3002",
            ("e2",),
            key="billing_auto_renewal_enabled",
        ),
    ]

    report = AutoDreamAnalyzer().analyze(events=[], records=records)

    reviews = [
        proposal
        for proposal in report.proposals
        if proposal.action == "needs_review"
        and proposal.reason.startswith("current_state_conflict_with:")
    ]
    assert len(reviews) == 1
    assert reviews[0].target_memory_id in {"renewal-off", "renewal-on"}
    assert reviews[0].metadata["semantic_state_attribute"] == "enabled"


def test_auto_dream_worker_persists_checkpoint_and_applies_proposals(tmp_path) -> None:
    stores = SQLiteStoreBundle(tmp_path / "dream-runtime.sqlite")
    runtime = _runtime_from_stores(stores)
    runtime.ingest(
        _message(
            "dream-message-1",
            0,
            "\u4ee5\u540e Python \u4ee3\u7801\u8981\u5199\u7c7b\u578b\u6807\u6ce8",
        )
    )

    runtime.on_session_end(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="assistant",
        session_id="s1",
    )
    report = AutoDreamWorker(runtime=runtime, store=stores.dream_store).run_once()

    assert report.analyzed is True
    assert report.applied == 1
    assert stores.memory_store.list_records()[0].content.endswith("\u7c7b\u578b\u6807\u6ce8")
    assert stores.audit_store.list_memory_logs()[0].proposal_id.startswith("auto-dream:")
    persisted = stores.dream_store.checkpoint_for(stores.dream_store.list_jobs()[0])
    assert persisted.last_processed_sequence == 1


def test_auto_dream_worker_retains_review_for_conflicting_same_key(tmp_path) -> None:
    stores = SQLiteStoreBundle(tmp_path / "dream-review.sqlite")
    runtime = _runtime_from_stores(stores)
    stores.memory_store.upsert(_record("db-1", "The user uses MySQL.", ("e1",), key="database"))
    stores.memory_store.upsert(
        _record("db-2", "The user uses PostgreSQL.", ("e2",), key="database")
    )

    runtime.schedule_auto_dream(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="assistant",
        session_id="s1",
        reason="test",
    )
    report = runtime.run_auto_dream_once()

    assert report.review == 1
    reviews = stores.dream_store.list_reviews()
    assert reviews[0]["status"] == "needs_review"
    assert "same_key_conflict" in str(reviews[0]["reason"])


def test_duplicate_proposal_is_idempotent_and_does_not_reinforce_twice() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)

    first = service.save_memory(
        {
            "kind": "preference.updated",
            "key": "answer_style",
            "content": "Use concise answers.",
        },
        identity=_identity(),
        idempotency_key="same-proposal",
    )
    second = service.save_memory(
        {
            "kind": "preference.updated",
            "key": "answer_style",
            "content": "Use concise answers.",
        },
        identity=_identity(),
        idempotency_key="same-proposal",
    )

    record = runtime.memory_store.get(first.memory_ids[0])
    assert second.memory_ids == first.memory_ids
    assert record is not None
    assert record.version == 1
    assert record.reinforcement_count == 1
    assert len(runtime.audit_store.list_memory_logs()) == 1


def test_optimistic_lock_conflict_is_retryable() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)
    saved = service.save_memory(
        {
            "kind": "belief.stated",
            "key": "database",
            "content": "The user uses MySQL.",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="lock-save",
    )
    record = runtime.memory_store.get(saved.memory_ids[0])
    assert record is not None

    result = runtime.apply_memory_proposal(
        _proposal(
            proposal_id="manual-stale-revise",
            action="revise",
            target_memory_id=record.memory_id,
            key="database",
            content="The user uses PostgreSQL.",
            expected_version=record.version - 1,
        )
    )

    assert result.status == "conflict"
    assert result.retryable is True
    unchanged = runtime.memory_store.get(record.memory_id)
    assert unchanged is not None
    assert unchanged.content == "The user uses MySQL."


def test_cross_tenant_proposal_is_rejected() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)
    saved = service.save_memory(
        {
            "kind": "belief.stated",
            "key": "database",
            "content": "The user uses MySQL.",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="tenant-save",
    )

    result = runtime.apply_memory_proposal(
        _proposal(
            proposal_id="manual-cross-tenant",
            action="revise",
            target_memory_id=saved.memory_ids[0],
            key="database",
            content="The user uses PostgreSQL.",
            tenant_id="tenant-b",
        )
    )

    assert result.status == "rejected"
    assert result.reason == "cross_tenant_write"


def test_visible_to_expansion_requires_review() -> None:
    runtime = AgentMemoryRuntime()
    service = MemoryIntakeService(runtime)
    saved = service.save_memory(
        {
            "kind": "belief.stated",
            "key": "database",
            "content": "The user uses MySQL.",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="visibility-save",
    )

    result = runtime.apply_memory_proposal(
        _proposal(
            proposal_id="manual-expand-visibility",
            action="revise",
            target_memory_id=saved.memory_ids[0],
            key="database",
            content="The user uses MySQL.",
            scope="shared",
            visible_to=("assistant", "agent-b"),
        )
    )

    assert result.status == "needs_review"
    assert result.reason == "visible_to_expansion_requires_review"


def test_sqlite_write_succeeds_and_embedding_outbox_is_retained(tmp_path) -> None:
    provider = CallableEmbeddingProvider(
        EmbeddingSpec(provider="test", model_id="proposal-outbox", dimensions=3),
        query_embedder=lambda _text: [1.0, 0.0, 0.0],
        document_embedder=lambda _texts: (_ for _ in ()).throw(RuntimeError("qdrant down")),
    )
    stores = _stores_with_provider(tmp_path / "proposal-outbox.sqlite", provider)
    runtime = AgentMemoryRuntime(
        config=RuntimeConfig(hybrid_retrieval=HybridRetrievalConfig(enable_semantic=False)),
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        tombstone_store=stores.tombstone_store,
        audit_store=stores.audit_store,
        transaction_manager=stores,
    )
    service = MemoryIntakeService(runtime)

    result = service.save_memory(
        {
            "kind": "preference.updated",
            "key": "java_style",
            "content": "Use explicit loops in Java examples.",
            "layer": "core",
        },
        identity=_identity(),
        idempotency_key="sqlite-outbox",
    )

    assert result.status == "succeeded"
    assert stores.memory_store.get(result.memory_ids[0]) is not None
    jobs = stores.embedding_jobs.list_jobs(generation=provider.spec.generation)
    assert len(jobs) == 1
    assert jobs[0].status == "pending"
    report = stores.embedding_worker(provider).run_until_idle()
    assert report.failed == 1
    assert stores.memory_store.get(result.memory_ids[0]) is not None


def test_default_event_ingest_is_audit_only_not_memory_derivation() -> None:
    runtime = AgentMemoryRuntime()

    result = runtime.ingest(
        Event(
            event_id="audit-only-event",
            kind="message.created",
            actor_id="user-a",
            session_id="s1",
            tenant_id="tenant-a",
            user_id="user-a",
            agent_id="assistant",
            payload={
                "agent_id": "assistant",
                "subject_id": "user-a",
                "text": "This should not become memory from Event.",
            },
        )
    )

    assert result.records == ()
    assert runtime.memory_store.list_records() == []
    assert runtime.event_store.get("audit-only-event") is not None
    assert runtime.audit_store.list_envelopes()[-1].audit_type == "memory_event_audit"


def _identity() -> MemoryToolIdentity:
    return MemoryToolIdentity(
        actor_id="user-a",
        agent_id="assistant",
        session_id="s1",
        tenant_id="tenant-a",
        user_id="user-a",
    )


def _message(event_id: str, sequence: int, text: str) -> Event:
    return Event(
        event_id=event_id,
        sequence=sequence,
        kind="message.created",
        actor_id="user-a",
        session_id="s1",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="assistant",
        payload={"agent_id": "assistant", "subject_id": "user-a", "text": text},
    )


def _record(
    memory_id: str,
    content: str,
    source_event_ids: tuple[str, ...],
    *,
    key: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        memory_type="belief",
        scope="private",
        layer="core",
        session_id="s1",
        subject_id="user-a",
        content=content,
        source_event_ids=source_event_ids,
        rule_id="test",
        owner_id="assistant",
        visible_to=("assistant",),
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="assistant",
        created_at="2026-07-28T00:00:00+00:00",
        updated_at="2026-07-28T00:00:00+00:00",
        last_event_sequence=1,
        metadata=({} if key is None else {"key": key}),
    )


def _proposal(
    *,
    proposal_id: str,
    action: str,
    target_memory_id: str | None,
    key: str,
    content: str,
    tenant_id: str = "tenant-a",
    scope: str = "private",
    visible_to: tuple[str, ...] = ("assistant",),
    expected_version: int | None = None,
) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=proposal_id,
        source="test",
        action=action,
        target_memory_id=target_memory_id,
        subject_id="user-a",
        key=key,
        content=content,
        memory_type="belief",
        layer="core",
        scope=scope,
        visible_to=visible_to,
        confidence=0.9,
        salience=0.9,
        source_message_ids=("message-1",),
        source_memory_ids=(() if target_memory_id is None else (target_memory_id,)),
        evidence_text=content,
        reason="test",
        actor_id="user-a",
        agent_id="assistant",
        tenant_id=tenant_id,
        user_id="user-a",
        session_id="s1",
        labels=("private",),
        expected_version=expected_version,
    )


def _stores_with_provider(path: object, provider: object) -> SQLiteStoreBundle:
    staging = SQLiteStoreBundle(path)
    staging.embedding_generations.register(provider.spec, status="backfill")
    staging.activate_embedding_generation(provider.spec.generation)
    return SQLiteStoreBundle(path, embedding_provider=provider)


def _runtime_from_stores(stores: SQLiteStoreBundle) -> AgentMemoryRuntime:
    return AgentMemoryRuntime(
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        tombstone_store=stores.tombstone_store,
        audit_store=stores.audit_store,
        dream_store=stores.dream_store,
        transaction_manager=stores,
    )
