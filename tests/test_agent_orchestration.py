from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import deque
from dataclasses import replace
from typing import Any

import pytest

from agent_memory_runtime.agent import (
    AgentDefinition,
    AgentDefinitionRegistry,
    AgentFunctionTool,
    AgentGraph,
    AgentIdentityError,
    AgentOrchestrator,
    AgentPolicyError,
    AgentRequest,
    AgentRun,
    AgentRunConflictError,
    ApprovalRecord,
    BusinessAgentRuntime,
    DelegatedTask,
    DelegationRecord,
    DelegationStatus,
    InMemoryOrchestrationStore,
    ModelResponse,
    ModelToolCall,
    OrchestrationPolicy,
    OrchestrationRequest,
    OrchestrationRun,
    OrchestrationStatus,
    SQLiteOrchestrationStore,
    ToolCallRecord,
)
from agent_memory_runtime.agent.models import RunStatus
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
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


def test_graph_and_policy_reject_cycles_unregistered_agents_and_excess_fanout() -> None:
    with pytest.raises(ValueError, match="cycle"):
        AgentGraph(
            tasks=(
                DelegatedTask("a", "agent-a", "a", depends_on=("b",)),
                DelegatedTask("b", "agent-b", "b", depends_on=("a",)),
            )
        )

    graph = AgentGraph(
        tasks=(
            DelegatedTask("a", "agent-a", "a"),
            DelegatedTask("b", "agent-a", "b"),
        )
    )
    request = OrchestrationRequest(graph=graph)
    with pytest.raises(AgentPolicyError, match="max_fan_out"):
        OrchestrationPolicy(max_fan_out=1).validate(request)
    deep_graph = AgentGraph(
        tasks=(
            DelegatedTask("one", "agent-a", "one"),
            DelegatedTask("two", "agent-a", "two", depends_on=("one",)),
            DelegatedTask("three", "agent-a", "three", depends_on=("two",)),
        )
    )
    with pytest.raises(AgentPolicyError, match="max_depth"):
        OrchestrationPolicy(max_depth=2).validate(
            OrchestrationRequest(graph=deep_graph)
        )

    orchestrator = AgentOrchestrator(registry=AgentDefinitionRegistry())

    async def scenario() -> None:
        with pytest.raises(AgentRunConflictError, match="not registered"):
            _ = [event async for event in orchestrator.run(request)]

    asyncio.run(scenario())


