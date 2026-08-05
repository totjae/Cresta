export type SessionData = {
  request_id: string;
  login_id: string;
  expires_at: string;
  csrf_token: string;
};

export type ExecutionMode = "AUTOMATIC" | "MANUAL_APPROVAL" | "DISABLED";
export type ExecutionPolicy = {
  buy: ExecutionMode;
  partial_sell: ExecutionMode;
  full_sell: ExecutionMode;
  take_profit: ExecutionMode;
  fixed_stop_loss: ExecutionMode;
  trailing_stop: ExecutionMode;
  end_of_day_liquidation: ExecutionMode;
  emergency_exit: ExecutionMode;
};
export type ExecutionPolicyCurrent = {
  active_version_id: string | null;
  source: "SAFE_DEFAULT" | "USER_DEFAULT";
  policy: ExecutionPolicy;
};
export type ExecutionPolicyVersion = {
  version_id: string;
  sequence: number;
  state: string;
  policy: ExecutionPolicy;
  reason: string;
  created_at: string;
  validated_at: string | null;
  activated_at: string | null;
};
export type DecisionData = {
  decision_id: string;
  purpose: "DIAGNOSTIC" | "TRADING";
  evaluation_request_id: string;
  symbol: string;
  market: string;
  input_snapshot_id: string;
  decision_input_id: string | null;
  input_schema_version: string | null;
  input_hash: string | null;
  indicator_snapshot_id: string | null;
  indicator_calculator_version: string | null;
  model_id: string;
  prompt_version: string;
  scout: { trend_state: string; entry_score: number; reason_codes: string[] };
  core: { action: string; confidence: string; risk_level: string; reason_codes: string[] };
  configuration_version_id: string | null;
  execution_mode: string | null;
  execution_outcome: string;
  execution: null | {
    execution_id: string;
    action: string;
    mode: string;
    stage: string;
    state: string;
    result_code: string | null;
    guard_evaluation_id: string | null;
    approval_id: string | null;
    order_intent_id: string | null;
    created_at: string;
    updated_at: string;
  };
  valid_until: string;
  created_at: string;
};

export type WatchlistItem = {
  id: string;
  symbol: string;
  market: "KRX";
  data_status: "WAITING_FOR_DATA" | "AVAILABLE" | "STALE" | "DEGRADED";
  quote: null | {
    last_price: string;
    cumulative_volume: number;
    quality: string;
    age_seconds: string;
    is_fresh: boolean;
    received_at: string;
  };
  indicators: null | {
    calculator_version: string;
    vwap: string;
    sma5: string | null;
    session_high: string;
    drawdown_from_high_pct: string;
    spread_pct: string | null;
    minute_bar_count: number;
    calculated_at: string;
  };
  created_at: string;
};

export type WatchlistData = {
  schema_version: "1.0";
  request_id: string;
  limit: 3;
  remaining_slots: number;
  items: WatchlistItem[];
};

export type SystemHealth = {
  schema_version: "1.0";
  request_id: string;
  environment: string;
  live_trading_enabled: boolean;
  execution_stage: string;
  decision_execution_status: string;
  buy_execution_ready: boolean;
  buy_execution_block_reason: string | null;
  analysis_scheduler: {
    state: string;
    lease_valid: boolean;
    last_heartbeat_at: string | null;
    last_tick_at: string | null;
    last_completed_at: string | null;
    next_due_at: string | null;
    processed_count: number;
    decision_count: number;
    skipped_count: number;
    failed_count: number;
    last_error_code: string | null;
  };
  database_status: string;
  paper_broker_status: string;
  kiwoom_broker_status: string;
  market_data_status: string;
  trading_gate: null | {
    account_alias: string;
    environment: string;
    status: string;
    reason: string | null;
    version: number;
    updated_at: string;
  };
  counts: { orders: number; active_orders: number; open_positions: number };
};

