from __future__ import annotations

from dataclasses import dataclass

from agent_memory_runtime.access.principal import Principal
from agent_memory_runtime.domain.enums import MemoryLabel, MemoryScope
from agent_memory_runtime.domain.memory import MemoryRecord


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = "allowed"


class AccessPolicy:
    def decide(self, principal: Principal, record: MemoryRecord) -> AccessDecision:
        if record.tenant_id != principal.tenant_id:
            return AccessDecision(False, "tenant_boundary_blocked")
        if (
            record.user_id is not None
            and record.user_id != principal.user_id
            and not principal.is_auditor
        ):
            return AccessDecision(False, "user_boundary_blocked")
        owner_agent_id = record.agent_id or record.owner_id
        labels = set(record.labels)
        if MemoryLabel.SENSITIVE.value in labels and not principal.is_auditor:
            if MemoryLabel.SENSITIVE.value not in set(principal.allowed_labels):
                return AccessDecision(False, "sensitive_label_blocked")
        if MemoryLabel.PRIVATE.value in labels and owner_agent_id != principal.agent_id:
            if principal.agent_id not in set(record.visible_to) and not principal.is_auditor:
                return AccessDecision(False, "private_label_blocked")
        if record.scope == MemoryScope.GLOBAL.value:
            return AccessDecision(True)
        if record.scope == MemoryScope.SHARED.value:
            if (
                not record.visible_to
                or principal.agent_id in set(record.visible_to)
                or principal.is_auditor
            ):
                return AccessDecision(True)
            return AccessDecision(False, "shared_scope_visibility_blocked")
        if record.scope == MemoryScope.PRIVATE.value:
            if (
                owner_agent_id == principal.agent_id
                or principal.agent_id in set(record.visible_to)
            ):
                return AccessDecision(True)
            if principal.is_auditor:
                return AccessDecision(True, "auditor_override")
            return AccessDecision(False, "private_scope_owner_blocked")
        return AccessDecision(False, "unknown_scope")
