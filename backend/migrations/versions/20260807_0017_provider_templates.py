"""Add provider template identity and soft deletion."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0017"
down_revision: str | None = "20260806_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_provider_profiles") as batch:
        batch.add_column(sa.Column("provider_template_id", sa.String(64)))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
        batch.drop_constraint("uq_llm_provider_profiles_owner_name", type_="unique")
        batch.create_index("ix_llm_provider_profiles_provider_template_id", ["provider_template_id"])
        batch.create_index("ix_llm_provider_profiles_deleted_at", ["deleted_at"])
    op.execute(
        "UPDATE llm_provider_profiles SET provider_template_id = CASE "
        "WHEN adapter_type = 'OPENAI_RESPONSES' THEN 'openai' "
        "WHEN adapter_type = 'ANTHROPIC_MESSAGES' THEN 'anthropic' "
        "WHEN adapter_type = 'GEMINI_GENERATE_CONTENT' THEN 'google' END"
    )
    op.create_index(
        "uq_llm_provider_profiles_owner_name_active",
        "llm_provider_profiles",
        ["owner_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_llm_provider_profiles_owner_name_active",
        table_name="llm_provider_profiles",
    )
    with op.batch_alter_table("llm_provider_profiles") as batch:
        batch.drop_index("ix_llm_provider_profiles_deleted_at")
        batch.drop_index("ix_llm_provider_profiles_provider_template_id")
        batch.create_unique_constraint(
            "uq_llm_provider_profiles_owner_name", ["owner_id", "name"]
        )
        batch.drop_column("deleted_at")
        batch.drop_column("provider_template_id")
