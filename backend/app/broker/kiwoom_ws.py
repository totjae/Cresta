from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect as websocket_connect

from app.config import Settings
from app.watch import QuoteEvent

ORDER_EVENT_TYPE = "00"
BALANCE_EVENT_TYPE = "04"
ACCOUNT_EVENT_TYPES = {ORDER_EVENT_TYPE, BALANCE_EVENT_TYPE}
TRADE_EVENT_TYPE = "0B"
ORDERBOOK_EVENT_TYPE = "0D"
WATCH_EVENT_TYPES = {TRADE_EVENT_TYPE, ORDERBOOK_EVENT_TYPE}
KST = ZoneInfo("Asia/Seoul")


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
    def __init__(
        self,
        settings: Settings,
        connector: Connector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.connector = connector or _default_connector
        self.clock = clock or (lambda: datetime.now(UTC))
        self.socket: WebSocketLike | None = None
        self.quote_symbols: tuple[str, ...] = ()
        self.pending_events: deque[str | QuoteEvent] = deque()
        self.trade_cache: dict[str, dict[str, Any]] = {}
        self.orderbook_cache: dict[str, dict[str, Any]] = {}

    @property
    def uri(self) -> str:
        return f"{self.settings.kiwoom_ws_base_url.rstrip('/')}/api/dostk/websocket"

    async def open(self, access_token: str) -> None:
        self.quote_symbols = ()
        self.pending_events.clear()
        self.trade_cache.clear()
        self.orderbook_cache.clear()
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

    async def sync_quotes(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(sorted(set(symbols)))
        if any(len(symbol) != 6 or not symbol.isdigit() for symbol in normalized):
            raise KiwoomWebSocketError("KIWOOM_WS_INVALID_WATCHLIST")
        if normalized == self.quote_symbols:
            return
        if not normalized:
            if self.quote_symbols:
                await self._send({"trnm": "REMOVE", "grp_no": "2"})
                await self._wait_for_ack("REMOVE", "KIWOOM_WS_UNSUBSCRIBE_FAILED")
        else:
            await self._send(
                {
                    "trnm": "REG",
                    "grp_no": "2",
                    "refresh": "0",
                    "data": [
                        {
                            "item": list(normalized),
                            "type": [TRADE_EVENT_TYPE, ORDERBOOK_EVENT_TYPE],
                        }
                    ],
                }
            )
            await self._wait_for_ack("REG", "KIWOOM_WS_QUOTE_SUBSCRIBE_FAILED")
        removed = set(self.quote_symbols) - set(normalized)
        for symbol in removed:
            self.trade_cache.pop(symbol, None)
            self.orderbook_cache.pop(symbol, None)
        self.quote_symbols = normalized

    async def receive(self) -> str | QuoteEvent:
        if self.pending_events:
            return self.pending_events.popleft()
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
            if not isinstance(item, dict):
                continue
            event_type = item.get("type")
            if event_type in ACCOUNT_EVENT_TYPES:
                self.pending_events.append("ACCOUNT_EVENT")
            elif event_type in WATCH_EVENT_TYPES:
                quote = self._parse_quote_item(item)
                if quote is not None:
                    self.pending_events.append(quote)
        return self.pending_events.popleft() if self.pending_events else "OTHER"

    async def close(self) -> None:
        socket, self.socket = self.socket, None
        self.quote_symbols = ()
        if socket is not None:
            await socket.close()

    def _parse_quote_item(self, item: dict[str, Any]) -> QuoteEvent | None:
        event_type = item.get("type")
        symbol = item.get("item")
        values = item.get("values")
        if (
            event_type not in WATCH_EVENT_TYPES
            or not isinstance(symbol, str)
            or symbol not in self.quote_symbols
            or not isinstance(values, dict)
        ):
            return None
        received_at = self.clock().astimezone(UTC)
        try:
            if event_type == TRADE_EVENT_TYPE:
                trade = {
                    "last_price": _absolute_decimal(values["10"]),
                    "open_price": _absolute_decimal(values["16"]),
                    "high_price": _absolute_decimal(values["17"]),
                    "low_price": _absolute_decimal(values["18"]),
                    "cumulative_volume": _nonnegative_int(values["13"]),
                    "event_at": _event_time(values["20"], received_at),
                }
                self.trade_cache[symbol] = trade
            else:
                book = {
                    "best_ask_price": _absolute_decimal(values["41"]),
                    "best_ask_quantity": _nonnegative_int(values["61"]),
                    "best_bid_price": _absolute_decimal(values["51"]),
                    "best_bid_quantity": _nonnegative_int(values["71"]),
                    "event_at": _event_time(values["21"], received_at),
                }
                self.orderbook_cache[symbol] = book
            trade = self.trade_cache.get(symbol)
            if trade is None:
                return None
            book = self.orderbook_cache.get(symbol)
            # A merged quote follows the trade clock so an independently newer
            # orderbook timestamp cannot make the next trade look late.
            event_at = trade["event_at"]
            identity = hashlib.sha256(
                json.dumps(
                    {"type": event_type, "item": symbol, "values": values},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            return QuoteEvent(
                symbol=symbol,
                market="KRX",
                source="KIWOOM_WS",
                sequence_or_hash=identity,
                event_at=event_at,
                received_at=received_at,
                last_price=trade["last_price"],
                open_price=trade["open_price"],
                high_price=trade["high_price"],
                low_price=trade["low_price"],
                cumulative_volume=trade["cumulative_volume"],
                best_ask_price=book["best_ask_price"] if book else None,
                best_ask_quantity=book["best_ask_quantity"] if book else None,
                best_bid_price=book["best_bid_price"] if book else None,
                best_bid_quantity=book["best_bid_quantity"] if book else None,
                trading_status="TRADING",
                updates_trade=event_type == TRADE_EVENT_TYPE,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None

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


def _absolute_decimal(value: object) -> Decimal:
    normalized = str(value).strip().replace(",", "")
    result = abs(Decimal(normalized))
    if result <= 0:
        raise ValueError("price must be positive")
    return result


def _nonnegative_int(value: object) -> int:
    normalized = str(value).strip().replace(",", "")
    result = int(normalized)
    if result < 0:
        raise ValueError("quantity must be nonnegative")
    return result


def _event_time(value: object, received_at: datetime) -> datetime:
    normalized = str(value).strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError("invalid event time")
    parsed = time(int(normalized[0:2]), int(normalized[2:4]), int(normalized[4:6]))
    local_received = received_at.astimezone(KST)
    local_event = datetime.combine(local_received.date(), parsed, tzinfo=KST)
    if local_event > local_received + timedelta(minutes=5):
        local_event -= timedelta(days=1)
    return local_event.astimezone(UTC)
