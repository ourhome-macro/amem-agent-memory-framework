from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from database import get_connection


class ExperimentAssignments:
    """Stable user-level assignments with explicit opt-in feature flags."""

    def __init__(self, db_path: str, *, user_id: str) -> None:
        self.db_path = db_path
        self.user_id = user_id

    def memory_variant(self) -> str:
        return self.assign(
            "memory_context_v1",
            enabled=_env_bool("RECOMMEND_MEMORY_AB_ENABLED", False),
            treatment_percent=_env_int("RECOMMEND_MEMORY_AB_TREATMENT_PERCENT", 50),
        )

    def keyword_governance_variant(self) -> str:
        return self.assign(
            "keyword_governance_v1",
            enabled=_env_bool("RECOMMEND_KEYWORD_AB_ENABLED", False),
            treatment_percent=_env_int("RECOMMEND_KEYWORD_AB_TREATMENT_PERCENT", 50),
        )

    def assign(self, name: str, *, enabled: bool, treatment_percent: int) -> str:
        if not enabled:
            return "disabled"
        with get_connection(self.db_path) as conn:
            current = conn.execute(
                """
                SELECT variant FROM recommendation_experiment_assignments
                WHERE user_id=? AND experiment_name=?
                """,
                (self.user_id, name),
            ).fetchone()
            if current is not None:
                return str(current["variant"])
            bucket = (
                int.from_bytes(
                    hashlib.sha256(f"{name}:{self.user_id}".encode("utf-8")).digest()[:8],
                    "big",
                )
                % 100
            )
            variant = "treatment" if bucket < min(max(treatment_percent, 0), 100) else "control"
            conn.execute(
                """
                INSERT INTO recommendation_experiment_assignments (
                    user_id, experiment_name, variant, assigned_at
                ) VALUES (?, ?, ?, ?)
                """,
                (self.user_id, name, variant, _utc_now()),
            )
        return variant


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
