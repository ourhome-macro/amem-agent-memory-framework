from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from agent_memory_runtime.agent.cancellation import CancellationToken
from agent_memory_runtime.agent.errors import (
    AgentPolicyError,
    AgentReconciliationRequired,
)
from agent_memory_runtime.agent.models import (
    AgentRequest,
    ModelToolCall,
    ToolCallRecord,
    ToolCallStatus,
    ToolDefinition,
    ToolRisk,
)
from agent_memory_runtime.agent.policy import AgentPolicy
from agent_memory_runtime.agent.stores.base import AgentStateStore
from agent_memory_runtime.audit.hashing import secure_hash
from agent_memory_runtime.tools.base import Tool


@dataclass(frozen=True)
class ToolExecutionContext:
    """Stable context passed to tools; call_id is the external idempotency key."""

    call_id: str
    run_id: str
    request: AgentRequest
    attempt: int
    cancellation_token: CancellationToken | None = None


AgentToolHandler = Callable[
    [dict[str, Any], ToolExecutionContext],
    dict[str, Any] | Awaitable[dict[str, Any]],
]
AgentToolCompensator = Callable[
    [dict[str, Any], ToolExecutionContext],
    dict[str, Any] | Awaitable[dict[str, Any]],
]


@dataclass(frozen=True)
class AgentFunctionTool:
    name: str
    handler: AgentToolHandler
    description: str = ""
    input_schema: dict[str, Any] | None = None
    side_effects: bool = False
    idempotent: bool = True
    risk_level: ToolRisk | None = None
    requires_approval: bool = False
    compensator: AgentToolCompensator | None = None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        result = await _invoke_callable(self.handler, arguments, context)
        return _normalize_output(result)

    async def compensate(
        self,
        output: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self.compensator is None:
            raise AgentPolicyError(f"tool {self.name!r} does not support compensation")
        result = await _invoke_callable(self.compensator, output, context)
        return _normalize_output(result)


class ReliableToolRuntime:
    def __init__(self, *, state_store: AgentStateStore) -> None:
        self.state_store = state_store

    async def execute(
        self,
        record: ToolCallRecord,
        *,
        tool: Tool,
        request: AgentRequest,
        policy: AgentPolicy,
        cancellation_token: CancellationToken | None = None,
    ) -> ToolCallRecord:
        if record.status is ToolCallStatus.SUCCEEDED:
            return record
        policy.authorize_tool(record.tool_name, side_effects=record.side_effects)
        try:
            validate_tool_arguments(record.arguments, tool_input_schema(tool))
        except AgentPolicyError as error:
            return await self._store_error(record, error, status=ToolCallStatus.BLOCKED)

        if (
            record.status is ToolCallStatus.EXECUTING
            and record.side_effects
            and not record.idempotent
        ):
            return await self._update(
                replace(
                    record,
                    status=ToolCallStatus.RECONCILIATION_REQUIRED,
                    error_type="UnknownSideEffectOutcome",
                    error_hash=secure_hash(
                        {"type": "UnknownSideEffectOutcome", "call_id": record.call_id}
                    ),
                )
            )

        maximum_attempts = policy.tool_max_attempts if record.idempotent else 1
        current = record
        while current.attempts < maximum_attempts:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            current = await self._update(
                replace(
                    current,
                    status=ToolCallStatus.EXECUTING,
                    attempts=current.attempts + 1,
                    error_type=None,
                    error_hash=None,
                )
            )
            context = ToolExecutionContext(
                call_id=current.call_id,
                run_id=current.run_id,
                request=request,
                attempt=current.attempts,
                cancellation_token=cancellation_token,
            )
            try:
                async with asyncio.timeout(policy.tool_timeout_seconds):
                    output = await _invoke_tool(tool, current.arguments, context)
                return await self._update(
                    replace(
                        current,
                        status=ToolCallStatus.SUCCEEDED,
                        output=output,
                        error_type=None,
                        error_hash=None,
                    )
                )
            except asyncio.CancelledError:
                if current.side_effects and not current.idempotent:
                    await asyncio.shield(self._mark_reconciliation(current))
                    raise AgentReconciliationRequired(
                        "non-idempotent tool was interrupted with an unknown outcome"
                    ) from None
                raise
            except Exception as error:
                if current.side_effects and not current.idempotent:
                    return await self._mark_reconciliation(current, error=error)
                if current.attempts >= maximum_attempts:
                    return await self._store_error(
                        current,
                        error,
                        status=ToolCallStatus.FAILED,
                    )
                delay = policy.retry_base_seconds * (2 ** (current.attempts - 1))
                if delay > 0:
                    await asyncio.sleep(delay)
        return current

    async def _mark_reconciliation(
        self,
        record: ToolCallRecord,
        *,
        error: Exception | None = None,
    ) -> ToolCallRecord:
        error_type = type(error).__name__ if error is not None else "InterruptedSideEffect"
        return await self._update(
            replace(
                record,
                status=ToolCallStatus.RECONCILIATION_REQUIRED,
                error_type=error_type,
                error_hash=secure_hash(
                    {
                        "type": error_type,
                        "call_id": record.call_id,
                    }
                ),
            )
        )

    async def compensate(
        self,
        record: ToolCallRecord,
        *,
        tool: Tool,
        request: AgentRequest,
        timeout_seconds: float,
    ) -> ToolCallRecord:
        if record.status is not ToolCallStatus.SUCCEEDED:
            raise AgentPolicyError("only a succeeded tool call can be compensated")
        compensate = getattr(tool, "compensate", None)
        if not callable(compensate):
            raise AgentPolicyError(f"tool {record.tool_name!r} does not support compensation")
        context = ToolExecutionContext(
            call_id=record.call_id,
            run_id=record.run_id,
            request=request,
            attempt=record.attempts,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                await _invoke_callable(compensate, dict(record.output), context)
            return await self._update(
                replace(record, status=ToolCallStatus.COMPENSATED)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return await self._store_error(
                record,
                error,
                status=ToolCallStatus.COMPENSATION_FAILED,
            )

    async def _store_error(
        self,
        record: ToolCallRecord,
        error: Exception,
        *,
        status: ToolCallStatus,
    ) -> ToolCallRecord:
        return await self._update(
            replace(
                record,
                status=status,
                error_type=type(error).__name__,
                error_hash=secure_hash(
                    {"type": type(error).__name__, "message": str(error)}
                ),
            )
        )

    async def _update(self, record: ToolCallRecord) -> ToolCallRecord:
        return await asyncio.to_thread(
            self.state_store.update_tool_call,
            record,
            expected_version=record.version,
        )


def tool_definition(tool: Tool) -> ToolDefinition:
    return ToolDefinition(
        name=tool.name,
        description=str(getattr(tool, "description", "")),
        input_schema=tool_input_schema(tool),
    )


def tool_input_schema(tool: Tool) -> dict[str, Any]:
    value = getattr(tool, "input_schema", None)
    if value is None:
        return {"type": "object", "additionalProperties": True}
    if not isinstance(value, dict):
        raise AgentPolicyError(f"tool {tool.name!r} input_schema must be an object")
    return {str(key): item for key, item in value.items()}


def tool_side_effects(tool: Tool) -> bool:
    return bool(getattr(tool, "side_effects", False))


def tool_idempotent(tool: Tool) -> bool:
    default = not tool_side_effects(tool)
    value = getattr(tool, "idempotent", default)
    return default if value is None else bool(value)


def tool_risk(tool: Tool) -> ToolRisk:
    default = ToolRisk.HIGH if tool_side_effects(tool) else ToolRisk.LOW
    value = getattr(tool, "risk_level", None) or default
    try:
        return value if isinstance(value, ToolRisk) else ToolRisk(str(value))
    except ValueError as error:
        raise AgentPolicyError(f"tool {tool.name!r} has an invalid risk level") from error


def tool_requires_approval(tool: Tool) -> bool:
    return bool(getattr(tool, "requires_approval", False))


def new_tool_call_record(
    call: ModelToolCall,
    *,
    run_id: str,
    tenant_id: str,
    tool: Tool,
) -> ToolCallRecord:
    return ToolCallRecord(
        call_id=call.call_id,
        run_id=run_id,
        tenant_id=tenant_id,
        tool_name=call.name,
        arguments=dict(call.arguments),
        side_effects=tool_side_effects(tool),
        idempotent=tool_idempotent(tool),
    )


def validate_tool_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    _validate_schema_value(arguments, schema, path="$")


def _validate_schema_value(value: object, schema: object, *, path: str) -> None:
    if not isinstance(schema, dict):
        raise AgentPolicyError(f"invalid tool schema at {path}")
    if "$ref" in schema:
        raise AgentPolicyError("tool schemas with $ref are not supported by the built-in validator")
    if "enum" in schema:
        enum_values = schema["enum"]
        if not isinstance(enum_values, list) or value not in enum_values:
            raise AgentPolicyError(f"tool argument at {path} is outside the allowed enum")
    expected = schema.get("type")
    if isinstance(expected, list):
        matches = any(_matches_type(value, str(item)) for item in expected)
    elif expected is None:
        matches = True
    else:
        matches = _matches_type(value, str(expected))
    if not matches:
        raise AgentPolicyError(f"tool argument at {path} has an invalid type")

    if expected == "object" or (expected is None and isinstance(value, dict)):
        if not isinstance(value, dict):
            return
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise AgentPolicyError(f"invalid object properties schema at {path}")
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise AgentPolicyError(f"invalid required schema at {path}")
        for key in required:
            if str(key) not in value:
                raise AgentPolicyError(f"required tool argument {path}.{key} is missing")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                if additional is False:
                    raise AgentPolicyError(f"unexpected tool argument {path}.{key}")
                if isinstance(additional, dict):
                    child_schema = additional
            if child_schema is not None:
                _validate_schema_value(item, child_schema, path=f"{path}.{key}")
    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, path=f"{path}[{index}]")
    if expected == "string" and isinstance(value, str):
        minimum = int(schema.get("minLength", 0))
        maximum = int(schema.get("maxLength", len(value)))
        if not minimum <= len(value) <= maximum:
            raise AgentPolicyError(f"tool argument at {path} has an invalid length")
    if expected in {"integer", "number"} and _is_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise AgentPolicyError(f"tool argument at {path} is below its minimum")
        if maximum is not None and value > maximum:
            raise AgentPolicyError(f"tool argument at {path} exceeds its maximum")


def _matches_type(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": _is_number(value),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def _invoke_tool(
    tool: Tool,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    execute = getattr(tool, "execute", None)
    if callable(execute):
        result = await _invoke_callable(execute, dict(arguments), context)
    else:
        result = await asyncio.to_thread(tool.run, dict(arguments))
    return _normalize_output(result)


async def _invoke_callable(callable_: Callable[..., object], *args: object) -> object:
    if inspect.iscoroutinefunction(callable_):
        return await callable_(*args)
    result = await asyncio.to_thread(callable_, *args)
    if inspect.isawaitable(result):
        return await result
    return result


def _normalize_output(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentPolicyError("tool output must be a JSON object")
    output = {str(key): item for key, item in value.items()}
    try:
        json.dumps(output, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise AgentPolicyError("tool output must be JSON serializable") from error
    return output
