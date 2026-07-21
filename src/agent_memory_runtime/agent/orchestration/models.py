from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from agent_memory_runtime.agent.models import utc_now_iso


class OrchestrationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DelegationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_ORCHESTRATION_STATUSES = frozenset(
    {
        OrchestrationStatus.COMPLETED,
        OrchestrationStatus.FAILED,
        OrchestrationStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class DelegatedTask:
    task_id: str
    agent_id: str
    message: str
    depends_on: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    module_names: tuple[str, ...] = ()
    include_dependency_outputs: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.agent_id.strip():
            raise ValueError("delegated task_id and agent_id cannot be empty")
        if not self.message.strip():
            raise ValueError("delegated task message cannot be empty")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError(f"task {self.task_id!r} contains duplicate dependencies")
        if self.task_id in self.depends_on:
            raise ValueError(f"task {self.task_id!r} cannot depend on itself")
        _ensure_json(self.metadata, label="delegated task metadata")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "message": self.message,
            "depends_on": list(self.depends_on),
            "instructions": list(self.instructions),
            "module_names": list(self.module_names),
            "include_dependency_outputs": self.include_dependency_outputs,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DelegatedTask:
        return cls(
            task_id=str(value["task_id"]),
            agent_id=str(value["agent_id"]),
            message=str(value["message"]),
            depends_on=tuple(str(item) for item in _list(value.get("depends_on"))),
            instructions=tuple(str(item) for item in _list(value.get("instructions"))),
            module_names=tuple(str(item) for item in _list(value.get("module_names"))),
            include_dependency_outputs=bool(
                value.get("include_dependency_outputs", True)
            ),
            metadata=_dict(value.get("metadata")),
        )


@dataclass(frozen=True)
class AgentGraph:
    tasks: tuple[DelegatedTask, ...]
    output_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("agent graph must contain at least one task")
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("agent graph task IDs must be unique")
        known = set(task_ids)
        for task in self.tasks:
            missing = set(task.depends_on) - known
            if missing:
                raise ValueError(
                    f"task {task.task_id!r} has unknown dependencies: "
                    f"{', '.join(sorted(missing))}"
                )
        unknown_outputs = set(self.output_task_ids) - known
        if unknown_outputs:
            raise ValueError(
                f"agent graph has unknown output tasks: {', '.join(sorted(unknown_outputs))}"
            )
        if len(self.output_task_ids) != len(set(self.output_task_ids)):
            raise ValueError("agent graph output task IDs must be unique")
        _validate_acyclic(self.tasks)

    @property
    def task_map(self) -> dict[str, DelegatedTask]:
        return {task.task_id: task for task in self.tasks}

    @property
    def resolved_output_task_ids(self) -> tuple[str, ...]:
        if self.output_task_ids:
            return self.output_task_ids
        dependencies = {dependency for task in self.tasks for dependency in task.depends_on}
        return tuple(task.task_id for task in self.tasks if task.task_id not in dependencies)

    @property
    def maximum_fan_out(self) -> int:
        dependents: dict[str, int] = {task.task_id: 0 for task in self.tasks}
        roots = 0
        for task in self.tasks:
            if not task.depends_on:
                roots += 1
            for dependency in task.depends_on:
                dependents[dependency] += 1
        return max((roots, *dependents.values()))

    @property
    def maximum_depth(self) -> int:
        depths: dict[str, int] = {}
        remaining = list(self.tasks)
        while remaining:
            for task in tuple(remaining):
                if not all(dependency in depths for dependency in task.depends_on):
                    continue
                depths[task.task_id] = 1 + max(
                    (depths[dependency] for dependency in task.depends_on),
                    default=0,
                )
                remaining.remove(task)
        return max(depths.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [task.to_dict() for task in self.tasks],
            "output_task_ids": list(self.output_task_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentGraph:
        return cls(
            tasks=tuple(
                DelegatedTask.from_dict(_dict(item))
                for item in _list(value.get("tasks"))
            ),
            output_task_ids=tuple(
                str(item) for item in _list(value.get("output_task_ids"))
            ),
        )


@dataclass(frozen=True)
class OrchestrationRequest:
    graph: AgentGraph
    orchestrator_id: str = "orchestrator"
    actor_id: str = "user"
    tenant_id: str = "default"
    user_id: str | None = None
    session_id: str = "default"
    request_id: str = field(default_factory=lambda: str(uuid4()))
    root_orchestration_id: str | None = None
    parent_orchestration_id: str | None = None
    depth: int = 0
    instructions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.orchestrator_id.strip() or not self.actor_id.strip():
            raise ValueError("orchestrator_id and actor_id cannot be empty")
        if not self.tenant_id.strip() or not self.request_id.strip():
            raise ValueError("tenant_id and request_id cannot be empty")
        if self.depth < 0:
            raise ValueError("orchestration depth cannot be negative")
        _ensure_json(self.metadata, label="orchestration request metadata")

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph.to_dict(),
            "orchestrator_id": self.orchestrator_id,
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "root_orchestration_id": self.root_orchestration_id,
            "parent_orchestration_id": self.parent_orchestration_id,
            "depth": self.depth,
            "instructions": list(self.instructions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OrchestrationRequest:
        return cls(
            graph=AgentGraph.from_dict(_dict(value["graph"])),
            orchestrator_id=str(value.get("orchestrator_id") or "orchestrator"),
            actor_id=str(value.get("actor_id") or "user"),
            tenant_id=str(value.get("tenant_id") or "default"),
            user_id=_optional_str(value.get("user_id")),
            session_id=str(value.get("session_id") or "default"),
            request_id=str(value["request_id"]),
            root_orchestration_id=_optional_str(value.get("root_orchestration_id")),
            parent_orchestration_id=_optional_str(value.get("parent_orchestration_id")),
            depth=int(value.get("depth") or 0),
            instructions=tuple(str(item) for item in _list(value.get("instructions"))),
            metadata=_dict(value.get("metadata")),
        )


@dataclass(frozen=True)
class OrchestrationRun:
    orchestration_id: str
    request: OrchestrationRequest
    status: OrchestrationStatus = OrchestrationStatus.PENDING
    version: int = 0
    event_sequence: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    outputs: dict[str, str] = field(default_factory=dict)
    error_type: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: str | None = None

    @classmethod
    def new(
        cls,
        request: OrchestrationRequest,
        *,
        orchestration_id: str | None = None,
    ) -> OrchestrationRun:
        identifier = orchestration_id or str(uuid4())
        if request.root_orchestration_id is None:
            request = OrchestrationRequest.from_dict(
                {**request.to_dict(), "root_orchestration_id": identifier}
            )
        return cls(orchestration_id=identifier, request=request)

    @property
    def tenant_id(self) -> str:
        return self.request.tenant_id

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_ORCHESTRATION_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "orchestration_id": self.orchestration_id,
            "request": self.request.to_dict(),
            "status": self.status.value,
            "version": self.version,
            "event_sequence": self.event_sequence,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "outputs": dict(self.outputs),
            "error_type": self.error_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OrchestrationRun:
        return cls(
            orchestration_id=str(value["orchestration_id"]),
            request=OrchestrationRequest.from_dict(_dict(value["request"])),
            status=OrchestrationStatus(
                str(value.get("status") or OrchestrationStatus.PENDING.value)
            ),
            version=int(value.get("version") or 0),
            event_sequence=int(value.get("event_sequence") or 0),
            input_tokens=int(value.get("input_tokens") or 0),
            output_tokens=int(value.get("output_tokens") or 0),
            outputs={
                str(key): str(item) for key, item in _dict(value.get("outputs")).items()
            },
            error_type=_optional_str(value.get("error_type")),
            created_at=str(value.get("created_at") or utc_now_iso()),
            updated_at=str(value.get("updated_at") or utc_now_iso()),
            lease_owner=_optional_str(value.get("lease_owner")),
            lease_token=_optional_str(value.get("lease_token")),
            lease_expires_at=_optional_str(value.get("lease_expires_at")),
        )


@dataclass(frozen=True)
class DelegationRecord:
    orchestration_id: str
    task_id: str
    agent_id: str
    status: DelegationStatus = DelegationStatus.PENDING
    version: int = 0
    child_run_id: str | None = None
    output: str | None = None
    error_type: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "orchestration_id": self.orchestration_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "version": self.version,
            "child_run_id": self.child_run_id,
            "output": self.output,
            "error_type": self.error_type,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DelegationRecord:
        return cls(
            orchestration_id=str(value["orchestration_id"]),
            task_id=str(value["task_id"]),
            agent_id=str(value["agent_id"]),
            status=DelegationStatus(
                str(value.get("status") or DelegationStatus.PENDING.value)
            ),
            version=int(value.get("version") or 0),
            child_run_id=_optional_str(value.get("child_run_id")),
            output=_optional_str(value.get("output")),
            error_type=_optional_str(value.get("error_type")),
            input_tokens=int(value.get("input_tokens") or 0),
            output_tokens=int(value.get("output_tokens") or 0),
            created_at=str(value.get("created_at") or utc_now_iso()),
            updated_at=str(value.get("updated_at") or utc_now_iso()),
        )


@dataclass(frozen=True)
class OrchestrationEvent:
    type: str
    orchestration_id: str
    execution_id: str
    sequence: int
    tenant_id: str
    orchestrator_id: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "orchestration_id": self.orchestration_id,
            "execution_id": self.execution_id,
            "sequence": self.sequence,
            "tenant_id": self.tenant_id,
            "orchestrator_id": self.orchestrator_id,
            "session_id": self.session_id,
            "occurred_at": self.occurred_at,
            "data": dict(self.data),
        }


def _validate_acyclic(tasks: tuple[DelegatedTask, ...]) -> None:
    dependencies = {task.task_id: set(task.depends_on) for task in tasks}
    ready = [task_id for task_id, values in dependencies.items() if not values]
    visited = 0
    while ready:
        task_id = ready.pop()
        visited += 1
        for candidate, values in dependencies.items():
            if task_id not in values:
                continue
            values.remove(task_id)
            if not values:
                ready.append(candidate)
    if visited != len(tasks):
        cyclic = sorted(task_id for task_id, values in dependencies.items() if values)
        raise ValueError(f"agent graph contains a dependency cycle: {', '.join(cyclic)}")


def _ensure_json(value: object, *, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable") from error


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
