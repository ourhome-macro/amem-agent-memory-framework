from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionPolicy:
    archive_working_after_sequences: int = 30
    archive_below_salience: float | None = None
    delete_sensitive_after_sequences: int | None = None


@dataclass(frozen=True)
class RetentionAction:
    memory_id: str
    action: str
    reason: str


@dataclass(frozen=True)
class RetentionPlan:
    actions: tuple[RetentionAction, ...]
    current_sequence: int


@dataclass(frozen=True)
class RetentionReport:
    archived_memory_ids: tuple[str, ...]
    deleted_memory_ids: tuple[str, ...]
