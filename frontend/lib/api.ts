export type SessionData = {
  request_id: string;
  login_id: string;
  expires_at: string;
  csrf_token: string;
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
