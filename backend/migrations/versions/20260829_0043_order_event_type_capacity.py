"""Expand OrderEvent event_type capacity for normative authority events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0043"
down_revision: str | None = "20260828_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("order_events") as batch:
        batch.alter_column(
            "event_type",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    oversized = bind.scalar(
        sa.text("SELECT COUNT(*) FROM order_events WHERE length(event_type) > 32")
    )
    if oversized:
        raise RuntimeError(
            "Refusing downgrade of 20260829_0043: order event types exceed 32 characters"
        )
    with op.batch_alter_table("order_events") as batch:
        batch.alter_column(
            "event_type",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
