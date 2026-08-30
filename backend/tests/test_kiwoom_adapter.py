from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.broker.kiwoom import (
    ACCOUNT_FUNDS_API_ID,
    ACCOUNT_PATH,
    ORDER_CAPACITY_API_ID,
    KiwoomAdapterError,
    KiwoomCancelRequest,
    KiwoomMockClient,
    KiwoomOrderOutcomeUnknownError,
    KiwoomOrderRateLimiter,
    KiwoomOrderRejectedError,
    KiwoomOrderRequest,
    KiwoomReplaceRequest,
    OrderCapacityRequest,
    normalize_account_funds,
    normalize_basic_quote,
    normalize_open_order,
    normalize_position,
    normalize_signed_integer,
)
from app.config import Settings


@dataclass
class FakeResponse:
    status_code: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)

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
    for name, value in (
        ("key", "test-key"),
        ("secret", "test-secret"),
        ("account", "1234567890"),
    ):
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        files.append(str(path))
    return Settings(
        kiwoom_enabled=True,
        kiwoom_app_key_file=files[0],
        kiwoom_app_secret_file=files[1],
        kiwoom_account_id_file=files[2],
    )


def token_response(token: str, expires: str = "20991231235959") -> FakeResponse:
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


def account_response(account: Any = "1234567890") -> FakeResponse:
    return FakeResponse(200, {"acctNo": account, "return_code": 0})


def snapshot_responses() -> list[FakeResponse]:
    return [
        FakeResponse(
            200,
            {
                "return_code": 0,
                "oso": [
                    {
                        "ord_no": "1234567",
                        "stk_cd": "005930",
                        "ord_qty": "10",
                        "oso_qty": "4",
                        "ord_pric": "70000",
                        "trde_tp": "2",
                        "tm": "101500",
                    }
                ],
            },
        ),
        FakeResponse(
            200,
            {
                "return_code": 0,
                "cntr": [
                    {
                        "ord_no": "1234567",
                        "stk_cd": "005930",
                        "cntr_qty": "6",
                        "cntr_pric": "69900",
                        "tdy_trde_cmsn": "10",
                        "tdy_trde_tax": "0",
                        "trde_tp": "2",
                        "ord_tm": "101500",
                    }
                ],
            },
        ),
        FakeResponse(
            200,
            {
                "return_code": 0,
                "acnt_evlt_remn_indv_tot": [
                    {
                        "stk_cd": "A005930",
                        "rmnd_qty": "+0000000000010",
                        "trde_able_qty": "+0000000000008",
                        "pur_pric": "+000000000070000",
                    }
                ],
            },
        ),
    ]


OFFICIAL_SCHEMA_FIXTURE_KT00001 = {
    "entr": "0000010000",
    "ord_alow_amt": "0000009000",
    "pymn_alow_amt": "-0000000100",
    "d1_entra": "0000008000",
    "d1_buy_exct_amt": "0000001000",
    "d1_sel_exct_amt": "0000000200",
    "d1_pymn_alow_amt": "0000007000",
    "d2_entra": "0000006000",
    "d2_buy_exct_amt": "0000000300",
    "d2_sel_exct_amt": "0000000400",
    "d2_pymn_alow_amt": "0000005000",
    "return_code": 0,
}


OFFICIAL_SCHEMA_FIXTURE_KT00010 = {
    "ord_alowa": "0000700000",
    "entr": "0000800000",
    "wthd_alowa": "0000600000",
    "nxdy_wthd_alowa": "0000500000",
    "d2entra": "0000400000",
    "profa_20ord_alow_amt": "0001000000",
    "profa_20ord_alowq": "0000000014",
    "profa_30ord_alow_amt": "0000900000",
    "profa_30ord_alowq": "0000000012",
    "profa_40ord_alow_amt": "0000850000",
    "profa_40ord_alowq": "0000000011",
    "profa_50ord_alow_amt": "0000800000",
    "profa_50ord_alowq": "0000000010",
    "profa_60ord_alow_amt": "0000750000",
    "profa_60ord_alowq": "0000000009",
    "profa_rdex_60ord_alow_amt": "0000720000",
    "profa_rdex_60ord_alowq": "0000000008",
    "profa_100ord_alow_amt": "0000700000",
    "profa_100ord_alowq": "0000000007",
    "return_code": 0,
}


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


