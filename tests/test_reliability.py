from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import sleep

import pytest

from agent_memory_runtime.config import RuntimeConfig, WorkerConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryCandidate, MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import EventConflictError
from agent_memory_runtime.governance.queue import (
    DerivationJob,
    InMemoryDerivationQueueStore,
    JsonlDerivationQueueStore,
)
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_ingest_is_idempotent_without_reapplying_memory_or_snapshot() -> None:
    runtime = AgentMemoryRuntime(legacy_event_derivation=True)
    event = _event()

    first = runtime.ingest(event)
    second = runtime.ingest(event)

    assert second.event == first.event
    assert second.records == first.records
    assert len(runtime.event_store.list_events()) == 1
    assert runtime.memory_store.list_records()[0].reinforcement_count == 1


def test_event_id_cannot_be_reused_for_different_payload() -> None:
    runtime = AgentMemoryRuntime(legacy_event_derivation=True)
    original = _event()
    runtime.ingest(original)

    with pytest.raises(EventConflictError):
        runtime.ingest(
            Event.from_dict(
                {
                    **original.to_dict(),
                    "payload": {**original.payload, "text": "different fact"},
                }
            )
        )

    assert len(runtime.event_store.list_events()) == 1


def test_dict_retry_without_occurred_at_reuses_original_event_time() -> None:
    runtime = AgentMemoryRuntime(legacy_event_derivation=True)
    value = {
        "event_id": "evt-dict-retry",
        "kind": "message.created",
        "actor_id": "user",
        "payload": {
            "agent_id": "assistant",
            "subject_id": "user",
            "text": "stable fact",
        },
    }

    first = runtime.ingest(value)
    second = runtime.ingest(value)

    assert second.event == first.event
    assert len(runtime.event_store.list_events()) == 1


def test_tenant_identity_is_propagated_and_memory_ids_do_not_collide() -> None:
    runtime = AgentMemoryRuntime(legacy_event_derivation=True)

    tenant_a = runtime.ingest(_tenant_belief("tenant-a", "user-a", "evt-tenant-a"))
    tenant_b = runtime.ingest(_tenant_belief("tenant-b", "user-b", "evt-tenant-b"))

    first = tenant_a.records[0]
    second = tenant_b.records[0]
    assert first.memory_id == "v3:belief:tenant-a:user-a:assistant:appearance"
    assert second.memory_id == "v3:belief:tenant-b:user-b:assistant:appearance"
    assert first.tenant_id == "tenant-a"
    assert first.user_id == "user-a"
    assert first.agent_id == "assistant"
    assert first.memory_id != second.memory_id

    selected_a, _ = runtime.retrieve(
        MemoryQuery(
            agent_id="assistant",
            text="dark mode",
            session_id="s1",
            tenant_id="tenant-a",
            user_id="user-a",
        )
    )
    wrong_user, _ = runtime.retrieve(
        MemoryQuery(
            agent_id="assistant",
            text="dark mode",
            session_id="s1",
            tenant_id="tenant-a",
            user_id="user-b",
        )
    )
    default_tenant, _ = runtime.retrieve(
        MemoryQuery(agent_id="assistant", text="dark mode", session_id="s1")
    )

    assert [record.memory_id for record in selected_a] == [first.memory_id]
    assert wrong_user == []
    assert default_tenant == []


def test_memory_identity_roundtrip_distinguishes_new_and_legacy_records() -> None:
    record = replace(
        AgentMemoryRuntime(legacy_event_derivation=True).ingest(_event()).records[0],
        agent_id=None,
    )
    explicit_identity = MemoryRecord.from_dict(record.to_dict())
    legacy_value = record.to_dict()
    legacy_value.pop("agent_id")
    legacy_identity = MemoryRecord.from_dict(legacy_value)

    assert explicit_identity.agent_id is None
    assert legacy_identity.agent_id == record.owner_id


