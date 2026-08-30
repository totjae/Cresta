from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.main as main_module
import app.worker as worker_module
from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


class _Connection:
    def __init__(self, head: str) -> None:
        self.head = head

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, statement) -> None:
        assert "SELECT 1" in str(statement)

    def scalar(self, statement) -> str:
        assert "alembic_version" in str(statement)
        return self.head


class _Engine:
    def __init__(self, *, head: str = main_module.EXPECTED_MIGRATION_HEAD) -> None:
        self.head = head

    def connect(self) -> _Connection:
        return _Connection(self.head)


def test_liveness_is_dependency_free_and_readiness_requires_exact_head(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "engine", _Engine())
    with TestClient(main_module.create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "migration_head": "20260829_0044",
    }


def test_readiness_fails_closed_for_drift_and_database_failure(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "engine", _Engine(head="20260828_0042"))
    with TestClient(main_module.create_app()) as client:
        drift = client.get("/readyz")
    assert drift.status_code == 503
    assert drift.json()["code"] == "MIGRATION_HEAD_MISMATCH"

    class FailedEngine:
        def connect(self):
            raise OperationalError("database unavailable", {}, RuntimeError())

    monkeypatch.setattr(main_module, "engine", FailedEngine())
    with TestClient(main_module.create_app()) as client:
        unavailable = client.get("/readyz")
        assert client.get("/healthz").status_code == 200
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "DATABASE_UNAVAILABLE_OR_UNMIGRATED"


def test_env_example_covers_settings_without_embedding_direct_secrets() -> None:
    example = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    intentionally_absent = {"totp_encryption_key"}
    for field in Settings.model_fields.keys() - intentionally_absent:
        assert f"CRESTA_{field.upper()}" in example
    assert "CRESTA_TOTP_ENCRYPTION_KEY=" not in example
    assert "postgresql+psycopg://cresta@postgres:5432/cresta" in example
    assert "CRESTA_V7_SOURCED_HANDOFF_ENABLED=false" in example
    assert "CRESTA_LIVE_TRADING_ENABLED=false" in example


def test_broker_entrypoint_rejects_missing_mock_configuration_before_constructor(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        worker_module, "get_settings", lambda: Settings(kiwoom_enabled=False)
    )
    constructed = False

    class ForbiddenWorker:
        def __init__(self, settings) -> None:
            nonlocal constructed
            constructed = True

    monkeypatch.setattr(worker_module, "KiwoomBrokerWorker", ForbiddenWorker)
    assert asyncio.run(worker_module._run_worker("kiwoom")) == 2
    assert not constructed
