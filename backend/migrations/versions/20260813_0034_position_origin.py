"""Add position origin provenance (CRESTA_MANAGED/EXTERNAL)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0034"
down_revision: str | None = "20260812_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing positions are all Cresta-managed (reconciliation does not import
    # external positions). New positions default to CRESTA_MANAGED; external
    # positions detected by reconciliation are tagged EXTERNAL so automatic
    # sell triggers only act on Cresta-managed positions.
    with op.batch_alter_table("positions") as batch:
        batch.add_column(sa.Column("origin", sa.String(24), nullable=True))
    op.execute("UPDATE positions SET origin = 'CRESTA_MANAGED'")
    with op.batch_alter_table("positions") as batch:
        batch.alter_column("origin", existing_type=sa.String(24), nullable=False)
        batch.create_check_constraint(
            "ck_positions_origin",
            "origin IN ('CRESTA_MANAGED','EXTERNAL')",
        )

    # Track the order created on approval and the terminal reason code so the
    # approval card can show why an approval expired or was invalidated.
    with op.batch_alter_table("approvals") as batch:
        batch.add_column(sa.Column("order_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("result_code", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch:
        batch.drop_column("result_code")
        batch.drop_column("order_id")
    with op.batch_alter_table("positions") as batch:
        batch.drop_constraint("ck_positions_origin", type_="check")
        batch.drop_column("origin")
