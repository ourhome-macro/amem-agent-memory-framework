from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from agent_memory_runtime import (
    AgentFunctionTool,
    AgentPolicy,
    AgentRequest,
    BusinessAgentRuntime,
    ModelGatewayStreamEvent,
    ModelResponse,
    ModelToolCall,
)
from agent_memory_runtime.agent.errors import (
    AgentApprovalError,
    AgentRunConflictError,
    ModelProtocolError,
)
from agent_memory_runtime.agent.model_gateway import OpenAICompatibleModelGateway
from agent_memory_runtime.agent.models import (
    AgentCheckpoint,
    AgentRun,
    ApprovalRecord,
    ApprovalStatus,
    ModelMessage,
    RunStatus,
    ToolCallRecord,
    ToolCallStatus,
    ToolDefinition,
)
from agent_memory_runtime.agent.policy import StaticAgentPolicyResolver
from agent_memory_runtime.agent.stores import (
    InMemoryAgentStateStore,
    JsonStateCodec,
    SQLiteAgentStateStore,
)
from agent_memory_runtime.config import LLMConfig
from agent_memory_runtime.tools import ToolRegistry


def test_in_memory_state_store_fences_leases_and_checks_versions() -> None:
    store = InMemoryAgentStateStore()
    request = AgentRequest(agent_id="a", message="work", request_id="lease-request")
    run = store.create_run(AgentRun.new(request, run_id="run-lease"))

    claimed = store.claim_run(run.run_id, worker_id="worker-a", lease_seconds=0.02)
    assert claimed is not None
    assert store.claim_run(run.run_id, worker_id="worker-b", lease_seconds=1) is None
    assert store.renew_run(
        run.run_id,
        worker_id="worker-a",
        lease_token="wrong-token",
        lease_seconds=1,
    ) is False

    time.sleep(0.03)
    reclaimed = store.claim_run(run.run_id, worker_id="worker-b", lease_seconds=1)
    assert reclaimed is not None
    assert reclaimed.lease_token != claimed.lease_token
    assert store.renew_run(
        run.run_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token or "",
        lease_seconds=1,
    ) is False

    with pytest.raises(AgentRunConflictError):
        store.update_run(
            replace(claimed, status=RunStatus.COMPLETED),
            expected_version=claimed.version,
            lease_token=claimed.lease_token,
        )


def test_state_store_checkpoint_tool_and_approval_invariants(tmp_path) -> None:
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    request = AgentRequest(
        agent_id="a",
        message="work",
        tenant_id="tenant-a",
        request_id="state-request",
    )
    run = store.create_run(AgentRun.new(request, run_id="state-run"))
    checkpoint = store.save_checkpoint(
        AgentCheckpoint(
            run_id=run.run_id,
            messages=(ModelMessage(role="user", content="work"),),
        ),
        expected_version=None,
    )
    assert checkpoint.version == 1
    with pytest.raises(AgentRunConflictError):
        store.save_checkpoint(checkpoint, expected_version=0)

    call = store.create_tool_call(
        ToolCallRecord(
            call_id="state-call",
            run_id=run.run_id,
            tenant_id="tenant-a",
            tool_name="test.tool",
            arguments={},
        )
    )
    with pytest.raises(AgentRunConflictError):
        store.update_tool_call(
            replace(call, status=ToolCallStatus.SUCCEEDED),
            expected_version=99,
        )
    approval = store.create_approval(
        ApprovalRecord.new(
            run_id=run.run_id,
            call_id=call.call_id,
            tenant_id="tenant-a",
        )
    )
    decided = store.decide_approval(
        approval.approval_id,
        tenant_id="tenant-a",
        decision=ApprovalStatus.APPROVED,
        reviewer_id="reviewer",
        reason=None,
    )
    assert decided.status is ApprovalStatus.APPROVED
    assert (
        store.decide_approval(
            approval.approval_id,
            tenant_id="tenant-a",
            decision=ApprovalStatus.APPROVED,
            reviewer_id="reviewer",
            reason=None,
        ).status
        is ApprovalStatus.APPROVED
    )
    with pytest.raises(AgentApprovalError):
        store.decide_approval(
            approval.approval_id,
            tenant_id="tenant-a",
            decision=ApprovalStatus.REJECTED,
            reviewer_id="other",
            reason=None,
        )

    reopened = SQLiteAgentStateStore(tmp_path / "state.sqlite")
    assert reopened.get_checkpoint(run.run_id).version == 1
    assert reopened.get_approval_for_call(call.call_id).status is ApprovalStatus.APPROVED


