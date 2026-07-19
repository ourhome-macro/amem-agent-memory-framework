from __future__ import annotations

from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.tools.base import ToolRequest, ToolResult


def tool_result_to_event(request: ToolRequest, result: ToolResult) -> Event:
    return Event(
        kind="tool.result",
        actor_id=request.actor_id,
        session_id=request.session_id,
        labels=request.labels,
        tags=tuple(dict.fromkeys([*request.tags, "tool", request.tool_name])),
        payload={
            "agent_id": request.agent_id,
            "subject_id": request.tool_name,
            "tool_name": request.tool_name,
            "tool_request_id": request.request_id,
            "result_status": result.status,
            "output_hash": result.output_hash,
            "error_type": result.error_type,
            "summary": f"Tool {request.tool_name} {result.status}.",
        },
    )
