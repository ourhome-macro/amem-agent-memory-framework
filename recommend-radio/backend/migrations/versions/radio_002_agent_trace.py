"""Link durable business jobs to agent runs and W3C trace context."""

import sqlalchemy as sa
from alembic import op

revision = "radio_002"
down_revision = "radio_001"


def upgrade():
    op.add_column(
        "durable_jobs", sa.Column("trace_context", sa.Text(), nullable=False, server_default="{}")
    )
    op.add_column("durable_jobs", sa.Column("agent_run_id", sa.Text(), nullable=True))
    op.create_index("idx_durable_jobs_agent_run", "durable_jobs", ["agent_run_id"])
    op.execute("PRAGMA user_version = 26")


def downgrade():
    with op.batch_alter_table("durable_jobs") as batch:
        batch.drop_index("idx_durable_jobs_agent_run")
        batch.drop_column("agent_run_id")
        batch.drop_column("trace_context")
    op.execute("PRAGMA user_version = 25")
