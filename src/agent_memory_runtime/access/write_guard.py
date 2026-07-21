from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryLabel, MemoryScope
from agent_memory_runtime.domain.memory import MemoryCandidate, MemoryRecord
from agent_memory_runtime.exceptions import WriteGuardError


class WriteGuard:
    def validate(
        self,
        candidate: MemoryCandidate,
        *,
        source_event_exists: bool,
        current: MemoryRecord | None = None,
    ) -> None:
        if not candidate.source_event_ids or not source_event_exists:
            raise WriteGuardError(
                f"memory candidate {candidate.memory_id} must reference an existing source event"
            )
        if (
            MemoryLabel.SENSITIVE.value in set(candidate.labels)
            and candidate.scope == MemoryScope.GLOBAL.value
        ):
            raise WriteGuardError(
                f"sensitive memory {candidate.memory_id} cannot use global scope"
            )
        if (
            MemoryLabel.SENSITIVE.value in set(candidate.labels)
            and candidate.scope == MemoryScope.SHARED.value
            and not candidate.visible_to
        ):
            raise WriteGuardError(
                f"shared sensitive memory {candidate.memory_id} requires visible_to"
            )
        if candidate.scope == MemoryScope.PRIVATE.value and not candidate.owner_id:
            raise WriteGuardError(f"private memory {candidate.memory_id} requires owner_id")
        if current is not None:
            self._validate_information_flow(current, candidate)

    def _validate_information_flow(
        self,
        current: MemoryRecord,
        candidate: MemoryCandidate,
    ) -> None:
        if current.tenant_id != candidate.tenant_id:
            raise WriteGuardError(
                f"memory {candidate.memory_id} cannot cross tenant boundary"
            )
        if current.user_id != candidate.user_id:
            raise WriteGuardError(
                f"memory {candidate.memory_id} cannot cross user boundary"
            )
        current_agent_id = current.agent_id or current.owner_id
        candidate_agent_id = candidate.agent_id or candidate.owner_id
        if current_agent_id != candidate_agent_id:
            raise WriteGuardError(
                f"memory {candidate.memory_id} cannot cross agent boundary"
            )
        if (
            current.scope == MemoryScope.PRIVATE.value
            and candidate.scope != MemoryScope.PRIVATE.value
        ):
            raise WriteGuardError(
                f"private memory {candidate.memory_id} cannot be promoted to {candidate.scope}"
            )
        sensitive_dropped = (
            MemoryLabel.SENSITIVE.value in set(current.labels)
            and MemoryLabel.SENSITIVE.value not in set(candidate.labels)
        )
        if sensitive_dropped:
            raise WriteGuardError(
                f"sensitive memory {candidate.memory_id} cannot drop sensitive label"
            )
