from __future__ import annotations

from dataclasses import replace

from agent_memory_runtime.audit.decision import AuditDecision
from agent_memory_runtime.audit.envelope import AuditEnvelope
from agent_memory_runtime.audit.snapshot import RuntimeSnapshot
from agent_memory_runtime.audit.stores.base import AuditStore
from agent_memory_runtime.audit.subject import AuditSubject
from agent_memory_runtime.domain.enums import MemoryLayer, MemoryOperation, MemoryStatus
from agent_memory_runtime.governance.retention.policy import RetentionPlan, RetentionReport
from agent_memory_runtime.memory.stores.base import MemoryStore


class RetentionExecutor:
    def __init__(self, *, memory_store: MemoryStore, audit_store: AuditStore) -> None:
        self.memory_store = memory_store
        self.audit_store = audit_store

    def apply(self, plan: RetentionPlan, *, snapshot: RuntimeSnapshot) -> RetentionReport:
        action_by_id = {action.memory_id: action for action in plan.actions}
        archived: list[str] = []
        deleted: list[str] = []
        records = []
        for record in self.memory_store.list_records():
            action = action_by_id.get(record.memory_id)
            if action is None:
                records.append(record)
                continue
            if action.action == "delete":
                deleted.append(record.memory_id)
                continue
            if action.action == "archive":
                archived.append(record.memory_id)
                records.append(
                    replace(
                        record,
                        layer=MemoryLayer.ARCHIVAL.value,
                        status=MemoryStatus.ARCHIVED.value,
                        last_operation=MemoryOperation.ARCHIVE.value,
                    )
                )
                continue
            records.append(record)

        self.memory_store.replace_all(records)
        report = RetentionReport(
            archived_memory_ids=tuple(archived),
            deleted_memory_ids=tuple(deleted),
        )
        self._audit(plan, report, snapshot=snapshot)
        return report

    def _audit(
        self,
        plan: RetentionPlan,
        report: RetentionReport,
        *,
        snapshot: RuntimeSnapshot,
    ) -> None:
        if not plan.actions:
            return
        self.audit_store.append_envelope(
            AuditEnvelope(
                audit_type="retention",
                actor_id="governance",
                action="apply_retention",
                outcome="applied",
                decision=AuditDecision.ALLOW.value,
                subject=AuditSubject(subject_type="retention_plan", subject_id="latest"),
                rule_version=snapshot.rule_version,
                config_hash=snapshot.config_hash,
                last_event_sequence=snapshot.last_event_sequence,
                state_hash=snapshot.state_hash,
                payload={
                    "current_sequence": plan.current_sequence,
                    "archived_memory_ids": list(report.archived_memory_ids),
                    "deleted_memory_ids": list(report.deleted_memory_ids),
                    "actions": [
                        {
                            "memory_id": action.memory_id,
                            "action": action.action,
                            "reason": action.reason,
                        }
                        for action in plan.actions
                    ],
                },
            )
        )