def test_sqlite_agent_state_supports_an_at_rest_codec(tmp_path) -> None:
    class ReverseCodec:
        def __init__(self) -> None:
            self.json = JsonStateCodec()

        def encode(self, value: dict[str, Any]) -> str:
            return self.json.encode(value)[::-1]

        def decode(self, payload: str) -> dict[str, Any]:
            return self.json.decode(payload[::-1])

    path = tmp_path / "encoded.sqlite"
    codec = ReverseCodec()
    store = SQLiteAgentStateStore(path, codec=codec)
    run = store.create_run(
        AgentRun.new(
            AgentRequest(
                agent_id="a",
                message="sensitive checkpoint text",
                request_id="encoded-request",
            ),
            run_id="encoded-run",
        )
    )
    store.save_checkpoint(
        AgentCheckpoint(
            run_id=run.run_id,
            messages=(ModelMessage(role="user", content="sensitive checkpoint text"),),
        ),
        expected_version=None,
    )

    with sqlite3.connect(path) as connection:
        raw_run = connection.execute(
            "SELECT payload FROM agent_runs WHERE run_id = ?", (run.run_id,)
        ).fetchone()[0]
        raw_checkpoint = connection.execute(
            "SELECT payload FROM agent_checkpoints WHERE run_id = ?", (run.run_id,)
        ).fetchone()[0]
    assert "sensitive checkpoint text" not in raw_run
    assert "sensitive checkpoint text" not in raw_checkpoint
    reopened = SQLiteAgentStateStore(path, codec=ReverseCodec())
    assert reopened.get_run(run.run_id).request.message == "sensitive checkpoint text"


def test_openai_compatible_gateway_maps_messages_tools_and_usage() -> None:
    class Completions:
        def __init__(self) -> None:
            self.request: dict[str, Any] = {}

        def create(self, **request: Any) -> Any:
            self.request = request
            return SimpleNamespace(
                id="response-1",
                model="provider-model",
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4),
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="provider-call",
                                    function=SimpleNamespace(
                                        name="inventory.lookup",
                                        arguments='{"sku":"A-1"}',
                                    ),
                                )
                            ],
                        ),
                    )
                ],
            )

    async def scenario() -> None:
        completions = Completions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        gateway = OpenAICompatibleModelGateway(LLMConfig(), client=client)
        response = await gateway.complete(
            messages=(
                ModelMessage(role="system", content="system"),
                ModelMessage(role="user", content="lookup"),
            ),
            tools=(
                ToolDefinition(
                    name="inventory.lookup",
                    description="Lookup inventory.",
                    input_schema={
                        "type": "object",
                        "properties": {"sku": {"type": "string"}},
                        "required": ["sku"],
                    },
                ),
            ),
        )

        assert response.tool_calls == (
            ModelToolCall("provider-call", "inventory.lookup", {"sku": "A-1"}),
        )
        assert response.input_tokens == 11
        assert response.output_tokens == 4
        assert completions.request["tools"][0]["function"]["name"] == "inventory.lookup"
        assert completions.request["tool_choice"] == "auto"

    asyncio.run(scenario())


