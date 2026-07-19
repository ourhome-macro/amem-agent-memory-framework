from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PiiToken:
    token_id: str
    pii_type: str
    field_path: str

    @property
    def placeholder(self) -> str:
        return "${" + self.token_id + "}"
