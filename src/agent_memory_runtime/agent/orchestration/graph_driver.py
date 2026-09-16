from __future__ import annotations

import asyncio
import hashlib
import operator
from typing import Annotated, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_memory_runtime.agent.graph_driver import _saver
from agent_memory_runtime.agent.orchestration.models import DelegationStatus, OrchestrationStatus
from agent_memory_runtime.telemetry import span


class GraphState(TypedDict):
    completed_nodes: Annotated[list[str], operator.add]


async def drive_dag(runtime, progress, *, factory, token):
    from agent_memory_runtime.agent.orchestration.runtime import _token_totals

    plan = progress.run.request.graph
    event_lock = asyncio.Lock()

    async def records():
        rows = await asyncio.to_thread(
            runtime.state_store.list_delegations, progress.run.orchestration_id
        )
        return {row.task_id: row for row in rows}

    class StreamQueue:
        async def put(self, queued):
            async with event_lock:
                event = factory.create(queued.type, queued.data)
                await runtime._persist_event(progress, factory)
                get_stream_writer()(event)

    def make_node(task):
        async def execute(_state):
            token.raise_if_cancelled()
            current = await records()
            record = current[task.task_id]
            if record.status is DelegationStatus.COMPLETED:
                return {"completed_nodes": [task.task_id]}
            if any(current[d].status is not DelegationStatus.COMPLETED for d in task.depends_on):
                return {"completed_nodes": [task.task_id]}
            if sum(_token_totals(current.values())) > runtime.policy.max_total_tokens:
                return {"completed_nodes": [task.task_id]}
            with span("orchestration.node", attributes={"task.id": task.task_id}):
                outcome = await runtime._execute_node(
                    progress.run,
                    task,
                    record,
                    dependency_outputs={d: current[d].output or "" for d in task.depends_on},
                    queue=StreamQueue(),
                    token=token,
                )
            if outcome.record.status is DelegationStatus.WAITING:
                interrupt({"task_id": task.task_id})
            return {"completed_nodes": [task.task_id]}

        return execute

    async def finish(_state):
        current = await records()
        input_tokens, output_tokens = _token_totals(current.values())
        failed = next((r for r in current.values() if r.status is DelegationStatus.FAILED), None)
        cancelled = next(
            (r for r in current.values() if r.status is DelegationStatus.CANCELLED), None
        )
        over_budget = input_tokens + output_tokens > runtime.policy.max_total_tokens
        outputs = {key: current[key].output or "" for key in plan.resolved_output_task_ids}
        if cancelled:
            status, error = OrchestrationStatus.CANCELLED, cancelled.error_type
        elif failed or over_budget:
            status, error = (
                OrchestrationStatus.FAILED,
                "AgentPolicyError" if over_budget else failed.error_type,
            )
        elif all(r.status is DelegationStatus.COMPLETED for r in current.values()):
            status, error = OrchestrationStatus.COMPLETED, None
        else:
            raise RuntimeError("Incomplete graph without an interrupt or terminal failure")
        event = factory.create(
            "orchestration." + status.value,
            {
                "outputs": outputs if status is OrchestrationStatus.COMPLETED else {},
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "error_type": error,
            },
        )
        progress.run = await runtime._update_run(
            progress.run,
            status=status,
            outputs=outputs if status is OrchestrationStatus.COMPLETED else {},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_type=error,
            event_sequence=factory.sequence,
        )
        get_stream_writer()(event)
        return {}

    builder = StateGraph(GraphState)
    names = {task.task_id: f"task_{index}" for index, task in enumerate(plan.tasks)}
    for task in plan.tasks:
        builder.add_node(names[task.task_id], make_node(task))
    for task in plan.tasks:
        if task.depends_on:
            builder.add_edge([names[d] for d in task.depends_on], names[task.task_id])
        else:
            builder.add_edge(START, names[task.task_id])
    dependencies = {d for task in plan.tasks for d in task.depends_on}
    leaves = [names[t.task_id] for t in plan.tasks if t.task_id not in dependencies]
    builder.add_node("finish", finish)
    builder.add_edge(leaves, "finish")
    builder.add_edge("finish", END)
    identity = "dag:" + progress.run.tenant_id + ":" + progress.run.orchestration_id
    config = {
        "configurable": {"thread_id": hashlib.sha256(identity.encode()).hexdigest()},
        "max_concurrency": runtime.policy.max_parallelism,
        "recursion_limit": len(plan.tasks) + 16,
    }
    async with _saver(runtime) as saver:
        graph = builder.compile(checkpointer=saver)
        snapshot = await graph.aget_state(config)
        inputs = (
            (Command(resume=True) if snapshot.interrupts else None)
            if snapshot.next
            else {"completed_nodes": []}
        )
        async for event in graph.astream(inputs, config, stream_mode="custom", version="v1"):
            yield event
        snapshot = await graph.aget_state(config)
        if snapshot.interrupts:
            current = await records()
            waiting = [
                key for key, row in current.items() if row.status is DelegationStatus.WAITING
            ]
            event = factory.create("orchestration.waiting", {"task_ids": waiting})
            progress.run = await runtime._update_run(
                progress.run, status=OrchestrationStatus.WAITING, event_sequence=factory.sequence
            )
            yield event
