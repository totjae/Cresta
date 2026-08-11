"""Use provider defaults for sampling and safer route timeouts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0025"
down_revision: str | None = "20260811_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_model_profiles") as batch:
        batch.alter_column(
            "temperature",
            existing_type=sa.Numeric(4, 3),
            existing_nullable=False,
            nullable=True,
            server_default=None,
        )
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.alter_column(
            "timeout_ms",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("120000"),
        )


def downgrade() -> None:
    op.execute("UPDATE llm_model_profiles SET temperature = 0 WHERE temperature IS NULL")
    with op.batch_alter_table("llm_model_profiles") as batch:
        batch.alter_column(
            "temperature",
            existing_type=sa.Numeric(4, 3),
            existing_nullable=True,
            nullable=False,
            server_default=sa.text("0"),
        )
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.alter_column(
            "timeout_ms",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("30000"),
        )
