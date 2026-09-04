from __future__ import annotations

from enum import StrEnum


class EventKind(StrEnum):
    MESSAGE = "message.created"
    OBSERVATION = "observation.created"
    TOOL_RESULT = "tool.result"
    PREFERENCE = "preference.updated"
    BELIEF = "belief.stated"
    RELATIONSHIP = "relationship.signal"
    TASK_OUTCOME = "task.outcome"
    NOTE = "note.created"


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    BELIEF = "belief"
    RELATIONSHIP = "relationship"
    STRATEGY = "strategy"


class MemoryLevel(StrEnum):
    RAW = "L0"
    ATOM = "L1"
    SCENARIO = "L2"
    PROFILE = "L3"


class MemoryVisibility(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"
    PUBLIC = "public"


class MemoryTemperature(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class MemorySessionPolicy(StrEnum):
    EXACT = "exact"
    PROFILE = "profile"
    ALL = "all"


class MemoryOperation(StrEnum):
    CREATE = "create"
    MERGE = "merge"
    SUPERSEDE = "supersede"
    IGNORE = "ignore"
    DELETE = "delete"


class MemoryLabel(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"
    CONFLICTED = "conflicted"
