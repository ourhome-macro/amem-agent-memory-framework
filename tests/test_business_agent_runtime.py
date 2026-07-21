from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from typing import Any

import pytest

from agent_memory_runtime import (
    AgentFunctionTool,
    AgentModuleRegistry,
    AgentPolicy,
    AgentRequest,
    BusinessAgentRuntime,
    InMemoryAgentStateStore,
    ModelResponse,
    ModelToolCall,
)
from agent_memory_runtime.agent.errors import (
    AgentIdentityError,
    AgentRunConflictError,
)
from agent_memory_runtime.agent.models import RunStatus, ToolCallStatus
from agent_memory_runtime.agent.modules import StaticAgentModule
from agent_memory_runtime.agent.policy import StaticAgentPolicyResolver
from agent_memory_runtime.agent.stores import SQLiteAgentStateStore
from agent_memory_runtime.tools import ToolRegistry


class ScriptedGateway:
    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **request: Any) -> ModelResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("scripted model response was exhausted")
        return self.responses.popleft()


def test_text_run_is_durable_streamed_and_request_idempotent() -> None:
    async def scenario() -> None:
        gateway = ScriptedGateway(
            ModelResponse(
                content="完成",
                model="scripted",
                response_id="response-1",
                input_tokens=7,
                output_tokens=2,
            )
        )
        runtime = BusinessAgentRuntime(model_gateway=gateway)
        request = AgentRequest(
            agent_id="assistant",
            message="处理任务",
            tenant_id="tenant-a",
            user_id="user-a",
            request_id="request-1",
        )

        events = [event async for event in runtime.run(request)]
        run_id = events[0].run_id
        stored = await runtime.get_run(
            run_id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        replay = [event async for event in runtime.run(request)]

        assert [event.type for event in events] == [
            "run.started",
            "context.ready",
            "model.started",
            "model.output.delta",
            "model.completed",
            "run.completed",
        ]
        assert [event.sequence for event in events] == list(range(1, 7))
        assert len({event.event_id for event in events}) == len(events)
        assert all(event.execution_id == events[0].execution_id for event in events)
        assert stored.status is RunStatus.COMPLETED
        assert stored.final_output == "完成"
        assert stored.input_tokens == 7
        assert stored.output_tokens == 2
        assert replay[0].run_id == run_id
        assert replay[0].type == "run.completed"
        assert replay[0].data["replayed"] is True
        assert len(gateway.calls) == 1

        with pytest.raises(AgentRunConflictError):
            _ = [
                event
                async for event in runtime.run(
                    replace(request, message="different request")
                )
            ]

    asyncio.run(scenario())


def test_tool_loop_validates_schema_retries_and_records_tenant_event() -> None:
    async def scenario() -> None:
        attempts: list[str] = []

        def handler(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
            attempts.append(context.call_id)
            if len(attempts) == 1:
                raise OSError("temporary failure")
            return {"sum": arguments["a"] + arguments["b"]}

        tool = AgentFunctionTool(
            name="math.add",
            description="Add two integers.",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            handler=handler,
        )
        registry = ToolRegistry()
        registry.register(tool)
        gateway = ScriptedGateway(
            ModelResponse(
                tool_calls=(ModelToolCall("call-add", "math.add", {"a": 2, "b": 3}),),
                model="scripted",
            ),
            ModelResponse(content="结果是 5", model="scripted"),
        )
        runtime = BusinessAgentRuntime(model_gateway=gateway, tool_registry=registry)

        events = [
            event
            async for event in runtime.run(
                AgentRequest(
                    agent_id="assistant",
                    message="2+3",
                    tenant_id="tenant-tools",
                    user_id="user-tools",
                    request_id="request-tools",
                )
            )
        ]

        completed = next(event for event in events if event.type == "tool.completed")
        assert completed.data["status"] == "succeeded"
        assert completed.data["attempts"] == 2
        assert completed.data["output"] == {"sum": 5}
        assert attempts == ["call-add", "call-add"]
        assert gateway.calls[1]["messages"][-1].role == "tool"
        assert '"sum": 5' in gateway.calls[1]["messages"][-1].content

        tool_event = runtime.memory_runtime.event_store.get("agent-tool:call-add")
        assert tool_event is not None
        assert tool_event.tenant_id == "tenant-tools"
        assert tool_event.user_id == "user-tools"
        assert tool_event.agent_id == "assistant"
        assert "output" not in tool_event.payload
        assert "sum" not in tool_event.payload.values()

    asyncio.run(scenario())


def test_invalid_tool_arguments_are_blocked_without_invoking_handler() -> None:
    async def scenario() -> None:
        called = False

        def handler(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
            nonlocal called
            called = True
            return {"ok": True}

        tool = AgentFunctionTool(
            name="strict.tool",
            handler=handler,
            input_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
                "additionalProperties": False,
            },
        )
        registry = ToolRegistry()
        registry.register(tool)
        gateway = ScriptedGateway(
            ModelResponse(
                tool_calls=(
                    ModelToolCall("strict-1", "strict.tool", {"count": "not-an-int"}),
                ),
                model="scripted",
            ),
            ModelResponse(content="参数无效", model="scripted"),
        )
        runtime = BusinessAgentRuntime(model_gateway=gateway, tool_registry=registry)

        events = [
            event
            async for event in runtime.run(
                AgentRequest(agent_id="a", message="run", request_id="strict-request")
            )
        ]
        completed = next(event for event in events if event.type == "tool.completed")

        assert called is False
        assert completed.data["status"] == "blocked"
        assert completed.data["error_type"] == "AgentPolicyError"
        assert events[-1].type == "run.completed"

    asyncio.run(scenario())


def test_side_effect_requires_approval_then_resumes_from_checkpoint() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        def write(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
            calls.append(context.call_id)
            return {"written": arguments["value"]}

        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="record.write",
                handler=write,
                side_effects=True,
                idempotent=True,
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        )
        gateway = ScriptedGateway(
            ModelResponse(
                tool_calls=(
                    ModelToolCall("write-1", "record.write", {"value": "saved"}),
                ),
                model="scripted",
            ),
            ModelResponse(content="已保存", model="scripted"),
        )
        runtime = BusinessAgentRuntime(model_gateway=gateway, tool_registry=registry)
        request = AgentRequest(
            agent_id="assistant",
            message="保存",
            tenant_id="tenant-a",
            user_id="user-a",
            request_id="approval-request",
        )

        first = [event async for event in runtime.run(request)]
        approval_event = first[-1]
        assert approval_event.type == "approval.required"
        assert calls == []

        run_id = first[0].run_id
        waiting = await runtime.get_run(
            run_id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        assert waiting.status is RunStatus.WAITING_APPROVAL

        await runtime.decide_approval(
            approval_event.data["approval_id"],
            tenant_id="tenant-a",
            reviewer_id="reviewer",
            approved=True,
        )
        resumed = [
            event
            async for event in runtime.resume(
                run_id,
                tenant_id="tenant-a",
                user_id="user-a",
            )
        ]

        assert calls == ["write-1"]
        assert resumed[0].sequence > first[-1].sequence
        assert resumed[0].execution_id != first[0].execution_id
        assert "context.ready" not in [event.type for event in resumed]
        assert resumed[-1].type == "run.completed"
        assert resumed[-1].data["output"] == "已保存"

    asyncio.run(scenario())


def test_rejected_approval_becomes_a_tool_result_for_the_model() -> None:
    async def scenario() -> None:
        called = False

        def dangerous(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
            nonlocal called
            called = True
            return {"deleted": True}

        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="dangerous.delete",
                handler=dangerous,
                side_effects=True,
                idempotent=False,
            )
        )
        gateway = ScriptedGateway(
            ModelResponse(
                tool_calls=(
                    ModelToolCall("delete-1", "dangerous.delete", {"id": "x"}),
                ),
                model="scripted",
            ),
            ModelResponse(content="操作已取消", model="scripted"),
        )
        runtime = BusinessAgentRuntime(model_gateway=gateway, tool_registry=registry)
        request = AgentRequest(agent_id="a", message="delete", request_id="reject-request")
        first = [event async for event in runtime.run(request)]
        await runtime.decide_approval(
            first[-1].data["approval_id"],
            tenant_id="default",
            reviewer_id="reviewer",
            approved=False,
            reason="not allowed",
        )
        resumed = [
            event
            async for event in runtime.resume(
                first[0].run_id,
                tenant_id="default",
                user_id=None,
            )
        ]

        assert called is False
        assert any(event.type == "tool.rejected" for event in resumed)
        assert '"status": "rejected"' in gateway.calls[1]["messages"][-1].content
        assert resumed[-1].data["output"] == "操作已取消"

    asyncio.run(scenario())


def test_unknown_non_idempotent_outcome_requires_explicit_reconciliation() -> None:
    async def scenario() -> None:
        invoked = False

        def side_effect(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
            nonlocal invoked
            invoked = True
            return {"external_id": "should-not-run"}

        state_store = InMemoryAgentStateStore()
        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="external.create",
                handler=side_effect,
                side_effects=True,
                idempotent=False,
            )
        )
        gateway = ScriptedGateway(
            ModelResponse(
                tool_calls=(
                    ModelToolCall("external-1", "external.create", {"name": "x"}),
                ),
                model="scripted",
            ),
            ModelResponse(content="已确认", model="scripted"),
        )
        runtime = BusinessAgentRuntime(
            model_gateway=gateway,
            state_store=state_store,
            tool_registry=registry,
        )
        first = [
            event
            async for event in runtime.run(
                AgentRequest(agent_id="a", message="create", request_id="reconcile-request")
            )
        ]
        await runtime.decide_approval(
            first[-1].data["approval_id"],
            tenant_id="default",
            reviewer_id="reviewer",
            approved=True,
        )
        record = state_store.get_tool_call("external-1")
        assert record is not None
        state_store.update_tool_call(
            replace(record, status=ToolCallStatus.EXECUTING, attempts=1),
            expected_version=record.version,
        )

        second = [
            event
            async for event in runtime.resume(
                first[0].run_id,
                tenant_id="default",
                user_id=None,
            )
        ]
        assert second[-1].type == "tool.reconciliation_required"
        assert invoked is False
        run = await runtime.get_run(
            first[0].run_id,
            tenant_id="default",
            user_id=None,
        )
        assert run.status is RunStatus.NEEDS_RECONCILIATION

        await runtime.reconcile_tool_call(
            "external-1",
            tenant_id="default",
            reviewer_id="operator",
            succeeded=True,
            output={"external_id": "confirmed"},
        )
        third = [
            event
            async for event in runtime.resume(
                first[0].run_id,
                tenant_id="default",
                user_id=None,
            )
        ]
        assert invoked is False
        assert third[-1].type == "run.completed"
        assert third[-1].data["output"] == "已确认"

    asyncio.run(scenario())


def test_sqlite_checkpoint_survives_runtime_recreation(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "agent.sqlite"
        registry = ToolRegistry()
        registry.register(
            AgentFunctionTool(
                name="state.write",
                handler=lambda arguments, context: {"ok": True},
                side_effects=True,
                idempotent=True,
            )
        )
        first_runtime = BusinessAgentRuntime(
            model_gateway=ScriptedGateway(
                ModelResponse(
                    tool_calls=(ModelToolCall("sqlite-call", "state.write", {}),),
                    model="scripted",
                )
            ),
            state_store=SQLiteAgentStateStore(path),
            tool_registry=registry,
        )
        request = AgentRequest(
            agent_id="a",
            message="write",
            tenant_id="tenant-sqlite",
            user_id="user-sqlite",
            request_id="sqlite-request",
        )
        first = [event async for event in first_runtime.run(request)]

        second_runtime = BusinessAgentRuntime(
            model_gateway=ScriptedGateway(
                ModelResponse(content="durable", model="scripted")
            ),
            state_store=SQLiteAgentStateStore(path),
            tool_registry=registry,
        )
        await second_runtime.decide_approval(
            first[-1].data["approval_id"],
            tenant_id="tenant-sqlite",
            reviewer_id="reviewer",
            approved=True,
        )
        resumed = [
            event
            async for event in second_runtime.resume(
                first[0].run_id,
                tenant_id="tenant-sqlite",
                user_id="user-sqlite",
            )
        ]

        assert resumed[-1].type == "run.completed"
        reopened = SQLiteAgentStateStore(path)
        assert reopened.get_run(first[0].run_id).final_output == "durable"
        assert len(reopened.list_turns(first[0].run_id)) == 2
        assert reopened.get_checkpoint(first[0].run_id).version >= 3

    asyncio.run(scenario())


def test_run_identity_cancellation_modules_and_policy_budget() -> None:
    async def scenario() -> None:
        captured: list[dict[str, Any]] = []

        class CapturingGateway:
            async def complete(self, **request: Any) -> ModelResponse:
                captured.append(request)
                return ModelResponse(
                    tool_calls=(ModelToolCall("noop-1", "module.noop", {}),),
                    model="scripted",
                )

        module_registry = AgentModuleRegistry()
        module_registry.register(
            StaticAgentModule(
                name="business",
                system_instructions=("Business rule 42.",),
                module_tools=(
                    AgentFunctionTool(
                        name="module.noop",
                        handler=lambda arguments, context: {"ok": True},
                    ),
                ),
            )
        )
        runtime = BusinessAgentRuntime(
            model_gateway=CapturingGateway(),
            module_registry=module_registry,
            policy_resolver=StaticAgentPolicyResolver(AgentPolicy(max_steps=1)),
        )
        request = AgentRequest(
            agent_id="a",
            message="loop",
            tenant_id="tenant-a",
            user_id="user-a",
            request_id="budget-request",
            module_names=("business",),
        )
        events = [event async for event in runtime.run(request)]
        run_id = events[0].run_id

        assert "Business rule 42." in captured[0]["messages"][0].content
        assert captured[0]["tools"][0].name == "module.noop"
        assert events[-1].type == "run.failed"
        assert events[-1].data["error_type"] == "AgentPolicyError"

        with pytest.raises(AgentIdentityError):
            await runtime.get_run(run_id, tenant_id="tenant-b", user_id="user-a")
        with pytest.raises(AgentIdentityError):
            await runtime.get_run(run_id, tenant_id="tenant-a", user_id="user-b")

        cancel_gateway = ScriptedGateway(ModelResponse(content="unused", model="scripted"))
        cancel_runtime = BusinessAgentRuntime(model_gateway=cancel_gateway)
        cancel_events = []
        async for event in cancel_runtime.run(
            AgentRequest(agent_id="a", message="cancel", request_id="cancel-request")
        ):
            cancel_events.append(event)
            if event.type == "model.started":
                await cancel_runtime.cancel(
                    event.run_id,
                    tenant_id="default",
                    user_id=None,
                )

        assert cancel_events[-1].type == "run.cancelled"
        assert cancel_gateway.calls == []

    asyncio.run(scenario())


def test_module_and_tool_registration_collisions_are_rejected() -> None:
    module_registry = AgentModuleRegistry()
    module = StaticAgentModule(name="duplicate")
    module_registry.register(module)
    with pytest.raises(AgentRunConflictError):
        module_registry.register(module)

    tools = ToolRegistry()
    tool = AgentFunctionTool(name="duplicate.tool", handler=lambda args, context: {})
    tools.register(tool)
    with pytest.raises(ValueError):
        tools.register(tool)
