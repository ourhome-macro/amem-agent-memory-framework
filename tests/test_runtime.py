from __future__ import annotations

import pytest

from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import WriteGuardError
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_ingest_writes_event_before_memory_derivation() -> None:
    runtime = AgentMemoryRuntime()
    result = runtime.ingest(_message_event())

    assert result.event.sequence == 1
    assert runtime.event_store.list_events()[0].event_id == "evt-1"
    records = runtime.memory_store.list_records()
    assert [record.memory_id for record in records] == ["episodic:s1:evt-1"]
    assert records[0].source_event_ids == ("evt-1",)


def test_replay_rebuilds_memory_from_event_store_only() -> None:
    runtime = AgentMemoryRuntime()
    runtime.ingest(_message_event())
    original = runtime.snapshot()

    runtime.memory_store.clear()
    assert runtime.memory_store.list_records() == []

    replayed = runtime.replay()

    assert replayed.state_hash == original.state_hash
    assert runtime.memory_store.get("episodic:s1:evt-1") is not None


def test_builtin_rules_create_typed_memories() -> None:
    runtime = AgentMemoryRuntime()
    runtime.ingest(_message_event())
    runtime.ingest(
        Event(
            event_id="evt-2",
            kind="belief.stated",
            actor_id="user",
            session_id="s1",
            labels=("private",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "user",
                "key": "refund_channel",
                "belief": "User believes email updates are more reliable.",
            },
        )
    )
    runtime.ingest(
        Event(
            event_id="evt-3",
            kind="relationship.signal",
            actor_id="support_agent",
            session_id="s1",
            payload={"target_id": "user", "sentiment": "trust increased"},
        )
    )
    runtime.ingest(
        Event(
            event_id="evt-4",
            kind="task.outcome",
            actor_id="support_agent",
            session_id="s1",
            labels=("private",),
            payload={
                "agent_id": "support_agent",
                "task": "refund status",
                "result": "success",
                "outcome": "Check gateway status first.",
            },
        )
    )

    types = {record.memory_type for record in runtime.memory_store.list_records()}

    assert types == {"episodic", "belief", "relationship", "strategy"}


def test_lifecycle_idempotently_reinforces_existing_memory() -> None:
    runtime = AgentMemoryRuntime()
    event = Event(
        event_id="evt-pref-1",
        kind="preference.updated",
        actor_id="user",
        session_id="s1",
        labels=("private",),
        payload={
            "agent_id": "support_agent",
            "subject_id": "user",
            "key": "refund_channel",
            "preference": "User prefers email refund updates.",
            "salience": 0.5,
        },
    )
    runtime.ingest(event)
    runtime.ingest(
        Event(
            event_id="evt-pref-2",
            kind="preference.updated",
            actor_id="user",
            session_id="s1",
            labels=("private",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "user",
                "key": "refund_channel",
                "preference": "User prefers email refund updates.",
                "salience": 0.9,
            },
        )
    )

    record = runtime.memory_store.get("belief:s1:support_agent:refund_channel")

    assert record is not None
    assert record.reinforcement_count == 2
    assert record.salience == 0.9
    assert record.source_event_ids == ("evt-pref-1", "evt-pref-2")


def test_access_blocks_other_agent_private_memory_from_context() -> None:
    runtime = AgentMemoryRuntime()
    runtime.ingest(_message_event(agent_id="agent_a"))

    context = runtime.project(
        MemoryQuery(agent_id="agent_b", text="refund status", session_id="s1")
    )

    assert context.selected_memory_ids == ()
    assert context.blocked_memory_count == 1


def test_sensitive_memory_does_not_enter_normal_context() -> None:
    runtime = AgentMemoryRuntime()
    runtime.ingest(_message_event(labels=("sensitive",), text="Card details were provided."))

    context = runtime.project(
        MemoryQuery(agent_id="support_agent", text="card details", session_id="s1")
    )

    assert context.selected_memory_ids == ()
    assert context.blocked_memory_count == 1


def test_private_memory_cannot_be_promoted_to_shared() -> None:
    runtime = AgentMemoryRuntime()
    runtime.ingest(
        Event(
            event_id="evt-private-1",
            kind="preference.updated",
            actor_id="user",
            session_id="s1",
            labels=("private",),
            payload={
                "agent_id": "support_agent",
                "subject_id": "user",
                "key": "channel",
                "preference": "Private preference.",
                "scope": "private",
            },
        )
    )

    with pytest.raises(WriteGuardError):
        runtime.ingest(
            Event(
                event_id="evt-private-2",
                kind="preference.updated",
                actor_id="user",
                session_id="s1",
                labels=("private",),
                payload={
                    "agent_id": "support_agent",
                    "subject_id": "user",
                    "key": "channel",
                    "preference": "Try to promote.",
                    "scope": "shared",
                },
            )
        )


def test_context_budget_keeps_high_salience_memory() -> None:
    runtime = AgentMemoryRuntime(config=RuntimeConfig(context_token_budget=14))
    runtime.ingest(
        _message_event(event_id="low", salience=0.2, text="refund status low value note")
    )
    runtime.ingest(
        _message_event(event_id="high", salience=0.95, text="refund status critical update")
    )

    context = runtime.project(
        MemoryQuery(agent_id="support_agent", text="refund status", session_id="s1")
    )

    assert context.selected_memory_ids == ("episodic:s1:high",)


def test_replay_detects_rule_or_config_change() -> None:
    runtime = AgentMemoryRuntime()
    runtime.ingest(_message_event())
    expected = runtime.snapshot()
    events = runtime.event_store.list_events()

    changed = AgentMemoryRuntime(config=RuntimeConfig(rule_version="builtin-v2"))
    for event in events:
        changed.event_store.append(event)
    actual = changed.replay()

    assert actual.state_hash != expected.state_hash


def _message_event(
    *,
    event_id: str = "evt-1",
    agent_id: str = "support_agent",
    labels: tuple[str, ...] = ("private",),
    text: str = "Customer asked about refund status.",
    salience: float = 0.65,
) -> Event:
    return Event(
        event_id=event_id,
        kind="message.created",
        actor_id="customer",
        session_id="s1",
        labels=labels,
        tags=("refund",),
        payload={
            "agent_id": agent_id,
            "subject_id": "order_1",
            "text": text,
            "topic": "refund status",
            "salience": salience,
        },
    )
