from __future__ import annotations

from enum import StrEnum


class AuditDecision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
    REVIEW = "review"
    QUARANTINE = "quarantine"
    OBSERVE = "observe"
