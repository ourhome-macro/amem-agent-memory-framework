from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    agent_id: str
    roles: tuple[str, ...] = ("agent",)
    allowed_labels: tuple[str, ...] = ("public", "private")
    # Appended to preserve the pre-v0.2 positional constructor contract.
    tenant_id: str = "default"
    user_id: str | None = None

    @property
    def is_auditor(self) -> bool:
        return "auditor" in set(self.roles)
