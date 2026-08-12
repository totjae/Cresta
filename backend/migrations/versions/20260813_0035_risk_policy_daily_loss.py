"""Risk policy daily-loss / consecutive-loss fields (payload-only, no schema change).

RiskPolicyPayload gains ``daily_loss_limit_pct``, ``daily_loss_basis`` and
``max_consecutive_losses``. These live inside ``configuration_versions.payload_json``
(JSON text), so no column change is needed. Existing ACTIVE RISK_POLICY versions
keep their payload verbatim; the new fields default in when the payload is
parsed (``RiskPolicyPayload.model_validate_json`` fills missing fields with the
SAFE_DEFAULT values).
"""

from collections.abc import Sequence

revision: str = "20260813_0035"
down_revision: str | None = "20260813_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No DDL: the new fields are JSON payload keys on configuration_versions.
    pass


def downgrade() -> None:
    # Nothing to revert.
    pass
