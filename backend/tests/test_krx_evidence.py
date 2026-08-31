from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import krx as krx_module
from app.agents.dart import DartCollection, DartDisclosure
from app.agents.krx import (
    KrxCollection,
    KrxCollectionError,
    KrxDailyMarket,
    collect_krx_daily_market,
)
from app.agents.naver_news import NaverNewsCollection, NaverNewsItem
from app.agents.worker import process_agent_work_once
from app.config import Settings
from app.models import AgentRun, EvidenceBundle, EvidenceItem, TradingOrder
from tests.test_agent_runtime import _login, _market_fixture, _routes


@pytest.fixture(autouse=True)
def _clear_krx_cache() -> None:
    krx_module._daily_cache.clear()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    key_file = tmp_path / "krx_api_key"
    key_file.write_text("a" * 40, encoding="utf-8")
    return Settings(
        krx_enabled=True,
        krx_api_key_file=str(key_file),
        **overrides,
    )


def _row(symbol: str = "005930", market: str = "KOSPI") -> dict[str, str]:
    return {
        "BAS_DD": "20260810",
        "ISU_CD": symbol,
        "ISU_NM": "Samsung Electronics",
        "MKT_NM": market,
        "SECT_TP_NM": "Common Stock",
        "TDD_CLSPRC": "70000",
        "CMPPREVDD_PRC": "1000",
        "FLUC_RT": "1.45",
        "TDD_OPNPRC": "69000",
        "TDD_HGPRC": "70500",
        "TDD_LWPRC": "68800",
        "ACC_TRDVOL": "12345678",
        "ACC_TRDVAL": "864197460000",
        "MKTCAP": "417884500000000",
        "LIST_SHRS": "5969782550",
    }


def test_krx_adapter_matches_exact_symbol_and_caches_daily_market(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        rows = [_row()] if request.url.path.endswith("stk_bydd_trd") else []
        return httpx.Response(200, json={"OutBlock_1": rows})

    transport = httpx.MockTransport(handler)
    first = collect_krx_daily_market(
        _settings(tmp_path),
        symbol="005930",
        now=datetime(2026, 8, 11, 1, tzinfo=UTC),
        transport=transport,
    )
    second = collect_krx_daily_market(
        _settings(tmp_path),
        symbol="005930",
        now=datetime(2026, 8, 11, 1, tzinfo=UTC),
        transport=transport,
    )
    assert first.item is not None
    assert first.item.base_date == "20260810"
    assert first.item.market_name == "KOSPI"
    assert first.item.facts()["close_price"] == "70000"
    assert first.requests_made == 1
    assert second.requests_made == 0
    assert len(requests) == 1
    assert requests[0].headers["AUTH_KEY"] == "a" * 40
    assert requests[0].url.params["basDd"] == "20260810"


def test_krx_cache_retains_only_active_lookback_and_credential(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.url.params["basDd"]))
        return httpx.Response(200, json={"OutBlock_1": []})

    settings = _settings(tmp_path, krx_lookback_days=2)
    transport = httpx.MockTransport(handler)
    start = datetime(2026, 8, 11, 1, tzinfo=UTC)
    caplog.set_level(logging.DEBUG, logger=krx_module.__name__)

    first = collect_krx_daily_market(
        settings, symbol="005930", now=start, transport=transport
    )
    assert first.requests_made == 4
    assert len(krx_module._daily_cache) == 4
    assert collect_krx_daily_market(
        settings, symbol="005930", now=start, transport=transport
    ).requests_made == 0

    for offset in range(1, 31):
        collect_krx_daily_market(
            settings,
            symbol="005930",
            now=start + timedelta(days=offset),
            transport=transport,
        )
        assert len(krx_module._daily_cache) <= 4

    before_historical_refetch = len(requests)
    historical = collect_krx_daily_market(
        settings, symbol="005930", now=start, transport=transport
    )
    assert historical.requests_made == 4
    assert len(requests) == before_historical_refetch + 4

    key_file = tmp_path / "krx_api_key"
    key_file.write_text("b" * 40, encoding="utf-8")
    rotated = collect_krx_daily_market(
        settings, symbol="005930", now=start, transport=transport
    )
    active_fingerprint = hashlib.sha256(("b" * 40).encode()).hexdigest()
    assert rotated.requests_made == 4
    assert len(krx_module._daily_cache) == 4
    assert {key[2] for key in krx_module._daily_cache} == {active_fingerprint}
    assert "cache=krx_daily" in caplog.text
    assert "a" * 40 not in caplog.text
    assert "b" * 40 not in caplog.text


def test_krx_adapter_searches_both_markets_and_returns_normal_empty(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"OutBlock_1": []})

    result = collect_krx_daily_market(
        _settings(tmp_path, krx_lookback_days=2),
        symbol="005930",
        now=datetime(2026, 8, 11, 1, tzinfo=UTC),
        transport=httpx.MockTransport(handler),
    )
    assert result.item is None
    assert result.dates_queried == ("20260810", "20260809")
    assert result.requests_made == 4
    assert len(requests) == 4


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, json={"error": "bad key"}), "KRX_RESPONSE_INVALID"),
        (httpx.Response(503), "KRX_PROVIDER_ERROR"),
    ],
)
def test_krx_provider_failures_are_stable_and_fail_closed(
    tmp_path: Path, response: httpx.Response, code: str
) -> None:
    with pytest.raises(KrxCollectionError, match=code):
        collect_krx_daily_market(
            _settings(tmp_path),
            symbol="005930",
            now=datetime(2026, 8, 11, 1, tzinfo=UTC),
            transport=httpx.MockTransport(lambda request: response),
        )


