from __future__ import annotations

from typing import Any

from agent_memory_runtime.agent import AgentFunctionTool, ToolExecutionContext, ToolRisk
from agent_memory_runtime.memory.intake.models import MemoryToolIdentity
from agent_memory_runtime.memory.intake.service import MemoryIntakeService


def build_memory_intake_tools(runtime: object) -> tuple[AgentFunctionTool, ...]:
    service = MemoryIntakeService(runtime)
    return (
        AgentFunctionTool(
            name="save_memory",
            description=(
                "Save an explicit user preference, belief, or task outcome as a "
                "structured memory event."
            ),
            handler=lambda args, ctx: service.save_memory(
                args,
                identity=_identity(ctx),
                idempotency_key=ctx.call_id,
            ).to_dict(),
            input_schema=_SAVE_SCHEMA,
            side_effects=True,
            idempotent=True,
            risk_level=ToolRisk.MEDIUM,
        ),
        AgentFunctionTool(
            name="revise_memory",
            description=(
                "Revise or supersede an existing explicit memory by emitting a "
                "structured memory event."
            ),
            handler=lambda args, ctx: service.revise_memory(
                args,
                identity=_identity(ctx),
                idempotency_key=ctx.call_id,
            ).to_dict(),
            input_schema=_REVISE_SCHEMA,
            side_effects=True,
            idempotent=True,
            risk_level=ToolRisk.MEDIUM,
        ),
        AgentFunctionTool(
            name="forget_memory",
            description=(
                "Delete or archive one authorized memory by memory_id or by an "
                "unambiguous query."
            ),
            handler=lambda args, ctx: service.forget_memory(
                args,
                identity=_identity(ctx),
                idempotency_key=ctx.call_id,
            ).to_dict(),
            input_schema=_FORGET_SCHEMA,
            side_effects=True,
            idempotent=True,
            risk_level=ToolRisk.HIGH,
            requires_approval=True,
        ),
    )


def _identity(context: ToolExecutionContext) -> MemoryToolIdentity:
    request = context.request
    return MemoryToolIdentity(
        actor_id=request.actor_id,
        agent_id=request.agent_id,
        session_id=request.session_id,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        labels=("private",),
        tags=("agent-tool",),
    )


_COMMON_WRITE_PROPERTIES: dict[str, Any] = {
    "kind": {
        "type": "string",
        "enum": ["preference.updated", "belief.stated", "task.outcome"],
    },
    "key": {"type": "string", "minLength": 1, "maxLength": 120},
    "content": {"type": "string", "minLength": 1, "maxLength": 4000},
    "subject_id": {"type": "string", "minLength": 1, "maxLength": 200},
    "visibility": {"type": "string", "enum": ["private", "shared", "public"]},
    "level": {"type": "string", "enum": ["L0", "L1", "L2", "L3"]},
    "status": {"type": "string", "enum": ["active", "superseded", "archived", "deleted"]},
    "priority": {"type": "number", "minimum": 0, "maximum": 1},
    "salience": {"type": "number", "minimum": 0, "maximum": 1},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "reason": {"type": "string", "maxLength": 1000},
    "value": {},
    "truth_value": {},
    "visible_to": {"type": "array", "items": {"type": "string"}},
    "source_memory_ids": {"type": "array", "items": {"type": "string"}},
    "evidence_event_ids": {"type": "array", "items": {"type": "string"}},
    "result": {"type": "string", "maxLength": 200},
    "explicit": {"type": "boolean"},
}

_SAVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": _COMMON_WRITE_PROPERTIES,
    "required": ["kind", "key", "content"],
    "additionalProperties": False,
}

_REVISE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_COMMON_WRITE_PROPERTIES,
        "target_memory_id": {"type": "string", "minLength": 1},
        "operation": {"type": "string", "enum": ["merge", "supersede"]},
    },
    "required": ["kind", "key", "content"],
    "additionalProperties": False,
}

_FORGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "memory_id": {"type": "string", "minLength": 1},
        "query": {"type": "string", "minLength": 1, "maxLength": 1000},
        "mode": {"type": "string", "enum": ["delete", "archive"]},
        "reason": {"type": "string", "maxLength": 1000},
    },
    "additionalProperties": False,
}
