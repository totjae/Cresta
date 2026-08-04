"""Create persistent watchlist items."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0008"
down_revision: str | None = "20260804_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX')", name="ck_watchlist_items_market"),
        sa.UniqueConstraint("user_id", "market", "symbol", name="uq_watchlist_items_user_market_symbol"),
    )
    op.create_index("ix_watchlist_items_user_id", "watchlist_items", ["user_id"])
    op.create_index("ix_watchlist_items_market_symbol", "watchlist_items", ["market", "symbol"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_items_market_symbol", table_name="watchlist_items")
    op.drop_index("ix_watchlist_items_user_id", table_name="watchlist_items")
    op.drop_table("watchlist_items")