def test_parallel_dag_injects_untrusted_dependencies_and_replays_idempotently() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        started: set[str] = set()

        class ParallelGateway:
            def __init__(self, name: str) -> None:
                self.name = name
                self.calls = 0

            async def complete(self, **request: Any) -> ModelResponse:
                self.calls += 1
                started.add(self.name)
                if len(started) == 2:
                    gate.set()
                await asyncio.wait_for(gate.wait(), timeout=1)
                return ModelResponse(
                    content=f"{self.name}-result",
                    model="scripted",
                    input_tokens=2,
                    output_tokens=1,
                )

        class MergeGateway:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def complete(self, **request: Any) -> ModelResponse:
                self.calls.append(request)
                return ModelResponse(
                    content="merged",
                    model="scripted",
                    input_tokens=3,
                    output_tokens=1,
                )

        gateway_a = ParallelGateway("a")
        gateway_b = ParallelGateway("b")
        merge_gateway = MergeGateway()
        registry = AgentDefinitionRegistry()
        registry.register(
            AgentDefinition("agent-a", BusinessAgentRuntime(model_gateway=gateway_a))
        )
        registry.register(
            AgentDefinition("agent-b", BusinessAgentRuntime(model_gateway=gateway_b))
        )
        registry.register(
            AgentDefinition(
                "merge-agent",
                BusinessAgentRuntime(model_gateway=merge_gateway),
            )
        )
        orchestrator = AgentOrchestrator(
            registry=registry,
            policy=OrchestrationPolicy(max_parallelism=2),
        )
        request = OrchestrationRequest(
            graph=AgentGraph(
                tasks=(
                    DelegatedTask("a", "agent-a", "research a"),
                    DelegatedTask("b", "agent-b", "research b"),
                    DelegatedTask(
                        "merge",
                        "merge-agent",
                        "merge results",
                        depends_on=("a", "b"),
                    ),
                ),
                output_task_ids=("merge",),
            ),
            tenant_id="tenant-a",
            user_id="user-a",
            request_id="parallel-request",
        )

        events = [event async for event in orchestrator.run(request)]

        assert events[-1].type == "orchestration.completed"
        assert events[-1].data["outputs"] == {"merge": "merged"}
        assert started == {"a", "b"}
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        merge_user_message = merge_gateway.calls[0]["messages"][-1].content
        assert "<dependency_results_json>" in merge_user_message
        assert '"a":"a-result"' in merge_user_message
        assert '"b":"b-result"' in merge_user_message
        assert "untrusted data" in merge_gateway.calls[0]["messages"][0].content

        run_id = events[0].orchestration_id
        records = await orchestrator.list_delegations(
            run_id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        assert {record.status for record in records} == {DelegationStatus.COMPLETED}
        assert all(record.child_run_id for record in records)

        replay = [event async for event in orchestrator.run(request)]
        assert [event.type for event in replay] == ["orchestration.completed"]
        assert replay[0].data["replayed"] is True
        assert gateway_a.calls == 1
        assert gateway_b.calls == 1
        assert len(merge_gateway.calls) == 1

    asyncio.run(scenario())


def test_concurrent_duplicate_request_is_busy_not_conflicting() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingGateway:
            async def complete(self, **request: Any) -> ModelResponse:
                entered.set()
                await release.wait()
                return ModelResponse(content="done", model="scripted")

        registry = AgentDefinitionRegistry()
        registry.register(
            AgentDefinition(
                "worker",
                BusinessAgentRuntime(model_gateway=BlockingGateway()),
            )
        )
        orchestrator = AgentOrchestrator(registry=registry)
        request = OrchestrationRequest(
            graph=AgentGraph(tasks=(DelegatedTask("one", "worker", "work"),)),
            tenant_id="tenant-a",
            request_id="concurrent-idempotency",
        )

        first_task = asyncio.create_task(
            _collect_orchestration(orchestrator, request)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        duplicate = await asyncio.wait_for(
            _collect_orchestration(orchestrator, request),
            timeout=1,
        )
        release.set()
        first = await asyncio.wait_for(first_task, timeout=1)

        assert duplicate[-1].type == "orchestration.busy"
        assert duplicate[-1].orchestration_id == first[0].orchestration_id
        assert first[-1].type == "orchestration.completed"

    asyncio.run(scenario())


def test_child_approval_pauses_parent_and_resumes_from_checkpoint() -> None:
    async def scenario() -> None:
        writes: list[str] = []

        def write(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
            writes.append(context.call_id)
            return {"saved": arguments["value"]}

        tools = ToolRegistry()
        tools.register(
            AgentFunctionTool(
                name="record.write",
                handler=write,
                side_effects=True,
                idempotent=True,
            )
        )
        gateway = ScriptedGateway(
            ModelResponse(
                tool_calls=(
                    ModelToolCall("write-1", "record.write", {"value": "v"}),
                ),
                model="scripted",
            ),
            ModelResponse(content="saved", model="scripted"),
        )
        child = BusinessAgentRuntime(model_gateway=gateway, tool_registry=tools)
        registry = AgentDefinitionRegistry()
        registry.register(AgentDefinition("writer", child))
        orchestrator = AgentOrchestrator(registry=registry)
        request = OrchestrationRequest(
            graph=AgentGraph(tasks=(DelegatedTask("write", "writer", "save"),)),
            tenant_id="tenant-a",
            user_id="user-a",
            request_id="approval-orchestration",
        )

        first = [event async for event in orchestrator.run(request)]
        approval_child = next(
            event.data["event"]
            for event in first
            if event.type == "delegation.child_event"
            and event.data["event"]["type"] == "approval.required"
        )
        assert first[-1].type == "orchestration.waiting"
        assert writes == []

        orchestration_id = first[0].orchestration_id
        foreign_run = child.state_store.create_run(
            AgentRun.new(
                AgentRequest(
                    agent_id="writer",
                    message="foreign",
                    tenant_id="tenant-a",
                    user_id="user-a",
                    request_id="foreign-approval-run",
                )
            )
        )
        foreign_call = child.state_store.create_tool_call(
            ToolCallRecord(
                call_id="foreign-call",
                run_id=foreign_run.run_id,
                tenant_id="tenant-a",
                tool_name="record.write",
                arguments={},
            )
        )
        foreign_approval = child.state_store.create_approval(
            ApprovalRecord.new(
                run_id=foreign_run.run_id,
                call_id=foreign_call.call_id,
                tenant_id="tenant-a",
            )
        )
        with pytest.raises(AgentIdentityError, match="delegated child run"):
            await orchestrator.decide_approval(
                orchestration_id,
                task_id="write",
                approval_id=foreign_approval.approval_id,
                tenant_id="tenant-a",
                user_id="user-a",
                reviewer_id="reviewer",
                approved=True,
            )
        await orchestrator.decide_approval(
            orchestration_id,
            task_id="write",
            approval_id=approval_child["data"]["approval_id"],
            tenant_id="tenant-a",
            user_id="user-a",
            reviewer_id="reviewer",
            approved=True,
        )
        resumed = [
            event
            async for event in orchestrator.resume(
                orchestration_id,
                tenant_id="tenant-a",
                user_id="user-a",
            )
        ]

        assert writes == ["write-1"]
        assert resumed[-1].type == "orchestration.completed"
        assert resumed[-1].data["outputs"] == {"write": "saved"}
        assert resumed[0].execution_id != first[0].execution_id
        assert resumed[0].sequence > first[-1].sequence

    asyncio.run(scenario())


def test_failure_blocks_dependents_and_global_token_budget_is_enforced() -> None:
    async def scenario() -> None:
        class FailingGateway:
            async def complete(self, **request: Any) -> ModelResponse:
                raise ValueError("failed")

        downstream = ScriptedGateway(ModelResponse(content="should-not-run"))
        registry = AgentDefinitionRegistry()
        registry.register(
            AgentDefinition("bad", BusinessAgentRuntime(model_gateway=FailingGateway()))
        )
        registry.register(
            AgentDefinition(
                "downstream",
                BusinessAgentRuntime(model_gateway=downstream),
            )
        )
        failed_orchestrator = AgentOrchestrator(registry=registry)
        failed = [
            event
            async for event in failed_orchestrator.run(
                OrchestrationRequest(
                    graph=AgentGraph(
                        tasks=(
                            DelegatedTask("bad", "bad", "fail"),
                            DelegatedTask(
                                "downstream",
                                "downstream",
                                "never",
                                depends_on=("bad",),
                            ),
                        )
                    ),
                    request_id="failed-orchestration",
                )
            )
        ]
        assert failed[-1].type == "orchestration.failed"
        assert downstream.calls == []

        budget_gateway = ScriptedGateway(
            ModelResponse(
                content="costly",
                input_tokens=6,
                output_tokens=5,
            )
        )
        budget_registry = AgentDefinitionRegistry()
        budget_registry.register(
            AgentDefinition(
                "costly",
                BusinessAgentRuntime(model_gateway=budget_gateway),
            )
        )
        budget_orchestrator = AgentOrchestrator(
            registry=budget_registry,
            policy=OrchestrationPolicy(max_total_tokens=10),
        )
        budget_events = [
            event
            async for event in budget_orchestrator.run(
                OrchestrationRequest(
                    graph=AgentGraph(
                        tasks=(DelegatedTask("cost", "costly", "work"),)
                    ),
                    request_id="budget-orchestration",
                )
            )
        ]
        assert budget_events[-1].type == "orchestration.failed"
        assert budget_events[-1].data["error_type"] == "AgentPolicyError"

        root_gateway = ScriptedGateway(ModelResponse(content="large", model="scripted"))
        blocked_gateway = ScriptedGateway(
            ModelResponse(content="should-not-run", model="scripted")
        )
        payload_registry = AgentDefinitionRegistry()
        payload_registry.register(
            AgentDefinition("root", BusinessAgentRuntime(model_gateway=root_gateway))
        )
        payload_registry.register(
            AgentDefinition(
                "blocked",
                BusinessAgentRuntime(model_gateway=blocked_gateway),
            )
        )
        payload_orchestrator = AgentOrchestrator(
            registry=payload_registry,
            policy=OrchestrationPolicy(max_dependency_payload_chars=4),
        )
        payload_events = [
            event
            async for event in payload_orchestrator.run(
                OrchestrationRequest(
                    graph=AgentGraph(
                        tasks=(
                            DelegatedTask("root", "root", "root"),
                            DelegatedTask(
                                "blocked",
                                "blocked",
                                "blocked",
                                depends_on=("root",),
                            ),
                        )
                    ),
                    request_id="dependency-payload-limit",
                )
            )
        ]
        assert payload_events[-1].type == "orchestration.failed"
        assert payload_events[-1].data["error_type"] == "AgentPolicyError"
        assert blocked_gateway.calls == []

    asyncio.run(scenario())


def test_cancellation_propagates_to_active_child() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        class BlockingGateway:
            async def complete(self, **request: Any) -> ModelResponse:
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        child = BusinessAgentRuntime(model_gateway=BlockingGateway())
        registry = AgentDefinitionRegistry()
        registry.register(AgentDefinition("slow", child))
        orchestrator = AgentOrchestrator(registry=registry)
        request = OrchestrationRequest(
            graph=AgentGraph(tasks=(DelegatedTask("slow", "slow", "wait"),)),
            tenant_id="tenant-a",
            user_id="user-a",
            request_id="cancel-orchestration",
        )
        events: list[Any] = []

        async def consume() -> None:
            async for event in orchestrator.run(request):
                events.append(event)

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), timeout=1)
        orchestration_id = events[0].orchestration_id
        await orchestrator.cancel(
            orchestration_id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        await asyncio.wait_for(consumer, timeout=1)

        run = await orchestrator.get_run(
            orchestration_id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        records = await orchestrator.list_delegations(
            orchestration_id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        child_run = await child.get_run(
            records[0].child_run_id or "",
            tenant_id="tenant-a",
            user_id="user-a",
        )
        assert events[-1].type == "orchestration.cancelled"
        assert run.status is OrchestrationStatus.CANCELLED
        assert records[0].status is DelegationStatus.CANCELLED
        assert child_run.status is RunStatus.CANCELLED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "store_factory",
    [
        lambda tmp_path: InMemoryOrchestrationStore(),
        lambda tmp_path: SQLiteOrchestrationStore(tmp_path / "lease.sqlite"),
    ],
)
def test_orchestration_store_fences_leases_and_checks_identity(
    tmp_path: Any,
    store_factory: Any,
) -> None:
    store = store_factory(tmp_path)
    request = OrchestrationRequest(
        graph=AgentGraph(tasks=(DelegatedTask("one", "agent", "work"),)),
        tenant_id="tenant-a",
        request_id="lease-request",
    )
    run = store.create_run(OrchestrationRun.new(request, orchestration_id="orch-1"))
    store.create_delegations(
        (DelegationRecord(run.orchestration_id, "one", "agent"),)
    )
    claimed = store.claim_run(
        run.orchestration_id,
        worker_id="worker-a",
        lease_seconds=0.02,
    )
    assert claimed is not None
    assert (
        store.claim_run(
            run.orchestration_id,
            worker_id="worker-b",
            lease_seconds=1,
        )
        is None
    )
    time.sleep(0.03)
    reclaimed = store.claim_run(
        run.orchestration_id,
        worker_id="worker-b",
        lease_seconds=1,
    )
    assert reclaimed is not None
    assert reclaimed.lease_token != claimed.lease_token
    with pytest.raises(AgentRunConflictError, match="version changed"):
        store.update_run(
            replace(claimed, status=OrchestrationStatus.COMPLETED),
            expected_version=claimed.version,
            lease_token=claimed.lease_token,
        )
    with pytest.raises(AgentIdentityError):
        store.cancel_run(run.orchestration_id, tenant_id="tenant-b")


def test_sqlite_orchestration_survives_restart_and_bundle_uses_schema_v7(
    tmp_path: Any,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "orchestration.sqlite"
        bundle = SQLiteStoreBundle(path)
        gateway = ScriptedGateway(ModelResponse(content="durable", model="scripted"))
        registry = AgentDefinitionRegistry()
        registry.register(
            AgentDefinition("worker", BusinessAgentRuntime(model_gateway=gateway))
        )
        orchestrator = AgentOrchestrator(
            registry=registry,
            state_store=bundle.orchestration_store,
        )
        events = [
            event
            async for event in orchestrator.run(
                OrchestrationRequest(
                    graph=AgentGraph(
                        tasks=(DelegatedTask("one", "worker", "work"),)
                    ),
                    request_id="durable-orchestration",
                )
            )
        ]
        orchestration_id = events[0].orchestration_id

        reopened = SQLiteOrchestrationStore(path)
        stored = reopened.get_run(orchestration_id)
        records = reopened.list_delegations(orchestration_id)
        assert stored is not None
        assert stored.status is OrchestrationStatus.COMPLETED
        assert stored.outputs == {"one": "durable"}
        assert records[0].status is DelegationStatus.COMPLETED
        assert SQLiteStoreBundle(path).schema_version == 7
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    asyncio.run(scenario())


async def _collect_orchestration(
    orchestrator: AgentOrchestrator,
    request: OrchestrationRequest,
) -> list[Any]:
    return [event async for event in orchestrator.run(request)]