def test_account_query_uses_official_contract_and_masks_verified_account(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient([token_response("token"), account_response()])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    verification = client.verify_account()

    assert verification.status == "ACCOUNT_VERIFIED"
    assert verification.masked_account == "********90"
    account_call = http.calls[1]
    assert account_call["url"] == "https://mockapi.kiwoom.com/api/dostk/acnt"
    assert account_call["headers"]["api-id"] == "ka00001"
    assert account_call["json"] == {}


@pytest.mark.parametrize("account", [None, "", "12345678", "12345678901", "12345678AB"])
def test_account_query_rejects_missing_or_non_ten_digit_response(
    tmp_path: Path, account: Any
) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient([token_response("token"), account_response(account)])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    with pytest.raises(KiwoomAdapterError) as invalid:
        client.get_account_number()
    assert invalid.value.code == "KIWOOM_INVALID_RESPONSE"


def test_account_verification_rejects_mismatch_without_exposing_accounts(tmp_path: Path) -> None:
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    http = FakeHttpClient([token_response("token"), account_response("9999999999")])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    with pytest.raises(KiwoomAdapterError) as mismatch:
        client.verify_account()
    assert mismatch.value.code == "KIWOOM_ACCOUNT_MISMATCH"
    assert "1234567890" not in mismatch.value.message
    assert "9999999999" not in mismatch.value.message


def test_account_verification_rejects_eight_digit_prefix(tmp_path: Path) -> None:
    settings = configured_settings(tmp_path)
    Path(settings.kiwoom_account_id_file or "").write_text("12345678", encoding="utf-8")
    client = KiwoomMockClient(settings, http_client=FakeHttpClient([]))

    with pytest.raises(KiwoomAdapterError) as invalid:
        client.verify_account()
    assert invalid.value.code == "KIWOOM_ACCOUNT_ID_INVALID"


def test_account_snapshot_uses_official_contract_and_normalizes_all_sections(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 1, 1, 30, tzinfo=UTC)
    http = FakeHttpClient([token_response("token"), *snapshot_responses()])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    snapshot = client.get_account_snapshot()

    assert snapshot.observed_at == now
    assert snapshot.open_orders[0].filled_quantity == 6
    assert snapshot.open_orders[0].remaining_quantity == 4
    assert snapshot.fills[0].quantity == 6
    assert snapshot.positions[0].symbol == "005930"
    assert snapshot.positions[0].available_quantity == 8
    api_calls = http.calls[1:]
    assert [call["headers"]["api-id"] for call in api_calls] == [
        "ka10075",
        "ka10076",
        "kt00018",
    ]
    assert api_calls[0]["json"] == {
        "all_stk_tp": "0",
        "trde_tp": "0",
        "stk_cd": "",
        "stex_tp": "1",
    }
    assert api_calls[2]["json"] == {"qry_tp": "1", "dmst_stex_tp": "KRX"}


def test_continuation_headers_are_forwarded_and_pages_are_combined(tmp_path: Path) -> None:
    http = FakeHttpClient(
        [
            token_response("token"),
            FakeResponse(
                200, {"return_code": 0, "items": [1]}, {"cont-yn": "Y", "next-key": "page-2"}
            ),
            FakeResponse(200, {"return_code": 0, "items": [2]}),
        ]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    pages = client.request_all_pages(api_id="ka10075", path=ACCOUNT_PATH, body={})

    assert [page["items"] for page in pages] == [[1], [2]]
    assert http.calls[2]["headers"]["cont-yn"] == "Y"
    assert http.calls[2]["headers"]["next-key"] == "page-2"


def test_repeated_continuation_key_fails_closed(tmp_path: Path) -> None:
    page = FakeResponse(200, {"return_code": 0}, {"cont-yn": "Y", "next-key": "same"})
    http = FakeHttpClient([token_response("token"), page, page])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomAdapterError) as invalid:
        client.request_all_pages(api_id="ka10075", path=ACCOUNT_PATH, body={})
    assert invalid.value.code == "KIWOOM_INVALID_PAGINATION"


def test_continuation_page_limit_fails_closed(tmp_path: Path) -> None:
    pages = [
        FakeResponse(
            200,
            {"return_code": 0},
            {"cont-yn": "Y", "next-key": f"page-{index}"},
        )
        for index in range(1, 21)
    ]
    http = FakeHttpClient([token_response("token"), *pages])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomAdapterError) as limit:
        client.request_all_pages(api_id="ka10075", path=ACCOUNT_PATH, body={})
    assert limit.value.code == "KIWOOM_INVALID_PAGINATION"
    assert len(http.calls) == 21


