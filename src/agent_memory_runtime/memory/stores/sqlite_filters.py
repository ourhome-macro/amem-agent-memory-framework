from __future__ import annotations

from agent_memory_runtime.domain.enums import MemoryLevel, MemorySessionPolicy, MemoryStatus
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

    statuses = query.statuses or (MemoryStatus.ACTIVE.value,)
    _append_in_filter(where, parameters, f"{alias}.status", statuses)

    if query.session_id is not None:
        policy = MemorySessionPolicy(query.session_policy)
        if policy is MemorySessionPolicy.EXACT:
            where.append(f"{alias}.session_id = ?")
            parameters.append(query.session_id)
        elif policy is MemorySessionPolicy.PROFILE:
            where.append(f"({alias}.session_id = ? OR {alias}.level = ?)")
            parameters.extend([query.session_id, MemoryLevel.PROFILE.value])

    _append_in_filter(where, parameters, f"{alias}.memory_type", query.memory_types)
    _append_in_filter(where, parameters, f"{alias}.level", query.levels)
    _append_in_filter(where, parameters, f"{alias}.visibility", query.visibilities)
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
