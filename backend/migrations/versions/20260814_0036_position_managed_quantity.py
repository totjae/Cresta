"""Add broker availability and Cresta-managed position quantities."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0036"
down_revision: str | None = "20260813_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("positions") as batch:
        batch.drop_constraint("ck_positions_origin", type_="check")
        batch.add_column(
            sa.Column("available_quantity", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("managed_quantity", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "managed_average_price",
                sa.Numeric(18, 4),
                nullable=False,
                server_default="0",
            )
        )

    op.execute("UPDATE positions SET available_quantity = quantity")
    op.execute(
        "UPDATE positions SET managed_quantity = quantity, "
        "managed_average_price = average_price WHERE origin = 'CRESTA_MANAGED'"
    )

    with op.batch_alter_table("positions") as batch:
        batch.create_check_constraint(
            "ck_positions_available_quantity_range",
            "available_quantity >= 0 AND available_quantity <= quantity",
        )
        batch.create_check_constraint(
            "ck_positions_managed_quantity_range",
            "managed_quantity >= 0 AND managed_quantity <= quantity",
        )
        batch.create_check_constraint(
            "ck_positions_managed_average_nonnegative",
            "managed_average_price >= 0",
        )
        batch.create_check_constraint(
            "ck_positions_origin",
            "origin IN ('CRESTA_MANAGED','EXTERNAL','MIXED')",
        )
        batch.create_check_constraint(
            "ck_positions_open_origin_matches_managed_quantity",
            "state != 'OPEN' OR "
            "(origin = 'EXTERNAL' AND managed_quantity = 0) OR "
            "(origin = 'CRESTA_MANAGED' AND managed_quantity = quantity AND quantity > 0) OR "
            "(origin = 'MIXED' AND managed_quantity > 0 AND managed_quantity < quantity)",
        )


def downgrade() -> None:
    # The legacy schema cannot represent mixed ownership. Preserve safety by
    # classifying every mixed position as EXTERNAL before removing quantities.
    with op.batch_alter_table("positions") as batch:
        batch.drop_constraint("ck_positions_open_origin_matches_managed_quantity", type_="check")
        batch.drop_constraint("ck_positions_origin", type_="check")

    op.execute("UPDATE positions SET origin = 'EXTERNAL' WHERE origin = 'MIXED'")

    with op.batch_alter_table("positions") as batch:
        batch.drop_constraint("ck_positions_managed_average_nonnegative", type_="check")
        batch.drop_constraint("ck_positions_managed_quantity_range", type_="check")
        batch.drop_constraint("ck_positions_available_quantity_range", type_="check")
        batch.drop_column("managed_average_price")
        batch.drop_column("managed_quantity")
        batch.drop_column("available_quantity")
        batch.create_check_constraint(
            "ck_positions_origin",
            "origin IN ('CRESTA_MANAGED','EXTERNAL')",
        )
