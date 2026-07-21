from __future__ import annotations

from agent_memory_runtime.domain.enums import (
    MemoryLayer,
    MemorySessionPolicy,
    MemoryStatus,
)
from agent_memory_runtime.domain.memory import MemoryRecord
from agent_memory_runtime.domain.query import MemoryQuery


def hard_filter(record: MemoryRecord, query: MemoryQuery) -> bool:
    retrievable_archival = (
        record.status == MemoryStatus.ARCHIVED.value
        and record.layer == MemoryLayer.ARCHIVAL.value
        and MemoryLayer.ARCHIVAL.value in set(query.layers)
    )
    if record.status != MemoryStatus.ACTIVE.value and not retrievable_archival:
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
        if policy is MemorySessionPolicy.PROFILE and record.layer == MemoryLayer.WORKING.value:
            return False
    if query.scopes and record.scope not in set(query.scopes):
        return False
    if query.memory_types and record.memory_type not in set(query.memory_types):
        return False
    if query.layers and record.layer not in set(query.layers):
        return False
    if query.tags and not set(query.tags) & set(record.tags):
        return False
    return True
