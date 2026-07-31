from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.watch import SUPPORTED_TRADING_STATUSES, QuoteEvent

TOKEN_PATH = "/oauth2/token"
BASIC_QUOTE_PATH = "/api/dostk/stkinfo"
BASIC_QUOTE_API_ID = "ka10001"
KST = timezone(timedelta(hours=9))


class KiwoomAdapterError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any: ...


class HttpClientLike(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> ResponseLike: ...


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: datetime


class KiwoomMockClient:
    """Fail-closed Kiwoom MOCK REST client with an in-memory access token."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: HttpClientLike | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        settings.validate_safety()
        if settings.kiwoom_configuration_status() != "CONFIGURED":
            raise KiwoomAdapterError("KIWOOM_NOT_CONFIGURED", "Kiwoom MOCK is not configured")
        self.settings = settings
        self._http = http_client or httpx.Client()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token: AccessToken | None = None
        self._token_lock = threading.Lock()

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            now = _as_utc(self._clock())
            refresh_before = timedelta(minutes=self.settings.kiwoom_token_refresh_minutes)
            if (
                not force_refresh
                and self._token is not None
                and now < self._token.expires_at - refresh_before
            ):
                return self._token.value
            self._token = self._issue_token()
            return self._token.value

    def invalidate_token(self) -> None:
        with self._token_lock:
            self._token = None

    def request(self, *, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("Kiwoom API path must start with '/'")
        for attempt in range(2):
            token = self.get_access_token(force_refresh=attempt == 1)
            response = self._post(
                path,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "api-id": api_id,
                    "authorization": f"Bearer {token}",
                },
                body=body,
            )
            if response.status_code == 401 and attempt == 0:
                self.invalidate_token()
                continue
            if response.status_code == 401:
                self.invalidate_token()
                raise KiwoomAdapterError(
                    "KIWOOM_AUTH_FAILED", "Kiwoom authentication failed after one retry"
                )
            payload = _json_object(response)
            if response.status_code >= 400:
                raise KiwoomAdapterError(
                    "KIWOOM_HTTP_ERROR",
                    f"Kiwoom request failed with HTTP {response.status_code}",
                    retryable=response.status_code >= 500,
                )
            _require_success(payload)
            return payload
        raise KiwoomAdapterError("KIWOOM_AUTH_FAILED", "Kiwoom authentication failed")

    def get_basic_quote(
        self,
        symbol: str,
        *,
        trading_status: str,
        received_at: datetime | None = None,
    ) -> QuoteEvent:
        payload = self.request(
            api_id=BASIC_QUOTE_API_ID,
            path=BASIC_QUOTE_PATH,
            body={"stk_cd": symbol},
        )
        return normalize_basic_quote(
            payload,
            symbol=symbol,
            trading_status=trading_status,
            received_at=received_at or self._clock(),
        )

    def _issue_token(self) -> AccessToken:
        app_key, app_secret, _ = self.settings.load_kiwoom_credentials()
        response = self._post(
            TOKEN_PATH,
            headers={"Content-Type": "application/json;charset=UTF-8"},
            body={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "secretkey": app_secret,
            },
        )
        payload = _json_object(response)
        if response.status_code >= 400:
            raise KiwoomAdapterError(
                "KIWOOM_AUTH_FAILED",
                f"Kiwoom authentication failed with HTTP {response.status_code}",
            )
        _require_success(payload)
        token = payload.get("token")
        expires = payload.get("expires_dt")
        if not isinstance(token, str) or not token or not isinstance(expires, str):
            raise KiwoomAdapterError(
                "KIWOOM_INVALID_RESPONSE", "Kiwoom token response is incomplete"
            )
        try:
            expires_at = (
                datetime.strptime(expires, "%Y%m%d%H%M%S").replace(tzinfo=KST).astimezone(UTC)
            )
        except ValueError as exc:
            raise KiwoomAdapterError(
                "KIWOOM_INVALID_RESPONSE", "Kiwoom token expiry is invalid"
            ) from exc
        return AccessToken(value=token, expires_at=expires_at)

    def _post(
        self,
        path: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> ResponseLike:
        try:
            return self._http.post(
                f"{self.settings.kiwoom_rest_base_url.rstrip('/')}{path}",
                headers=headers,
                json=body,
                timeout=float(self.settings.kiwoom_timeout_seconds),
            )
        except httpx.TimeoutException as exc:
            raise KiwoomAdapterError(
                "KIWOOM_TIMEOUT", "Kiwoom request timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise KiwoomAdapterError(
                "KIWOOM_CONNECTION_ERROR", "Kiwoom connection failed", retryable=True
            ) from exc


def normalize_basic_quote(
    payload: dict[str, Any],
    *,
    symbol: str,
    trading_status: str,
    received_at: datetime,
) -> QuoteEvent:
    if not symbol.isdigit() or len(symbol) != 6:
        raise KiwoomAdapterError("KIWOOM_INVALID_SYMBOL", "Domestic symbol must be six digits")
    response_symbol = str(payload.get("stk_cd", symbol)).strip()
    if response_symbol != symbol:
        raise KiwoomAdapterError("KIWOOM_SYMBOL_MISMATCH", "Kiwoom quote symbol does not match")
    if trading_status not in SUPPORTED_TRADING_STATUSES:
        raise KiwoomAdapterError("KIWOOM_INVALID_TRADING_STATUS", "Trading status is invalid")
    observed_at = _as_utc(received_at)
    normalized = {
        "symbol": symbol,
        "last_price": _absolute_decimal(payload.get("cur_prc"), "cur_prc"),
        "open_price": _absolute_decimal(payload.get("open_pric"), "open_pric"),
        "high_price": _absolute_decimal(payload.get("high_pric"), "high_pric"),
        "low_price": _absolute_decimal(payload.get("low_pric"), "low_pric"),
        "cumulative_volume": _non_negative_int(payload.get("trde_qty"), "trde_qty"),
        "trading_status": trading_status,
        "received_at": observed_at.isoformat(),
    }
    hash_payload = {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in normalized.items()
    }
    sequence_or_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return QuoteEvent(
        symbol=symbol,
        market="KRX",
        source="KIWOOM_REST_KA10001",
        sequence_or_hash=sequence_or_hash,
        event_at=observed_at,
        received_at=observed_at,
        last_price=normalized["last_price"],
        open_price=normalized["open_price"],
        high_price=normalized["high_price"],
        low_price=normalized["low_price"],
        cumulative_volume=normalized["cumulative_volume"],
        trading_status=trading_status,
        recovery_snapshot=True,
    )


def _json_object(response: ResponseLike) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", "Kiwoom response is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise KiwoomAdapterError("KIWOOM_INVALID_RESPONSE", "Kiwoom response must be an object")
    return payload


def _require_success(payload: dict[str, Any]) -> None:
    code = payload.get("return_code")
    try:
        normalized = int(code) if code is not None else 0
    except (TypeError, ValueError) as exc:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", "Kiwoom return code is invalid"
        ) from exc
    if normalized != 0:
        raise KiwoomAdapterError("KIWOOM_API_ERROR", f"Kiwoom API rejected request ({normalized})")


def _absolute_decimal(value: Any, field: str) -> Decimal:
    try:
        result = abs(Decimal(str(value).strip()))
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} is invalid"
        ) from exc
    if result <= 0:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be positive"
        )
    return result


def _non_negative_int(value: Any, field: str) -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} is invalid"
        ) from exc
    if result < 0:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be non-negative"
        )
    return result


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise KiwoomAdapterError("KIWOOM_TIMEZONE_REQUIRED", "Received time must include timezone")
    return value.astimezone(UTC)
