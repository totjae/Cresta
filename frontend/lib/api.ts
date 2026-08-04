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
  evaluation_request_id: string;
  symbol: string;
  market: string;
  input_snapshot_id: string;
  model_id: string;
  prompt_version: string;
  scout: { trend_state: string; entry_score: number; reason_codes: string[] };
  core: { action: string; confidence: string; risk_level: string; reason_codes: string[] };
  configuration_version_id: string | null;
  execution_mode: string | null;
  execution_outcome: string;
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
