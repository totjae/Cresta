from __future__ import annotations

import json
from types import SimpleNamespace

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


class FakeSessionContext:
    def __enter__(self):
        return object()

    def __exit__(self, *_args) -> None:
        return None


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


def test_reconcile_check_reports_safe_snapshot_summary(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "KiwoomMockClient", lambda _: SuccessfulClient())
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionContext)
    monkeypatch.setattr(
        cli,
        "run_kiwoom_reconciliation",
        lambda *_: SimpleNamespace(
            gate_status="RECONCILING",
            gate_reason="PERMANENT_WORKER_REQUIRED",
            run_id="run-id",
            open_order_count=0,
            fill_count=0,
            position_count=0,
            mismatch_count=0,
            critical_mismatch_count=0,
        ),
    )

    assert cli.reconcile_kiwoom() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RECONCILING"
    assert payload["snapshot"] == {"open_orders": 0, "fills": 0, "positions": 0}
    assert payload["mismatch_count"] == 0
    assert "account" not in payload


def test_reconcile_check_returns_four_for_critical_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "KiwoomMockClient", lambda _: SuccessfulClient())
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionContext)
    monkeypatch.setattr(
        cli,
        "run_kiwoom_reconciliation",
        lambda *_: SimpleNamespace(
            gate_status="HALTED",
            gate_reason="RECONCILIATION_MISMATCH",
            run_id="run-id",
            open_order_count=1,
            fill_count=0,
            position_count=1,
            mismatch_count=2,
            critical_mismatch_count=2,
        ),
    )

    assert cli.reconcile_kiwoom() == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "HALTED"
    assert payload["critical_mismatch_count"] == 2


def test_reconcile_check_reports_adapter_error_without_raw_message(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "KiwoomMockClient", lambda _: SuccessfulClient())
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionContext)

    def fail(*_args):
        raise KiwoomAdapterError("KIWOOM_TIMEOUT", "secret-token", retryable=True)

    monkeypatch.setattr(cli, "run_kiwoom_reconciliation", fail)

    assert cli.reconcile_kiwoom() == 3
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "DEGRADED"
    assert payload["error_code"] == "KIWOOM_TIMEOUT"
    assert "secret-token" not in output
