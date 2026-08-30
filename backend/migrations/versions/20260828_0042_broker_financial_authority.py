"""Add append-only Kiwoom financial authority snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0042"
down_revision: str | None = "20260828_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_funds_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("broker", sa.String(24), nullable=False),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("source_api_id", sa.String(10), nullable=False),
        sa.Column("query_type", sa.String(1), nullable=False),
        sa.Column("deposit", sa.BigInteger()),
        sa.Column("generic_orderable_amount", sa.BigInteger()),
        sa.Column("withdrawable_amount", sa.BigInteger()),
        sa.Column("d1_estimated_deposit", sa.BigInteger()),
        sa.Column("d1_buy_settlement_amount", sa.BigInteger()),
        sa.Column("d1_sell_settlement_amount", sa.BigInteger()),
        sa.Column("d1_withdrawable_amount", sa.BigInteger()),
        sa.Column("d2_estimated_deposit", sa.BigInteger()),
        sa.Column("d2_buy_settlement_amount", sa.BigInteger()),
        sa.Column("d2_sell_settlement_amount", sa.BigInteger()),
        sa.Column("d2_withdrawable_amount", sa.BigInteger()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("broker = 'KIWOOM'", name="ck_account_funds_broker"),
        sa.CheckConstraint("environment = 'MOCK'", name="ck_account_funds_environment"),
        sa.CheckConstraint("source_api_id = 'kt00001'", name="ck_account_funds_source"),
        sa.CheckConstraint("query_type IN ('2','3')", name="ck_account_funds_query_type"),
    )
    op.create_index(
        "ix_account_funds_authority_latest",
        "account_funds_snapshots",
        ["broker", "account_alias", "environment", "received_at"],
    )

    capacity_columns = [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("broker", sa.String(24), nullable=False),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("source_api_id", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("trade_type", sa.String(1), nullable=False),
        sa.Column("requested_price", sa.BigInteger(), nullable=False),
        sa.Column("io_amount", sa.BigInteger()),
        sa.Column("requested_quantity", sa.BigInteger()),
        sa.Column("expected_buy_price", sa.BigInteger()),
    ]
    for name in (
        "orderable_cash",
        "deposit",
        "withdrawable_amount",
        "next_day_withdrawable_amount",
        "d2_estimated_deposit",
        "margin_20_orderable_amount",
        "margin_20_orderable_quantity",
        "margin_30_orderable_amount",
        "margin_30_orderable_quantity",
        "margin_40_orderable_amount",
        "margin_40_orderable_quantity",
        "margin_50_orderable_amount",
        "margin_50_orderable_quantity",
        "margin_60_orderable_amount",
        "margin_60_orderable_quantity",
        "reduced_margin_60_orderable_amount",
        "reduced_margin_60_orderable_quantity",
        "margin_100_orderable_amount",
        "margin_100_orderable_quantity",
    ):
        capacity_columns.append(sa.Column(name, sa.BigInteger()))
    capacity_columns.extend(
        [
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("broker = 'KIWOOM'", name="ck_order_capacity_broker"),
            sa.CheckConstraint(
                "environment = 'MOCK'", name="ck_order_capacity_environment"
            ),
            sa.CheckConstraint(
                "source_api_id = 'kt00010'", name="ck_order_capacity_source"
            ),
            sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_order_capacity_side"),
            sa.CheckConstraint(
                "trade_type IN ('1','2')", name="ck_order_capacity_trade_type"
            ),
            sa.CheckConstraint("requested_price > 0", name="ck_order_capacity_price"),
            sa.CheckConstraint(
                "requested_quantity IS NULL OR requested_quantity > 0",
                name="ck_order_capacity_requested_quantity",
            ),
            sa.CheckConstraint(
                "expected_buy_price IS NULL OR expected_buy_price > 0",
                name="ck_order_capacity_expected_buy_price",
            ),
            sa.CheckConstraint(
                "(side = 'BUY' AND trade_type = '2') OR "
                "(side = 'SELL' AND trade_type = '1')",
                name="ck_order_capacity_side_trade_type",
            ),
            sa.CheckConstraint(
                "(margin_20_orderable_quantity IS NULL OR margin_20_orderable_quantity >= 0) AND "
                "(margin_30_orderable_quantity IS NULL OR margin_30_orderable_quantity >= 0) AND "
                "(margin_40_orderable_quantity IS NULL OR margin_40_orderable_quantity >= 0) AND "
                "(margin_50_orderable_quantity IS NULL OR margin_50_orderable_quantity >= 0) AND "
                "(margin_60_orderable_quantity IS NULL OR margin_60_orderable_quantity >= 0) AND "
                "(reduced_margin_60_orderable_quantity IS NULL OR "
                "reduced_margin_60_orderable_quantity >= 0) AND "
                "(margin_100_orderable_quantity IS NULL OR "
                "margin_100_orderable_quantity >= 0)",
                name="ck_order_capacity_quantities_nonnegative",
            ),
        ]
    )
    op.create_table("order_capacity_snapshots", *capacity_columns)
    op.create_index(
        "ix_order_capacity_authority_latest",
        "order_capacity_snapshots",
        [
            "broker",
            "account_alias",
            "environment",
            "symbol",
            "side",
            "requested_price",
            "received_at",
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM account_funds_snapshots")) or bind.scalar(
        sa.text("SELECT COUNT(*) FROM order_capacity_snapshots")
    ):
        raise RuntimeError(
            "Refusing downgrade of 20260828_0042: financial authority evidence exists"
        )
    op.drop_index(
        "ix_order_capacity_authority_latest", table_name="order_capacity_snapshots"
    )
    op.drop_table("order_capacity_snapshots")
    op.drop_index(
        "ix_account_funds_authority_latest", table_name="account_funds_snapshots"
    )
    op.drop_table("account_funds_snapshots")
