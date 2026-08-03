from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PasswordLoginRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordLoginResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    challenge_id: str
    expires_at: datetime


class TotpLoginRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    challenge_id: str = Field(min_length=32, max_length=128)
    totp_code: str = Field(pattern=r"^\d{6}$")


class SessionResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    login_id: str
    expires_at: datetime
    csrf_token: str


class ReauthRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    totp_code: str = Field(pattern=r"^\d{6}$")
    target_action: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=128)


class ReauthResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    reauth_proof: str
    target_action: str
    target_id: str
    expires_at: datetime


class MessageResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    status: str


class ErrorDetail(StrictModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool = False


class ErrorResponse(StrictModel):
    error: ErrorDetail


class OrderSummary(StrictModel):
    id: str
    order_group_id: str
    parent_order_id: str | None
    symbol: str
    market: str
    side: str
    order_type: str
    limit_price: Decimal | None
    requested_quantity: int
    filled_quantity: int
    cancelled_quantity: int
    remaining_quantity: int
    status: str
    environment: str
    client_order_id: str
    broker_order_id: str | None
    replacement_sequence: int
    trading_date: date
    version: int
    created_at: datetime
    updated_at: datetime


class OrderEventResponse(StrictModel):
    id: str
    event_type: str
    source: str
    occurred_at: datetime


class FillResponse(StrictModel):
    id: str
    quantity: int
    price: Decimal
    fee: Decimal
    tax: Decimal
    filled_at: datetime


class OrderListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[OrderSummary]


class OrderDetailResponse(OrderSummary):
    schema_version: str = "1.0"
    request_id: str
    events: list[OrderEventResponse]
    fills: list[FillResponse]


class PositionSummary(StrictModel):
    id: str
    account_alias: str
    environment: str = "MOCK"
    market: str = "KRX"
    symbol: str
    quantity: int
    average_price: Decimal
    state: str
    version: int
    created_at: datetime
    updated_at: datetime


class PositionListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[PositionSummary]


class PositionDetailResponse(PositionSummary):
    schema_version: str = "1.0"
    request_id: str


class TradingGateResponse(StrictModel):
    account_alias: str
    environment: str
    status: str
    reason: str | None
    version: int
    updated_at: datetime


class SystemCountResponse(StrictModel):
    orders: int
    active_orders: int
    open_positions: int


class SystemHealthResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    environment: str
    live_trading_enabled: bool
    database_status: str
    paper_broker_status: str
    kiwoom_broker_status: str
    market_data_status: str
    trading_gate: TradingGateResponse | None
    counts: SystemCountResponse


class BrokerStatusResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    environment: str = "MOCK"
    account_alias: str = "KIWOOM_MOCK_PRIMARY"
    state: str
    gate_status: str | None
    gate_reason: str | None
    fencing_token: int | None
    lease_valid: bool
    websocket_connected: bool
    subscriptions_ready: bool
    last_heartbeat_at: datetime | None
    last_reconciliation_at: datetime | None
    last_reconciliation_run_id: str | None
    last_error_code: str | None


class MockOrderTestRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    test_request_id: str = Field(min_length=16, max_length=128)
    symbol: str = Field(pattern=r"^\d{6}$")
    order_type: str = Field(pattern=r"^(MARKET|LIMIT)$")
    limit_price: Decimal | None = Field(default=None, gt=0)
    reauth_proof: str = Field(min_length=32, max_length=256)
    confirmation: str = Field(pattern=r"^KIWOOM_MOCK_ONE_SHARE$")


class MockOrderTestResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    result_type: str = "ORDER_QUEUED"
    order_id: str
    status: str
    environment: str = "MOCK"
    account_alias: str = "KIWOOM_MOCK_PRIMARY"
    symbol: str
    side: str = "BUY"
    requested_quantity: int = 1


class QuoteResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    symbol: str
    market: str
    source: str
    sequence_or_hash: str
    source_sequence: int | None
    last_price: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    cumulative_volume: int
    best_bid_price: Decimal | None
    best_bid_quantity: int | None
    best_ask_price: Decimal | None
    best_ask_quantity: int | None
    trading_status: str
    quality: str
    age_seconds: Decimal
    is_fresh: bool
    event_at: datetime
    received_at: datetime
    stream_version: int
