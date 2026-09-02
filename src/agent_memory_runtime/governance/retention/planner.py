from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryLabel, MemoryLevel, MemoryStatus
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.governance.retention.policy import (
    RetentionAction,
    RetentionPlan,
    RetentionPolicy,
)


class RetentionPlanner:
    def __init__(self, policy: RetentionPolicy | None = None) -> None:
        self.policy = policy or RetentionPolicy()

    def plan(self, records: list[MemoryRecord], *, current_sequence: int) -> RetentionPlan:
        actions: list[RetentionAction] = []
        for record in records:
            if record.status != MemoryStatus.ACTIVE.value:
                continue
            age = max(0, current_sequence - record.last_event_sequence)
            if self._should_delete_sensitive(record, age):
                actions.append(
                    RetentionAction(
                        memory_id=record.memory_id,
                        action="delete",
                        reason="sensitive_retention_expired",
                    )
                )
                continue
            if self._should_archive_working(record, age):
                actions.append(
                    RetentionAction(
                        memory_id=record.memory_id,
                        action="mark_archived",
                        reason="working_memory_retention_expired",
                    )
                )
        return RetentionPlan(actions=tuple(actions), current_sequence=current_sequence)

    def _should_delete_sensitive(self, record: MemoryRecord, age: int) -> bool:
        limit = self.policy.delete_sensitive_after_sequences
        return (
            limit is not None
            and MemoryLabel.SENSITIVE.value in set(record.labels)
            and age >= limit
        )

    def _should_archive_working(self, record: MemoryRecord, age: int) -> bool:
        if record.level == MemoryLevel.PROFILE.value:
            return False
        if age < self.policy.archive_working_after_sequences:
            return False
        salience_limit = self.policy.archive_below_salience
        return salience_limit is None or record.salience <= salience_limit
