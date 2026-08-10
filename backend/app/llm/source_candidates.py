from __future__ import annotations

from collections.abc import Iterable
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from app.llm.contracts import EvidenceSourceCandidate


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 1000:
        return None
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname.lower() == "localhost"
        ):
            return None
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    except (TypeError, ValueError):
        return None


def _candidate(value: object) -> EvidenceSourceCandidate | None:
    if isinstance(value, str):
        url = _safe_https_url(value)
        return EvidenceSourceCandidate(url=url, title="") if url else None
    if not isinstance(value, dict):
        return None
    nested = value.get("url_citation")
    if isinstance(nested, dict):
        value = {**value, **nested}
    url = _safe_https_url(value.get("url") or value.get("uri") or value.get("link"))
    if url is None:
        return None
    raw_title = value.get("title") or value.get("name") or ""
    title = str(raw_title).strip()[:500]
    published = value.get("published_at") or value.get("publishedAt") or value.get("date")
    return EvidenceSourceCandidate(
        url=url,
        title=title,
        published_at=str(published)[:64] if published is not None else None,
    )


def candidates_from_values(values: Iterable[object]) -> list[EvidenceSourceCandidate]:
    candidates: list[EvidenceSourceCandidate] = []
    seen: set[str] = set()
    for value in values:
        item = _candidate(value)
        if item is None or item.url in seen:
            continue
        seen.add(item.url)
        candidates.append(item)
        if len(candidates) >= 20:
            break
    return candidates