def test_snapshot_normalization_rejects_unsafe_quantity_and_non_stock_symbol() -> None:
    with pytest.raises(KiwoomAdapterError) as quantity:
        normalize_open_order(
            {
                "ord_no": "1234567",
                "stk_cd": "005930",
                "ord_qty": "4",
                "oso_qty": "5",
                "ord_pric": "70000",
                "trde_tp": "2",
                "tm": "101500",
            }
        )
    assert quantity.value.code == "KIWOOM_INVALID_RESPONSE"

    with pytest.raises(KiwoomAdapterError) as unsupported:
        normalize_position(
            {
                "stk_cd": "Q005930",
                "rmnd_qty": "1",
                "trde_able_qty": "1",
                "pur_pric": "70000",
            }
        )
    assert unsupported.value.code == "KIWOOM_INVALID_RESPONSE"


def test_account_snapshot_rejects_duplicate_broker_order_identity(tmp_path: Path) -> None:
    responses = snapshot_responses()
    responses[0].payload["oso"].append(dict(responses[0].payload["oso"][0]))
    http = FakeHttpClient([token_response("token"), *responses])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomAdapterError) as duplicate:
        client.get_account_snapshot()
    assert duplicate.value.code == "KIWOOM_INVALID_RESPONSE"


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


def test_new_buy_and_sell_orders_use_official_contract(tmp_path: Path) -> None:
    http = FakeHttpClient(
        [
            token_response("token"),
            FakeResponse(200, {"return_code": 0, "ord_no": "1234567", "dmst_stex_tp": "KRX"}),
            FakeResponse(200, {"return_code": 0, "ord_no": "1234568", "dmst_stex_tp": "KRX"}),
        ]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    buy = client.place_order(
        KiwoomOrderRequest("005930", "BUY", 2, "LIMIT", Decimal(70000))
    )
    sell = client.place_order(
        KiwoomOrderRequest("005930", "SELL", 1, "MARKET", None)
    )

    assert buy.broker_order_id == "1234567"
    assert sell.broker_order_id == "1234568"
    assert http.calls[1]["headers"]["api-id"] == "kt10000"
    assert http.calls[1]["json"] == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "005930",
        "ord_qty": "2",
        "ord_uv": "70000",
        "trde_tp": "0",
        "cond_uv": "",
    }
    assert http.calls[2]["headers"]["api-id"] == "kt10001"
    assert http.calls[2]["json"]["ord_uv"] == ""
    assert http.calls[2]["json"]["trde_tp"] == "3"


