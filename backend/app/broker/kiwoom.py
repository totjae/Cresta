from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Mapping
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
ACCOUNT_PATH = "/api/dostk/acnt"
ACCOUNT_API_ID = "ka00001"
OPEN_ORDERS_API_ID = "ka10075"
FILLS_API_ID = "ka10076"
POSITIONS_API_ID = "kt00018"
MAX_CONTINUATION_PAGES = 20
KST = timezone(timedelta(hours=9))


class KiwoomAdapterError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]

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


@dataclass(frozen=True)
class AccountVerification:
    status: str
    masked_account: str


@dataclass(frozen=True)
class KiwoomPage:
    payload: dict[str, Any]
    continuation: bool
    next_key: str | None


@dataclass(frozen=True)
class BrokerOpenOrder:
    broker_order_id: str
    symbol: str
    side: str
    requested_quantity: int
    filled_quantity: int
    remaining_quantity: int
    limit_price: Decimal | None
    order_time: str
    market: str = "KRX"


@dataclass(frozen=True)
class BrokerFillSummary:
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    fee: Decimal
    tax: Decimal
    order_time: str
    market: str = "KRX"


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: int
    available_quantity: int
    average_price: Decimal
    market: str = "KRX"


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    open_orders: tuple[BrokerOpenOrder, ...]
    fills: tuple[BrokerFillSummary, ...]
    positions: tuple[BrokerPosition, ...]
    observed_at: datetime


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
        return self._request_page(api_id=api_id, path=path, body=body).payload

    def request_all_pages(
        self, *, api_id: str, path: str, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        next_key: str | None = None
        seen_keys: set[str] = set()
        for _ in range(MAX_CONTINUATION_PAGES):
            continuation_headers = (
                {"cont-yn": "Y", "next-key": next_key} if next_key is not None else {}
            )
            page = self._request_page(
                api_id=api_id,
                path=path,
                body=body,
                continuation_headers=continuation_headers,
            )
            pages.append(page.payload)
            if not page.continuation:
                return pages
            if not page.next_key or page.next_key in seen_keys:
                raise KiwoomAdapterError(
                    "KIWOOM_INVALID_PAGINATION", "Kiwoom continuation key is invalid"
                )
            seen_keys.add(page.next_key)
            next_key = page.next_key
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_PAGINATION", "Kiwoom continuation page limit exceeded"
        )

    def _request_page(
        self,
        *,
        api_id: str,
        path: str,
        body: dict[str, Any],
        continuation_headers: dict[str, str] | None = None,
    ) -> KiwoomPage:
        if not path.startswith("/"):
            raise ValueError("Kiwoom API path must start with '/'")
        for attempt in range(2):
            token = self.get_access_token(force_refresh=attempt == 1)
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": api_id,
                "authorization": f"Bearer {token}",
            }
            headers.update(continuation_headers or {})
            response = self._post(
                path,
                headers=headers,
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
            continuation = _response_header(response, "cont-yn").upper() == "Y"
            raw_next_key = _response_header(response, "next-key").strip()
            return KiwoomPage(
                payload=payload,
                continuation=continuation,
                next_key=raw_next_key or None,
            )
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

    def get_account_number(self) -> str:
        payload = self.request(api_id=ACCOUNT_API_ID, path=ACCOUNT_PATH, body={})
        account_number = payload.get("acctNo")
        if not isinstance(account_number, str):
            raise KiwoomAdapterError(
                "KIWOOM_INVALID_RESPONSE", "Kiwoom account response is incomplete"
            )
        normalized = account_number.strip()
        if len(normalized) != 10 or not normalized.isdigit():
            raise KiwoomAdapterError(
                "KIWOOM_INVALID_RESPONSE", "Kiwoom account identifier must be ten digits"
            )
        return normalized

    def verify_account(self) -> AccountVerification:
        _, _, expected_account = self.settings.load_kiwoom_credentials()
        if len(expected_account) != 10 or not expected_account.isdigit():
            raise KiwoomAdapterError(
                "KIWOOM_ACCOUNT_ID_INVALID",
                "Configured Kiwoom account identifier must be ten digits",
            )
        actual_account = self.get_account_number()
        if not hmac.compare_digest(expected_account, actual_account):
            raise KiwoomAdapterError(
                "KIWOOM_ACCOUNT_MISMATCH", "Kiwoom token account does not match configuration"
            )
        return AccountVerification(
            status="ACCOUNT_VERIFIED",
            masked_account=f"********{actual_account[-2:]}",
        )

    def get_account_snapshot(self) -> BrokerAccountSnapshot:
        open_order_pages = self.request_all_pages(
            api_id=OPEN_ORDERS_API_ID,
            path=ACCOUNT_PATH,
            body={"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "1"},
        )
        fill_pages = self.request_all_pages(
            api_id=FILLS_API_ID,
            path=ACCOUNT_PATH,
            body={"stk_cd": "", "qry_tp": "0", "sell_tp": "0", "ord_no": "", "stex_tp": "1"},
        )
        position_pages = self.request_all_pages(
            api_id=POSITIONS_API_ID,
            path=ACCOUNT_PATH,
            body={"qry_tp": "1", "dmst_stex_tp": "KRX"},
        )
        open_orders = tuple(
            normalize_open_order(item) for item in _page_items(open_order_pages, "oso")
        )
        fills = tuple(normalize_fill(item) for item in _page_items(fill_pages, "cntr"))
        positions = tuple(
            position
            for item in _page_items(position_pages, "acnt_evlt_remn_indv_tot")
            if (position := normalize_position(item)) is not None
        )
        _require_unique(open_orders, "broker_order_id")
        _require_unique(positions, "symbol")
        return BrokerAccountSnapshot(
            open_orders=open_orders,
            fills=fills,
            positions=positions,
            observed_at=_as_utc(self._clock()),
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


def normalize_open_order(payload: dict[str, Any]) -> BrokerOpenOrder:
    requested = _positive_int(payload.get("ord_qty"), "ord_qty")
    remaining = _positive_int(payload.get("oso_qty"), "oso_qty")
    if remaining > requested:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", "Kiwoom open order remaining quantity is invalid"
        )
    raw_price = _non_negative_decimal(payload.get("ord_pric"), "ord_pric")
    return BrokerOpenOrder(
        broker_order_id=_broker_order_id(payload.get("ord_no")),
        symbol=_domestic_symbol(payload.get("stk_cd")),
        side=_order_side(payload),
        requested_quantity=requested,
        filled_quantity=requested - remaining,
        remaining_quantity=remaining,
        limit_price=raw_price if raw_price > 0 else None,
        order_time=_hhmmss(payload.get("tm"), "tm"),
    )


def normalize_fill(payload: dict[str, Any]) -> BrokerFillSummary:
    return BrokerFillSummary(
        broker_order_id=_broker_order_id(payload.get("ord_no")),
        symbol=_domestic_symbol(payload.get("stk_cd")),
        side=_order_side(payload),
        quantity=_positive_int(payload.get("cntr_qty"), "cntr_qty"),
        price=_positive_decimal(payload.get("cntr_pric"), "cntr_pric"),
        fee=_non_negative_decimal(payload.get("tdy_trde_cmsn", 0), "tdy_trde_cmsn"),
        tax=_non_negative_decimal(payload.get("tdy_trde_tax", 0), "tdy_trde_tax"),
        order_time=_hhmmss(payload.get("ord_tm"), "ord_tm"),
    )


def normalize_position(payload: dict[str, Any]) -> BrokerPosition | None:
    quantity = _non_negative_int(payload.get("rmnd_qty"), "rmnd_qty")
    available = _non_negative_int(payload.get("trde_able_qty"), "trde_able_qty")
    if available > quantity:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", "Kiwoom available position quantity is invalid"
        )
    if quantity == 0:
        return None
    return BrokerPosition(
        symbol=_domestic_symbol(payload.get("stk_cd"), allow_stock_prefix=True),
        quantity=quantity,
        available_quantity=available,
        average_price=_positive_decimal(payload.get("pur_pric"), "pur_pric"),
    )


def _page_items(pages: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in pages:
        value = page.get(field, [])
        if value is None:
            continue
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise KiwoomAdapterError(
                "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be an object list"
            )
        items.extend(value)
    return items


def _require_unique(items: tuple[Any, ...], attribute: str) -> None:
    values = [getattr(item, attribute) for item in items]
    if len(values) != len(set(values)):
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {attribute} contains duplicates"
        )


def _response_header(response: ResponseLike, name: str) -> str:
    headers = getattr(response, "headers", {})
    for key, value in headers.items():
        if str(key).casefold() == name.casefold():
            return str(value)
    return ""


def _broker_order_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized.isdigit() or not 1 <= len(normalized) <= 10:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", "Kiwoom broker order identifier is invalid"
        )
    return normalized


