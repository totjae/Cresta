from __future__ import annotations

import pytest

from app.agents.reason_codes import (
    REASON_CODE_POLICY_VERSION,
    ROLE_REASON_CODES,
    allowed_reason_codes,
    invalid_reason_codes,
    output_schema_for_role,
    reason_code_context,
)


@pytest.mark.parametrize("role", sorted(ROLE_REASON_CODES))
def test_role_reason_code_policy_is_unique_and_embedded_in_schema(role: str) -> None:
    codes = allowed_reason_codes(role)
    assert codes
    assert len(codes) == len(set(codes))
    assert output_schema_for_role(role)["properties"]["reason_codes"]["items"][
        "enum"
    ] == list(codes)
    assert reason_code_context(role) == {
        "reason_code_policy_version": REASON_CODE_POLICY_VERSION,
        "allowed_reason_codes": list(codes),
    }


def test_unknown_reason_code_is_reported_without_weakening_allowlist() -> None:
    assert invalid_reason_codes(
        "CORE", ["DIAGNOSTIC_WAIT_ONLY", "UNREGISTERED_REASON"]
    ) == ["UNREGISTERED_REASON"]


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported agent role"):
        allowed_reason_codes("UNKNOWN_ROLE")
