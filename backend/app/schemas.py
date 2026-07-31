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
