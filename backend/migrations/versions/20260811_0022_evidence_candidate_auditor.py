"""Allow the internal evidence candidate auditor stage."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_0022"
down_revision: str | None = "20260811_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ROLES = (
    "'INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
    "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT','CORE'"
)
NEW_ROLES = (
    "'INTEL_COLLECTOR','EVIDENCE_VERIFIER','TECHNICAL_SCOUT',"
    "'NEWS_DISCLOSURE_SCOUT','MARKET_SECTOR_SCOUT','POSITION_RISK_SCOUT',"
    "'EVIDENCE_CANDIDATE_AUDITOR','CORE'"
)


def _replace_role_constraint(roles: str) -> None:
    with op.batch_alter_table("agent_stage_runs") as batch:
        batch.drop_constraint("ck_agent_stage_runs_role", type_="check")
        batch.create_check_constraint("ck_agent_stage_runs_role", f"role IN ({roles})")


def upgrade() -> None:
    _replace_role_constraint(NEW_ROLES)


def downgrade() -> None:
    _replace_role_constraint(OLD_ROLES)
