from __future__ import annotations

import hashlib
import html
import ipaddress
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

NAVER_NEWS_SOURCE_POLICY_VERSION = "naver-api-hub-news-v1"
NAVER_NEWS_PATH = "/search/v1/news"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TAG_PATTERN = re.compile(r"<[^>]*>")
_cache: dict[
    tuple[str, str], tuple[datetime, tuple[dict[str, Any], ...], int]
] = {}


def _evict_cache(*, active_fingerprint: str, now: datetime) -> None:
    obsolete = [
        key
        for key, (expires_at, _, _) in _cache.items()
        if key[0] != active_fingerprint or expires_at <= now
    ]
    for key in obsolete:
        del _cache[key]
    if obsolete:
        logger.debug(
            "Agent cache eviction cache=naver_news evicted=%s retained=%s",
            len(obsolete),
            len(_cache),
        )


class NaverNewsCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class NaverNewsItem:
    title: str
    source_url: str
    source_host: str
    published_at: datetime
    matched_identity: str
    stale: bool

    def facts(self) -> dict[str, str]:
        return {
            "source_host": self.source_host,
            "published_at": self.published_at.isoformat(),
            "matched_identity": self.matched_identity,
            "freshness": "STALE" if self.stale else "FRESH",
        }


@dataclass(frozen=True)
class NaverNewsCollection:
    items: tuple[NaverNewsItem, ...]
    query_identity: str
    returned_count: int
    irrelevant_count: int
    unsafe_url_count: int
    cache_hit: bool

    @property
    def fresh_items(self) -> tuple[NaverNewsItem, ...]:
        return tuple(item for item in self.items if not item.stale)

    @property
    def stale_items(self) -> tuple[NaverNewsItem, ...]:
        return tuple(item for item in self.items if item.stale)


def _plain_text(value: object, limit: int) -> str:
    text = html.unescape(_TAG_PATTERN.sub("", str(value or "")))
    return " ".join(text.split())[:limit]


def _identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _safe_https_url(value: object) -> str | None:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        return None
    if host == "localhost" or host.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port not in (None, 443):
        return None
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _published_at(value: object) -> datetime:
    try:
        parsed = parsedate_to_datetime(str(value or ""))
    except (TypeError, ValueError):
        raise NaverNewsCollectionError("NAVER_NEWS_RESPONSE_INVALID") from None
    if parsed.tzinfo is None:
        raise NaverNewsCollectionError("NAVER_NEWS_RESPONSE_INVALID")
    return parsed.astimezone(UTC)


def _parse_items(
    rows: tuple[dict[str, Any], ...],
    *,
    query_identity: str,
    now: datetime,
    lookback_hours: int,
) -> tuple[tuple[NaverNewsItem, ...], int, int]:
    match_key = _identity(query_identity)
    if not match_key:
        raise NaverNewsCollectionError("NAVER_NEWS_QUERY_INVALID")
    items: list[NaverNewsItem] = []
    seen_urls: set[str] = set()
    irrelevant_count = 0
    unsafe_url_count = 0
    stale_before = now.astimezone(UTC) - timedelta(hours=lookback_hours)
    future_limit = now.astimezone(UTC) + timedelta(minutes=5)
    for row in rows:
        title = _plain_text(row.get("title"), 500)
        description = _plain_text(row.get("description"), 2000)
        if match_key not in _identity(f"{title} {description}"):
            irrelevant_count += 1
            continue
        source_url = _safe_https_url(row.get("originallink")) or _safe_https_url(
            row.get("link")
        )
        if source_url is None:
            unsafe_url_count += 1
            continue
        published_at = _published_at(row.get("pubDate"))
        if published_at > future_limit:
            raise NaverNewsCollectionError("NAVER_NEWS_RESPONSE_INVALID")
        if not title or source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        items.append(
            NaverNewsItem(
                title=title,
                source_url=source_url,
                source_host=urlsplit(source_url).hostname or "",
                published_at=published_at,
                matched_identity=query_identity,
                stale=published_at < stale_before,
            )
        )
    return tuple(items), irrelevant_count, unsafe_url_count


