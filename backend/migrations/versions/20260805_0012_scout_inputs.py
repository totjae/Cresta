"""Add immutable Scout inputs and version two indicators."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0012"
down_revision: str | None = "20260805_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("indicator_snapshots", sa.Column("price_vs_vwap_pct", sa.Numeric(10, 6)))
    op.add_column("indicator_snapshots", sa.Column("sma5_slope_pct", sa.Numeric(10, 6)))
    op.add_column("indicator_snapshots", sa.Column("relative_volume_5", sa.Numeric(12, 6)))
    op.add_column(
        "indicator_snapshots", sa.Column("realized_volatility_pct", sa.Numeric(10, 6))
    )
    op.create_table(
        "decision_input_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column(
            "market_snapshot_id",
            sa.String(36),
            sa.ForeignKey("market_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "indicator_snapshot_id", sa.String(36), sa.ForeignKey("indicator_snapshots.id")
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_quality", sa.String(24), nullable=False),
        sa.Column("session_state", sa.String(32), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "purpose",
            "market_snapshot_id",
            "input_hash",
            name="uq_decision_input_snapshots_identity",
        ),
        sa.CheckConstraint(
            "purpose IN ('DIAGNOSTIC','TRADING')",
            name="ck_decision_input_snapshots_purpose",
        ),
        sa.CheckConstraint(
            "market IN ('KRX','NXT')", name="ck_decision_input_snapshots_market"
        ),
    )
    op.create_index(
        "ix_decision_input_snapshots_symbol_created",
        "decision_input_snapshots",
        ["market", "symbol", "created_at"],
    )
    with op.batch_alter_table("decisions") as batch_op:
        batch_op.add_column(sa.Column("decision_input_id", sa.String(36)))
        batch_op.create_foreign_key(
            "fk_decisions_decision_input_id",
            "decision_input_snapshots",
            ["decision_input_id"],
            ["id"],
        )
        batch_op.create_index("ix_decisions_decision_input_id", ["decision_input_id"])


def downgrade() -> None:
    with op.batch_alter_table("decisions") as batch_op:
        batch_op.drop_index("ix_decisions_decision_input_id")
        batch_op.drop_constraint("fk_decisions_decision_input_id", type_="foreignkey")
        batch_op.drop_column("decision_input_id")
    op.drop_index(
        "ix_decision_input_snapshots_symbol_created", table_name="decision_input_snapshots"
    )
    op.drop_table("decision_input_snapshots")
    op.drop_column("indicator_snapshots", "realized_volatility_pct")
    op.drop_column("indicator_snapshots", "relative_volume_5")
    op.drop_column("indicator_snapshots", "sma5_slope_pct")
    op.drop_column("indicator_snapshots", "price_vs_vwap_pct")
