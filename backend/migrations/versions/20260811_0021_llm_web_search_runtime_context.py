"""Add role web search policy and invocation runtime context."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0021"
down_revision: str | None = "20260810_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.add_column(
            sa.Column(
                "web_search_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("llm_invocations") as batch:
        batch.add_column(sa.Column("runtime_context_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "web_search_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_invocations") as batch:
        batch.drop_column("web_search_enabled")
        batch.drop_column("runtime_context_at")
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_column("web_search_enabled")
