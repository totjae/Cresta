from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings

DART_SOURCE_POLICY_VERSION = "opendart-list-v1"
DART_VIEWER_BASE_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
MAX_CORP_CODE_ZIP_BYTES = 10 * 1024 * 1024
MAX_CORP_CODE_XML_BYTES = 30 * 1024 * 1024
_corp_code_cache: dict[str, tuple[datetime, dict[str, str]]] = {}
_corp_code_cache_lock = threading.Lock()


class DartCollectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DartDisclosure:
    receipt_number: str
    corporation_code: str
    stock_code: str
    corporation_name: str
    report_name: str
    submitter_name: str
    corporation_class: str
    receipt_date: str
    correction_flag: str

    @property
    def source_url(self) -> str:
        return f"{DART_VIEWER_BASE_URL}{self.receipt_number}"

    def facts(self) -> list[dict[str, str]]:
        return [
            {"name": "receipt_number", "value": self.receipt_number},
            {"name": "corporation_code", "value": self.corporation_code},
            {"name": "stock_code", "value": self.stock_code},
            {"name": "corporation_name", "value": self.corporation_name},
            {"name": "report_name", "value": self.report_name},
            {"name": "submitter_name", "value": self.submitter_name},
            {"name": "corporation_class", "value": self.corporation_class},
            {"name": "receipt_date", "value": self.receipt_date},
            {"name": "receipt_date_precision", "value": "DAY"},
            {"name": "correction_flag", "value": self.correction_flag},
        ]


@dataclass(frozen=True)
class DartCollection:
    disclosures: tuple[DartDisclosure, ...]
    start_date: str
    end_date: str
    pages_fetched: int


def _text(item: dict[str, Any], key: str, max_length: int) -> str:
    value = item.get(key)
    if value is None:
        return ""
    return str(value).strip()[:max_length]


def _parse_disclosure(item: object, expected_symbol: str) -> DartDisclosure | None:
    if not isinstance(item, dict):
        raise DartCollectionError("DART_RESPONSE_INVALID")
    stock_code = _text(item, "stock_code", 6)
    if stock_code != expected_symbol:
        return None
    receipt_number = _text(item, "rcept_no", 14)
    corporation_code = _text(item, "corp_code", 8)
    receipt_date = _text(item, "rcept_dt", 8)
    if (
        len(receipt_number) != 14
        or not receipt_number.isdigit()
        or len(corporation_code) != 8
        or not corporation_code.isdigit()
        or len(stock_code) != 6
        or not stock_code.isdigit()
        or len(receipt_date) != 8
        or not receipt_date.isdigit()
    ):
        raise DartCollectionError("DART_DISCLOSURE_INVALID")
    return DartDisclosure(
        receipt_number=receipt_number,
        corporation_code=corporation_code,
        stock_code=stock_code,
        corporation_name=_text(item, "corp_name", 200),
        report_name=_text(item, "report_nm", 500),
        submitter_name=_text(item, "flr_nm", 200),
        corporation_class=_text(item, "corp_cls", 1),
        receipt_date=receipt_date,
        correction_flag=_text(item, "rm", 100),
    )


