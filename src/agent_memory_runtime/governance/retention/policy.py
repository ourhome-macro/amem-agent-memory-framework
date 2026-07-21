from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionPolicy:
    archive_working_after_sequences: int = 30
    archive_below_salience: float | None = None
    delete_sensitive_after_sequences: int | None = None

    def __post_init__(self) -> None:
        if self.archive_working_after_sequences < 0:
            raise ValueError("archive_working_after_sequences cannot be negative")
        if (
            self.archive_below_salience is not None
            and not 0 <= self.archive_below_salience <= 1
        ):
            raise ValueError("archive_below_salience must be between 0 and 1")
        if (
            self.delete_sensitive_after_sequences is not None
            and self.delete_sensitive_after_sequences < 0
        ):
            raise ValueError("delete_sensitive_after_sequences cannot be negative")


@dataclass(frozen=True)
class RetentionAction:
    memory_id: str
    action: str
    reason: str


@dataclass(frozen=True)
class RetentionPlan:
    actions: tuple[RetentionAction, ...]
    current_sequence: int

    def __post_init__(self) -> None:
        if self.current_sequence < 0:
            raise ValueError("retention current_sequence cannot be negative")
        memory_ids = [action.memory_id for action in self.actions]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("retention plan cannot contain duplicate memory IDs")
        unsupported = sorted(
            {action.action for action in self.actions if action.action not in {"archive", "delete"}}
        )
        if unsupported:
            raise ValueError(f"unsupported retention actions: {', '.join(unsupported)}")


@dataclass(frozen=True)
class RetentionReport:
    archived_memory_ids: tuple[str, ...]
    deleted_memory_ids: tuple[str, ...]
