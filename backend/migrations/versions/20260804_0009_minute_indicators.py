"""Create one-minute bars and indicator snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0009"
down_revision: str | None = "20260804_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "minute_bars",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("high_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("low_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("close_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("turnover", sa.Numeric(28, 4), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("first_snapshot_id", sa.String(36), sa.ForeignKey("market_snapshots.id"), nullable=False),
        sa.Column("last_snapshot_id", sa.String(36), sa.ForeignKey("market_snapshots.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_minute_bars_market"),
        sa.UniqueConstraint("market", "symbol", "bucket_start", name="uq_minute_bars_stream_bucket"),
    )
    op.create_index("ix_minute_bars_stream_bucket", "minute_bars", ["market", "symbol", "bucket_start"])

    op.create_table(
        "indicator_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("market_snapshot_id", sa.String(36), sa.ForeignKey("market_snapshots.id"), nullable=False, unique=True),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("calculator_version", sa.String(32), nullable=False),
        sa.Column("vwap", sa.Numeric(18, 4), nullable=False),
        sa.Column("sma5", sa.Numeric(18, 4)),
        sa.Column("session_high", sa.Numeric(18, 4), nullable=False),
        sa.Column("drawdown_from_high_pct", sa.Numeric(10, 6), nullable=False),
        sa.Column("spread_pct", sa.Numeric(10, 6)),
        sa.Column("minute_bar_count", sa.Integer(), nullable=False),
        sa.Column("input_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_indicator_snapshots_market"),
    )
    op.create_index("ix_indicator_snapshots_market_snapshot_id", "indicator_snapshots", ["market_snapshot_id"])
    op.create_index("ix_indicator_snapshots_stream_created", "indicator_snapshots", ["market", "symbol", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_indicator_snapshots_stream_created", table_name="indicator_snapshots")
    op.drop_index("ix_indicator_snapshots_market_snapshot_id", table_name="indicator_snapshots")
    op.drop_table("indicator_snapshots")
    op.drop_index("ix_minute_bars_stream_bucket", table_name="minute_bars")
    op.drop_table("minute_bars")
