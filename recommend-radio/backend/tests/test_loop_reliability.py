from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_memory_runtime.agent.context_window import compact_checkpoint
from agent_memory_runtime.agent.errors import AgentPolicyError, AgentReconciliationRequired
from agent_memory_runtime.agent.models import (
    AgentCheckpoint,
    AgentRequest,
    ModelMessage,
    ToolCallRecord,
    ToolCallStatus,
)
from agent_memory_runtime.agent.policy import AgentPolicy
from agent_memory_runtime.agent.tool_runtime import (
    AgentFunctionTool,
    ReliableToolRuntime,
    validate_tool_arguments,
)
from agent_memory_runtime.tokens import AdaptiveTokenEstimator


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "string", "pattern": "^[0-9]+$"}, "abc"),
        ({"type": "array", "maxItems": 1}, [1, 2]),
        ({"type": "integer", "exclusiveMinimum": 0}, 0),
        ({"type": "string", "format": "date-time"}, "yesterday"),
        ({"oneOf": [{"type": "string"}, {"type": "integer"}]}, True),
    ],
)
def test_tool_schema_rejects_invalid_input(schema, value):
    with pytest.raises(AgentPolicyError):
        validate_tool_arguments({"x": value}, {"type": "object", "properties": {"x": schema}})


def test_local_refs_supported_remote_refs_never_fetched():
    schema = {
        "$defs": {"N": {"type": "integer"}},
        "type": "object",
        "properties": {"n": {"$ref": "#/$defs/N"}},
    }
    validate_tool_arguments({"n": 3}, schema)
    with pytest.raises(AgentPolicyError):
        validate_tool_arguments({"n": "bad"}, schema)
    with pytest.raises(AgentPolicyError):
        validate_tool_arguments({}, {"$ref": "https://invalid.example/schema"})


def test_two_compactions_preserve_original_task_pins_and_summary():
    checkpoint = AgentCheckpoint(
        run_id="r",
        messages=(
            ModelMessage(role="system", content="system policy"),
            ModelMessage(role="user", content="Original task: build project A"),
            ModelMessage(role="user", content="必须保留租户隔离 unique-pin"),
            ModelMessage(role="assistant", content="已完成阶段 unique-history"),
            *(ModelMessage(role="assistant", content="ordinary filler " * 150) for _ in range(12)),
        ),
    )
    policy = AgentPolicy(
        model_context_tokens=6000,
        reserved_output_tokens=500,
        context_compaction_ratio=0.1,
        context_summary_max_tokens=1500,
        context_keep_recent_messages=2,
    )
    for _ in range(2):
        checkpoint, report = compact_checkpoint(
            checkpoint, tools=(), estimator=AdaptiveTokenEstimator(), policy=policy, model=None
        )
        assert report is not None
        rendered = "\n".join(m.content for m in checkpoint.messages)
        assert "Original task: build project A" in rendered
        assert "unique-pin" in rendered
        assert "unique-history" in rendered
        checkpoint = AgentCheckpoint.from_dict(checkpoint.to_dict())
        checkpoint = replace(
            checkpoint,
            messages=(
                *checkpoint.messages,
                *(ModelMessage(role="assistant", content="more filler " * 150) for _ in range(12)),
            ),
        )


def test_sync_tool_timeout_marks_unknown_outcome_without_overlapping_retry():
    released = threading.Event()
    calls = []
    states = []

    def handler(_arguments, context):
        calls.append(context)
        released.wait(2)
        return {"done": True}

    def update(record, **_kwargs):
        states.append(record)
        return replace(record, version=record.version + 1)

    async def scenario():
        runtime = ReliableToolRuntime(state_store=SimpleNamespace(update_tool_call=update))
        try:
            with pytest.raises(AgentReconciliationRequired):
                await runtime.execute(
                    ToolCallRecord(
                        call_id="c",
                        run_id="r",
                        tenant_id="t",
                        tool_name="slow",
                        arguments={},
                        side_effects=True,
                        idempotent=True,
                    ),
                    tool=AgentFunctionTool("slow", handler, side_effects=True),
                    request=AgentRequest("a", "do task"),
                    policy=AgentPolicy(tool_timeout_seconds=0.03, tool_max_attempts=3),
                )
            assert len(calls) == 1
            assert calls[0].stop_requested.is_set()
            assert states[-1].status is ToolCallStatus.RECONCILIATION_REQUIRED
        finally:
            released.set()
            await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_legacy_compacted_checkpoint_upgrades_without_losing_pins():
    checkpoint = AgentCheckpoint(
        run_id="old",
        compaction_count=1,
        messages=(
            ModelMessage(role="system", content="system policy"),
            ModelMessage(
                role="system",
                content="<pinned-facts>\n- role=user: 必须保留 old-pin\n</pinned-facts>",
            ),
            ModelMessage(
                role="system", content="<task-state>\ninitial_task_context: old-task\n</task-state>"
            ),
            ModelMessage(
                role="system",
                content="<compacted-conversation-summary>\n"
                "note\nmetadata\nold-summary\n</compacted-conversation-summary>",
            ),
            *(ModelMessage(role="assistant", content="filler " * 200) for _ in range(8)),
        ),
    )
    compacted, report = compact_checkpoint(
        checkpoint,
        tools=(),
        estimator=AdaptiveTokenEstimator(),
        model=None,
        policy=AgentPolicy(
            model_context_tokens=6000,
            reserved_output_tokens=500,
            context_compaction_ratio=0.1,
            context_keep_recent_messages=2,
        ),
    )
    assert report is not None
    rendered = "\n".join(message.content for message in compacted.messages)
    assert "old-pin" in rendered
    assert "old-task" in rendered
    assert "old-summary" in rendered
