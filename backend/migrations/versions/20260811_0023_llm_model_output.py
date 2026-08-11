"""Store bounded structured LLM model output for prompt diagnostics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0023"
down_revision: str | None = "20260811_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_invocations") as batch:
        batch.add_column(sa.Column("model_output_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("model_output_hash", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("model_output_captured_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_invocations") as batch:
        batch.drop_column("model_output_captured_at")
        batch.drop_column("model_output_hash")
        batch.drop_column("model_output_json")
