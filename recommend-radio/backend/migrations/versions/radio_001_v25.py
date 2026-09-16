"""Adopt existing v25 databases; replay frozen historical migrations for older databases."""

from pathlib import Path

from alembic import op

revision = "radio_001"
down_revision = None


def upgrade():
    from legacy_schema_v25 import upgrade_legacy_v25
    from migrations.legacy_repair import repair_session_references

    path = Path(op.get_context().config.attributes["db_path"])
    repair_session_references(path)
    upgrade_legacy_v25(path)
    for table in ("tracks", "app_users", "durable_jobs", "evaluation_traces"):
        if (
            not op.get_bind()
            .exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
            .fetchone()
        ):
            raise RuntimeError(f"Invalid legacy database: missing {table}")


def downgrade():
    raise RuntimeError("Baseline downgrade would destroy user data; restore a verified backup")
