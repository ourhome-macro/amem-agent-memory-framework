from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    agent_id: str
    roles: tuple[str, ...] = ("agent",)
    allowed_labels: tuple[str, ...] = ("public", "private")

    @property
    def is_auditor(self) -> bool:
        return "auditor" in set(self.roles)

