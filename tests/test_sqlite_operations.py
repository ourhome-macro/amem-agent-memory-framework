from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.governance.queue import DerivationJob, SQLiteDerivationQueueStore
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_sqlite_migrations_backup_and_shadow_replay(tmp_path) -> None:
    live_path = tmp_path / "live.sqlite"
    backup_path = tmp_path / "backup.sqlite"
    stores = SQLiteStoreBundle(live_path)
    runtime = _runtime(stores)
    runtime.ingest(_event(1))

    backup = stores.backup(backup_path)
    shadow = stores.shadow_replay()

    assert stores.schema_version == 4
    assert stores.integrity_check() == "ok"
    assert backup.path == backup_path
    assert backup.integrity_check == "ok"
    assert backup.schema_version == 4
    assert SQLiteStoreBundle(backup_path).event_store.get("evt-1") is not None
    assert shadow.ok is True
    assert shadow.event_count == 1


def test_sqlite_serializes_concurrent_event_sequences(tmp_path) -> None:
    stores = SQLiteStoreBundle(tmp_path / "concurrent.sqlite")
    runtime = _runtime(stores)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda number: runtime.ingest(_event(number)), range(1, 17)))

    events = stores.event_store.list_events()
    assert len(events) == 16
    assert [event.sequence for event in events] == list(range(1, 17))
    assert stores.integrity_check() == "ok"


def test_sqlite_queue_enqueue_is_atomic_across_store_instances(tmp_path) -> None:
    path = tmp_path / "queue.sqlite"
    queues = (SQLiteDerivationQueueStore(path), SQLiteDerivationQueueStore(path))
    jobs = (
        DerivationJob.new("evt-concurrent-enqueue"),
        DerivationJob.new("evt-concurrent-enqueue"),
    )
    barrier = Barrier(2)

    def enqueue(index: int) -> DerivationJob:
        barrier.wait()
        return queues[index].enqueue(jobs[index])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(enqueue, range(2)))

    assert results[0].job_id == results[1].job_id
    assert len(queues[0].list_jobs()) == 1


def test_sqlite_rejects_ack_after_lease_expiry(tmp_path) -> None:
    queue = SQLiteDerivationQueueStore(tmp_path / "expired.sqlite")
    queued = queue.enqueue(DerivationJob.new("evt-sqlite-expired", max_attempts=3))
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
    recovered = queue.get(queued.job_id)

    assert result is None
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.error_type == "LeaseExpired"


def _runtime(stores: SQLiteStoreBundle) -> AgentMemoryRuntime:
    return AgentMemoryRuntime(
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        audit_store=stores.audit_store,
        derivation_queue=stores.derivation_queue,
        transaction_manager=stores,
    )


def _event(number: int) -> Event:
    return Event(
        event_id=f"evt-{number}",
        kind="message.created",
        actor_id="user",
        session_id="sqlite",
        occurred_at=f"2026-07-21T00:00:{number:02d}+00:00",
        labels=("private",),
        payload={
            "agent_id": "assistant",
            "subject_id": "user",
            "text": f"fact {number}",
        },
    )