def test_expired_worker_lease_is_reclaimed_and_stale_owner_cannot_ack() -> None:
    queue = InMemoryDerivationQueueStore()
    queued = queue.enqueue(DerivationJob.new("evt-lease", max_attempts=3))
    first = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    assert first is not None
    queue.update(
        replace(
            first,
            lease_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
    )

    second = queue.claim_next(worker_id="worker-b", lease_seconds=30)

    assert second is not None
    assert second.job_id == queued.job_id
    assert second.attempts == 2
    assert second.lease_owner == "worker-b"
    assert queue.complete(
        second.job_id,
        worker_id="worker-a",
        lease_token=first.lease_token or "",
    ) is None
    assert queue.complete(
        second.job_id,
        worker_id="worker-b",
        lease_token=second.lease_token or "",
    ).status == "succeeded"


def test_lease_token_fences_stale_execution_from_same_worker() -> None:
    queue = InMemoryDerivationQueueStore()
    queued = queue.enqueue(DerivationJob.new("evt-fencing", max_attempts=3))
    first = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    assert first is not None
    queue.update(
        replace(
            first,
            lease_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
    )
    second = queue.claim_next(worker_id="worker-a", lease_seconds=30)

    assert second is not None
    assert first.lease_token != second.lease_token
    assert queue.complete(
        queued.job_id,
        worker_id="worker-a",
        lease_token=first.lease_token or "",
    ) is None
    assert queue.get(queued.job_id) == second
    assert queue.complete(
        queued.job_id,
        worker_id="worker-a",
        lease_token=second.lease_token or "",
    ).status == "succeeded"


@pytest.mark.parametrize("operation", ["complete", "fail", "renew"])
def test_expired_lease_owner_cannot_mutate_job_before_reclaim(operation: str) -> None:
    queue = InMemoryDerivationQueueStore()
    queued = queue.enqueue(DerivationJob.new(f"evt-expired-{operation}", max_attempts=3))
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    queue.update(
        replace(
            claimed,
            lease_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
    )

    if operation == "complete":
        result = queue.complete(
            queued.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token or "",
        )
    elif operation == "fail":
        result = queue.fail(
            queued.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token or "",
            error=RuntimeError("late failure"),
        )
    else:
        result = queue.renew_lease(
            queued.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token or "",
            lease_seconds=30,
        )

    recovered = queue.get(queued.job_id)
    assert result is None
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.lease_owner is None
    assert recovered.error_type == "LeaseExpired"


def test_jsonl_queue_persists_expired_lease_recovery(tmp_path) -> None:
    path = tmp_path / "derivation-queue.jsonl"
    queue = JsonlDerivationQueueStore(path)
    queued = queue.enqueue(DerivationJob.new("evt-jsonl-expired", max_attempts=3))
    claimed = queue.claim_next(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    queue.update(
        replace(
            claimed,
            lease_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
    )

    result = queue.complete(
        queued.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token or "",
    )
    recovered = JsonlDerivationQueueStore(path).get(queued.job_id)

    assert result is None
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.error_type == "LeaseExpired"


def test_crash_loop_reaches_dlq_and_requires_explicit_redrive() -> None:
    queue = InMemoryDerivationQueueStore()
    original = queue.enqueue(DerivationJob.new("evt-dlq", max_attempts=1))
    claimed = queue.claim_next(worker_id="worker-a")
    assert claimed is not None
    failed = queue.fail(
        claimed.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token or "",
        error=RuntimeError("boom"),
    )

    assert failed is not None
    assert failed.status == "dead_letter"
    assert queue.enqueue(DerivationJob.new("evt-dlq")).job_id == original.job_id
    redriven = queue.redrive(original.job_id)
    assert redriven is not None
    assert redriven.status == "pending"
    assert redriven.attempts == 0
    assert redriven.redrive_count == 1


def test_runtime_heartbeats_long_derivation_until_ack() -> None:
    runtime = AgentMemoryRuntime(
        legacy_event_derivation=True,
        config=RuntimeConfig(
            worker=WorkerConfig(
                lease_seconds=0.03,
                heartbeat_interval_seconds=0.005,
            )
        ),
        derivation_engine=_SlowDerivationEngine(),
    )
    runtime.ingest_async(_event())

    job = runtime.run_derivation_once()

    assert job is not None
    assert job.status == "succeeded"
    assert job.attempts == 1
    assert job.lease_token is None


def _event() -> Event:
    return Event(
        event_id="evt-idempotent",
        kind="message.created",
        actor_id="user",
        session_id="s1",
        occurred_at="2026-07-21T00:00:00+00:00",
        labels=("private",),
        payload={
            "agent_id": "assistant",
            "subject_id": "user",
            "text": "stable fact",
        },
    )


def _tenant_belief(tenant_id: str, user_id: str, event_id: str) -> Event:
    return Event(
        event_id=event_id,
        kind="preference.updated",
        actor_id=user_id,
        session_id="s1",
        occurred_at="2026-07-21T00:00:00+00:00",
        labels=("private",),
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id="assistant",
        payload={
            "agent_id": "assistant",
            "subject_id": user_id,
            "key": "appearance",
            "preference": "dark mode",
        },
    )


class _SlowDerivationEngine:
    def derive(self, event: Event) -> list[MemoryCandidate]:
        sleep(0.08)
        agent_id = event.agent_id or "assistant"
        return [
            MemoryCandidate(
                memory_id=f"slow:{event.event_id}",
                memory_type="episodic",
                scope="private",
                layer="working",
                session_id=event.session_id,
                subject_id=event.actor_id,
                content="slow but leased",
                source_event_ids=(event.event_id,),
                rule_id="test.slow",
                owner_id=agent_id,
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                agent_id=agent_id,
            )
        ]
