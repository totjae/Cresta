"""Add persisted unfilled-order action policy and schedule."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0037"
down_revision: str | None = "20260814_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.add_column(
            sa.Column("unfilled_policy", sa.String(length=16), nullable=False, server_default="NONE")
        )
        batch.add_column(
            sa.Column("fill_timeout_seconds", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("max_reprice_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("reprice_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("next_action_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_orders_unfilled_policy", "unfilled_policy IN ('NONE','CANCEL')"
        )
        batch.create_check_constraint(
            "ck_orders_fill_timeout_nonnegative", "fill_timeout_seconds >= 0"
        )
        batch.create_check_constraint(
            "ck_orders_reprice_attempts_range",
            "reprice_attempts >= 0 AND max_reprice_attempts >= 0 "
            "AND reprice_attempts <= max_reprice_attempts",
        )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("ck_orders_reprice_attempts_range", type_="check")
        batch.drop_constraint("ck_orders_fill_timeout_nonnegative", type_="check")
        batch.drop_constraint("ck_orders_unfilled_policy", type_="check")
        batch.drop_column("next_action_at")
        batch.drop_column("reprice_attempts")
        batch.drop_column("max_reprice_attempts")
        batch.drop_column("fill_timeout_seconds")
        batch.drop_column("unfilled_policy")
