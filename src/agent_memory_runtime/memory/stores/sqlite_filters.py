from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryLayer, MemorySessionPolicy, MemoryStatus
from agent_memory_runtime.domain.query import MemoryQuery


def structured_memory_where(
    query: MemoryQuery,
    *,
    alias: str = "memories",
    include_acl: bool = True,
) -> tuple[list[str], list[object]]:
    """Translate authoritative query boundaries to SQL before candidate LIMIT."""

    where = [f"{alias}.tenant_id = ?"]
    parameters: list[object] = [query.tenant_id]
    if query.user_id is None:
        where.append(f"{alias}.user_id IS NULL")
    else:
        where.append(f"({alias}.user_id IS NULL OR {alias}.user_id = ?)")
        parameters.append(query.user_id)

    archival_enabled = MemoryLayer.ARCHIVAL.value in set(query.layers)
    if archival_enabled:
        where.append(f"({alias}.status = ? OR ({alias}.status = ? AND {alias}.layer = ?))")
        parameters.extend(
            [
                MemoryStatus.ACTIVE.value,
                MemoryStatus.ARCHIVED.value,
                MemoryLayer.ARCHIVAL.value,
            ]
        )
    else:
        where.append(f"{alias}.status = ?")
        parameters.append(MemoryStatus.ACTIVE.value)

    if query.session_id is not None:
        policy = MemorySessionPolicy(query.session_policy)
        if policy is MemorySessionPolicy.EXACT:
            where.append(f"{alias}.session_id = ?")
            parameters.append(query.session_id)
        elif policy is MemorySessionPolicy.PROFILE:
            where.append(f"({alias}.session_id = ? OR {alias}.layer <> ?)")
            parameters.extend([query.session_id, MemoryLayer.WORKING.value])

    _append_in_filter(where, parameters, f"{alias}.scope", query.scopes)
    _append_in_filter(where, parameters, f"{alias}.memory_type", query.memory_types)
    _append_in_filter(where, parameters, f"{alias}.layer", query.layers)
    if query.tags:
        placeholders = ", ".join("?" for _ in query.tags)
        where.append(
            "EXISTS ("
            "SELECT 1 FROM memory_tags AS requested_tags "
            f"WHERE requested_tags.memory_id = {alias}.memory_id "
            f"AND requested_tags.tag IN ({placeholders})"
            ")"
        )
        parameters.extend(query.tags)
    if include_acl:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM memory_acl AS allowed_principals "
            f"WHERE allowed_principals.memory_id = {alias}.memory_id "
            "AND allowed_principals.principal_id IN ('*', ?)"
            ")"
        )
        parameters.append(query.agent_id)
    return where, parameters


def _append_in_filter(
    where: list[str],
    parameters: list[object],
    column: str,
    values: tuple[str, ...],
) -> None:
    if not values:
        return
    placeholders = ", ".join("?" for _ in values)
    where.append(f"{column} IN ({placeholders})")
    parameters.extend(values)