def collect_naver_news(
    settings: Settings,
    *,
    symbol: str,
    company_name: str | None,
    now: datetime,
    transport: httpx.BaseTransport | None = None,
) -> NaverNewsCollection:
    if settings.naver_news_configuration_status() != "CONFIGURED":
        raise NaverNewsCollectionError("NAVER_NEWS_NOT_CONFIGURED")
    if len(symbol) != 6 or not symbol.isdigit():
        raise NaverNewsCollectionError("NAVER_NEWS_SYMBOL_INVALID")
    query_identity = _plain_text(company_name, 200) if company_name else symbol
    if not query_identity:
        query_identity = symbol
    client_id, client_secret = settings.load_naver_news_credentials()
    fingerprint = hashlib.sha256(f"{client_id}\0{client_secret}".encode()).hexdigest()
    now_utc = now.astimezone(UTC)
    _evict_cache(active_fingerprint=fingerprint, now=now_utc)
    cache_key = (fingerprint, query_identity)
    cached = _cache.get(cache_key)
    cache_hit = bool(cached and cached[0] > now_utc)
    if cache_hit:
        assert cached is not None
        rows, returned_count = cached[1], cached[2]
    else:
        try:
            with httpx.Client(
                base_url=settings.naver_news_base_url,
                timeout=settings.naver_news_timeout_seconds,
                follow_redirects=False,
                transport=transport,
            ) as client:
                response = client.get(
                    NAVER_NEWS_PATH,
                    params={
                        "query": query_identity,
                        "display": settings.naver_news_display,
                        "start": 1,
                        "sort": "date",
                        "format": "json",
                    },
                    headers={
                        "X-NCP-APIGW-API-KEY-ID": client_id,
                        "X-NCP-APIGW-API-KEY": client_secret,
                    },
                )
                if response.status_code in {401, 403}:
                    raise NaverNewsCollectionError("NAVER_NEWS_AUTH_FAILED")
                if response.status_code == 429:
                    raise NaverNewsCollectionError("NAVER_NEWS_QUOTA_EXCEEDED")
                response.raise_for_status()
                if len(response.content) > _MAX_RESPONSE_BYTES:
                    raise NaverNewsCollectionError("NAVER_NEWS_RESPONSE_TOO_LARGE")
                try:
                    payload = response.json()
                except ValueError:
                    raise NaverNewsCollectionError(
                        "NAVER_NEWS_RESPONSE_INVALID"
                    ) from None
        except NaverNewsCollectionError:
            raise
        except httpx.TimeoutException:
            raise NaverNewsCollectionError("NAVER_NEWS_TIMED_OUT") from None
        except httpx.HTTPStatusError:
            raise NaverNewsCollectionError("NAVER_NEWS_PROVIDER_ERROR") from None
        except httpx.HTTPError:
            raise NaverNewsCollectionError("NAVER_NEWS_PROVIDER_ERROR") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise NaverNewsCollectionError("NAVER_NEWS_RESPONSE_INVALID")
        if not all(isinstance(row, dict) for row in payload["items"]):
            raise NaverNewsCollectionError("NAVER_NEWS_RESPONSE_INVALID")
        rows = tuple(payload["items"])
        returned_count = len(rows)
        _cache[cache_key] = (
            now_utc + timedelta(seconds=settings.naver_news_cache_seconds),
            rows,
            returned_count,
        )
    items, irrelevant_count, unsafe_url_count = _parse_items(
        rows,
        query_identity=query_identity,
        now=now,
        lookback_hours=settings.naver_news_lookback_hours,
    )
    return NaverNewsCollection(
        items=items,
        query_identity=query_identity,
        returned_count=returned_count,
        irrelevant_count=irrelevant_count,
        unsafe_url_count=unsafe_url_count,
        cache_hit=cache_hit,
    )
