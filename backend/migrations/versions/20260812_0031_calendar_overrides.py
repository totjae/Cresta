"""Add fail-closed operational market calendar overrides."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0031"
down_revision: str | None = "20260812_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_calendar_overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("override_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("source_reference", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("revoked_by", sa.String(36)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('ACTIVE','REVOKED')", name="ck_market_calendar_override_state"
        ),
        sa.CheckConstraint(
            "override_type = 'OPERATIONAL_CLOSURE'",
            name="ck_market_calendar_override_type",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_market_calendar_override_active_date",
        "market_calendar_overrides",
        ["market_date"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE'"),
        sqlite_where=sa.text("state = 'ACTIVE'"),
    )
    op.create_index(
        "ix_market_calendar_override_date_created",
        "market_calendar_overrides",
        ["market_date", "created_at"],
    )
    with op.batch_alter_table("venue_selection_evaluations") as batch_op:
        batch_op.add_column(sa.Column("calendar_override_id", sa.String(36)))
        batch_op.create_foreign_key(
            "fk_venue_selection_calendar_override",
            "market_calendar_overrides",
            ["calendar_override_id"],
            ["id"],
        )
        batch_op.drop_constraint("ck_venue_selection_calendar_reason", type_="check")
        batch_op.create_check_constraint(
            "ck_venue_selection_calendar_reason",
            "calendar_reason IN "
            "('WEEKDAY','WEEKEND','PUBLIC_HOLIDAY','LABOR_DAY',"
            "'YEAR_END_CLOSURE','CALENDAR_UNAVAILABLE','OPERATIONAL_CLOSURE')",
        )


def downgrade() -> None:
    with op.batch_alter_table("venue_selection_evaluations") as batch_op:
        batch_op.drop_constraint("ck_venue_selection_calendar_reason", type_="check")
        batch_op.create_check_constraint(
            "ck_venue_selection_calendar_reason",
            "calendar_reason IN "
            "('WEEKDAY','WEEKEND','PUBLIC_HOLIDAY','LABOR_DAY',"
            "'YEAR_END_CLOSURE','CALENDAR_UNAVAILABLE')",
        )
        batch_op.drop_constraint(
            "fk_venue_selection_calendar_override", type_="foreignkey"
        )
        batch_op.drop_column("calendar_override_id")
    op.drop_index(
        "ix_market_calendar_override_date_created",
        table_name="market_calendar_overrides",
    )
    op.drop_index(
        "uq_market_calendar_override_active_date",
        table_name="market_calendar_overrides",
    )
    op.drop_table("market_calendar_overrides")
