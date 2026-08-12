"""Add persistent PAUSE_ENTRY emergency stop state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0032"
down_revision: str | None = "20260812_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emergency_stops",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_alias", sa.String(64), nullable=False),
        sa.Column("level", sa.String(24), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("activation_key", sa.String(128), nullable=False),
        sa.Column("release_key", sa.String(128)),
        sa.Column("activated_by", sa.String(36), nullable=False),
        sa.Column("released_by", sa.String(36)),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("level = 'PAUSE_ENTRY'", name="ck_emergency_stops_level"),
        sa.CheckConstraint(
            "state IN ('ACTIVE','RELEASED')", name="ck_emergency_stops_state"
        ),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("account_alias", name="uq_emergency_stops_account"),
    )


def downgrade() -> None:
    op.drop_table("emergency_stops")
