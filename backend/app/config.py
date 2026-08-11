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
    execution_stage: str = "SHADOW"
    analysis_scheduler_poll_seconds: int = Field(default=5, ge=1, le=30)
    analysis_scheduler_lease_seconds: int = Field(default=30, ge=15, le=120)
    agent_worker_poll_seconds: int = Field(default=1, ge=1, le=10)
    agent_worker_lease_seconds: int = Field(default=30, ge=10, le=120)
    dart_enabled: bool = False
    dart_api_key_file: str | None = None
    dart_base_url: str = "https://opendart.fss.or.kr"
    dart_timeout_seconds: int = Field(default=5, ge=1, le=15)
    dart_max_pages: int = Field(default=10, ge=1, le=50)
    dart_lookback_days: int = Field(default=3, ge=1, le=7)
    krx_enabled: bool = False
    krx_api_key_file: str | None = None
    krx_base_url: str = "https://data-dbg.krx.co.kr"
    krx_timeout_seconds: int = Field(default=10, ge=1, le=30)
    krx_lookback_days: int = Field(default=7, ge=1, le=10)
    naver_news_enabled: bool = False
    naver_news_client_id_file: str | None = None
    naver_news_client_secret_file: str | None = None
    naver_news_base_url: str = "https://naverapihub.apigw.ntruss.com"
    naver_news_timeout_seconds: int = Field(default=10, ge=1, le=30)
    naver_news_display: int = Field(default=20, ge=1, le=20)
    naver_news_lookback_hours: int = Field(default=72, ge=1, le=168)
    naver_news_cache_seconds: int = Field(default=300, ge=60, le=1800)
    llm_secret_directory: str = "./secrets/llm"
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
    quote_stale_seconds: int = Field(default=2, ge=1, le=30)
    expected_egress_ip: str = "180.68.4.149"
    kiwoom_enabled: bool = False
    kiwoom_rest_base_url: str = "https://mockapi.kiwoom.com"
    kiwoom_ws_base_url: str = "wss://mockapi.kiwoom.com:10000"
    kiwoom_app_key_file: str | None = None
    kiwoom_app_secret_file: str | None = None
    kiwoom_account_id_file: str | None = None
    kiwoom_token_refresh_minutes: int = Field(default=60, ge=1, le=720)
    kiwoom_timeout_seconds: int = Field(default=5, ge=1, le=30)
    kiwoom_worker_lease_seconds: int = Field(default=60, ge=30, le=300)
    kiwoom_worker_heartbeat_seconds: int = Field(default=10, ge=5, le=60)
    kiwoom_reconcile_interval_seconds: int = Field(default=60, ge=30, le=600)
    kiwoom_event_debounce_seconds: int = Field(default=1, ge=1, le=10)
    kiwoom_watchlist_sync_seconds: int = Field(default=5, ge=1, le=30)
    kiwoom_sor_enabled: bool = False
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

    @property
    def alembic_database_url(self) -> str:
        """Escape ConfigParser interpolation markers without changing the SQLAlchemy URL."""
        return self.resolved_database_url.replace("%", "%%")

    def validate_safety(self) -> None:
        if self.environment.upper() != "MOCK":
            raise RuntimeError("Only MOCK broker environment is allowed in the first release")
        if self.live_trading_enabled:
            raise RuntimeError("Live trading must remain disabled in the first release")
        if self.execution_stage != "SHADOW":
            raise RuntimeError("Only the SHADOW decision execution stage is currently implemented")
        if self.analysis_scheduler_poll_seconds * 2 >= self.analysis_scheduler_lease_seconds:
            raise RuntimeError("Analysis scheduler poll must be less than half the lease duration")
        if self.agent_worker_poll_seconds * 2 >= self.agent_worker_lease_seconds:
            raise RuntimeError("Agent worker poll must be less than half the lease duration")
        if self.dart_base_url.rstrip("/") != "https://opendart.fss.or.kr":
            raise RuntimeError("Only the official OpenDART endpoint is allowed")
        if self.krx_base_url.rstrip("/") != "https://data-dbg.krx.co.kr":
            raise RuntimeError("Only the official KRX OPEN API endpoint is allowed")
        if self.naver_news_base_url.rstrip("/") != "https://naverapihub.apigw.ntruss.com":
            raise RuntimeError("Only the official NAVER API HUB endpoint is allowed")
        if self.kiwoom_rest_base_url.rstrip("/") != "https://mockapi.kiwoom.com":
            raise RuntimeError("Only the Kiwoom MOCK REST endpoint is allowed in the first release")
        if self.kiwoom_ws_base_url.rstrip("/") != "wss://mockapi.kiwoom.com:10000":
            raise RuntimeError("Only the Kiwoom MOCK WebSocket endpoint is allowed in the first release")
        if self.kiwoom_worker_heartbeat_seconds * 2 >= self.kiwoom_worker_lease_seconds:
            raise RuntimeError("Kiwoom worker heartbeat must be less than half the lease duration")

    def kiwoom_configuration_status(self) -> str:
        if not self.kiwoom_enabled:
            return "NOT_CONFIGURED"
        paths = (
            self.kiwoom_app_key_file,
            self.kiwoom_app_secret_file,
            self.kiwoom_account_id_file,
        )
        if not all(paths):
            return "NOT_CONFIGURED"
        try:
            values = [Path(path).read_text(encoding="utf-8").strip() for path in paths if path]
        except OSError:
            return "NOT_CONFIGURED"
        return "CONFIGURED" if len(values) == 3 and all(values) else "NOT_CONFIGURED"

    def load_kiwoom_credentials(self) -> tuple[str, str, str]:
        if self.kiwoom_configuration_status() != "CONFIGURED":
            raise RuntimeError("Kiwoom MOCK credentials are not configured")
        assert self.kiwoom_app_key_file
        assert self.kiwoom_app_secret_file
        assert self.kiwoom_account_id_file
        return tuple(
            Path(path).read_text(encoding="utf-8").strip()
            for path in (
                self.kiwoom_app_key_file,
                self.kiwoom_app_secret_file,
                self.kiwoom_account_id_file,
            )
        )

    def dart_configuration_status(self) -> str:
        if not self.dart_enabled:
            return "DISABLED"
        if not self.dart_api_key_file:
            return "NOT_CONFIGURED"
        try:
            key = Path(self.dart_api_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            return "NOT_CONFIGURED"
        return "CONFIGURED" if len(key) == 40 and not any(char.isspace() for char in key) else "INVALID"

    def load_dart_api_key(self) -> str:
        if self.dart_configuration_status() != "CONFIGURED":
            raise RuntimeError("OpenDART API key is not configured")
        assert self.dart_api_key_file
        return Path(self.dart_api_key_file).read_text(encoding="utf-8").strip()

    def krx_configuration_status(self) -> str:
        if not self.krx_enabled:
            return "DISABLED"
        if not self.krx_api_key_file:
            return "NOT_CONFIGURED"
        try:
            key = Path(self.krx_api_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            return "NOT_CONFIGURED"
        valid = len(key) == 40 and all(char in "0123456789abcdefABCDEF" for char in key)
        return "CONFIGURED" if valid else "INVALID"

    def load_krx_api_key(self) -> str:
        if self.krx_configuration_status() != "CONFIGURED":
            raise RuntimeError("KRX OPEN API key is not configured")
        assert self.krx_api_key_file
        return Path(self.krx_api_key_file).read_text(encoding="utf-8").strip()

    def naver_news_configuration_status(self) -> str:
        if not self.naver_news_enabled:
            return "DISABLED"
        paths = (self.naver_news_client_id_file, self.naver_news_client_secret_file)
        if not all(paths):
            return "NOT_CONFIGURED"
        try:
            values = tuple(
                Path(path).read_text(encoding="utf-8").strip() for path in paths if path
            )
        except OSError:
            return "NOT_CONFIGURED"
        valid = len(values) == 2 and all(
            8 <= len(value) <= 128 and not any(char.isspace() for char in value)
            for value in values
        )
        return "CONFIGURED" if valid else "INVALID"

    def load_naver_news_credentials(self) -> tuple[str, str]:
        if self.naver_news_configuration_status() != "CONFIGURED":
            raise RuntimeError("NAVER API HUB credentials are not configured")
        assert self.naver_news_client_id_file
        assert self.naver_news_client_secret_file
        return (
            Path(self.naver_news_client_id_file).read_text(encoding="utf-8").strip(),
            Path(self.naver_news_client_secret_file).read_text(encoding="utf-8").strip(),
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_safety()
    return settings
