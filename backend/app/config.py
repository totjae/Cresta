from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRESTA_", case_sensitive=False)

    environment: str = "MOCK"
    live_trading_enabled: bool = False
    database_url: str = "sqlite:///./cresta.db"
    database_password_file: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    cookie_secure: bool = True
    allowed_origins: str = "https://localhost"
    session_cookie_name: str = "cresta_session"
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8
    challenge_minutes: int = 5
    reauth_minutes: int = 5
    auth_max_failures: int = 5
    auth_lock_minutes: int = 15
    totp_encryption_key: str | None = Field(default=None, repr=False)
    totp_encryption_key_file: str | None = None

    @property
    def origin_allowlist(self) -> set[str]:
        return {item.strip().rstrip("/") for item in self.allowed_origins.split(",") if item.strip()}

    def load_totp_encryption_key(self) -> str:
        if self.totp_encryption_key:
            return self.totp_encryption_key.strip()
        if self.totp_encryption_key_file:
            return Path(self.totp_encryption_key_file).read_text(encoding="utf-8").strip()
        raise RuntimeError("TOTP encryption key is not configured")

    @property
    def resolved_database_url(self) -> str:
        if not self.database_password_file:
            return self.database_url
        password = Path(self.database_password_file).read_text(encoding="utf-8").strip()
        marker = "postgresql+psycopg://"
        if not self.database_url.startswith(marker):
            raise RuntimeError("Database password files require a PostgreSQL URL")
        authority, separator, remainder = self.database_url[len(marker) :].partition("@")
        if not separator or ":" in authority:
            raise RuntimeError("Database URL must contain a username without an embedded password")
        return f"{marker}{authority}:{quote(password, safe='')}@{remainder}"

    def validate_safety(self) -> None:
        if self.environment.upper() != "MOCK":
            raise RuntimeError("Only MOCK broker environment is allowed in the first release")
        if self.live_trading_enabled:
            raise RuntimeError("Live trading must remain disabled in the first release")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_safety()
    return settings
