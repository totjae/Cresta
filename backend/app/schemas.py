from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


ExecutionMode = Literal["AUTOMATIC", "MANUAL_APPROVAL", "DISABLED"]


class ExecutionPolicyPayload(StrictModel):
    buy: ExecutionMode
    partial_sell: ExecutionMode
    full_sell: ExecutionMode
    take_profit: ExecutionMode
    fixed_stop_loss: ExecutionMode
    trailing_stop: ExecutionMode
    end_of_day_liquidation: ExecutionMode
    emergency_exit: ExecutionMode


class ExecutionPolicyDraftRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    policy: ExecutionPolicyPayload
    reason: str = Field(min_length=1, max_length=500)


class ExecutionPolicyActivateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")


class ExecutionPolicyVersionResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    version_id: str
    sequence: int
    state: str
    source: str = "USER_DEFAULT"
    policy: ExecutionPolicyPayload
    reason: str
    created_at: datetime
    validated_at: datetime | None
    activated_at: datetime | None


class ExecutionPolicyResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    active_version_id: str | None
    source: str
    policy: ExecutionPolicyPayload


class ExecutionPolicyHistoryResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[ExecutionPolicyVersionResponse]


class RiskPolicyPayload(StrictModel):
    entry_order_amount: int | None = Field(default=None, ge=10_000, le=100_000_000)
    max_single_order_amount: int = Field(ge=10_000, le=100_000_000)
    max_position_amount_per_symbol: int = Field(ge=10_000, le=100_000_000)
    max_total_position_amount: int = Field(ge=10_000, le=100_000_000)
    max_open_positions: int = Field(ge=1, le=3)
    max_daily_entries: int = Field(ge=1, le=20)
    fixed_stop_loss_pct: Decimal = Field(ge=Decimal("-20.0"), le=Decimal("-0.1"))
    quote_stale_seconds: int = Field(ge=1, le=30)
    max_spread_pct: Decimal = Field(ge=Decimal("0.01"), le=Decimal("5.00"))
    max_price_deviation_pct: Decimal = Field(ge=Decimal("0.01"), le=Decimal("5.00"))
    daily_loss_limit_pct: Decimal = Field(
        default=Decimal("5.0"), ge=Decimal("0.1"), le=Decimal("20.0")
    )
    daily_loss_basis: Literal["REALIZED_ONLY", "REALIZED_PLUS_UNREALIZED"] = (
        "REALIZED_PLUS_UNREALIZED"
    )
    max_consecutive_losses: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_amount_order(self) -> RiskPolicyPayload:
        entry = self.entry_order_amount
        if entry is not None and entry > self.max_single_order_amount:
            raise ValueError("entry_order_amount exceeds max_single_order_amount")
        if self.max_single_order_amount > self.max_position_amount_per_symbol:
            raise ValueError("max_single_order_amount exceeds symbol limit")
        if self.max_position_amount_per_symbol > self.max_total_position_amount:
            raise ValueError("symbol limit exceeds total limit")
        return self


class RiskPolicyDraftRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    policy: RiskPolicyPayload
    reason: str = Field(min_length=1, max_length=500)


class RiskPolicyActivateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")


class RiskPolicyVersionResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    version_id: str
    sequence: int
    state: str
    source: str = "USER_DEFAULT"
    policy: RiskPolicyPayload
    reason: str
    created_at: datetime
    validated_at: datetime | None
    activated_at: datetime | None


class RiskPolicyResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    active_version_id: str | None
    source: str
    policy: RiskPolicyPayload


class RiskPolicyHistoryResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[RiskPolicyVersionResponse]


class MockDecisionRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    evaluation_request_id: str = Field(min_length=16, max_length=64)
    symbol: str = Field(pattern=r"^\d{6}$")
    market: Literal["KRX", "NXT"] = "KRX"


class ScoutOutputResponse(StrictModel):
    trend_state: str
    volume_state: str
    volatility_state: str
    entry_score: int
    exit_risk_score: int
    core_review_required: bool
    suggested_review: str
    reason_codes: list[str]


