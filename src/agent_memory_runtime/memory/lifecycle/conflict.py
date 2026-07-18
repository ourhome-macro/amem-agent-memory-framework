from __future__ import annotations

from agent_memory_runtime.domain.memory import MemoryRecord


def explain_conflict(record: MemoryRecord) -> str | None:
    conflict = record.metadata.get("conflict")
    if not isinstance(conflict, dict):
        return None
    return (
        "truth_value conflict: "
        f"{conflict.get('current_truth_value')} vs {conflict.get('candidate_truth_value')}"
    )

