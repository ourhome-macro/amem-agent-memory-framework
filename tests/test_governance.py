from __future__ import annotations

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate, MemoryRecord
from agent_memory_runtime.governance.pii import PiiProtector, SimpleEncryptedPiiVault
from agent_memory_runtime.governance.queue import (
    InMemoryDerivationQueueStore,
    SQLiteDerivationQueueStore,
)
from agent_memory_runtime.governance.queue.worker import DerivationWorker
from agent_memory_runtime.governance.retention import (
    RetentionExecutor,
    RetentionPlanner,
    RetentionPolicy,
)
from agent_memory_runtime.governance.review import InMemoryReviewQueue, ReviewGuard
from agent_memory_runtime.memory.stores.sqlite import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_async_ingest_persists_event_and_defers_memory_derivation() -> None:
    queue = InMemoryDerivationQueueStore()
    runtime = AgentMemoryRuntime(derivation_queue=queue)

    result = runtime.ingest_async(_message_event())

    assert result.event.sequence == 1
    assert result.job.event_id == "evt-1"
    assert result.job.status == "pending"
    assert [event.event_id for event in runtime.event_store.list_events()] == ["evt-1"]
    assert runtime.memory_store.list_records() == []

    processed = runtime.run_derivation_once()

    assert processed is not None
    assert processed.status == "succeeded"
    assert runtime.memory_store.get("episodic:s1:evt-1") is not None
    assert queue.pending_count() == 0
    assert [record.audit_type for record in runtime.audit_store.list_envelopes()] == [
        "governance_job"
    ]


def test_async_worker_retries_failed_job_without_losing_event_source() -> None:
    queue = InMemoryDerivationQueueStore()
    runtime = AgentMemoryRuntime(derivation_queue=queue)
    runtime.ingest_async(
        Event(
            event_id="evt-bad",
            kind="preference.updated",
            actor_id="user",
            session_id="s1",
            labels=("sensitive",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "user",
                "key": "secret",
                "preference": "Sensitive global preference.",
                "scope": "global",
            },
        )
    )

    failed = runtime.run_derivation_once()

    assert failed is not None
    assert failed.status == "pending"
    assert failed.attempts == 1
    assert failed.error_type == "WriteGuardError"
    assert runtime.memory_store.list_records() == []
    assert runtime.event_store.list_events()[0].event_id == "evt-bad"
    audit = runtime.audit_store.list_envelopes()[-1]
    assert audit.audit_type == "governance_job"
    assert audit.decision == "block"
    assert audit.payload["error_type"] == "WriteGuardError"
    assert "Sensitive global preference" not in str(audit.to_dict())


