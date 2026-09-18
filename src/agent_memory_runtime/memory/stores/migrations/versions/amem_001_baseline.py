"""Adopt the immutable v1-v10 memory schema and verify historical checksums."""

from alembic import op

revision = "amem_001"
down_revision = None


def upgrade():
    op.get_context().config.attributes["manager"]._migrate_legacy()


def downgrade():
    raise RuntimeError("Baseline contains user memory; restore a verified backup instead")
