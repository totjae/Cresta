"""Add market calendar provenance to venue selection evaluations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0030"
down_revision: str | None = "20260812_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "venue_selection_evaluations",
        sa.Column(
            "trading_day_status",
            sa.String(16),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "venue_selection_evaluations",
        sa.Column(
            "calendar_reason",
            sa.String(32),
            nullable=False,
            server_default="CALENDAR_UNAVAILABLE",
        ),
    )
    op.add_column(
        "venue_selection_evaluations",
        sa.Column(
            "calendar_policy_version",
            sa.String(32),
            nullable=False,
            server_default="legacy-unavailable",
        ),
    )
    with op.batch_alter_table("venue_selection_evaluations") as batch_op:
        batch_op.create_check_constraint(
            "ck_venue_selection_trading_day_status",
            "trading_day_status IN ('OPEN','CLOSED','UNKNOWN')",
        )
        batch_op.create_check_constraint(
            "ck_venue_selection_calendar_reason",
            "calendar_reason IN "
            "('WEEKDAY','WEEKEND','PUBLIC_HOLIDAY','LABOR_DAY',"
            "'YEAR_END_CLOSURE','CALENDAR_UNAVAILABLE')",
        )
        batch_op.alter_column("trading_day_status", server_default=None)
        batch_op.alter_column("calendar_reason", server_default=None)
        batch_op.alter_column("calendar_policy_version", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("venue_selection_evaluations") as batch_op:
        batch_op.drop_constraint(
            "ck_venue_selection_calendar_reason", type_="check"
        )
        batch_op.drop_constraint(
            "ck_venue_selection_trading_day_status", type_="check"
        )
        batch_op.drop_column("calendar_policy_version")
        batch_op.drop_column("calendar_reason")
        batch_op.drop_column("trading_day_status")