export type OrderSummary = {
  id: string;
  order_group_id: string;
  parent_order_id: string | null;
  symbol: string;
  market: string;
  side: string;
  order_type: string;
  limit_price: string | null;
  requested_quantity: number;
  filled_quantity: number;
  cancelled_quantity: number;
  remaining_quantity: number;
  status: string;
  environment: string;
  client_order_id: string;
  broker_order_id: string | null;
  replacement_sequence: number;
  trading_date: string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type OrderDetail = OrderSummary & {
  events: Array<{ id: string; event_type: string; source: string; occurred_at: string }>;
  fills: Array<{ id: string; quantity: number; price: string; fee: string; tax: string; filled_at: string }>;
};

export type PositionSummary = {
  id: string;
  account_alias: string;
  environment: string;
  market: string;
  symbol: string;
  quantity: number;
  average_price: string;
  state: string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type BrokerStatus = {
  schema_version: "1.0";
  request_id: string;
  environment: "MOCK";
  account_alias: string;
  state: string;
  gate_status: string | null;
  gate_reason: string | null;
  fencing_token: number | null;
  lease_valid: boolean;
  websocket_connected: boolean;
  subscriptions_ready: boolean;
  last_heartbeat_at: string | null;
  last_reconciliation_at: string | null;
  last_reconciliation_run_id: string | null;
  last_error_code: string | null;
};

export type MockOrderTestResult = {
  schema_version: "1.0";
  request_id: string;
  result_type: "ORDER_QUEUED";
  order_id: string;
  status: string;
  environment: "MOCK";
  account_alias: string;
  symbol: string;
  side: "BUY";
  requested_quantity: 1;
};

export type LlmCapabilities = {
  structured_output: boolean;
  tool_calling: boolean;
  web_search: boolean;
  streaming: boolean;
  reasoning: boolean;
  seed: boolean;
  usage_reporting: boolean;
  local_execution: boolean;
};

export type LlmProviderProfile = {
  id: string;
  name: string;
  adapter_type: string;
  endpoint: string | null;
  credential_configured: boolean;
  data_policy: string;
  state: string;
  health_status: string;
  last_tested_at: string | null;
  version: number;
  created_at: string;
};

export type LlmModelProfile = {
  id: string;
  provider_profile_id: string;
  alias: string;
  provider_model_id: string;
  capabilities: LlmCapabilities;
  max_context_tokens: number | null;
  max_output_tokens: number;
  temperature: string;
  state: string;
  validated_at: string | null;
  version: number;
  created_at: string;
};

export type LlmRoleRoute = {
  id: string;
  role: string;
  primary_model_profile_id: string;
  primary_model_alias: string;
  fallback_policy: "NONE";
  execution_stage: "SHADOW";
  timeout_ms: number;
  max_attempts: number;
  daily_call_limit: number;
  daily_cost_limit_krw: string;
  prompt_version: string;
  output_schema_version: string;
  state: string;
  reason: string;
  validated_at: string | null;
  version: number;
  created_at: string;
};

type PasswordChallenge = {
  request_id: string;
  challenge_id: string;
  expires_at: string;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message = "요청을 처리할 수 없습니다.",
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return response.json() as Promise<T>;
}

export const authApi = {
  session(signal?: AbortSignal) {
    return request<SessionData>("/api/v1/auth/session", { signal });
  },
  password(loginId: string, password: string) {
    return request<PasswordChallenge>("/api/v1/auth/login/password", {
      method: "POST",
      body: JSON.stringify({ schema_version: "1.0", login_id: loginId, password }),
    });
  },
  totp(challengeId: string, code: string) {
    return request<SessionData>("/api/v1/auth/login/totp", {
      method: "POST",
      body: JSON.stringify({
        schema_version: "1.0",
        challenge_id: challengeId,
        totp_code: code,
      }),
    });
  },
  reauthTotp(csrfToken: string, code: string, targetAction: string, targetId: string) {
    return request<{ reauth_proof: string; expires_at: string }>("/api/v1/auth/reauth/totp", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        totp_code: code,
        target_action: targetAction,
        target_id: targetId,
      }),
    });
  },
  logout(csrfToken: string) {
    return request<{ status: string }>("/api/v1/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
};

export const systemApi = {
  health(signal?: AbortSignal) {
    return request<SystemHealth>("/api/v1/system/health", { signal });
  },
  broker(signal?: AbortSignal) {
    return request<BrokerStatus>("/api/v1/system/broker", { signal });
  },
  mockOrderTest(
    csrfToken: string,
    payload: {
      test_request_id: string;
      symbol: string;
      order_type: "MARKET" | "LIMIT";
      limit_price: string | null;
      reauth_proof: string;
    },
  ) {
    return request<MockOrderTestResult>("/api/v1/system/broker/mock-order-test", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        ...payload,
        confirmation: "KIWOOM_MOCK_ONE_SHARE",
      }),
    });
  },
};

