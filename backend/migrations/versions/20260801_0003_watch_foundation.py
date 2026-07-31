"""Create persisted Watch snapshots and stream states."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0003"
down_revision: str | None = "20260801_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("sequence_or_hash", sa.String(128), nullable=False),
        sa.Column("source_sequence", sa.Integer()),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("last_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("open_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("high_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("low_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("cumulative_volume", sa.BigInteger(), nullable=False),
        sa.Column("best_bid_price", sa.Numeric(18, 4)),
        sa.Column("best_bid_quantity", sa.BigInteger()),
        sa.Column("best_ask_price", sa.Numeric(18, 4)),
        sa.Column("best_ask_quantity", sa.BigInteger()),
        sa.Column("trading_status", sa.String(32), nullable=False),
        sa.Column("quality", sa.String(24), nullable=False),
        sa.Column("recovery_snapshot", sa.Boolean(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_market_snapshots_market"),
        sa.CheckConstraint(
            "quality IN ('NORMAL','LATE','GAP_DETECTED')",
            name="ck_market_snapshots_quality",
        ),
        sa.UniqueConstraint(
            "source",
            "market",
            "symbol",
            "sequence_or_hash",
            name="uq_market_snapshots_source_identity",
        ),
    )
    op.create_index(
        "ix_market_snapshots_stream_event",
        "market_snapshots",
        ["market", "symbol", "event_at"],
    )
    op.create_table(
        "market_stream_states",
        sa.Column("market", sa.String(16), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "current_snapshot_id",
            sa.String(36),
            sa.ForeignKey("market_snapshots.id"),
        ),
        sa.Column("last_sequence", sa.Integer()),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("last_received_at", sa.DateTime(timezone=True)),
        sa.Column("cumulative_volume", sa.BigInteger()),
        sa.Column("quality", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_market_stream_states_market"),
        sa.CheckConstraint(
            "quality IN ('NORMAL','GAP_DETECTED')",
            name="ck_market_stream_states_quality",
        ),
    )


def downgrade() -> None:
    op.drop_table("market_stream_states")
    op.drop_index("ix_market_snapshots_stream_event", table_name="market_snapshots")
    op.drop_table("market_snapshots")
