from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.broker.kiwoom_ws import KiwoomAccountWebSocket, KiwoomWebSocketError
from app.config import Settings
from app.watch import QuoteEvent


class FakeSocket:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = [json.dumps(item) for item in responses]
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_login_register_ping_and_account_event_contract(settings: Settings) -> None:
    socket = FakeSocket(
        [
            {"trnm": "PING", "timestamp": "safe"},
            {"trnm": "LOGIN", "return_code": 0},
            {"trnm": "REG", "return_code": 0},
            {"trnm": "PING", "timestamp": "same"},
            {"trnm": "REAL", "data": [{"type": "00", "values": {"9203": "1"}}]},
        ]
    )
    uris: list[str] = []

    async def connector(uri: str) -> FakeSocket:
        uris.append(uri)
        return socket

    async def scenario() -> None:
        session = KiwoomAccountWebSocket(settings, connector)
        await session.open("memory-only-token")
        assert await session.receive() == "PING"
        assert await session.receive() == "ACCOUNT_EVENT"
        await session.close()

    asyncio.run(scenario())

    assert uris == ["wss://mockapi.kiwoom.com:10000/api/dostk/websocket"]
    assert socket.sent[0] == {"trnm": "LOGIN", "token": "memory-only-token"}
    assert socket.sent[1] == {"trnm": "PING", "timestamp": "safe"}
    assert socket.sent[2] == {
        "trnm": "REG",
        "grp_no": "1",
        "refresh": "1",
        "data": [{"item": [""], "type": ["00", "04"]}],
    }
    assert socket.sent[3] == {"trnm": "PING", "timestamp": "same"}
    assert socket.closed is True


def test_nonzero_login_and_invalid_real_payload_fail_closed(settings: Settings) -> None:
    async def login_failure() -> None:
        socket = FakeSocket([{"trnm": "LOGIN", "return_code": 1, "return_msg": "secret"}])

        async def connector(_: str) -> FakeSocket:
            return socket

        session = KiwoomAccountWebSocket(settings, connector)
        with pytest.raises(KiwoomWebSocketError, match="KIWOOM_WS_LOGIN_FAILED"):
            await session.open("token")

    async def invalid_real() -> None:
        socket = FakeSocket(
            [
                {"trnm": "LOGIN", "return_code": 0},
                {"trnm": "REG", "return_code": 0},
                {"trnm": "REAL", "data": "invalid"},
            ]
        )

        async def connector(_: str) -> FakeSocket:
            return socket

        session = KiwoomAccountWebSocket(settings, connector)
        await session.open("token")
        with pytest.raises(KiwoomWebSocketError, match="KIWOOM_WS_INVALID_RESPONSE"):
            await session.receive()

    asyncio.run(login_failure())
    asyncio.run(invalid_real())


def test_quote_subscription_trade_orderbook_and_remove_contract(settings: Settings) -> None:
    socket = FakeSocket(
        [
            {"trnm": "LOGIN", "return_code": 0},
            {"trnm": "REG", "return_code": 0},
            {"trnm": "REG", "return_code": 0},
            {"trnm": "REAL", "data": [{
                "type": "0B", "name": "주식체결", "item": "005930",
                "values": {"20": "101530", "10": "-70000", "13": "12345", "16": "69000", "17": "+70500", "18": "-68800", "27": "+70100", "28": "-70000"},
            }]},
            {"trnm": "REAL", "data": [{
                "type": "0D", "name": "주식호가잔량", "item": "005930",
                "values": {"21": "101531", "41": "+70100", "61": "120", "51": "-70000", "71": "90"},
            }]},
            {"trnm": "REMOVE", "return_code": 0},
        ]
    )

    async def connector(_: str) -> FakeSocket:
        return socket

    async def scenario() -> None:
        session = KiwoomAccountWebSocket(
            settings,
            connector,
            clock=lambda: datetime(2026, 8, 4, 1, 15, 32, tzinfo=UTC),
        )
        await session.open("token")
        await session.sync_quotes(("005930",))
        trade = await session.receive()
        assert isinstance(trade, QuoteEvent)
        assert trade.last_price == Decimal(70000)
        assert trade.cumulative_volume == 12345
        assert trade.best_ask_price is None
        book = await session.receive()
        assert isinstance(book, QuoteEvent)
        assert book.best_ask_price == Decimal(70100)
        assert book.best_ask_quantity == 120
        assert book.best_bid_price == Decimal(70000)
        assert book.best_bid_quantity == 90
        await session.sync_quotes(())

    asyncio.run(scenario())
    assert socket.sent[2] == {
        "trnm": "REG", "grp_no": "2", "refresh": "0",
        "data": [{"item": ["005930", "005930_NX"], "type": ["0B", "0D"]}],
    }
    assert socket.sent[3] == {"trnm": "REMOVE", "grp_no": "2"}


def test_nxt_wire_item_is_normalized_and_cache_is_market_isolated(
    settings: Settings,
) -> None:
    socket = FakeSocket(
        [
            {"trnm": "LOGIN", "return_code": 0},
            {"trnm": "REG", "return_code": 0},
            {"trnm": "REG", "return_code": 0},
            {
                "trnm": "REAL",
                "data": [
                    {
                        "type": "0B",
                        "item": "005930",
                        "values": {
                            "20": "101530", "10": "70000", "13": "100",
                            "16": "69000", "17": "70500", "18": "68800",
                        },
                    },
                    {
                        "type": "0B",
                        "item": "005930_NX",
                        "values": {
                            "20": "101531", "10": "70100", "13": "200",
                            "16": "69100", "17": "70600", "18": "68900",
                        },
                    },
                    {
                        "type": "0D",
                        "item": "005930_NX",
                        "values": {
                            "21": "101532", "41": "70200", "61": "30",
                            "51": "70100", "71": "40",
                        },
                    },
                ],
            },
        ]
    )

    async def connector(_: str) -> FakeSocket:
        return socket

    async def scenario() -> None:
        session = KiwoomAccountWebSocket(
            settings,
            connector,
            clock=lambda: datetime(2026, 8, 4, 1, 15, 33, tzinfo=UTC),
        )
        await session.open("token")
        await session.sync_quotes(("005930",))
        krx = await session.receive()
        nxt_trade = await session.receive()
        nxt_book = await session.receive()
        assert isinstance(krx, QuoteEvent) and krx.market == "KRX"
        assert krx.symbol == "005930" and krx.last_price == Decimal(70000)
        assert isinstance(nxt_trade, QuoteEvent) and nxt_trade.market == "NXT"
        assert nxt_trade.symbol == "005930" and nxt_trade.last_price == Decimal(70100)
        assert isinstance(nxt_book, QuoteEvent) and nxt_book.market == "NXT"
        assert nxt_book.best_ask_price == Decimal(70200)
        assert nxt_book.best_bid_quantity == 40
        assert krx.best_ask_price is None

    asyncio.run(scenario())
