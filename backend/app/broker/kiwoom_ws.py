from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from websockets.asyncio.client import connect as websocket_connect

from app.config import Settings

ORDER_EVENT_TYPE = "00"
BALANCE_EVENT_TYPE = "04"
ACCOUNT_EVENT_TYPES = {ORDER_EVENT_TYPE, BALANCE_EVENT_TYPE}


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


Connector = Callable[[str], Awaitable[WebSocketLike]]


class KiwoomWebSocketError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _default_connector(uri: str) -> WebSocketLike:
    return await websocket_connect(
        uri,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_048_576,
    )


class KiwoomAccountWebSocket:
    def __init__(self, settings: Settings, connector: Connector | None = None) -> None:
        self.settings = settings
        self.connector = connector or _default_connector
        self.socket: WebSocketLike | None = None

    @property
    def uri(self) -> str:
        return f"{self.settings.kiwoom_ws_base_url.rstrip('/')}/api/dostk/websocket"

    async def open(self, access_token: str) -> None:
        self.socket = await self.connector(self.uri)
        await self._send({"trnm": "LOGIN", "token": access_token})
        await self._wait_for_ack("LOGIN", "KIWOOM_WS_LOGIN_FAILED")
        await self._send(
            {
                "trnm": "REG",
                "grp_no": "1",
                "refresh": "1",
                "data": [{"item": [""], "type": [ORDER_EVENT_TYPE, BALANCE_EVENT_TYPE]}],
            }
        )
        await self._wait_for_ack("REG", "KIWOOM_WS_SUBSCRIBE_FAILED")

    async def receive(self) -> str:
        payload = await self._receive_json()
        trnm = payload.get("trnm")
        if trnm == "PING":
            await self._send(payload)
            return "PING"
        if trnm != "REAL":
            return "OTHER"
        data = payload.get("data")
        if not isinstance(data, list):
            raise KiwoomWebSocketError("KIWOOM_WS_INVALID_RESPONSE")
        for item in data:
            if isinstance(item, dict) and item.get("type") in ACCOUNT_EVENT_TYPES:
                return "ACCOUNT_EVENT"
        return "OTHER"

    async def close(self) -> None:
        socket, self.socket = self.socket, None
        if socket is not None:
            await socket.close()

    async def _wait_for_ack(self, expected: str, error_code: str) -> None:
        for _ in range(10):
            payload = await self._receive_json()
            if payload.get("trnm") == "PING":
                await self._send(payload)
                continue
            if payload.get("trnm") != expected:
                raise KiwoomWebSocketError("KIWOOM_WS_PROTOCOL_ERROR")
            if payload.get("return_code") != 0:
                raise KiwoomWebSocketError(error_code)
            return
        raise KiwoomWebSocketError("KIWOOM_WS_PROTOCOL_ERROR")

    async def _receive_json(self) -> dict[str, Any]:
        if self.socket is None:
            raise KiwoomWebSocketError("KIWOOM_WS_NOT_CONNECTED")
        try:
            raw = await asyncio.wait_for(
                self.socket.recv(), timeout=self.settings.kiwoom_timeout_seconds
            )
        except TimeoutError as exc:
            raise KiwoomWebSocketError("KIWOOM_WS_TIMEOUT") from exc
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise KiwoomWebSocketError("KIWOOM_WS_INVALID_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise KiwoomWebSocketError("KIWOOM_WS_INVALID_RESPONSE")
        return payload

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.socket is None:
            raise KiwoomWebSocketError("KIWOOM_WS_NOT_CONNECTED")
        await self.socket.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
