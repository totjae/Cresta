from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.broker.kiwoom import KiwoomAdapterError, KiwoomMockClient, normalize_basic_quote
from app.config import Settings


@dataclass
class FakeResponse:
    status_code: int
    payload: Any

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeHttpClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def configured_settings(tmp_path: Path) -> Settings:
    files = []
    for name, value in (("key", "test-key"), ("secret", "test-secret"), ("account", "12345678")):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        files.append(str(path))
    return Settings(
        kiwoom_enabled=True,
        kiwoom_app_key_file=files[0],
        kiwoom_app_secret_file=files[1],
        kiwoom_account_id_file=files[2],
    )


def token_response(token: str, expires: str = "20260801120000") -> FakeResponse:
    return FakeResponse(
        200,
        {
            "expires_dt": expires,
            "token_type": "bearer",
            "token": token,
            "return_code": 0,
            "return_msg": "success",
        },
    )


def quote_response() -> FakeResponse:
    return FakeResponse(
        200,
        {
            "stk_cd": "005930",
            "cur_prc": "-70100",
            "open_pric": "+70000",
            "high_pric": "+70200",
            "low_pric": "-69900",
            "trde_qty": "12345",
            "return_code": 0,
        },
    )


def test_token_is_kst_parsed_memory_only_and_reused(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient([token_response("memory-token")])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    assert client.get_access_token() == "memory-token"
    assert client.get_access_token() == "memory-token"
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == "https://mockapi.kiwoom.com/oauth2/token"
    assert http.calls[0]["json"]["grant_type"] == "client_credentials"


def test_concurrent_token_requests_issue_once(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient([token_response("shared-token")])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: client.get_access_token(), range(16)))

    assert tokens == ["shared-token"] * 16
    assert len(http.calls) == 1


def test_token_refreshes_inside_sixty_minute_buffer(tmp_path: Path) -> None:
    clock = [datetime(2026, 8, 1, 0, 0, tzinfo=UTC)]
    http = FakeHttpClient(
        [token_response("first", "20260801110000"), token_response("second", "20260801130000")]
    )
    client = KiwoomMockClient(
        configured_settings(tmp_path), http_client=http, clock=lambda: clock[0]
    )

    assert client.get_access_token() == "first"
    clock[0] += timedelta(hours=1, minutes=1)
    assert client.get_access_token() == "second"
    assert len(http.calls) == 2


def test_rest_request_reauthenticates_once_on_401(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient(
        [token_response("old"), FakeResponse(401, {}), token_response("new"), quote_response()]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    event = client.get_basic_quote("005930", trading_status="TRADING", received_at=now)

    assert event.last_price == 70100
    assert event.recovery_snapshot is True
    quote_calls = [call for call in http.calls if call["url"].endswith("/api/dostk/stkinfo")]
    assert len(quote_calls) == 2
    assert quote_calls[0]["headers"]["authorization"] == "Bearer old"
    assert quote_calls[1]["headers"]["authorization"] == "Bearer new"
    assert quote_calls[1]["headers"]["api-id"] == "ka10001"


def test_rest_request_stops_after_second_401(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient(
        [token_response("old"), FakeResponse(401, {}), token_response("new"), FakeResponse(401, {})]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    with pytest.raises(KiwoomAdapterError) as failure:
        client.get_basic_quote("005930", trading_status="TRADING", received_at=now)
    assert failure.value.code == "KIWOOM_AUTH_FAILED"
    assert len(http.calls) == 4


def test_basic_quote_normalization_is_strict_and_deterministic() -> None:
    observed = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    payload = quote_response().payload
    first = normalize_basic_quote(
        payload, symbol="005930", trading_status="PRE_MARKET", received_at=observed
    )
    second = normalize_basic_quote(
        payload, symbol="005930", trading_status="PRE_MARKET", received_at=observed
    )

    assert first.sequence_or_hash == second.sequence_or_hash
    assert first.open_price == 70000
    assert first.high_price == 70200
    assert first.low_price == 69900
    assert first.cumulative_volume == 12345
    assert first.market == "KRX"

    with pytest.raises(KiwoomAdapterError) as invalid_price:
        normalize_basic_quote(
            {**payload, "cur_prc": "0"},
            symbol="005930",
            trading_status="TRADING",
            received_at=observed,
        )
    assert invalid_price.value.code == "KIWOOM_INVALID_RESPONSE"

    with pytest.raises(KiwoomAdapterError) as mismatch:
        normalize_basic_quote(
            payload,
            symbol="000660",
            trading_status="TRADING",
            received_at=observed,
        )
    assert mismatch.value.code == "KIWOOM_SYMBOL_MISMATCH"


def test_invalid_or_rejected_response_never_becomes_quote(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient(
        [token_response("token"), FakeResponse(200, {"return_code": 17, "return_msg": "secret"})]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    with pytest.raises(KiwoomAdapterError) as rejected:
        client.get_basic_quote("005930", trading_status="TRADING", received_at=now)
    assert rejected.value.code == "KIWOOM_API_ERROR"
    assert "secret" not in rejected.value.message


def test_non_json_response_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient([token_response("token"), FakeResponse(200, ValueError("not json"))])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    with pytest.raises(KiwoomAdapterError) as invalid:
        client.get_basic_quote("005930", trading_status="TRADING", received_at=now)
    assert invalid.value.code == "KIWOOM_INVALID_RESPONSE"
