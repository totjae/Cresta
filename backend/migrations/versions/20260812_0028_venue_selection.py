"""Add SHADOW venue selection evaluations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0028"
down_revision: str | None = "20260811_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "venue_selection_evaluations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("urgency", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("execution_stage", sa.String(16), nullable=False),
        sa.Column("session", sa.String(32), nullable=False),
        sa.Column("nxt_eligible", sa.Boolean(), nullable=False),
        sa.Column("nxt_eligibility_status", sa.String(16), nullable=False),
        sa.Column("sor_supported", sa.Boolean(), nullable=False),
        sa.Column("selected_venue", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("order_creation_allowed", sa.Boolean(), nullable=False),
        sa.Column("krx_snapshot_id", sa.String(36), nullable=True),
        sa.Column("nxt_snapshot_id", sa.String(36), nullable=True),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_venue_selection_side"),
        sa.CheckConstraint(
            "order_type IN ('LIMIT','MARKET')", name="ck_venue_selection_order_type"
        ),
        sa.CheckConstraint(
            "urgency IN ('NORMAL','EMERGENCY')", name="ck_venue_selection_urgency"
        ),
        sa.CheckConstraint(
            "selected_venue IN ('KRX','NXT','SOR','WAIT')",
            name="ck_venue_selection_selected_venue",
        ),
        sa.CheckConstraint(
            "state IN ('SELECTED','WAIT')", name="ck_venue_selection_state"
        ),
        sa.CheckConstraint(
            "execution_stage = 'SHADOW'", name="ck_venue_selection_shadow_only"
        ),
        sa.CheckConstraint(
            "NOT order_creation_allowed", name="ck_venue_selection_no_order_creation"
        ),
        sa.CheckConstraint(
            "nxt_eligibility_status IN ('VERIFIED','INELIGIBLE','UNKNOWN')",
            name="ck_venue_selection_nxt_eligibility",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["krx_snapshot_id"], ["market_snapshots.id"]),
        sa.ForeignKeyConstraint(["nxt_snapshot_id"], ["market_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_venue_selection_evaluations_owner_id",
        "venue_selection_evaluations",
        ["owner_id"],
    )
    op.create_index(
        "ix_venue_selection_input_hash",
        "venue_selection_evaluations",
        ["input_hash"],
    )
    op.create_index(
        "ix_venue_selection_symbol_created",
        "venue_selection_evaluations",
        ["symbol", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_venue_selection_symbol_created",
        table_name="venue_selection_evaluations",
    )
    op.drop_index(
        "ix_venue_selection_input_hash", table_name="venue_selection_evaluations"
    )
    op.drop_index(
        "ix_venue_selection_evaluations_owner_id",
        table_name="venue_selection_evaluations",
    )
    op.drop_table("venue_selection_evaluations")