def test_sqlite_derivation_queue_recovers_pending_jobs_after_restart(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite"
    bundle = SQLiteStoreBundle(db_path)
    runtime = AgentMemoryRuntime(
        event_store=bundle.event_store,
        memory_store=bundle.memory_store,
        snapshot_store=bundle.snapshot_store,
        audit_store=bundle.audit_store,
        derivation_queue=bundle.derivation_queue,
        transaction_manager=bundle,
    )
    runtime.ingest_async(_message_event())

    restarted = SQLiteStoreBundle(db_path)
    restarted_runtime = AgentMemoryRuntime(
        event_store=restarted.event_store,
        memory_store=restarted.memory_store,
        snapshot_store=restarted.snapshot_store,
        audit_store=restarted.audit_store,
        derivation_queue=SQLiteDerivationQueueStore(db_path),
        transaction_manager=restarted,
    )

    assert restarted_runtime.derivation_queue.pending_count() == 1
    job = restarted_runtime.run_derivation_once()

    assert job is not None
    assert job.status == "succeeded"
    assert restarted_runtime.memory_store.get("episodic:s1:evt-1") is not None


def test_derivation_worker_runs_until_queue_is_idle() -> None:
    queue = InMemoryDerivationQueueStore()
    runtime = AgentMemoryRuntime(derivation_queue=queue)
    runtime.ingest_async(_message_event(event_id="evt-1"))
    runtime.ingest_async(_message_event(event_id="evt-2"))

    report = DerivationWorker(runtime).run_until_idle(max_jobs=10)

    assert report.processed == 2
    assert report.succeeded == 2
    assert report.failed == 0
    assert queue.pending_count() == 0


def test_retention_policy_archives_old_working_memory_and_deletes_expired_sensitive() -> None:
    runtime = AgentMemoryRuntime()
    active = _record(
        memory_id="m-active",
        layer="working",
        labels=("private",),
        salience=0.7,
        last_event_sequence=9,
    )
    old = _record(
        memory_id="m-old",
        layer="working",
        labels=("private",),
        salience=0.1,
        last_event_sequence=1,
    )
    sensitive = _record(
        memory_id="m-sensitive",
        layer="working",
        labels=("sensitive",),
        salience=0.9,
        last_event_sequence=2,
    )
    runtime.memory_store.replace_all([active, old, sensitive])
    policy = RetentionPolicy(
        archive_working_after_sequences=5,
        archive_below_salience=0.2,
        delete_sensitive_after_sequences=6,
    )

    plan = RetentionPlanner(policy).plan(
        runtime.memory_store.list_records(),
        current_sequence=10,
    )
    report = RetentionExecutor(
        memory_store=runtime.memory_store,
        audit_store=runtime.audit_store,
    ).apply(plan, snapshot=runtime.snapshot())

    assert report.archived_memory_ids == ("m-old",)
    assert report.deleted_memory_ids == ("m-sensitive",)
    remaining = {record.memory_id: record for record in runtime.memory_store.list_records()}
    assert remaining["m-active"].status == "active"
    assert remaining["m-old"].status == "archived"
    assert remaining["m-old"].layer == "archival"
    assert "m-sensitive" not in remaining
    assert any(
        envelope.audit_type == "retention" for envelope in runtime.audit_store.list_envelopes()
    )


def test_human_review_quarantines_high_risk_candidate_until_approval() -> None:
    review_queue = InMemoryReviewQueue()
    runtime = AgentMemoryRuntime(
        review_guard=ReviewGuard(review_queue=review_queue, risk_threshold=0.7)
    )
    runtime.ingest(
        Event(
            event_id="evt-review",
            kind="preference.updated",
            actor_id="user",
            session_id="s1",
            labels=("sensitive",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "user",
                "key": "health",
                "preference": "User shared a sensitive medical preference.",
            },
        )
    )

    assert runtime.memory_store.list_records() == []
    pending = review_queue.pending_items()
    assert len(pending) == 1
    assert pending[0].candidate.memory_id == "belief:s1:support_agent:[redacted]"
    assert "medical preference" not in pending[0].candidate.content

    approved = runtime.approve_review_item(pending[0].review_id, reviewer_id="operator")

    assert approved is not None
    assert approved.memory_id == "belief:s1:support_agent:[redacted]"
    assert review_queue.pending_items() == []
    assert runtime.memory_store.get("belief:s1:support_agent:[redacted]") is not None
    decisions = [envelope.decision for envelope in runtime.audit_store.list_envelopes()]
    assert "review" in decisions
    assert "allow" in decisions


def test_pii_vault_tokenizes_payload_and_keeps_raw_value_out_of_memory_shape() -> None:
    vault = SimpleEncryptedPiiVault(secret_key="test-key")
    protector = PiiProtector(vault=vault)
    protected = protector.protect_payload(
        {
            "text": "Please refund card 4242 4242 4242 4242 and email a@example.com.",
            "metadata": {"customer_email": "a@example.com"},
        },
        owner_id="support_agent",
    )

    serialized = str(protected.payload)
    assert "4242 4242 4242 4242" not in serialized
    assert "a@example.com" not in serialized
    assert "${PII_" in serialized
    assert len(protected.tokens) == 3
    assert vault.resolve(protected.tokens[0].token_id, owner_id="support_agent")
    assert vault.resolve(protected.tokens[0].token_id, owner_id="other_agent") is None


def _message_event(*, event_id: str = "evt-1") -> Event:
    return Event(
        event_id=event_id,
        kind="message.created",
        actor_id="customer",
        session_id="s1",
        labels=("private",),
        tags=("refund",),
        payload={
            "agent_id": "support_agent",
            "subject_id": "order_1",
            "text": "Customer asked about refund status.",
            "salience": 0.65,
        },
    )


def _record(
    *,
    memory_id: str,
    layer: str,
    labels: tuple[str, ...],
    salience: float,
    last_event_sequence: int,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        memory_type="episodic",
        scope="private",
        layer=layer,
        session_id="s1",
        subject_id="subject",
        content=f"{memory_id} content",
        source_event_ids=("evt-source",),
        rule_id="test",
        owner_id="support_agent",
        visible_to=("support_agent",),
        labels=labels,
        salience=salience,
        last_event_sequence=last_event_sequence,
    )


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        memory_id="candidate",
        memory_type="belief",
        scope="private",
        layer="core",
        session_id="s1",
        subject_id="subject",
        content="candidate",
        source_event_ids=("evt-source",),
        rule_id="test",
        owner_id="support_agent",
    )
