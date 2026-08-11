"""Add persistent instrument venue eligibility state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0029"
down_revision: str | None = "20260812_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instrument_venue_states",
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("venue", sa.String(8), nullable=False),
        sa.Column("eligibility_status", sa.String(16), nullable=False),
        sa.Column("evidence_source", sa.String(32), nullable=False),
        sa.Column("evidence_ref", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("venue IN ('KRX','NXT','SOR')", name="ck_instrument_venue_state_venue"),
        sa.CheckConstraint(
            "eligibility_status IN ('VERIFIED','INELIGIBLE','UNKNOWN')",
            name="ck_instrument_venue_state_eligibility",
        ),
        sa.PrimaryKeyConstraint("symbol", "venue"),
    )
    op.create_index(
        "ix_instrument_venue_state_status",
        "instrument_venue_states",
        ["venue", "eligibility_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_instrument_venue_state_status", table_name="instrument_venue_states")
    op.drop_table("instrument_venue_states")