def test_replace_and_cancel_orders_use_official_contract(tmp_path: Path) -> None:
    http = FakeHttpClient(
        [
            token_response("token"),
            FakeResponse(
                200,
                {
                    "return_code": 0,
                    "ord_no": "2234567",
                    "base_orig_ord_no": "1234567",
                    "mdfy_qty": "+000000000002",
                    "dmst_stex_tp": "KRX",
                },
            ),
            FakeResponse(
                200,
                {
                    "return_code": 0,
                    "ord_no": "3234567",
                    "base_orig_ord_no": "1234567",
                    "cncl_qty": "+000000000000",
                },
            ),
        ]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    replaced = client.replace_order(
        KiwoomReplaceRequest("1234567", "005930", 2, Decimal(70100))
    )
    cancelled = client.cancel_order(KiwoomCancelRequest("1234567", "005930"))

    assert replaced.affected_quantity == 2
    assert cancelled.affected_quantity == 0
    assert http.calls[1]["headers"]["api-id"] == "kt10002"
    assert http.calls[1]["json"]["mdfy_qty"] == "2"
    assert http.calls[2]["headers"]["api-id"] == "kt10003"
    assert http.calls[2]["json"]["cncl_qty"] == "0"


@pytest.mark.parametrize(
    "order_request,code",
    [
        (KiwoomOrderRequest("005930", "BUY", 1, "LIMIT", Decimal(70000), "NXT"), "UNSUPPORTED_IN_MOCK"),
        (KiwoomOrderRequest("A00593", "BUY", 1, "LIMIT", Decimal(70000)), "KIWOOM_INVALID_SYMBOL"),
        (KiwoomOrderRequest("005930", "BUY", 0, "LIMIT", Decimal(70000)), "KIWOOM_INVALID_ORDER_QUANTITY"),
        (KiwoomOrderRequest("005930", "BUY", 1, "LIMIT", Decimal("70000.5")), "KIWOOM_INVALID_ORDER_PRICE"),
        (KiwoomOrderRequest("005930", "BUY", 1, "MARKET", Decimal(70000)), "KIWOOM_INVALID_ORDER_PRICE"),
    ],
)
def test_invalid_new_order_is_blocked_before_http(
    tmp_path: Path, order_request: KiwoomOrderRequest, code: str
) -> None:
    http = FakeHttpClient([])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomAdapterError) as invalid:
        client.place_order(order_request)

    assert invalid.value.code == code
    assert http.calls == []


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(401, {}),
        FakeResponse(500, {}),
        FakeResponse(200, ValueError("not json")),
        FakeResponse(200, {"return_code": 0, "ord_no": "bad", "dmst_stex_tp": "KRX"}),
    ],
)
def test_ambiguous_order_response_is_not_retried(
    tmp_path: Path, response: FakeResponse
) -> None:
    http = FakeHttpClient([token_response("token"), response])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomOrderOutcomeUnknownError):
        client.place_order(
            KiwoomOrderRequest("005930", "BUY", 1, "LIMIT", Decimal(70000))
        )

    order_calls = [call for call in http.calls if call["url"].endswith("/api/dostk/ordr")]
    assert len(order_calls) == 1


def test_explicit_order_rejection_is_distinct_and_not_retried(tmp_path: Path) -> None:
    http = FakeHttpClient(
        [
            token_response("token"),
            FakeResponse(
                200,
                {
                    "return_code": 8030,
                    "return_msg": (
                        "입력 값 오류입니다[8030:투자구분 불일치] "
                        "계좌 1234567890 authorization=Bearer top-secret-token"
                    ),
                },
            ),
        ]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomOrderRejectedError) as rejected:
        client.place_order(
            KiwoomOrderRequest("005930", "BUY", 1, "LIMIT", Decimal(70000))
        )

    assert rejected.value.code == "KIWOOM_ORDER_REJECTED"
    assert rejected.value.broker_result_code == "8030"
    assert "투자구분 불일치" in rejected.value.broker_result_message
    assert "1234567890" not in rejected.value.broker_result_message
    assert "top-secret-token" not in rejected.value.broker_result_message
    assert "top-secret-token" not in rejected.value.message
    assert len(http.calls) == 2


def test_order_timeout_is_unknown_and_not_retried(tmp_path: Path) -> None:
    class TimeoutHttpClient(FakeHttpClient):
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.calls.append({"url": url, **kwargs})
            if url.endswith("/api/dostk/ordr"):
                raise httpx.ReadTimeout("timed out")
            return self.responses.pop(0)

    http = TimeoutHttpClient([token_response("token")])
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http)

    with pytest.raises(KiwoomOrderOutcomeUnknownError):
        client.place_order(
            KiwoomOrderRequest("005930", "BUY", 1, "LIMIT", Decimal(70000))
        )

    order_calls = [call for call in http.calls if call["url"].endswith("/api/dostk/ordr")]
    assert len(order_calls) == 1


