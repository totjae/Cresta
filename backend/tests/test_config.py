from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_database_password_file_is_encoded_without_exposure(tmp_path: Path) -> None:
    password_file = tmp_path / "postgres_password"
    password_file.write_text("p@ss word", encoding="utf-8")
    settings = Settings(
        database_url="postgresql+psycopg://cresta@postgres:5432/cresta",
        database_password_file=str(password_file),
    )
    assert settings.resolved_database_url == (
        "postgresql+psycopg://cresta:p%40ss%20word@postgres:5432/cresta"
    )


def test_live_environment_is_rejected() -> None:
    settings = Settings(environment="LIVE", live_trading_enabled=False)
    try:
        settings.validate_safety()
    except RuntimeError as exc:
        assert "MOCK" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("LIVE environment must be rejected")
