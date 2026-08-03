"""Create immutable execution policy versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0006"
down_revision: str | None = "20260804_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "configuration_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("base_active_version_id", sa.String(36)),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('DRAFT','VALIDATED','ACTIVE','SUPERSEDED')",
            name="ck_configuration_versions_state",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_configuration_versions_sequence_positive"),
        sa.UniqueConstraint(
            "scope", "target_id", "category", "sequence", name="uq_configuration_sequence"
        ),
    )
    op.create_index(
        "uq_configuration_active_target",
        "configuration_versions",
        ["scope", "target_id", "category"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE'"),
        sqlite_where=sa.text("state = 'ACTIVE'"),
    )
    op.create_index(
        "ix_configuration_target_created",
        "configuration_versions",
        ["target_id", "category", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_configuration_target_created", table_name="configuration_versions")
    op.drop_index("uq_configuration_active_target", table_name="configuration_versions")
    op.drop_table("configuration_versions")