def test_openai_compatible_gateway_rejects_invalid_tool_json() -> None:
    response = SimpleNamespace(
        id="response-invalid",
        model="provider-model",
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-invalid",
                            function=SimpleNamespace(name="tool", arguments="not-json"),
                        )
                    ],
                ),
            )
        ],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **request: response)
        )
    )

    async def scenario() -> None:
        gateway = OpenAICompatibleModelGateway(LLMConfig(), client=client)
        with pytest.raises(ModelProtocolError):
            await gateway.complete(
                messages=(ModelMessage(role="user", content="call"),),
                tools=(),
            )

    asyncio.run(scenario())


def test_business_runtime_forwards_native_model_deltas_without_replaying_content() -> None:
    class StreamingGateway:
        async def complete(self, **request: Any) -> ModelResponse:
            raise AssertionError("streaming gateway should not use complete")

        async def stream(self, **request: Any) -> Any:
            yield ModelGatewayStreamEvent(type="delta", delta="你")
            yield ModelGatewayStreamEvent(type="delta", delta="好")
            yield ModelGatewayStreamEvent(
                type="completed",
                response=ModelResponse(
                    content="你好",
                    model="streamed",
                    input_tokens=3,
                    output_tokens=2,
                ),
            )

    async def scenario() -> None:
        runtime = BusinessAgentRuntime(model_gateway=StreamingGateway())
        events = [
            event
            async for event in runtime.run(
                AgentRequest(agent_id="a", message="hello", request_id="stream-request")
            )
        ]

        deltas = [
            event.data["delta"]
            for event in events
            if event.type == "model.output.delta"
        ]
        assert deltas == ["你", "好"]
        assert events[-1].data["output"] == "你好"
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    asyncio.run(scenario())


def test_openai_compatible_gateway_assembles_streamed_tool_call_fragments() -> None:
    class Completions:
        def __init__(self) -> None:
            self.request: dict[str, Any] = {}

        def create(self, **request: Any) -> Any:
            self.request = request
            return iter(
                [
                    SimpleNamespace(
                        id="stream-response",
                        model="provider-model",
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                finish_reason=None,
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id="stream-call",
                                            function=SimpleNamespace(
                                                name="inventory.",
                                                arguments='{"sku":',
                                            ),
                                        )
                                    ],
                                ),
                            )
                        ],
                    ),
                    SimpleNamespace(
                        id="stream-response",
                        model="provider-model",
                        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3),
                        choices=[
                            SimpleNamespace(
                                finish_reason="tool_calls",
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id=None,
                                            function=SimpleNamespace(
                                                name="lookup",
                                                arguments='"A-1"}',
                                            ),
                                        )
                                    ],
                                ),
                            )
                        ],
                    ),
                ]
            )

    async def scenario() -> None:
        completions = Completions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        gateway = OpenAICompatibleModelGateway(LLMConfig(), client=client)
        events = [
            event
            async for event in gateway.stream(
                messages=(ModelMessage(role="user", content="lookup"),),
                tools=(
                    ToolDefinition(
                        name="inventory.lookup",
                        description="Lookup inventory.",
                        input_schema={"type": "object"},
                    ),
                ),
            )
        ]

        response = events[-1].response
        assert response is not None
        assert response.tool_calls == (
            ModelToolCall("stream-call", "inventory.lookup", {"sku": "A-1"}),
        )
        assert response.input_tokens == 8
        assert response.output_tokens == 3
        assert completions.request["stream"] is True

    asyncio.run(scenario())