def test_krx_primary_evidence_joins_partial_bundle_without_order(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    item = KrxDailyMarket(
        base_date="20260810",
        symbol="005930",
        name="Samsung Electronics",
        market_name="KOSPI",
        sector_type_name="Common Stock",
        close_price="70000",
        change_price="1000",
        change_rate="1.45",
        open_price="69000",
        high_price="70500",
        low_price="68800",
        trading_volume="12345678",
        trading_value="864197460000",
        market_cap="417884500000000",
        listed_shares="5969782550",
        endpoint_path="/svc/apis/sto/stk_bydd_trd",
    )
    monkeypatch.setattr("app.agents.runtime.get_settings", lambda: settings)
    monkeypatch.setattr("app.agents.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.agents.worker.collect_krx_daily_market",
        lambda settings, *, symbol, now: KrxCollection(item, ("20260810",), 1),
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
        assert process_agent_work_once(db, worker_id="krx-worker", lease_seconds=30)
    run = db.get(AgentRun, run_id)
    assert run is not None and run.state == "PARTIAL"
    evidence = db.scalar(select(EvidenceItem).where(EvidenceItem.run_id == run_id))
    assert evidence is not None
    assert evidence.source_type == "KRX_DAILY_MARKET"
    assert evidence.source_tier == "PRIMARY"
    assert evidence.source_name == "KRX_OPEN_API"
    assert "a" * 40 not in evidence.facts_json
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run_id))
    assert bundle is not None and bundle.state == "PARTIAL"
    assert bundle.evidence_ids_json == f'["{evidence.id}"]'
    assert bundle.reason_codes_json == '["KRX_PRIMARY_EVIDENCE_VERIFIED"]'
    assert db.scalar(select(TradingOrder).limit(1)) is None


def test_krx_enabled_without_valid_secret_rejects_run_admission(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_settings = Settings(krx_enabled=True)
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
    assert response.json()["error"]["code"] == "AGENT_KRX_NOT_CONFIGURED"
    assert db.scalar(select(AgentRun).limit(1)) is None


def test_dart_krx_and_news_create_verified_bundle_without_order(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dart_key = tmp_path / "dart_api_key"
    dart_key.write_text("d" * 40, encoding="utf-8")
    naver_client_id = tmp_path / "naver_client_id"
    naver_client_secret = tmp_path / "naver_client_secret"
    naver_client_id.write_text("client-id-123456", encoding="utf-8")
    naver_client_secret.write_text("client-secret-123456", encoding="utf-8")
    settings = _settings(
        tmp_path,
        dart_enabled=True,
        dart_api_key_file=str(dart_key),
        naver_news_enabled=True,
        naver_news_client_id_file=str(naver_client_id),
        naver_news_client_secret_file=str(naver_client_secret),
    )
    disclosure = DartDisclosure(
        receipt_number="20260811000001",
        corporation_code="00126380",
        stock_code="005930",
        corporation_name="Samsung Electronics",
        report_name="Material disclosure",
        submitter_name="Samsung Electronics",
        corporation_class="Y",
        receipt_date="20260811",
        correction_flag="",
    )
    market = KrxDailyMarket(
        base_date="20260810",
        symbol="005930",
        name="Samsung Electronics",
        market_name="KOSPI",
        sector_type_name="Common Stock",
        close_price="70000",
        change_price="1000",
        change_rate="1.45",
        open_price="69000",
        high_price="70500",
        low_price="68800",
        trading_volume="12345678",
        trading_value="864197460000",
        market_cap="417884500000000",
        listed_shares="5969782550",
        endpoint_path="/svc/apis/sto/stk_bydd_trd",
    )
    monkeypatch.setattr("app.agents.runtime.get_settings", lambda: settings)
    monkeypatch.setattr("app.agents.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.agents.worker.collect_dart_disclosures",
        lambda settings, *, symbol, now: DartCollection(
            (disclosure,), "20260809", "20260811", 1
        ),
    )
    monkeypatch.setattr(
        "app.agents.worker.collect_krx_daily_market",
        lambda settings, *, symbol, now: KrxCollection(market, ("20260810",), 1),
    )
    news = NaverNewsItem(
        title="Samsung Electronics update",
        source_url="https://news.example.com/samsung",
        source_host="news.example.com",
        published_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
        matched_identity="Samsung Electronics",
        stale=False,
    )
    monkeypatch.setattr(
        "app.agents.worker.collect_naver_news",
        lambda settings, *, symbol, company_name, now: NaverNewsCollection(
            items=(news,),
            query_identity=company_name or symbol,
            returned_count=1,
            irrelevant_count=0,
            unsafe_url_count=0,
            cache_hit=False,
        ),
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
        assert process_agent_work_once(db, worker_id="multi-source-worker", lease_seconds=30)
    evidence = list(
        db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.run_id == run_id)
            .order_by(EvidenceItem.source_type)
        )
    )
    assert [item.source_type for item in evidence] == [
        "DART_DISCLOSURE",
        "KRX_DAILY_MARKET",
        "NEWS",
    ]
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run_id))
    assert bundle is not None and bundle.state == "VERIFIED"
    assert set(json.loads(bundle.evidence_ids_json)) == {
        item.id for item in evidence
    }
    assert json.loads(bundle.reason_codes_json) == [
        "DART_PRIMARY_EVIDENCE_VERIFIED",
        "KRX_PRIMARY_EVIDENCE_VERIFIED",
        "NAVER_NEWS_SECONDARY_EVIDENCE_VERIFIED",
    ]
    assert db.scalar(select(TradingOrder).limit(1)) is None
