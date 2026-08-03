from __future__ import annotations

import json

import pytest

from app import cli
from app.broker.kiwoom import AccountVerification, KiwoomAdapterError


class SuccessfulClient:
    def verify_account(self) -> AccountVerification:
        return AccountVerification(status="ACCOUNT_VERIFIED", masked_account="********90")


class FailingClient:
    def __init__(self, error: KiwoomAdapterError) -> None:
        self.error = error

    def verify_account(self) -> AccountVerification:
        raise self.error


def test_kiwoom_check_reports_masked_verified_state(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "KiwoomMockClient", lambda _: SuccessfulClient())

    assert cli.check_kiwoom() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": "1.0",
        "environment": "MOCK",
        "status": "ACCOUNT_VERIFIED",
        "account": "********90",
    }
    assert "1234567890" not in json.dumps(payload)


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (KiwoomAdapterError("KIWOOM_ACCOUNT_ID_INVALID", "secret-account"), 2),
        (KiwoomAdapterError("KIWOOM_ACCOUNT_MISMATCH", "actual-account"), 2),
        (KiwoomAdapterError("KIWOOM_TIMEOUT", "network", retryable=True), 3),
    ],
)
def test_kiwoom_check_reports_stable_error_without_message_or_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    error: KiwoomAdapterError,
    exit_code: int,
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "KiwoomMockClient", lambda _: FailingClient(error))

    assert cli.check_kiwoom() == exit_code
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "FAILED"
    assert payload["error_code"] == error.code
    assert payload["retryable"] == error.retryable
    assert error.message not in output
    assert "token" not in output.casefold()
