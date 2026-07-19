from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditSubject:
    subject_type: str
    subject_id: str
    content_hash: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> AuditSubject:
        return cls(
            subject_type=str(value["subject_type"]),
            subject_id=str(value["subject_id"]),
            content_hash=_optional_str(value.get("content_hash")),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "content_hash": self.content_hash,
        }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
