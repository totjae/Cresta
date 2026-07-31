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
    assert settings.alembic_database_url == (
        "postgresql+psycopg://cresta:p%%40ss%%20word@postgres:5432/cresta"
    )


def test_alembic_database_url_escapes_encoded_slash(tmp_path: Path) -> None:
    password_file = tmp_path / "postgres_password"
    password_file.write_text("secret/value", encoding="utf-8")
    settings = Settings(
        database_url="postgresql+psycopg://cresta@postgres:5432/cresta",
        database_password_file=str(password_file),
    )

    assert "%2F" in settings.resolved_database_url
    assert "%%2F" in settings.alembic_database_url


def test_live_environment_is_rejected() -> None:
    settings = Settings(environment="LIVE", live_trading_enabled=False)
    try:
        settings.validate_safety()
    except RuntimeError as exc:
        assert "MOCK" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("LIVE environment must be rejected")


def test_kiwoom_configuration_requires_enabled_readable_nonempty_secrets(tmp_path: Path) -> None:
    paths = []
    for name, value in (("app_key", "key"), ("app_secret", "secret"), ("account", "12345678")):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        paths.append(str(path))

    disabled = Settings(
        kiwoom_app_key_file=paths[0],
        kiwoom_app_secret_file=paths[1],
        kiwoom_account_id_file=paths[2],
    )
    configured = Settings(
        kiwoom_enabled=True,
        kiwoom_app_key_file=paths[0],
        kiwoom_app_secret_file=paths[1],
        kiwoom_account_id_file=paths[2],
    )
    assert disabled.kiwoom_configuration_status() == "NOT_CONFIGURED"
    assert Settings(kiwoom_enabled=True).kiwoom_configuration_status() == "NOT_CONFIGURED"
    assert configured.kiwoom_configuration_status() == "CONFIGURED"
    assert configured.load_kiwoom_credentials() == ("key", "secret", "12345678")


def test_kiwoom_live_endpoint_is_rejected() -> None:
    settings = Settings(kiwoom_rest_base_url="https://api.kiwoom.com")
    try:
        settings.validate_safety()
    except RuntimeError as exc:
        assert "MOCK" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("Kiwoom live endpoint must be rejected")
