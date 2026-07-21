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


class MemoryScope(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"
    GLOBAL = "global"


class MemoryLayer(StrEnum):
    CORE = "core"
    WORKING = "working"
    ARCHIVAL = "archival"


class MemorySessionPolicy(StrEnum):
    EXACT = "exact"
    PROFILE = "profile"
    ALL = "all"


class MemoryOperation(StrEnum):
    CREATE = "create"
    REINFORCE = "reinforce"
    REVISE = "revise"
    SUPERSEDE = "supersede"
    ARCHIVE = "archive"


class MemoryLabel(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    CONFLICTED = "conflicted"
