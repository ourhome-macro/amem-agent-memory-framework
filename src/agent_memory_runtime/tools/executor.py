from __future__ import annotations

from time import perf_counter

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.hashing import secure_hash
from agent_memory_runtime.audit.stores import InMemoryAuditStore
from agent_memory_runtime.audit.stores.base import AuditStore
from agent_memory_runtime.audit.subject import AuditSubject
from agent_memory_runtime.tools.base import ToolExecution, ToolRequest, ToolResult
from agent_memory_runtime.tools.normalizer import tool_result_to_event
from agent_memory_runtime.tools.policy import ToolPolicy, ToolPolicyError
from agent_memory_runtime.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        audit_store: AuditStore | None = None,
        policy: ToolPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.audit_store = audit_store or InMemoryAuditStore()
        self.policy = policy or ToolPolicy()

    def execute(self, request: ToolRequest) -> ToolExecution:
        started_at = perf_counter()
        tool = self.registry.get(request.tool_name)
        try:
            self.policy.authorize(request, tool)
            if tool is None:
                raise ToolPolicyError(f"tool {request.tool_name} is not registered")
            output = tool.run(dict(request.arguments))
            result = ToolResult.succeeded(
                tool_name=request.tool_name,
                output=output,
                duration_ms=_elapsed_ms(started_at),
            )
            decision = AuditDecision.ALLOW.value
        except ToolPolicyError as error:
            result = ToolResult.blocked(
                tool_name=request.tool_name,
                error=error,
                duration_ms=_elapsed_ms(started_at),
            )
            decision = AuditDecision.BLOCK.value
        except Exception as error:
            result = ToolResult.failed(
                tool_name=request.tool_name,
                error=error,
                duration_ms=_elapsed_ms(started_at),
            )
            decision = AuditDecision.BLOCK.value

        event = tool_result_to_event(request, result)
        self._audit(request, result, decision=decision, event_id=event.event_id)
        return ToolExecution(request=request, result=result, event=event)

    def _audit(
        self,
        request: ToolRequest,
        result: ToolResult,
        *,
        decision: str,
        event_id: str,
    ) -> None:
        self.audit_store.append_envelope(
            AuditEnvelope(
                audit_type="tool_call",
                actor_id=request.actor_id,
                action=request.tool_name,
                outcome=result.status,
                decision=decision,
                subject=AuditSubject(subject_type="tool", subject_id=request.tool_name),
                rule_version="tool-runtime-v1",
                config_hash="",
                last_event_sequence=0,
                state_hash="",
                payload={
                    "tool_request_id": request.request_id,
                    "tool_name": request.tool_name,
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "event_id": event_id,
                    "argument_keys": sorted(str(key) for key in request.arguments),
                    "input_hash": secure_hash(request.arguments),
                    "output_hash": result.output_hash,
                    "duration_ms": result.duration_ms,
                    "error_type": result.error_type,
                    "error_hash": result.error_hash,
                },
            )
        )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))
