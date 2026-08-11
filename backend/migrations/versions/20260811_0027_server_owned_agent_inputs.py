"""Add server-owned Agent position and market-context provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0027"
down_revision: str | None = "20260811_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_context_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("source_tier", sa.String(16), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('KRX','NXT')", name="ck_market_context_market"),
        sa.CheckConstraint(
            "source_tier IN ('PRIMARY','CONTRACTED')",
            name="ck_market_context_source_tier",
        ),
        sa.CheckConstraint(
            "quality IN ('NORMAL','INCOMPLETE')", name="ck_market_context_quality"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "market",
            "symbol",
            "source_ref",
            name="uq_market_context_source_identity",
        ),
    )
    op.create_index(
        "ix_market_context_payload_hash",
        "market_context_snapshots",
        ["payload_hash"],
    )
    op.create_index(
        "ix_market_context_selection",
        "market_context_snapshots",
        ["market", "symbol", "quality", "observed_at"],
    )
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(
            sa.Column("server_input_policy_version", sa.String(32), nullable=True)
        )
        batch.add_column(
            sa.Column("market_context_snapshot_id", sa.String(36), nullable=True)
        )
        batch.add_column(
            sa.Column("market_context_snapshot_hash", sa.String(64), nullable=True)
        )
        batch.create_foreign_key(
            "fk_agent_runs_market_context_snapshot",
            "market_context_snapshots",
            ["market_context_snapshot_id"],
            ["id"],
        )
        batch.create_index(
            "ix_agent_runs_market_context_snapshot_id",
            ["market_context_snapshot_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_market_context_snapshot_id")
        batch.drop_constraint(
            "fk_agent_runs_market_context_snapshot", type_="foreignkey"
        )
        batch.drop_column("market_context_snapshot_hash")
        batch.drop_column("market_context_snapshot_id")
        batch.drop_column("server_input_policy_version")
    op.drop_index("ix_market_context_selection", table_name="market_context_snapshots")
    op.drop_index("ix_market_context_payload_hash", table_name="market_context_snapshots")
    op.drop_table("market_context_snapshots")