def _domestic_symbol(value: Any, *, allow_stock_prefix: bool = False) -> str:
    normalized = str(value or "").strip()
    if allow_stock_prefix and len(normalized) == 7 and normalized.startswith("A"):
        normalized = normalized[1:]
    if len(normalized) != 6 or not normalized.isdigit():
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", "Kiwoom domestic stock symbol is invalid"
        )
    return normalized


def _order_side(payload: dict[str, Any]) -> str:
    raw_code = str(payload.get("trde_tp", "")).strip()
    raw_name = str(payload.get("io_tp_nm", "")).strip()
    combined = f"{raw_code} {raw_name}"
    if raw_code == "1" or "매도" in combined:
        return "SELL"
    if raw_code == "2" or "매수" in combined:
        return "BUY"
    raise KiwoomAdapterError(
        "KIWOOM_UNSUPPORTED_RESPONSE", "Kiwoom order side cannot be interpreted safely"
    )


def _hhmmss(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise KiwoomAdapterError("KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be HHmmss")
    hour, minute, second = int(normalized[:2]), int(normalized[2:4]), int(normalized[4:])
    if hour > 23 or minute > 59 or second > 59:
        raise KiwoomAdapterError("KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be HHmmss")
    return normalized


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
        result = abs(Decimal(_numeric_text(value)))
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
        result = int(_numeric_text(value))
    except (TypeError, ValueError) as exc:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} is invalid"
        ) from exc
    if result < 0:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be non-negative"
        )
    return result


def _positive_int(value: Any, field: str) -> int:
    result = _non_negative_int(value, field)
    if result == 0:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be positive"
        )
    return result


def _non_negative_decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(_numeric_text(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} is invalid"
        ) from exc
    if result < 0:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be non-negative"
        )
    return result


def _positive_decimal(value: Any, field: str) -> Decimal:
    result = _non_negative_decimal(value, field)
    if result == 0:
        raise KiwoomAdapterError(
            "KIWOOM_INVALID_RESPONSE", f"Kiwoom field {field} must be positive"
        )
    return result


def _numeric_text(value: Any) -> str:
    return str(value).strip().replace(",", "")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise KiwoomAdapterError("KIWOOM_TIMEZONE_REQUIRED", "Received time must include timezone")
    return value.astimezone(UTC)
