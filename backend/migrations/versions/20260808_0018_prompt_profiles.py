"""Add immutable role prompt profiles."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0018"
down_revision: str | None = "20260807_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_prompt_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(64), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('TECHNICAL_SCOUT','NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT','CORE')",
            name="ck_llm_prompt_profiles_role",
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','VALIDATED','DISABLED')",
            name="ck_llm_prompt_profiles_state",
        ),
        sa.UniqueConstraint(
            "owner_id", "role", "version_number", name="uq_llm_prompt_role_number"
        ),
        sa.UniqueConstraint(
            "owner_id", "role", "version_label", name="uq_llm_prompt_role_label"
        ),
    )
    op.create_index("ix_llm_prompt_profiles_owner_id", "llm_prompt_profiles", ["owner_id"])
    op.create_index("ix_llm_prompt_profiles_role", "llm_prompt_profiles", ["role"])
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.add_column(sa.Column("prompt_profile_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_llm_role_routes_prompt_profile",
            "llm_prompt_profiles",
            ["prompt_profile_id"],
            ["id"],
        )
        batch.create_index("ix_llm_role_routes_prompt_profile_id", ["prompt_profile_id"])


def downgrade() -> None:
    with op.batch_alter_table("llm_role_routes") as batch:
        batch.drop_index("ix_llm_role_routes_prompt_profile_id")
        batch.drop_constraint("fk_llm_role_routes_prompt_profile", type_="foreignkey")
        batch.drop_column("prompt_profile_id")
    op.drop_index("ix_llm_prompt_profiles_role", table_name="llm_prompt_profiles")
    op.drop_index("ix_llm_prompt_profiles_owner_id", table_name="llm_prompt_profiles")
    op.drop_table("llm_prompt_profiles")
