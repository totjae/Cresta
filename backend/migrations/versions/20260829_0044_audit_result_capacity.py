"""Expand AuditLog result capacity for server-owned exact results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0044"
down_revision: str | None = "20260829_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "result",
            existing_type=sa.String(length=24),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    oversized = bind.scalar(
        sa.text("SELECT COUNT(*) FROM audit_logs WHERE length(result) > 24")
    )
    if oversized:
        raise RuntimeError(
            "Refusing downgrade of 20260829_0044: audit results exceed 24 characters"
        )
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "result",
            existing_type=sa.String(length=64),
            type_=sa.String(length=24),
            existing_nullable=False,
        )
