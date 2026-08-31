from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

KRX_SOURCE_POLICY_VERSION = "krx-stock-daily-v1"
KRX_SERVICE_PAGE = (
    "https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S1.cmd"
)
KRX_MARKET_PATHS = (
    ("KOSPI", "/svc/apis/sto/stk_bydd_trd"),
    ("KOSDAQ", "/svc/apis/sto/ksq_bydd_trd"),
)
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_daily_cache: dict[tuple[str, str, str], tuple[dict[str, Any], ...]] = {}


def _evict_daily_cache(
    *, credential_fingerprint: str, required_dates: frozenset[str]
) -> None:
    required_paths = {path for _, path in KRX_MARKET_PATHS}
    obsolete = [
        key
        for key in _daily_cache
        if key[2] != credential_fingerprint
        or key[1] not in required_dates
        or key[0] not in required_paths
    ]
    for key in obsolete:
        del _daily_cache[key]
    if obsolete:
        logger.debug(
            "Agent cache eviction cache=krx_daily evicted=%s retained=%s",
            len(obsolete),
            len(_daily_cache),
        )


class KrxCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class KrxDailyMarket:
    base_date: str
    symbol: str
    name: str
    market_name: str
    sector_type_name: str
    close_price: str
    change_price: str
    change_rate: str
    open_price: str
    high_price: str
    low_price: str
    trading_volume: str
    trading_value: str
    market_cap: str
    listed_shares: str
    endpoint_path: str

    @property
    def source_url(self) -> str:
        return f"https://data-dbg.krx.co.kr{self.endpoint_path}?basDd={self.base_date}"

    def facts(self) -> dict[str, str]:
        return {
            "base_date": self.base_date,
            "symbol": self.symbol,
            "name": self.name,
            "market_name": self.market_name,
            "sector_type_name": self.sector_type_name,
            "close_price": self.close_price,
            "change_price": self.change_price,
            "change_rate": self.change_rate,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "trading_volume": self.trading_volume,
            "trading_value": self.trading_value,
            "market_cap": self.market_cap,
            "listed_shares": self.listed_shares,
        }


@dataclass(frozen=True)
class KrxCollection:
    item: KrxDailyMarket | None
    dates_queried: tuple[str, ...]
    requests_made: int


def base_date_as_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d").replace(
        tzinfo=ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)


