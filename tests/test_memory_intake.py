from __future__ import annotations

import asyncio

from agent_memory_runtime.agent import AgentRequest, ToolExecutionContext
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.memory.intake import (
    AutoDreamAnalyzer,
    DreamCheckpoint,
    MemoryIntakeService,
    MemoryToolIdentity,
    build_memory_intake_tools,
)
from agent_memory_runtime.runtime import AgentMemoryRuntime


def test_save_memory_tool_emits_structured_event_and_derives_core_preference() -> None:
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
    assert result.event is not None
    assert result.event.kind == "preference.updated"
    assert result.memory_ids == ("v3:belief:tenant-a:user-a:assistant:java_style",)
    record = runtime.memory_store.get(result.memory_ids[0])
    assert record is not None
    assert record.content == "Use explicit loops in Java examples."
    assert record.layer == "core"
    assert record.metadata["key"] == "java_style"


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
    assert record.last_operation == "supersede"
    assert record.source_memory_ids == (original.memory_ids[0],)
    assert set(record.source_event_ids) == {
        "memory-intake:save_memory:save-db",
        "memory-intake:revise_memory:revise-db",
    }


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
    assert [proposal.action for proposal in report.proposals] == ["forget_memory"]
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
    assert report.proposals[0].action == "save_memory"
    assert report.proposals[0].reason == "typed_event_without_derived_memory"


def test_auto_dream_detects_duplicate_active_memories() -> None:
    records = [
        _record("m1", "Repeated memory", ("e1",)),
        _record("m2", "Repeated memory", ("e2",)),
    ]

    report = AutoDreamAnalyzer().analyze(events=[], records=records)

    assert len(report.proposals) == 1
    assert report.proposals[0].action == "forget_memory"
    assert report.proposals[0].target_memory_id == "m2"
    assert report.proposals[0].reason == "duplicate_of:m1"


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


def _record(memory_id: str, content: str, source_event_ids: tuple[str, ...]) -> MemoryRecord:
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
    )
