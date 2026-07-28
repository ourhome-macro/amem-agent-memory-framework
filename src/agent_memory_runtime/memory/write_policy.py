from __future__ import annotations

import re
from dataclasses import dataclass

from agent_memory_runtime.domain.enums import MemoryLabel, MemoryLayer, MemoryOperation, MemoryScope
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.memory.intake.models import MemoryProposal


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    reason: str | None = None
    retryable: bool = False

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


class MemoryValidator:
    def validate(self, proposal: MemoryProposal) -> PolicyDecision:
        if not proposal.proposal_id.strip():
            return PolicyDecision("rejected", "proposal_id_required")
        if proposal.action not in {
            MemoryOperation.CREATE.value,
            MemoryOperation.REINFORCE.value,
            MemoryOperation.REVISE.value,
            MemoryOperation.SUPERSEDE.value,
            MemoryOperation.ARCHIVE.value,
            MemoryOperation.DELETE.value,
            MemoryOperation.KEEP_BOTH.value,
            MemoryOperation.NEEDS_REVIEW.value,
        }:
            return PolicyDecision("rejected", "unsupported_action")
        if proposal.action in {MemoryOperation.KEEP_BOTH.value, MemoryOperation.NEEDS_REVIEW.value}:
            return PolicyDecision("needs_review", proposal.reason or proposal.action)
        if proposal.action in {
            MemoryOperation.REINFORCE.value,
            MemoryOperation.REVISE.value,
            MemoryOperation.SUPERSEDE.value,
            MemoryOperation.ARCHIVE.value,
            MemoryOperation.DELETE.value,
        } and not proposal.target_memory_id:
            return PolicyDecision("rejected", "target_memory_id_required")
        if proposal.action not in {MemoryOperation.ARCHIVE.value, MemoryOperation.DELETE.value}:
            if not proposal.subject_id.strip():
                return PolicyDecision("rejected", "subject_id_required")
            if not proposal.key or not proposal.key.strip():
                return PolicyDecision("rejected", "key_required")
            if not proposal.content.strip():
                return PolicyDecision("rejected", "content_required")
        if proposal.layer not in {item.value for item in MemoryLayer}:
            return PolicyDecision("rejected", "invalid_layer")
        if proposal.scope not in {item.value for item in MemoryScope}:
            return PolicyDecision("rejected", "invalid_scope")
        if not 0.0 <= proposal.confidence <= 1.0:
            return PolicyDecision("rejected", "invalid_confidence")
        if not 0.0 <= proposal.salience <= 1.0:
            return PolicyDecision("rejected", "invalid_salience")
        return PolicyDecision("allowed")


class AccessPolicy:
    def validate(
        self,
        proposal: MemoryProposal,
        *,
        current: MemoryRecord | None,
    ) -> PolicyDecision:
        if current is None:
            return PolicyDecision("allowed")
        if proposal.tenant_id != current.tenant_id:
            return PolicyDecision("rejected", "cross_tenant_write")
        if current.user_id is not None and proposal.user_id != current.user_id:
            return PolicyDecision("rejected", "cross_user_write")
        if current.agent_id is not None and proposal.agent_id != current.agent_id:
            return PolicyDecision("rejected", "cross_agent_write")
        if proposal.subject_id and proposal.subject_id != current.subject_id:
            return PolicyDecision("rejected", "cross_subject_write")
        if proposal.expected_version is not None and proposal.expected_version != current.version:
            return PolicyDecision("conflict", "version_changed", retryable=True)
        return PolicyDecision("allowed")


class RiskGuard:
    def validate(
        self,
        proposal: MemoryProposal,
        *,
        current: MemoryRecord | None,
    ) -> PolicyDecision:
        if proposal.action == MemoryOperation.DELETE.value and proposal.source != "forget_memory":
            return PolicyDecision("needs_review", "delete_requires_review")
        if _contains_high_risk_secret(proposal.content):
            return PolicyDecision("needs_review", "sensitive_content_requires_review")
        if current is not None and _expands_visibility(current, proposal):
            return PolicyDecision("needs_review", "visible_to_expansion_requires_review")
        if MemoryLabel.SENSITIVE.value in set(proposal.labels):
            return PolicyDecision("needs_review", "sensitive_label_requires_review")
        return PolicyDecision("allowed")


class MemoryWritePolicy:
    def __init__(
        self,
        *,
        validator: MemoryValidator | None = None,
        access_policy: AccessPolicy | None = None,
        risk_guard: RiskGuard | None = None,
    ) -> None:
        self.validator = validator or MemoryValidator()
        self.access_policy = access_policy or AccessPolicy()
        self.risk_guard = risk_guard or RiskGuard()

    def validate(
        self,
        proposal: MemoryProposal,
        *,
        current: MemoryRecord | None,
    ) -> PolicyDecision:
        for decision in (
            self.validator.validate(proposal),
            self.access_policy.validate(proposal, current=current),
            self.risk_guard.validate(proposal, current=current),
        ):
            if not decision.allowed:
                return decision
        return PolicyDecision("allowed")


def _expands_visibility(current: MemoryRecord, proposal: MemoryProposal) -> bool:
    if current.scope == proposal.scope and set(proposal.visible_to).issubset(current.visible_to):
        return False
    if current.scope == MemoryScope.PRIVATE.value and proposal.scope != MemoryScope.PRIVATE.value:
        return True
    if current.scope == MemoryScope.SHARED.value and proposal.scope == MemoryScope.GLOBAL.value:
        return True
    return not set(proposal.visible_to).issubset(set(current.visible_to))


def _contains_high_risk_secret(content: str) -> bool:
    normalized = content.casefold()
    if any(marker in normalized for marker in ("password", "api key", "secret", "token", "cvv")):
        return True
    if re.search(r"\b(?:\d[ -]*?){13,19}\b", content):
        return True
    if re.search(r"\b\d{3}-\d{2}-\d{4}\b", content):
        return True
    return False
