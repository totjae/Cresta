"""Raise the default structured LLM output token budget."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0024"
down_revision: str | None = "20260811_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_model_profiles") as batch:
        batch.alter_column(
            "max_output_tokens",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("8192"),
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_model_profiles") as batch:
        batch.alter_column(
            "max_output_tokens",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("1024"),
        )
