from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import naver_news as news_module
from app.agents.naver_news import (
    NaverNewsCollection,
    NaverNewsCollectionError,
    NaverNewsItem,
    collect_naver_news,
)
from app.agents.worker import process_agent_work_once
from app.config import Settings
from app.models import AgentRun, EvidenceBundle, EvidenceItem, TradingOrder
from tests.test_agent_runtime import _login, _market_fixture, _routes


@pytest.fixture(autouse=True)
def _clear_news_cache() -> None:
    news_module._cache.clear()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    client_id = tmp_path / "naver_client_id"
    client_secret = tmp_path / "naver_client_secret"
    client_id.write_text("client-id-123456", encoding="utf-8")
    client_secret.write_text("client-secret-1234567890", encoding="utf-8")
    return Settings(
        naver_news_enabled=True,
        naver_news_client_id_file=str(client_id),
        naver_news_client_secret_file=str(client_secret),
        **overrides,
    )


def _row(
    *,
    title: str,
    description: str,
    url: str,
    published: str,
) -> dict[str, str]:
    return {
        "title": title,
        "description": description,
        "originallink": url,
        "link": url,
        "pubDate": published,
    }


def test_naver_news_filters_relevance_url_freshness_and_caches(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    rows = [
        _row(
            title="<b>Samsung Electronics</b> announces new investment",
            description="Samsung Electronics expands production.",
            url="https://news.example.com/articles/1#tracking",
            published="Tue, 11 Aug 2026 20:00:00 +0900",
        ),
        _row(
            title="Samsung Electronics historical article",
            description="Samsung Electronics archive.",
            url="https://news.example.com/articles/2",
            published="Fri, 07 Aug 2026 10:00:00 +0900",
        ),
        _row(
            title="Unrelated market story",
            description="No target identity.",
            url="https://news.example.com/articles/3",
            published="Tue, 11 Aug 2026 19:00:00 +0900",
        ),
        _row(
            title="Samsung Electronics insecure link",
            description="Samsung Electronics update.",
            url="http://news.example.com/articles/4",
            published="Tue, 11 Aug 2026 18:00:00 +0900",
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": rows, "total": len(rows)})

    settings = _settings(tmp_path)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    first = collect_naver_news(
        settings,
        symbol="005930",
        company_name="Samsung Electronics",
        now=now,
        transport=httpx.MockTransport(handler),
    )
    second = collect_naver_news(
        settings,
        symbol="005930",
        company_name="Samsung Electronics",
        now=now,
        transport=httpx.MockTransport(handler),
    )
    assert len(first.fresh_items) == 1
    assert len(first.stale_items) == 1
    assert first.irrelevant_count == 1
    assert first.unsafe_url_count == 1
    assert first.items[0].title == "Samsung Electronics announces new investment"
    assert first.items[0].source_url == "https://news.example.com/articles/1"
    assert not first.cache_hit and second.cache_hit
    assert len(requests) == 1
    assert requests[0].url.path == "/search/v1/news"
    assert requests[0].url.params["sort"] == "date"
    assert requests[0].headers["X-NCP-APIGW-API-KEY-ID"] == "client-id-123456"
    assert requests[0].headers["X-NCP-APIGW-API-KEY"] == "client-secret-1234567890"


def test_naver_news_cache_globally_expires_and_rotates_credentials(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["query"])
        return httpx.Response(200, json={"items": []})

    settings = _settings(tmp_path, naver_news_cache_seconds=60)
    transport = httpx.MockTransport(handler)
    start = datetime(2026, 8, 11, 12, tzinfo=UTC)
    caplog.set_level(logging.DEBUG, logger=news_module.__name__)

    first = collect_naver_news(
        settings,
        symbol="005930",
        company_name="first company",
        now=start,
        transport=transport,
    )
    assert first.cache_hit is False
    assert collect_naver_news(
        settings,
        symbol="005930",
        company_name="first company",
        now=start + timedelta(seconds=30),
        transport=transport,
    ).cache_hit is True
    collect_naver_news(
        settings,
        symbol="005930",
        company_name="second company",
        now=start + timedelta(seconds=30),
        transport=transport,
    )
    assert len(news_module._cache) == 2

    collect_naver_news(
        settings,
        symbol="005930",
        company_name="third company",
        now=start + timedelta(seconds=61),
        transport=transport,
    )
    assert {key[1] for key in news_module._cache} == {
        "second company",
        "third company",
    }

    for index in range(30):
        collect_naver_news(
            settings,
            symbol="005930",
            company_name=f"rolling company {index}",
            now=start + timedelta(seconds=122 + index * 61),
            transport=transport,
        )
        assert len(news_module._cache) == 1

    (tmp_path / "naver_client_id").write_text(
        "rotated-client-id", encoding="utf-8"
    )
    (tmp_path / "naver_client_secret").write_text(
        "rotated-client-secret", encoding="utf-8"
    )
    collect_naver_news(
        settings,
        symbol="005930",
        company_name="rotated company",
        now=start + timedelta(hours=1),
        transport=transport,
    )
    active_fingerprint = hashlib.sha256(
        b"rotated-client-id\0rotated-client-secret"
    ).hexdigest()
    assert len(news_module._cache) == 1
    assert {key[0] for key in news_module._cache} == {active_fingerprint}
    assert "cache=naver_news" in caplog.text
    assert "rotated-client-id" not in caplog.text
    assert "rotated company" not in caplog.text


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, "NAVER_NEWS_AUTH_FAILED"),
        (403, "NAVER_NEWS_AUTH_FAILED"),
        (429, "NAVER_NEWS_QUOTA_EXCEEDED"),
        (500, "NAVER_NEWS_PROVIDER_ERROR"),
    ],
)
def test_naver_news_provider_failures_are_stable_and_fail_closed(
    tmp_path: Path, status_code: int, code: str
) -> None:
    with pytest.raises(NaverNewsCollectionError, match=code):
        collect_naver_news(
            _settings(tmp_path),
            symbol="005930",
            company_name="Samsung Electronics",
            now=datetime(2026, 8, 11, 12, tzinfo=UTC),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, json={"error": "redacted"})
            ),
        )


