"""Create broker worker lease and status tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0005"
down_revision: str | None = "20260803_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_leases",
        sa.Column("account_alias", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fencing_token > 0", name="ck_broker_leases_fencing_positive"),
        sa.CheckConstraint("version > 0", name="ck_broker_leases_version_positive"),
    )
    op.create_table(
        "broker_worker_states",
        sa.Column("account_alias", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("websocket_connected", sa.Boolean(), nullable=False),
        sa.Column("subscriptions_ready", sa.Boolean(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True)),
        sa.Column("last_reconciliation_run_id", sa.String(36)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('STARTING','AUTHENTICATING','CONNECTING','SUBSCRIBING',"
            "'RECONCILING','READY','DEGRADED','STOPPED')",
            name="ck_broker_worker_states_state",
        ),
        sa.CheckConstraint("fencing_token > 0", name="ck_broker_worker_states_fencing_positive"),
    )


def downgrade() -> None:
    op.drop_table("broker_worker_states")
    op.drop_table("broker_leases")
