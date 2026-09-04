from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.tokens import AdaptiveTokenEstimator, TokenEstimator


def estimate_tokens(
    record: MemoryRecord,
    *,
    estimator: TokenEstimator | None = None,
    model: str | None = None,
) -> int:
    counter = estimator or AdaptiveTokenEstimator()
    projection = (
        f"[{record.memory_id}] type={record.memory_type} level={record.level} "
        f"visibility={record.visibility} status={record.status} "
        f"temperature={record.temperature} priority={record.priority:.2f} "
        f"confidence={record.confidence:.2f} :: {record.content}"
    )
    return max(1, counter.count_text(projection, model=model))