def test_naver_news_future_timestamp_is_invalid(tmp_path: Path) -> None:
    with pytest.raises(NaverNewsCollectionError, match="NAVER_NEWS_RESPONSE_INVALID"):
        collect_naver_news(
            _settings(tmp_path),
            symbol="005930",
            company_name="Samsung Electronics",
            now=datetime(2026, 8, 11, 12, tzinfo=UTC),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "items": [
                            _row(
                                title="Samsung Electronics future",
                                description="Samsung Electronics",
                                url="https://news.example.com/future",
                                published="Wed, 12 Aug 2026 10:00:00 +0900",
                            )
                        ]
                    },
                )
            ),
        )


def test_naver_news_secondary_evidence_stays_partial_without_primary_coverage(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    published = datetime(2026, 8, 11, 11, tzinfo=UTC)
    item = NaverNewsItem(
        title="Samsung Electronics update",
        source_url="https://news.example.com/samsung",
        source_host="news.example.com",
        published_at=published,
        matched_identity="Samsung Electronics",
        stale=False,
    )
    collection = NaverNewsCollection(
        items=(item,),
        query_identity="Samsung Electronics",
        returned_count=1,
        irrelevant_count=0,
        unsafe_url_count=0,
        cache_hit=False,
    )
    monkeypatch.setattr("app.agents.runtime.get_settings", lambda: settings)
    monkeypatch.setattr("app.agents.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.agents.worker.collect_naver_news",
        lambda settings, *, symbol, company_name, now: collection,
    )
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": _routes(client, headers),
        },
    )
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    for _ in range(8):
        assert process_agent_work_once(db, worker_id="news-worker", lease_seconds=30)
    run = db.get(AgentRun, run_id)
    assert run is not None and run.state == "PARTIAL"
    evidence = db.scalar(select(EvidenceItem).where(EvidenceItem.run_id == run_id))
    assert evidence is not None
    assert evidence.source_type == "NEWS"
    assert evidence.source_tier == "SECONDARY"
    assert evidence.source_name == "NAVER_API_HUB_NEWS"
    assert "description" not in evidence.facts_json
    assert "client-secret" not in evidence.facts_json
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run_id))
    assert bundle is not None and bundle.state == "PARTIAL"
    assert bundle.evidence_ids_json == f'["{evidence.id}"]'
    assert bundle.reason_codes_json == '["NAVER_NEWS_SECONDARY_EVIDENCE_VERIFIED"]'
    assert db.scalar(select(TradingOrder).limit(1)) is None


def test_naver_news_enabled_without_valid_pair_rejects_run_admission(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_settings = Settings(naver_news_enabled=True)
    monkeypatch.setattr("app.agents.runtime.get_settings", lambda: invalid_settings)
    _market_fixture(db)
    csrf = _login(client)
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    response = client.post(
        "/api/v1/ai/agent-runs/diagnostic",
        headers=headers,
        json={
            "schema_version": "1.0",
            "market": "KRX",
            "symbol": "005930",
            "route_ids": _routes(client, headers),
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_NAVER_NEWS_NOT_CONFIGURED"
    assert db.scalar(select(AgentRun).limit(1)) is None