export const settingsApi = {
  executionPolicy(signal?: AbortSignal) {
    return request<ExecutionPolicyCurrent>("/api/v1/settings/execution-policy", { signal });
  },
  createDraft(csrfToken: string, policy: ExecutionPolicy, reason: string) {
    return request<ExecutionPolicyVersion>("/api/v1/settings/execution-policy/drafts", {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0", policy, reason }),
    });
  },
  validate(csrfToken: string, versionId: string) {
    return request<ExecutionPolicyVersion>(`/api/v1/settings/execution-policy/${encodeURIComponent(versionId)}/validate`, {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
    });
  },
  activate(csrfToken: string, versionId: string, reauthProof: string) {
    return request<ExecutionPolicyVersion>(`/api/v1/settings/execution-policy/${encodeURIComponent(versionId)}/activate`, {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0", reauth_proof: reauthProof }),
    });
  },
};

export const llmApi = {
  providers(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmProviderProfile[] }>(
      "/api/v1/ai/providers",
      { signal },
    );
  },
  createMockProvider(csrfToken: string, name: string) {
    return request<LlmProviderProfile>("/api/v1/ai/providers", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        name,
        adapter_type: "MOCK",
        endpoint: null,
        credential_secret_ref: null,
        data_policy: "NONE",
      }),
    });
  },
  testProvider(csrfToken: string, providerId: string) {
    return request<{ provider: LlmProviderProfile; external_network_used: boolean }>(
      `/api/v1/ai/providers/${encodeURIComponent(providerId)}/test`,
      { method: "POST", headers: { "X-CSRF-Token": csrfToken } },
    );
  },
  models(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmModelProfile[] }>(
      "/api/v1/ai/models",
      { signal },
    );
  },
  createMockModel(
    csrfToken: string,
    providerProfileId: string,
    alias: string,
    providerModelId: string,
  ) {
    return request<LlmModelProfile>("/api/v1/ai/models", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        provider_profile_id: providerProfileId,
        alias,
        provider_model_id: providerModelId,
        capabilities: {
          structured_output: true,
          tool_calling: false,
          web_search: false,
          streaming: false,
          reasoning: false,
          seed: true,
          usage_reporting: true,
          local_execution: true,
        },
        max_context_tokens: 4096,
        max_output_tokens: 1024,
        temperature: "0",
      }),
    });
  },
  validateModel(csrfToken: string, modelId: string) {
    return request<LlmModelProfile>(`/api/v1/ai/models/${encodeURIComponent(modelId)}/validate`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
  routes(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmRoleRoute[] }>(
      "/api/v1/ai/routes",
      { signal },
    );
  },
  createShadowRoute(
    csrfToken: string,
    role: string,
    modelProfileId: string,
    reason: string,
  ) {
    return request<LlmRoleRoute>("/api/v1/ai/routes", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        role,
        primary_model_profile_id: modelProfileId,
        timeout_ms: 10000,
        daily_call_limit: 100,
        daily_cost_limit_krw: "0",
        prompt_version: `${role.toLowerCase()}-shadow-v1`,
        output_schema_version: "agent-assessment-v1",
        reason,
      }),
    });
  },
  validateRoute(csrfToken: string, routeId: string) {
    return request<LlmRoleRoute>(`/api/v1/ai/routes/${encodeURIComponent(routeId)}/validate`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
};

export const decisionApi = {
  list(signal?: AbortSignal) {
    return request<{ items: DecisionData[] }>("/api/v1/decisions", { signal });
  },
  mockEvaluate(csrfToken: string, symbol: string, market: "KRX" | "NXT") {
    return request<DecisionData>("/api/v1/decisions/mock-evaluate", {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0", evaluation_request_id: globalThis.crypto.randomUUID(), symbol, market,
      }),
    });
  },
};

export const orderApi = {
  list(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: OrderSummary[] }>(
      "/api/v1/orders",
      { signal },
    );
  },
  detail(orderId: string, signal?: AbortSignal) {
    return request<OrderDetail>(`/api/v1/orders/${encodeURIComponent(orderId)}`, { signal });
  },
};

export const positionApi = {
  list(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: PositionSummary[] }>(
      "/api/v1/positions",
      { signal },
    );
  },
};

export const watchlistApi = {
  list(signal?: AbortSignal) {
    return request<WatchlistData>("/api/v1/watchlist", { signal });
  },
  create(csrfToken: string, symbol: string) {
    return request<WatchlistData>("/api/v1/watchlist", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0", symbol, market: "KRX" }),
    });
  },
  remove(csrfToken: string, itemId: string) {
    return request<{ status: "DELETED" }>(`/api/v1/watchlist/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
};
