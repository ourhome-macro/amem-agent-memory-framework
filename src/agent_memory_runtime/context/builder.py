from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.access.projection import project_record
from agent_memory_runtime.config import RuntimeConfig
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import RetrievalTrace
from agent_memory_runtime.memory.compression import select_under_budget


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    selected_memory_ids: tuple[str, ...]
    blocked_memory_count: int
    projected_context: str
    memories: tuple[dict[str, object], ...]
    trace: RetrievalTrace


class ContextBuilder:
    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()

    def build(
        self,
        *,
        agent_id: str,
        records: list[MemoryRecord],
        trace: RetrievalTrace,
    ) -> AgentContext:
        selected = select_under_budget(records, token_budget=self.config.context_token_budget)
        projected = tuple(project_record(record) for record in selected)
        return AgentContext(
            agent_id=agent_id,
            selected_memory_ids=tuple(record.memory_id for record in selected),
            blocked_memory_count=trace.blocked_count,
            projected_context=format_context(selected),
            memories=projected,
            trace=trace,
        )


def format_context(records: list[MemoryRecord]) -> str:
    lines: list[str] = []
    for record in records:
        lines.append(
            f"[{record.memory_id}] type={record.memory_type} scope={record.scope} "
            f"layer={record.layer} salience={record.salience:.2f} "
            f"confidence={record.confidence:.2f} :: {record.content}"
        )
    return "\n".join(lines)