def test_order_rate_limiter_waits_per_tr_with_injected_clock() -> None:
    now = [10.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = KiwoomOrderRateLimiter(monotonic=lambda: now[0], sleep=sleep)

    limiter.wait("kt10000")
    limiter.wait("kt10000")
    limiter.wait("kt10001")

    assert sleeps == [1.0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0000010000", 10_000),
        ("-0000010000", -10_000),
        ("0000000000", 0),
        ("0", 0),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_financial_integer_normalization(raw: object, expected: int | None) -> None:
    assert normalize_signed_integer(raw, "field") == expected


@pytest.mark.parametrize("raw", ["abc", "1.0", "1,000", 1000])
def test_financial_integer_normalization_rejects_noncanonical(raw: object) -> None:
    with pytest.raises(KiwoomAdapterError, match="field"):
        normalize_signed_integer(raw, "field")


def test_kt00001_official_schema_fixture_and_server_context(tmp_path: Path) -> None:
    now = datetime(2026, 8, 28, 4, 0, tzinfo=UTC)
    http = FakeHttpClient(
        [token_response("token"), account_response(), FakeResponse(200, OFFICIAL_SCHEMA_FIXTURE_KT00001)]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)

    data = client.get_account_funds(query_type="3")

    assert data.deposit == 10_000
    assert data.withdrawable_amount == -100
    assert data.d2_withdrawable_amount == 5_000
    assert data.account_alias == "KIWOOM_MOCK_PRIMARY"
    assert data.environment == "MOCK"
    assert data.received_at == now
    call = http.calls[-1]
    assert call["headers"]["api-id"] == ACCOUNT_FUNDS_API_ID
    assert call["json"] == {"qry_tp": "3"}


def test_kt00010_preserves_exact_optional_request_context(tmp_path: Path) -> None:
    now = datetime(2026, 8, 28, 4, 1, tzinfo=UTC)
    http = FakeHttpClient(
        [token_response("token"), account_response(), FakeResponse(200, OFFICIAL_SCHEMA_FIXTURE_KT00010)]
    )
    client = KiwoomMockClient(configured_settings(tmp_path), http_client=http, clock=lambda: now)
    request = OrderCapacityRequest("005930", "BUY", 70_000, -100, 7, 69_900)

    data = client.query_order_capacity(request)

    assert data.trade_type == "2"
    assert data.orderable_cash == 700_000
    assert data.margin_100_orderable_amount == 700_000
    assert data.margin_100_orderable_quantity == 7
    assert data.margin_20_orderable_amount == 1_000_000
    call = http.calls[-1]
    assert call["headers"]["api-id"] == ORDER_CAPACITY_API_ID
    assert call["json"] == {
        "stk_cd": "005930",
        "trde_tp": "2",
        "uv": "70000",
        "io_amt": "-100",
        "trde_qty": "7",
        "exp_buy_unp": "69900",
    }


def test_financial_missing_is_not_zero_and_negative_quantity_is_invalid() -> None:
    normalized = normalize_account_funds(
        {"entr": "0"}, query_type="3", received_at=datetime(2026, 8, 28, tzinfo=UTC)
    )
    assert normalized.deposit == 0
    assert normalized.generic_orderable_amount is None

    bad = dict(OFFICIAL_SCHEMA_FIXTURE_KT00010)
    bad["profa_100ord_alowq"] = "-0000000001"
    with pytest.raises(KiwoomAdapterError, match="cannot be negative"):
        from app.broker.kiwoom import normalize_order_capacity

        normalize_order_capacity(
            bad,
            request=OrderCapacityRequest("005930", "BUY", 70_000),
            trade_type="2",
            received_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