def _safe_decimal_text(value: object, *, integer: bool = False) -> str:
    text = str(value).strip().replace(",", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise KrxCollectionError("KRX_RESPONSE_INVALID") from None
    if not number.is_finite() or (integer and number != number.to_integral_value()):
        raise KrxCollectionError("KRX_RESPONSE_INVALID")
    return str(int(number)) if integer else format(number, "f")


def _parse_row(row: object, *, endpoint_path: str, symbol: str) -> KrxDailyMarket:
    if not isinstance(row, dict):
        raise KrxCollectionError("KRX_RESPONSE_INVALID")
    base_date = str(row.get("BAS_DD", "")).strip()
    issue_code = str(row.get("ISU_CD", "")).strip()
    market_name = str(row.get("MKT_NM", "")).strip()
    if len(base_date) != 8 or not base_date.isdigit():
        raise KrxCollectionError("KRX_RESPONSE_INVALID")
    if issue_code != symbol or market_name not in {"KOSPI", "KOSDAQ"}:
        raise KrxCollectionError("KRX_RESPONSE_INVALID")
    return KrxDailyMarket(
        base_date=base_date,
        symbol=issue_code,
        name=str(row.get("ISU_NM", "")).strip(),
        market_name=market_name,
        sector_type_name=str(row.get("SECT_TP_NM", "")).strip(),
        close_price=_safe_decimal_text(row.get("TDD_CLSPRC"), integer=True),
        change_price=_safe_decimal_text(row.get("CMPPREVDD_PRC"), integer=True),
        change_rate=_safe_decimal_text(row.get("FLUC_RT")),
        open_price=_safe_decimal_text(row.get("TDD_OPNPRC"), integer=True),
        high_price=_safe_decimal_text(row.get("TDD_HGPRC"), integer=True),
        low_price=_safe_decimal_text(row.get("TDD_LWPRC"), integer=True),
        trading_volume=_safe_decimal_text(row.get("ACC_TRDVOL"), integer=True),
        trading_value=_safe_decimal_text(row.get("ACC_TRDVAL"), integer=True),
        market_cap=_safe_decimal_text(row.get("MKTCAP"), integer=True),
        listed_shares=_safe_decimal_text(row.get("LIST_SHRS"), integer=True),
        endpoint_path=endpoint_path,
    )


def _load_market_rows(
    client: httpx.Client,
    *,
    endpoint_path: str,
    base_date: str,
    api_key: str,
    credential_fingerprint: str,
) -> tuple[tuple[dict[str, Any], ...], bool]:
    cache_key = (endpoint_path, base_date, credential_fingerprint)
    cached = _daily_cache.get(cache_key)
    if cached is not None:
        return cached, False
    response = client.get(
        endpoint_path,
        params={"basDd": base_date},
        headers={"AUTH_KEY": api_key},
    )
    response.raise_for_status()
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise KrxCollectionError("KRX_RESPONSE_TOO_LARGE")
    try:
        payload = response.json()
    except ValueError:
        raise KrxCollectionError("KRX_RESPONSE_INVALID") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
        raise KrxCollectionError("KRX_RESPONSE_INVALID")
    rows = tuple(row for row in payload["OutBlock_1"] if isinstance(row, dict))
    if len(rows) != len(payload["OutBlock_1"]):
        raise KrxCollectionError("KRX_RESPONSE_INVALID")
    _daily_cache[cache_key] = rows
    return rows, True


def collect_krx_daily_market(
    settings: Settings,
    *,
    symbol: str,
    now: datetime,
    transport: httpx.BaseTransport | None = None,
) -> KrxCollection:
    if settings.krx_configuration_status() != "CONFIGURED":
        raise KrxCollectionError("KRX_NOT_CONFIGURED")
    if len(symbol) != 6 or not symbol.isdigit():
        raise KrxCollectionError("KRX_SYMBOL_INVALID")
    kst_day = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    api_key = settings.load_krx_api_key()
    credential_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    required_dates = tuple(
        (kst_day - timedelta(days=days_ago)).strftime("%Y%m%d")
        for days_ago in range(1, settings.krx_lookback_days + 1)
    )
    _evict_daily_cache(
        credential_fingerprint=credential_fingerprint,
        required_dates=frozenset(required_dates),
    )
    dates_queried: list[str] = []
    requests_made = 0
    try:
        with httpx.Client(
            base_url=settings.krx_base_url,
            timeout=settings.krx_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            for base_date in required_dates:
                dates_queried.append(base_date)
                for expected_market, endpoint_path in KRX_MARKET_PATHS:
                    rows, requested = _load_market_rows(
                        client,
                        endpoint_path=endpoint_path,
                        base_date=base_date,
                        api_key=api_key,
                        credential_fingerprint=credential_fingerprint,
                    )
                    requests_made += int(requested)
                    matches = [row for row in rows if str(row.get("ISU_CD", "")).strip() == symbol]
                    if len(matches) > 1:
                        raise KrxCollectionError("KRX_DUPLICATE_SYMBOL")
                    if matches:
                        item = _parse_row(
                            matches[0], endpoint_path=endpoint_path, symbol=symbol
                        )
                        if item.market_name != expected_market:
                            raise KrxCollectionError("KRX_RESPONSE_INVALID")
                        return KrxCollection(item, tuple(dates_queried), requests_made)
    except httpx.TimeoutException:
        raise KrxCollectionError("KRX_TIMED_OUT") from None
    except httpx.HTTPStatusError:
        raise KrxCollectionError("KRX_PROVIDER_ERROR") from None
    except httpx.HTTPError:
        raise KrxCollectionError("KRX_PROVIDER_ERROR") from None
    return KrxCollection(None, tuple(dates_queried), requests_made)