def _corp_code_map(response: httpx.Response) -> dict[str, str]:
    content = response.content
    if not content or len(content) > MAX_CORP_CODE_ZIP_BYTES:
        raise DartCollectionError("DART_CORP_CODE_RESPONSE_INVALID")
    if not content.startswith(b"PK"):
        status = ""
        try:
            decoded = content.decode("utf-8")
            if decoded.lstrip().startswith("{"):
                payload = json.loads(decoded)
                status = str(payload.get("status", "")) if isinstance(payload, dict) else ""
            else:
                status = (ElementTree.fromstring(content).findtext("status") or "").strip()
        except (UnicodeDecodeError, json.JSONDecodeError, ElementTree.ParseError):
            status = ""
        if len(status) == 3 and status.isdigit() and status != "000":
            raise DartCollectionError(f"DART_STATUS_{status}")
        raise DartCollectionError("DART_CORP_CODE_RESPONSE_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if len(names) != 1 or names[0].upper() != "CORPCODE.XML":
                raise DartCollectionError("DART_CORP_CODE_RESPONSE_INVALID")
            info = archive.getinfo(names[0])
            if info.file_size > MAX_CORP_CODE_XML_BYTES:
                raise DartCollectionError("DART_CORP_CODE_RESPONSE_INVALID")
            xml_bytes = archive.read(info)
        root = ElementTree.fromstring(xml_bytes)
    except DartCollectionError:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise DartCollectionError("DART_CORP_CODE_RESPONSE_INVALID") from None
    mapping: dict[str, str] = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corporation_code = (item.findtext("corp_code") or "").strip()
        if (
            len(stock_code) == 6
            and stock_code.isdigit()
            and len(corporation_code) == 8
            and corporation_code.isdigit()
        ):
            mapping[stock_code] = corporation_code
    if not mapping:
        raise DartCollectionError("DART_CORP_CODE_RESPONSE_INVALID")
    return mapping


def _resolve_corporation_code(
    client: httpx.Client,
    *,
    api_key: str,
    symbol: str,
    now: datetime,
) -> str:
    cache_key = hashlib.sha256(api_key.encode()).hexdigest()
    with _corp_code_cache_lock:
        cached = _corp_code_cache.get(cache_key)
        if cached and cached[0] > now:
            corporation_code = cached[1].get(symbol)
            if corporation_code:
                return corporation_code
    response = client.get("/api/corpCode.xml", params={"crtfc_key": api_key})
    response.raise_for_status()
    mapping = _corp_code_map(response)
    with _corp_code_cache_lock:
        _corp_code_cache[cache_key] = (now + timedelta(hours=24), mapping)
    corporation_code = mapping.get(symbol)
    if corporation_code is None:
        raise DartCollectionError("DART_CORP_CODE_NOT_FOUND")
    return corporation_code


def collect_dart_disclosures(
    settings: Settings,
    *,
    symbol: str,
    now: datetime,
    transport: httpx.BaseTransport | None = None,
) -> DartCollection:
    if settings.dart_configuration_status() != "CONFIGURED":
        raise DartCollectionError("DART_NOT_CONFIGURED")
    if len(symbol) != 6 or not symbol.isdigit():
        raise DartCollectionError("DART_SYMBOL_INVALID")
    api_key = settings.load_dart_api_key()
    end_day = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    start_day = end_day - timedelta(days=settings.dart_lookback_days - 1)
    start_date = start_day.strftime("%Y%m%d")
    end_date = end_day.strftime("%Y%m%d")
    disclosures: dict[str, DartDisclosure] = {}
    page = 1
    pages_fetched = 0
    try:
        with httpx.Client(
            base_url=settings.dart_base_url,
            timeout=settings.dart_timeout_seconds,
            transport=transport,
        ) as client:
            corporation_code = _resolve_corporation_code(
                client,
                api_key=api_key,
                symbol=symbol,
                now=now,
            )
            while True:
                response = client.get(
                    "/api/list.json",
                    params={
                        "crtfc_key": api_key,
                        "corp_code": corporation_code,
                        "bgn_de": start_date,
                        "end_de": end_date,
                        "page_no": page,
                        "page_count": 100,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise DartCollectionError("DART_RESPONSE_INVALID")
                status = str(payload.get("status", ""))
                if status == "013":
                    return DartCollection(tuple(disclosures.values()), start_date, end_date, pages_fetched)
                if len(status) != 3 or not status.isdigit():
                    raise DartCollectionError("DART_RESPONSE_INVALID")
                if status != "000":
                    raise DartCollectionError(f"DART_STATUS_{status}")
                rows = payload.get("list", [])
                if not isinstance(rows, list):
                    raise DartCollectionError("DART_RESPONSE_INVALID")
                pages_fetched += 1
                for row in rows:
                    disclosure = _parse_disclosure(row, symbol)
                    if disclosure is not None:
                        disclosures[disclosure.receipt_number] = disclosure
                try:
                    total_pages = int(payload.get("total_page", 1))
                except (TypeError, ValueError) as exc:
                    raise DartCollectionError("DART_RESPONSE_INVALID") from exc
                if total_pages < 1:
                    raise DartCollectionError("DART_RESPONSE_INVALID")
                if total_pages > settings.dart_max_pages:
                    raise DartCollectionError("DART_PAGE_LIMIT_EXCEEDED")
                if page >= total_pages:
                    break
                page += 1
    except DartCollectionError:
        raise
    except httpx.TimeoutException:
        raise DartCollectionError("DART_TIMED_OUT") from None
    except (httpx.HTTPError, ValueError):
        raise DartCollectionError("DART_PROVIDER_ERROR") from None
    ordered = tuple(sorted(disclosures.values(), key=lambda item: item.receipt_number))
    return DartCollection(ordered, start_date, end_date, pages_fetched)


def receipt_date_as_utc(receipt_date: str) -> datetime:
    local = datetime.strptime(receipt_date, "%Y%m%d").replace(
        tzinfo=ZoneInfo("Asia/Seoul")
    )
    return local.astimezone(UTC)
