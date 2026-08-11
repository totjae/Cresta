from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import dart as dart_module
from app.agents.dart import (
    DartCollection,
    DartCollectionError,
    DartDisclosure,
    collect_dart_disclosures,
)
from app.agents.worker import process_agent_work_once
from app.config import Settings
from app.models import AgentRun, EvidenceBundle, EvidenceItem, TradingOrder
from tests.test_agent_runtime import _login, _market_fixture, _routes


@pytest.fixture(autouse=True)
def _clear_dart_cache() -> None:
    dart_module._corp_code_cache.clear()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    key_file = tmp_path / "dart_api_key"
    key_file.write_text("d" * 40, encoding="utf-8")
    return Settings(
        dart_enabled=True,
        dart_api_key_file=str(key_file),
        **overrides,
    )


def _corp_code_zip() -> bytes:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list>
  <list><corp_code>00000001</corp_code><stock_code>000001</stock_code></list>
</result>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("CORPCODE.xml", payload)
    return buffer.getvalue()


def test_dart_adapter_filters_exact_symbol_and_paginates_without_exposing_key(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/corpCode.xml":
            return httpx.Response(200, content=_corp_code_zip())
        page = request.url.params["page_no"]
        rows = [
            {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "stock_code": "005930",
                "corp_cls": "Y",
                "report_nm": "주요사항보고서",
                "rcept_no": f"2026081100000{page}",
                "flr_nm": "삼성전자",
                "rcept_dt": "20260811",
                "rm": "",
            },
            {
                "corp_code": "00000001",
                "corp_name": "다른회사",
                "stock_code": "000001",
                "corp_cls": "K",
                "report_nm": "무관 공시",
                "rcept_no": f"2026081100001{page}",
                "flr_nm": "다른회사",
                "rcept_dt": "20260811",
                "rm": "",
            },
        ]
        return httpx.Response(
            200,
            json={"status": "000", "total_page": 2, "list": rows},
        )

    result = collect_dart_disclosures(
        _settings(tmp_path),
        symbol="005930",
        now=datetime(2026, 8, 11, 1, tzinfo=UTC),
        transport=httpx.MockTransport(handler),
    )
    assert result.start_date == "20260809"
    assert result.end_date == "20260811"
    assert result.pages_fetched == 2
    assert [item.receipt_number for item in result.disclosures] == [
        "20260811000001",
        "20260811000002",
    ]
    assert all(item.stock_code == "005930" for item in result.disclosures)
    assert all(request.url.host == "opendart.fss.or.kr" for request in requests)
    assert all(request.url.params["crtfc_key"] == "d" * 40 for request in requests)
    list_requests = [request for request in requests if request.url.path == "/api/list.json"]
    assert all(request.url.params["corp_code"] == "00126380" for request in list_requests)


def test_dart_no_data_is_successful_empty_coverage(tmp_path: Path) -> None:
    result = collect_dart_disclosures(
        _settings(tmp_path),
        symbol="005930",
        now=datetime(2026, 8, 11, 1, tzinfo=UTC),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=_corp_code_zip())
            if request.url.path == "/api/corpCode.xml"
            else httpx.Response(200, json={"status": "013", "message": "no data"})
        ),
    )
    assert result.disclosures == ()
    assert result.pages_fetched == 0


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"status": "010", "message": "invalid key"}, "DART_STATUS_010"),
        ({"status": "000", "total_page": 11, "list": []}, "DART_PAGE_LIMIT_EXCEEDED"),
    ],
)
def test_dart_provider_failures_are_stable_and_fail_closed(
    tmp_path: Path, payload: dict[str, object], code: str
) -> None:
    with pytest.raises(DartCollectionError, match=code):
        collect_dart_disclosures(
            _settings(tmp_path),
            symbol="005930",
            now=datetime(2026, 8, 11, 1, tzinfo=UTC),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=_corp_code_zip())
                if request.url.path == "/api/corpCode.xml"
                else httpx.Response(200, json=payload)
            ),
        )


def test_dart_corporation_code_auth_error_preserves_safe_status(tmp_path: Path) -> None:
    with pytest.raises(DartCollectionError, match="DART_STATUS_010"):
        collect_dart_disclosures(
            _settings(tmp_path),
            symbol="005930",
            now=datetime(2026, 8, 11, 1, tzinfo=UTC),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"status": "010", "message": "invalid key"},
                )
            ),
        )


def test_dart_primary_evidence_enters_partial_bundle_without_creating_order(
    client: TestClient,
    db: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    dart_settings = _settings(tmp_path)
    disclosure = DartDisclosure(
        receipt_number="20260811000001",
        corporation_code="00126380",
        stock_code="005930",
        corporation_name="삼성전자",
        report_name="주요사항보고서",
        submitter_name="삼성전자",
        corporation_class="Y",
        receipt_date="20260811",
        correction_flag="",
    )
    collection = DartCollection((disclosure,), "20260809", "20260811", 1)
    monkeypatch.setattr("app.agents.runtime.get_settings", lambda: dart_settings)
    monkeypatch.setattr("app.agents.worker.get_settings", lambda: dart_settings)
    monkeypatch.setattr(
        "app.agents.worker.collect_dart_disclosures",
        lambda settings, *, symbol, now: collection,
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
        assert process_agent_work_once(db, worker_id="dart-worker", lease_seconds=30)
    run = db.get(AgentRun, run_id)
    assert run is not None
    assert run.state == "PARTIAL"
    evidence = db.scalar(select(EvidenceItem).where(EvidenceItem.run_id == run_id))
    assert evidence is not None
    assert evidence.source_type == "DART_DISCLOSURE"
    assert evidence.source_tier == "PRIMARY"
    assert evidence.source_name == "OPENDART"
    assert "d" * 40 not in evidence.facts_json
    bundle = db.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run_id))
    assert bundle is not None
    assert bundle.state == "PARTIAL"
    assert bundle.evidence_ids_json == f'["{evidence.id}"]'
    assert bundle.reason_codes_json == '["DART_PRIMARY_EVIDENCE_VERIFIED"]'
    assert db.scalar(select(TradingOrder).limit(1)) is None


def test_dart_enabled_without_valid_secret_rejects_run_admission(
    client: TestClient,
    db: Session,
    monkeypatch,
) -> None:
    invalid_settings = Settings(dart_enabled=True)
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
    assert response.json()["error"]["code"] == "AGENT_DART_NOT_CONFIGURED"
    assert db.scalar(select(AgentRun).limit(1)) is None