def test_non_idempotent_interruption_enters_reconciliation() -> None:
    class Gateway:
        async def complete(self, **request: Any) -> ModelResponse:
            return ModelResponse(
                tool_calls=(ModelToolCall("slow-call", "slow.side-effect", {}),),
                model="scripted",
            )

    async def slow(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        await asyncio.sleep(1)
        return {"ok": True}

    async def scenario() -> None:
        store = InMemoryAgentStateStore()
        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="slow.side-effect",
                handler=slow,
                side_effects=True,
                idempotent=False,
            )
        )
        policy = AgentPolicy(
            approval_risk_threshold=None,
            run_timeout_seconds=0.05,
            model_timeout_seconds=1,
            tool_timeout_seconds=1,
        )
        runtime = BusinessAgentRuntime(
            model_gateway=Gateway(),
            state_store=store,
            tool_registry=registry,
            policy_resolver=StaticAgentPolicyResolver(policy),
        )
        events = [
            event
            async for event in runtime.run(
                AgentRequest(agent_id="a", message="slow", request_id="slow-request")
            )
        ]

        assert events[-1].type == "tool.reconciliation_required"
        assert store.get_run(events[0].run_id).status is RunStatus.NEEDS_RECONCILIATION
        assert store.get_tool_call("slow-call").status is ToolCallStatus.RECONCILIATION_REQUIRED

    asyncio.run(scenario())


def test_retryable_idempotent_interruption_can_resume_with_same_call_id() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **request: Any) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    tool_calls=(ModelToolCall("stable-call", "slow.idempotent", {}),),
                    model="scripted",
                )
            return ModelResponse(content="done", model="scripted")

    attempts: list[str] = []

    async def slow_once(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        attempts.append(context.call_id)
        if len(attempts) == 1:
            await asyncio.sleep(1)
        return {"ok": True}

    async def scenario() -> None:
        store = InMemoryAgentStateStore()
        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="slow.idempotent",
                handler=slow_once,
                side_effects=True,
                idempotent=True,
            )
        )
        policy = AgentPolicy(
            approval_risk_threshold=None,
            run_timeout_seconds=0.05,
            model_timeout_seconds=1,
            tool_timeout_seconds=1,
        )
        runtime = BusinessAgentRuntime(
            model_gateway=Gateway(),
            state_store=store,
            tool_registry=registry,
            policy_resolver=StaticAgentPolicyResolver(policy),
        )
        first = [
            event
            async for event in runtime.run(
                AgentRequest(agent_id="a", message="slow", request_id="retry-request")
            )
        ]
        assert first[-1].type == "run.timed_out"
        assert store.get_run(first[0].run_id).status is RunStatus.PENDING

        second = [
            event
            async for event in runtime.resume(
                first[0].run_id,
                tenant_id="default",
                user_id=None,
            )
        ]
        assert attempts == ["stable-call", "stable-call"]
        assert second[-1].type == "run.completed"
        assert store.get_run(first[0].run_id).error_type is None

    asyncio.run(scenario())


def test_succeeded_tool_can_be_explicitly_compensated() -> None:
    async def scenario() -> None:
        compensated: list[str] = []

        def compensate(output: dict[str, Any], context: Any) -> dict[str, Any]:
            compensated.append(context.call_id)
            return {"reverted": output["external_id"]}

        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="external.update",
                handler=lambda arguments, context: {"external_id": "ext-1"},
                compensator=compensate,
                side_effects=True,
                idempotent=True,
            )
        )

        class Gateway:
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, **request: Any) -> ModelResponse:
                self.calls += 1
                if self.calls == 1:
                    return ModelResponse(
                        tool_calls=(
                            ModelToolCall("compensate-call", "external.update", {}),
                        ),
                        model="scripted",
                    )
                return ModelResponse(content="updated", model="scripted")

        runtime = BusinessAgentRuntime(
            model_gateway=Gateway(),
            tool_registry=registry,
            policy_resolver=StaticAgentPolicyResolver(
                AgentPolicy(approval_risk_threshold=None)
            ),
        )
        events = [
            event
            async for event in runtime.run(
                AgentRequest(agent_id="a", message="update", request_id="compensate-request")
            )
        ]
        result = await runtime.compensate_tool_call(
            "compensate-call",
            tenant_id="default",
            user_id=None,
        )

        assert events[-1].type == "run.completed"
        assert result.status is ToolCallStatus.COMPENSATED
        assert compensated == ["compensate-call"]

    asyncio.run(scenario())
