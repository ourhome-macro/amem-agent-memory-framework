from __future__ import annotations

from agent_memory_runtime.domain.enums import (
    MemoryLevel,
    MemorySessionPolicy,
    MemoryStatus,
)
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery


def hard_filter(record: MemoryRecord, query: MemoryQuery) -> bool:
    explicit_status_match = bool(query.statuses and record.status in set(query.statuses))
    if record.status != MemoryStatus.ACTIVE.value and not explicit_status_match:
        return False
    if record.tenant_id != query.tenant_id:
        return False
    # Apply the user boundary before candidate truncation. AccessChecker repeats
    # this decision as defense in depth, but delaying it until after the store's
    # candidate limit lets another user's records crowd out authorized results.
    if record.user_id is not None and record.user_id != query.user_id:
        return False
    if query.session_id is not None and record.session_id != query.session_id:
        policy = MemorySessionPolicy(query.session_policy)
        if policy is MemorySessionPolicy.EXACT:
            return False
        if policy is MemorySessionPolicy.PROFILE and record.level != MemoryLevel.PROFILE.value:
            return False
    if query.memory_types and record.memory_type not in set(query.memory_types):
        return False
    if query.levels and record.level not in set(query.levels):
        return False
    if query.visibilities and record.visibility not in set(query.visibilities):
        return False
    if query.temperatures and record.temperature not in set(query.temperatures):
        return False
    if query.tags and not set(query.tags) & set(record.tags):
        return False
    return True