class CoreOutputResponse(StrictModel):
    action: str
    confidence: Decimal
    risk_level: str
    sell_ratio: Decimal | None
    reason_codes: list[str]


class DecisionExecutionResponse(StrictModel):
    execution_id: str
    action: str
    mode: str
    stage: str
    state: str
    result_code: str | None
    guard_evaluation_id: str | None
    approval_id: str | None
    order_intent_id: str | None
    created_at: datetime
    updated_at: datetime


class DecisionResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    decision_id: str
    purpose: Literal["DIAGNOSTIC", "TRADING"]
    evaluation_request_id: str
    symbol: str
    market: str
    input_snapshot_id: str
    decision_input_id: str | None
    input_schema_version: str | None
    input_hash: str | None
    indicator_snapshot_id: str | None
    indicator_calculator_version: str | None
    model_id: str
    prompt_version: str
    scout: ScoutOutputResponse
    core: CoreOutputResponse
    configuration_version_id: str | None
    execution_mode: str | None
    execution_outcome: str
    execution: DecisionExecutionResponse | None
    valid_until: datetime
    created_at: datetime


class DecisionListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[DecisionResponse]


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
    unfilled_policy: str
    fill_timeout_seconds: int
    max_reprice_attempts: int
    reprice_attempts: int
    next_action_at: datetime | None
    trading_date: date
    version: int
    created_at: datetime
    updated_at: datetime


class OrderEventResponse(StrictModel):
    id: str
    event_type: str
    source: str
    broker_result_code: str | None = None
    broker_result_message: str | None = None
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
    available_quantity: int
    average_price: Decimal
    managed_quantity: int
    managed_average_price: Decimal
    external_quantity: int
    state: str
    origin: Literal["CRESTA_MANAGED", "EXTERNAL", "MIXED"]
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


class AnalysisSchedulerStatusResponse(StrictModel):
    state: str
    lease_valid: bool
    last_heartbeat_at: datetime | None
    last_tick_at: datetime | None
    last_completed_at: datetime | None
    next_due_at: datetime | None
    processed_count: int
    decision_count: int
    skipped_count: int
    failed_count: int
    last_error_code: str | None


class SystemHealthResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    environment: str
    live_trading_enabled: bool
    execution_stage: str
    decision_execution_status: str
    buy_execution_ready: bool
    buy_execution_block_reason: str | None
    pause_entry_active: bool
    analysis_scheduler: AnalysisSchedulerStatusResponse
    database_status: str
    paper_broker_status: str
    kiwoom_broker_status: str
    market_data_status: str
    trading_gate: TradingGateResponse | None
    counts: SystemCountResponse


class EmergencyStopRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    level: Literal["PAUSE_ENTRY"] = "PAUSE_ENTRY"
    reason: str = Field(min_length=5, max_length=500)


class EmergencyStopResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    stop_id: str | None
    account_alias: str
    level: Literal["PAUSE_ENTRY"]
    state: Literal["ACTIVE", "RELEASED"]
    reason: str | None
    version: int
    activated_at: datetime | None
    released_at: datetime | None


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


class ApprovalResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    approval_id: str
    execution_id: str
    decision_id: str
    user_id: str
    state: str
    symbol: str
    market: str
    action: str
    reference_price: Decimal | None
    quantity: int
    order_id: str | None
    result_code: str | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class ApprovalListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[ApprovalResponse]


class ApprovalActionRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    idempotency_key: str = Field(min_length=16, max_length=128)


class ApprovalActionResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    approval_id: str
    state: str
    order_id: str | None
    result_code: str | None


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


class WatchlistCreateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    symbol: str = Field(pattern=r"^\d{6}$")
    market: Literal["KRX", "NXT"] = "KRX"


class WatchlistQuoteSummary(StrictModel):
    last_price: Decimal
    cumulative_volume: int
    quality: str
    age_seconds: Decimal
    is_fresh: bool
    received_at: datetime


