from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agents.contracts import AgentAssessmentV2, AgentCoreOutputV2, score_band


def _assessment(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "stage_run_id": "stage-1",
        "role": "POSITION_RISK_SCOUT",
        "symbol": "005930",
        "input_refs": ["snapshot-1"],
        "status": "NOT_APPLICABLE",
        "stance": "UNKNOWN",
        "entry_score": None,
        "exit_risk_score": None,
        "confidence": 0,
        "uncertainty": 1,
        "reason_codes": ["OPEN_POSITION_NOT_FOUND"],
        "evidence_refs": [],
        "observed_at": now,
        "valid_until": now + timedelta(minutes=1),
        **overrides,
    }


def test_not_applicable_assessment_has_null_scores_and_unknown_stance() -> None:
    assessment = AgentAssessmentV2.model_validate(_assessment())
    assert assessment.schema_version == "agent-assessment-v2"
    assert assessment.score_policy_version == "score-policy-v1"
    assert assessment.entry_score is None
    assert assessment.exit_risk_score is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"entry_score": 0},
        {"exit_risk_score": 0},
        {"stance": "NEUTRAL"},
        {"status": "INSUFFICIENT_DATA", "entry_score": 50},
    ],
)
def test_non_success_assessment_rejects_scores_or_not_applicable_stance(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AgentAssessmentV2.model_validate(_assessment(**overrides))


def test_core_v2_keeps_wait_separate_from_shadow_assessment() -> None:
    core = AgentCoreOutputV2(
        action="WAIT",
        shadow_assessment="ENTRY_SUPPORTIVE",
        confidence=0.7,
        risk_level="MEDIUM",
        reason_codes=["AGENT_RUNTIME_SHADOW_ONLY"],
        incomplete_roles=[],
    )
    assert core.action == "WAIT"
    assert core.shadow_assessment == "ENTRY_SUPPORTIVE"
    assert core.schema_version == "agent-core-v2"


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "STRONGLY_ADVERSE"),
        (24, "STRONGLY_ADVERSE"),
        (25, "ADVERSE"),
        (44, "ADVERSE"),
        (45, "MIXED"),
        (55, "MIXED"),
        (56, "SUPPORTIVE"),
        (74, "SUPPORTIVE"),
        (75, "STRONGLY_SUPPORTIVE"),
        (100, "STRONGLY_SUPPORTIVE"),
    ],
)
def test_score_policy_v1_boundaries(score: int, expected: str) -> None:
    assert score_band(score) == expected
