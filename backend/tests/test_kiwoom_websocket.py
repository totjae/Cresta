from __future__ import annotations

import asyncio
import json

import pytest

from app.broker.kiwoom_ws import KiwoomAccountWebSocket, KiwoomWebSocketError
from app.config import Settings


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