class WatchlistIndicatorSummary(StrictModel):
    calculator_version: str
    vwap: Decimal
    sma5: Decimal | None
    session_high: Decimal
    drawdown_from_high_pct: Decimal
    spread_pct: Decimal | None
    minute_bar_count: int
    calculated_at: datetime


class WatchlistItemResponse(StrictModel):
    id: str
    symbol: str
    market: str
    data_status: Literal["WAITING_FOR_DATA", "AVAILABLE", "STALE", "DEGRADED"]
    quote: WatchlistQuoteSummary | None
    indicators: WatchlistIndicatorSummary | None
    created_at: datetime


class WatchlistResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    limit: int = 3
    remaining_slots: int
    items: list[WatchlistItemResponse]


class WatchlistDeleteResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    status: Literal["DELETED"] = "DELETED"


LlmAdapterType = Literal[
    "MOCK",
    "OPENAI_RESPONSES",
    "ANTHROPIC_MESSAGES",
    "GEMINI_GENERATE_CONTENT",
    "VERCEL_AI_GATEWAY",
    "OPENAI_COMPATIBLE",
    "OLLAMA_NATIVE",
    "OLLAMA_OPENAI_COMPATIBLE",
]
LlmDataPolicy = Literal["EXTERNAL_CLOUD", "GATEWAY", "LOCAL", "NONE"]
LlmRole = Literal[
    "INTEL_COLLECTOR",
    "EVIDENCE_VERIFIER",
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
]


class LlmCapabilitiesPayload(StrictModel):
    structured_output: bool = False
    tool_calling: bool = False
    web_search: bool = False
    streaming: bool = False
    reasoning: bool = False
    seed: bool = False
    usage_reporting: bool = False
    local_execution: bool = False


class LlmProviderCreateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    adapter_type: LlmAdapterType
    endpoint: str | None = Field(default=None, max_length=500)
    credential_secret_ref: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    data_policy: LlmDataPolicy


class LlmProviderResponse(StrictModel):
    id: str
    name: str
    provider_template_id: str | None
    adapter_type: LlmAdapterType
    endpoint: str | None
    credential_configured: bool
    data_policy: LlmDataPolicy
    state: str
    health_status: str
    last_tested_at: datetime | None
    version: int
    created_at: datetime


class LlmProviderListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[LlmProviderResponse]


class LlmProviderCatalogItem(StrictModel):
    template_id: str
    adapter_type: str
    label: str
    can_register: bool
    support_level: str
    configuration_fields: list[dict[str, object]]


class LlmProviderCatalogResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[LlmProviderCatalogItem]


class LlmProviderRegistrationPreviewRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    template_id: str | None = Field(default=None, min_length=2, max_length=64)
    adapter_type: str | None = Field(default=None, min_length=2, max_length=40)
    configuration: dict[str, str] = Field(default_factory=dict)


class LlmProviderRegistrationPreviewResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    target_action: Literal["LLM_PROVIDER_REGISTER"] = "LLM_PROVIDER_REGISTER"
    target_id: str


class LlmProviderRegistrationRequest(LlmProviderRegistrationPreviewRequest):
    credential: str = Field(min_length=1, max_length=8192, repr=False)


class LlmProviderDeletionPreviewResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    target_action: Literal["LLM_PROVIDER_DELETE"] = "LLM_PROVIDER_DELETE"
    target_id: str
    provider_id: str


class LlmProviderDeletionRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")


class LlmProviderTestResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    provider: LlmProviderResponse
    external_network_used: bool
    capabilities: LlmCapabilitiesPayload
    message_code: str


class LlmCredentialPreviewResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    target_action: Literal["LLM_PROVIDER_CREDENTIAL_SET"] = "LLM_PROVIDER_CREDENTIAL_SET"
    target_id: str
    provider_id: str


class LlmCredentialSetRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    credential: str = Field(min_length=1, max_length=8192, repr=False)


class LlmModelCreateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    provider_profile_id: str = Field(min_length=36, max_length=36)
    alias: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    provider_model_id: str = Field(min_length=1, max_length=128)
    capabilities: LlmCapabilitiesPayload
    max_context_tokens: int | None = Field(default=None, ge=1, le=2_000_000)
    max_output_tokens: int = Field(default=8192, ge=1, le=32768)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    top_p: Decimal | None = Field(default=None, ge=0, le=1)
    reasoning_effort: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    seed: int | None = Field(default=None, ge=-2147483648, le=2147483647)


class LlmModelResponse(StrictModel):
    id: str
    provider_profile_id: str
    alias: str
    provider_model_id: str
    capabilities: LlmCapabilitiesPayload
    max_context_tokens: int | None
    max_output_tokens: int
    temperature: Decimal | None
    top_p: Decimal | None
    reasoning_effort: Literal["LOW", "MEDIUM", "HIGH"] | None
    seed: int | None
    state: str
    validated_at: datetime | None
    version: int
    created_at: datetime


class LlmModelListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[LlmModelResponse]


class LlmProviderRegistrationResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    provider: LlmProviderResponse
    models: list[LlmModelResponse]


class LlmPromptCreateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    role: LlmRole
    system_prompt: str = Field(min_length=20, max_length=12000)
    reason: str = Field(min_length=1, max_length=500)


class LlmPromptResponse(StrictModel):
    id: str
    role: LlmRole
    version_number: int
    version_label: str
    system_prompt: str
    content_hash: str
    state: str
    reason: str
    validated_at: datetime | None
    version: int
    created_at: datetime


class LlmPromptListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[LlmPromptResponse]


class LlmRouteCreateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    role: LlmRole
    primary_model_profile_id: str = Field(min_length=36, max_length=36)
    failure_policy: Literal["FAIL_STOP", "FAILOVER"] = "FAIL_STOP"
    fallback_model_profile_id: str | None = Field(default=None, min_length=36, max_length=36)
    timeout_ms: int = Field(default=120000, ge=1000, le=600000)
    service_tier: Literal["DEFAULT", "PRIORITY", "FLEX"] = "DEFAULT"
    web_search_enabled: bool = False
    daily_call_limit: int = Field(default=100, ge=1, le=100000)
    daily_cost_limit_krw: Decimal = Field(default=Decimal(0), ge=0)
    prompt_profile_id: str | None = Field(default=None, min_length=36, max_length=36)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)
    output_schema_version: str = Field(min_length=1, max_length=64)
    temperature_override: Decimal | None = Field(default=None, ge=0, le=2)
    top_p_override: Decimal | None = Field(default=None, ge=0, le=1)
    max_output_tokens_override: int | None = Field(default=None, ge=1, le=32768)
    reasoning_effort_override: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    seed_override: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    reason: str = Field(min_length=1, max_length=500)


class LlmEffectiveGenerationParameters(StrictModel):
    temperature: Decimal | None
    temperature_source: str
    top_p: Decimal | None
    top_p_source: str
    max_output_tokens: int
    max_output_tokens_source: str
    reasoning_effort: Literal["LOW", "MEDIUM", "HIGH"] | None
    reasoning_effort_source: str
    seed: int | None
    seed_source: str


class LlmRouteResponse(StrictModel):
    id: str
    role: LlmRole
    primary_model_profile_id: str
    primary_model_alias: str
    failure_policy: Literal["FAIL_STOP", "FAILOVER"]
    fallback_model_profile_id: str | None
    fallback_model_alias: str | None
    execution_stage: Literal["SHADOW"]
    timeout_ms: int
    service_tier: Literal["DEFAULT", "PRIORITY", "FLEX"]
    web_search_enabled: bool
    max_attempts: int
    daily_call_limit: int
    daily_cost_limit_krw: Decimal
    prompt_version: str
    prompt_profile_id: str | None
    prompt_content_hash: str | None
    output_schema_version: str
    temperature_override: Decimal | None
    top_p_override: Decimal | None
    max_output_tokens_override: int | None
    reasoning_effort_override: Literal["LOW", "MEDIUM", "HIGH"] | None
    seed_override: int | None
    effective_parameters: LlmEffectiveGenerationParameters
    state: str
    reason: str
    validated_at: datetime | None
    version: int
    created_at: datetime


class LlmRouteListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[LlmRouteResponse]


AgentRouteRole = Literal[
    "TECHNICAL_SCOUT",
    "NEWS_DISCLOSURE_SCOUT",
    "MARKET_SECTOR_SCOUT",
    "POSITION_RISK_SCOUT",
    "CORE",
]


class LlmAssignmentActivationRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    route_ids: dict[AgentRouteRole, str]


class LlmAssignmentActivateRequest(LlmAssignmentActivationRequest):
    pass


class LlmAssignmentPreviewResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    target_action: Literal["LLM_ROLE_ASSIGNMENT_ACTIVATE"] = "LLM_ROLE_ASSIGNMENT_ACTIVATE"
    target_id: str
    routes: list[LlmRouteResponse]


class LlmAssignmentActivationResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    routes: list[LlmRouteResponse]


class LlmRoleAssignmentItem(StrictModel):
    role: AgentRouteRole
    current: LlmRouteResponse | None
    candidates: list[LlmRouteResponse]
    history_count: int
    status: Literal["UNASSIGNED", "CANDIDATE", "AMBIGUOUS", "ACTIVE"]


class LlmRoleAssignmentListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[LlmRoleAssignmentItem]


class AgentDiagnosticRunRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    market: Literal["KRX", "NXT"]
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    route_ids: dict[AgentRouteRole, str]


class AgentInvocationResponse(StrictModel):
    invocation_id: str
    attempt_number: int
    requested_model_profile_id: str | None
    requested_model_alias: str | None
    state: str
    actual_provider: str | None
    actual_model: str | None
    latency_ms: int
    validation_status: str
    error_code: str | None
    fallback_path: list[str]
    runtime_context_at: datetime | None
    web_search_enabled: bool
    created_at: datetime


class AgentInvocationOutputResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    run_id: str
    stage_run_id: str
    invocation_id: str
    state: str
    validation_status: str
    error_code: str | None
    output_available: bool
    model_output: dict[str, object] | None
    model_output_hash: str | None
    captured_at: datetime | None


class AgentStageRunResponse(StrictModel):
    stage_run_id: str
    role: str
    sequence: int
    dependencies: list[str]
    route_id: str | None
    state: str
    input_hash: str
    output: dict[str, object] | None
    output_hash: str | None
    error_code: str | None
    attempt_count: int
    max_attempts: int
    fencing_token: int
    lease_expires_at: datetime | None
    timeout_at: datetime | None
    invocation: AgentInvocationResponse | None
    invocations: list[AgentInvocationResponse]
    started_at: datetime | None
    completed_at: datetime | None


class AgentEvidenceBundleResponse(StrictModel):
    bundle_id: str
    state: str
    policy_version: str
    evidence_ids: list[str]
    reason_codes: list[str]
    bundle_hash: str
    as_of: datetime


class AgentRunResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    run_id: str
    created: bool = False
    purpose: Literal["DIAGNOSTIC", "TRADING_ADVISORY"]
    execution_stage: Literal["SHADOW"]
    market: Literal["KRX", "NXT"]
    symbol: str
    market_snapshot_id: str
    input_hash: str
    dag_version: str
    analysis_context: Literal["ENTRY", "POSITION"] | None
    position_snapshot_hash: str | None
    server_input_policy_version: str | None
    market_context_snapshot_id: str | None
    market_context_snapshot_hash: str | None
    assessment_schema_version: str | None
    core_schema_version: str | None
    score_policy_version: str | None
    route_versions: dict[str, object]
    state: str
    core_action: Literal["WAIT"] | None
    shadow_assessment: (
        Literal[
            "ENTRY_STRONG",
            "ENTRY_SUPPORTIVE",
            "NEUTRAL",
            "ENTRY_ADVERSE",
            "HOLD_SUPPORTIVE",
            "EXIT_RISK_ELEVATED",
            "EXIT_RISK_HIGH",
            "UNKNOWN",
        ]
        | None
    )
    basis_decision_id: str | None
    fusion_policy_version: str | None
    fusion_state: str | None
    fusion_reason_code: str | None
    fusion_decision_id: str | None
    valid_until: datetime
    stages: list[AgentStageRunResponse]
    evidence_bundle: AgentEvidenceBundleResponse | None
    created_at: datetime
    completed_at: datetime | None


class AgentRunListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[AgentRunResponse]


class VenueSelectionDiagnosticRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    side: Literal["BUY", "SELL"]
    quantity: int = Field(ge=1, le=1_000_000_000)
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    urgency: Literal["NORMAL", "EMERGENCY"] = "NORMAL"


class VenueSelectionQuoteResponse(StrictModel):
    market: Literal["KRX", "NXT"]
    snapshot_id: str
    bid_price: str | None
    bid_quantity: int | None
    ask_price: str | None
    ask_quantity: int | None
    event_at: datetime
    valid: bool


class VenueSelectionResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    selection_id: str
    policy_version: str
    execution_stage: Literal["SHADOW"]
    order_creation_allowed: Literal[False]
    environment: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    order_type: Literal["LIMIT", "MARKET"]
    urgency: Literal["NORMAL", "EMERGENCY"]
    session: str
    trading_day_status: Literal["OPEN", "CLOSED", "UNKNOWN"]
    calendar_reason: Literal[
        "WEEKDAY",
        "WEEKEND",
        "PUBLIC_HOLIDAY",
        "LABOR_DAY",
        "YEAR_END_CLOSURE",
        "CALENDAR_UNAVAILABLE",
        "OPERATIONAL_CLOSURE",
    ]
    calendar_policy_version: str
    calendar_override_id: str | None
    nxt_eligible: bool
    nxt_eligibility_status: Literal["VERIFIED", "INELIGIBLE", "UNKNOWN"]
    sor_supported: bool
    selected_venue: Literal["KRX", "NXT", "SOR", "WAIT"]
    state: Literal["SELECTED", "WAIT"]
    reason_codes: list[str]
    quotes: dict[str, VenueSelectionQuoteResponse | None]
    input_hash: str
    evaluated_at: datetime
    created_at: datetime


class VenueSelectionListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[VenueSelectionResponse]


class CalendarOverrideCreateRequest(StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    market_date: date
    reason: str = Field(min_length=5, max_length=200)
    source_reference: str = Field(min_length=3, max_length=200)


class CalendarOverrideResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    override_id: str
    market_date: date
    override_type: Literal["OPERATIONAL_CLOSURE"]
    state: Literal["ACTIVE", "REVOKED"]
    reason: str
    source_reference: str
    created_at: datetime
    revoked_at: datetime | None


class CalendarOverrideListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[CalendarOverrideResponse]


class StopTriggerResponse(StrictModel):
    trigger_id: str
    account_alias: str
    symbol: str
    market: str
    position_id: str
    position_version: int
    risk_policy_version_id: str | None
    stop_price: Decimal
    trigger_price: Decimal | None
    snapshot_id: str | None
    state: str
    result_code: str | None
    guard_evaluation_id: str | None
    risk_event_id: str | None
    halt_scope: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class StopTriggerListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[StopTriggerResponse]


class RiskEventResponse(StrictModel):
    event_id: str
    scope: str
    rule_code: str
    severity: str
    state: str
    account_alias: str
    symbol: str | None
    input_snapshot_id: str | None
    resolution: str | None
    resolved_at: datetime | None
    correlation_id: str
    created_at: datetime
    updated_at: datetime


class RiskEventListResponse(StrictModel):
    schema_version: str = "1.0"
    request_id: str
    items: list[RiskEventResponse]
