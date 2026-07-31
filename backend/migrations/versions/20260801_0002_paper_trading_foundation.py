"""Create Paper Broker order, fill, position, and gate tables."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0002"
down_revision: str | None = "20260731_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_gates",
        sa.Column("account_alias", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(256)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('STARTING','RECONCILING','READY','DEGRADED','HALTED')",
            name="ck_trading_gates_status",
        ),
    )
    op.bulk_insert(
        sa.table(
            "trading_gates",
            sa.column("account_alias", sa.String),
            sa.column("environment", sa.String),
            sa.column("status", sa.String),
            sa.column("reason", sa.String),
            sa.column("version", sa.Integer),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "account_alias": "PAPER",
                "environment": "MOCK",
                "status": "STARTING",
                "reason": "INITIAL_RECONCILIATION_REQUIRED",
                "version": 1,
                "updated_at": datetime.now(UTC),
            }
        ],
    )
    op.create_table(
        "order_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_group_id", sa.String(36), nullable=False, unique=True),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.String(36)),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("requested_quantity > 0", name="ck_order_intents_requested_positive"),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_order_intents_side"),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("average_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_positions_quantity_nonnegative"),
        sa.CheckConstraint("state IN ('OPEN','CLOSED')", name="ck_positions_state"),
        sa.UniqueConstraint("account_alias", "symbol", name="uq_positions_account_symbol"),
    )
    op.create_index("ix_positions_account_symbol", "positions", ["account_alias", "symbol"])
    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("intent_id", sa.String(36), sa.ForeignKey("order_intents.id"), nullable=False),
        sa.Column("order_group_id", sa.String(36), nullable=False),
        sa.Column("parent_order_id", sa.String(36), sa.ForeignKey("orders.id")),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(12), nullable=False),
        sa.Column("limit_price", sa.Numeric(18, 4)),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False),
        sa.Column("cancelled_quantity", sa.Integer(), nullable=False),
        sa.Column("remaining_quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("broker_order_id", sa.String(64)),
        sa.Column("replacement_sequence", sa.Integer(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("requested_quantity > 0", name="ck_orders_requested_positive"),
        sa.CheckConstraint("filled_quantity >= 0", name="ck_orders_filled_nonnegative"),
        sa.CheckConstraint("cancelled_quantity >= 0", name="ck_orders_cancelled_nonnegative"),
        sa.CheckConstraint("remaining_quantity >= 0", name="ck_orders_remaining_nonnegative"),
        sa.CheckConstraint(
            "requested_quantity = filled_quantity + cancelled_quantity + remaining_quantity",
            name="ck_orders_quantity_invariant",
        ),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_orders_side"),
        sa.CheckConstraint("order_type IN ('LIMIT','MARKET')", name="ck_orders_type"),
        sa.CheckConstraint(
            "status IN ('CREATED','VALIDATING','SUBMITTING','ACKNOWLEDGED','OPEN',"
            "'PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELLED','REPLACE_PENDING',"
            "'REPLACED','REJECTED','UNKNOWN','RECONCILING')",
            name="ck_orders_status",
        ),
        sa.UniqueConstraint(
            "environment",
            "account_alias",
            "broker_order_id",
            name="uq_orders_broker_identity",
        ),
    )
    op.create_index("ix_orders_intent_id", "orders", ["intent_id"])
    op.create_index("ix_orders_account_broker", "orders", ["account_alias", "broker_order_id"])
    op.create_index("ix_orders_symbol_status", "orders", ["symbol", "status"])
    op.create_index("ix_orders_group_created", "orders", ["order_group_id", "created_at"])
    op.create_table(
        "order_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_key", name="uq_order_events_source_key"),
    )
    op.create_index("ix_order_events_order_occurred", "order_events", ["order_id", "occurred_at"])
    op.create_table(
        "fills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("broker_fill_key", sa.String(128), nullable=False, unique=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("fee", sa.Numeric(18, 4), nullable=False),
        sa.Column("tax", sa.Numeric(18, 4), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_fills_quantity_positive"),
        sa.CheckConstraint("price > 0", name="ck_fills_price_positive"),
        sa.CheckConstraint("fee >= 0", name="ck_fills_fee_nonnegative"),
        sa.CheckConstraint("tax >= 0", name="ck_fills_tax_nonnegative"),
    )
    op.create_index("ix_fills_order_filled", "fills", ["order_id", "filled_at"])
    op.create_table(
        "position_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("position_id", sa.String(36), sa.ForeignKey("positions.id"), nullable=False),
        sa.Column("cause_type", sa.String(32), nullable=False),
        sa.Column("cause_id", sa.String(36), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_position_events_position_created",
        "position_events",
        ["position_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("position_events")
    op.drop_table("fills")
    op.drop_table("order_events")
    op.drop_table("orders")
    op.drop_table("positions")
    op.drop_table("order_intents")
    op.drop_table("trading_gates")
