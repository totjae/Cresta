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
export type RiskPolicy = {
  entry_order_amount: number | null;
  max_single_order_amount: number;
  max_position_amount_per_symbol: number;
  max_total_position_amount: number;
  max_open_positions: number;
  max_daily_entries: number;
  fixed_stop_loss_pct: string;
  quote_stale_seconds: number;
  max_spread_pct: string;
  max_price_deviation_pct: string;
};
export type RiskPolicyCurrent = {
  active_version_id: string | null;
  source: "SAFE_DEFAULT" | "USER_DEFAULT";
  policy: RiskPolicy;
};
export type RiskPolicyVersion = {
  version_id: string;
  sequence: number;
  state: string;
  policy: RiskPolicy;
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

export type AgentInvocationData = {
  invocation_id: string;
  attempt_number: number;
  requested_model_profile_id: string | null;
  requested_model_alias: string | null;
  state: string;
  actual_provider: string | null;
  actual_model: string | null;
  latency_ms: number;
  validation_status: string;
  error_code: string | null;
  fallback_path: string[];
  runtime_context_at: string | null;
  web_search_enabled: boolean;
  created_at: string;
};

export type AgentInvocationOutputData = {
  schema_version: "1.0";
  request_id: string;
  run_id: string;
  stage_run_id: string;
  invocation_id: string;
  state: string;
  validation_status: string;
  error_code: string | null;
  output_available: boolean;
  model_output: Record<string, unknown> | null;
  model_output_hash: string | null;
  captured_at: string | null;
};

export type AgentStageData = {
  stage_run_id: string;
  role: string;
  sequence: number;
  dependencies: string[];
  route_id: string | null;
  state: string;
  input_hash: string;
  output: Record<string, unknown> | null;
  output_hash: string | null;
  error_code: string | null;
  attempt_count: number;
  max_attempts: number;
  fencing_token: number;
  lease_expires_at: string | null;
  timeout_at: string | null;
  invocation: AgentInvocationData | null;
  invocations: AgentInvocationData[];
  started_at: string | null;
  completed_at: string | null;
};

export type AgentRunData = {
  schema_version: "1.0";
  request_id: string;
  run_id: string;
  created: boolean;
  purpose: "DIAGNOSTIC";
  execution_stage: "SHADOW";
  market: "KRX" | "NXT";
  symbol: string;
  market_snapshot_id: string;
  input_hash: string;
  dag_version: string;
  route_versions: Record<string, unknown>;
  state: string;
  core_action: "WAIT" | null;
  valid_until: string;
  stages: AgentStageData[];
  evidence_bundle: null | {
    bundle_id: string;
    state: string;
    policy_version: string;
    evidence_ids: string[];
    reason_codes: string[];
    bundle_hash: string;
    as_of: string;
  };
  created_at: string;
  completed_at: string | null;
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
  provider_template_id: string | null;
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

export type LlmProviderCatalogItem = {
  template_id: string;
  adapter_type: string;
  label: string;
  can_register: boolean;
  support_level: string;
  configuration_fields: Array<{
    key: string;
    label: string;
    minimum_length: number;
    maximum_length: number;
  }>;
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
  top_p: string | null;
  reasoning_effort: "LOW" | "MEDIUM" | "HIGH" | null;
  seed: number | null;
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
  failure_policy: "FAIL_STOP" | "FAILOVER";
  fallback_model_profile_id: string | null;
  fallback_model_alias: string | null;
  execution_stage: "SHADOW";
  timeout_ms: number;
  service_tier: "DEFAULT" | "PRIORITY" | "FLEX";
  web_search_enabled: boolean;
  max_attempts: number;
  daily_call_limit: number;
  daily_cost_limit_krw: string;
  prompt_version: string;
  prompt_profile_id: string | null;
  prompt_content_hash: string | null;
  output_schema_version: string;
  temperature_override: string | null;
  top_p_override: string | null;
  max_output_tokens_override: number | null;
  reasoning_effort_override: "LOW" | "MEDIUM" | "HIGH" | null;
  seed_override: number | null;
  effective_parameters: {
    temperature: string;
    temperature_source: string;
    top_p: string | null;
    top_p_source: string;
    max_output_tokens: number;
    max_output_tokens_source: string;
    reasoning_effort: "LOW" | "MEDIUM" | "HIGH" | null;
    reasoning_effort_source: string;
    seed: number | null;
    seed_source: string;
  };
  state: string;
  reason: string;
  validated_at: string | null;
  version: number;
  created_at: string;
};

export type LlmPromptProfile = {
  id: string;
  role: string;
  version_number: number;
  version_label: string;
  system_prompt: string;
  content_hash: string;
  state: "DRAFT" | "VALIDATED" | "DISABLED";
  reason: string;
  validated_at: string | null;
  version: number;
  created_at: string;
};

export type LlmRoleAssignment = {
  role: string;
  current: LlmRoleRoute | null;
  candidates: LlmRoleRoute[];
  history_count: number;
  status: "UNASSIGNED" | "CANDIDATE" | "AMBIGUOUS" | "ACTIVE";
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
    let code: string | null = null;
    try {
      const payload = await response.json() as { error?: { code?: string }; detail?: string };
      code = payload.error?.code ?? payload.detail ?? null;
    } catch {}
    throw new ApiError(response.status, code ?? undefined);
  }
  if (response.status === 204) return undefined as T;
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
  activate(csrfToken: string, versionId: string) {
    return request<ExecutionPolicyVersion>(`/api/v1/settings/execution-policy/${encodeURIComponent(versionId)}/activate`, {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0" }),
    });
  },
  riskPolicy(signal?: AbortSignal) {
    return request<RiskPolicyCurrent>("/api/v1/settings/risk-policy", { signal });
  },
  createRiskDraft(csrfToken: string, policy: RiskPolicy, reason: string) {
    return request<RiskPolicyVersion>("/api/v1/settings/risk-policy/drafts", {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0", policy, reason }),
    });
  },
  validateRisk(csrfToken: string, versionId: string) {
    return request<RiskPolicyVersion>(`/api/v1/settings/risk-policy/${encodeURIComponent(versionId)}/validate`, {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
    });
  },
  activateRisk(csrfToken: string, versionId: string) {
    return request<RiskPolicyVersion>(`/api/v1/settings/risk-policy/${encodeURIComponent(versionId)}/activate`, {
      method: "POST", headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0" }),
    });
  },
};

export const llmApi = {
  providerCatalog(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmProviderCatalogItem[] }>(
      "/api/v1/ai/provider-catalog",
      { signal },
    );
  },
  previewRegistration(csrfToken: string, name: string, templateId: string, configuration: Record<string, string>) {
    return request<{ target_action: "LLM_PROVIDER_REGISTER"; target_id: string }>(
      "/api/v1/ai/provider-registrations/preview",
      {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ schema_version: "1.0", name, template_id: templateId, configuration }),
      },
    );
  },
  registerProvider(
    csrfToken: string,
    name: string,
    templateId: string,
    configuration: Record<string, string>,
    credential: string,
  ) {
    return request<{ provider: LlmProviderProfile; models: LlmModelProfile[] }>(
      "/api/v1/ai/provider-registrations",
      {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify({
          schema_version: "1.0",
          name,
          template_id: templateId,
          configuration,
          credential,
        }),
      },
    );
  },
  providers(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmProviderProfile[] }>(
      "/api/v1/ai/providers",
      { signal },
    );
  },
  createProvider(
    csrfToken: string,
    payload: { name: string; adapterType: string; endpoint: string | null; dataPolicy: string },
  ) {
    return request<LlmProviderProfile>("/api/v1/ai/providers", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        name: payload.name,
        adapter_type: payload.adapterType,
        endpoint: payload.endpoint,
        credential_secret_ref: null,
        data_policy: payload.dataPolicy,
      }),
    });
  },
  previewCredential(csrfToken: string, providerId: string) {
    return request<{ target_action: "LLM_PROVIDER_CREDENTIAL_SET"; target_id: string; provider_id: string }>(
      `/api/v1/ai/providers/${encodeURIComponent(providerId)}/credential-preview`,
      { method: "POST", headers: { "X-CSRF-Token": csrfToken } },
    );
  },
  setCredential(csrfToken: string, providerId: string, credential: string) {
    return request<LlmProviderProfile>(
      `/api/v1/ai/providers/${encodeURIComponent(providerId)}/credential`,
      {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ schema_version: "1.0", credential }),
      },
    );
  },
  testProvider(csrfToken: string, providerId: string) {
    return request<{ provider: LlmProviderProfile; external_network_used: boolean }>(
      `/api/v1/ai/providers/${encodeURIComponent(providerId)}/test`,
      { method: "POST", headers: { "X-CSRF-Token": csrfToken } },
    );
  },
  syncProviderModels(csrfToken: string, providerId: string) {
    return request<{ provider: LlmProviderProfile; models: LlmModelProfile[] }>(
      `/api/v1/ai/providers/${encodeURIComponent(providerId)}/models/sync`,
      { method: "POST", headers: { "X-CSRF-Token": csrfToken } },
    );
  },
  previewDelete(csrfToken: string, providerId: string) {
    return request<{ target_action: "LLM_PROVIDER_DELETE"; target_id: string; provider_id: string }>(
      `/api/v1/ai/providers/${encodeURIComponent(providerId)}/delete-preview`,
      { method: "POST", headers: { "X-CSRF-Token": csrfToken } },
    );
  },
  deleteProvider(csrfToken: string, providerId: string) {
    return request<void>(`/api/v1/ai/providers/${encodeURIComponent(providerId)}`, {
      method: "DELETE",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ schema_version: "1.0" }),
    });
  },
  models(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmModelProfile[] }>(
      "/api/v1/ai/models",
      { signal },
    );
  },
  createModel(
    csrfToken: string,
    providerProfileId: string,
    alias: string,
    providerModelId: string,
    capabilities: LlmCapabilities,
    defaults: {
      temperature?: string;
      topP?: string | null;
      maxOutputTokens?: number;
      seed?: number | null;
    } = {},
  ) {
    return request<LlmModelProfile>("/api/v1/ai/models", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        provider_profile_id: providerProfileId,
        alias,
        provider_model_id: providerModelId,
        capabilities,
        max_context_tokens: 4096,
        max_output_tokens: defaults.maxOutputTokens ?? 1024,
        temperature: defaults.temperature ?? "0",
        top_p: defaults.topP ?? null,
        reasoning_effort: null,
        seed: defaults.seed === undefined ? 0 : defaults.seed,
      }),
    });
  },
  validateModel(csrfToken: string, modelId: string) {
    return request<LlmModelProfile>(`/api/v1/ai/models/${encodeURIComponent(modelId)}/validate`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
  disableModel(csrfToken: string, modelId: string) {
    return request<LlmModelProfile>(`/api/v1/ai/models/${encodeURIComponent(modelId)}/disable`, {
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
  prompts(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmPromptProfile[] }>(
      "/api/v1/ai/prompts",
      { signal },
    );
  },
  createPrompt(csrfToken: string, role: string, systemPrompt: string, reason: string) {
    return request<LlmPromptProfile>("/api/v1/ai/prompts", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        role,
        system_prompt: systemPrompt,
        reason,
      }),
    });
  },
  validatePrompt(csrfToken: string, promptId: string) {
    return request<LlmPromptProfile>(`/api/v1/ai/prompts/${encodeURIComponent(promptId)}/validate`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
  createShadowRoute(
    csrfToken: string,
    role: string,
    modelProfileId: string,
    promptProfileId: string,
    reason: string,
    failurePolicy: "FAIL_STOP" | "FAILOVER",
    fallbackModelProfileId: string | null,
    parameters: {
      temperature?: string | null;
      topP?: string | null;
      maxOutputTokens?: number | null;
      reasoningEffort?: "LOW" | "MEDIUM" | "HIGH" | null;
      seed?: number | null;
      timeoutMs?: number;
      serviceTier?: "DEFAULT" | "PRIORITY" | "FLEX";
      webSearchEnabled?: boolean;
    } = {},
  ) {
    return request<LlmRoleRoute>("/api/v1/ai/routes", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        role,
        primary_model_profile_id: modelProfileId,
        failure_policy: failurePolicy,
        fallback_model_profile_id: fallbackModelProfileId,
        timeout_ms: parameters.timeoutMs ?? 30000,
        service_tier: parameters.serviceTier ?? "DEFAULT",
        web_search_enabled: parameters.webSearchEnabled ?? false,
        daily_call_limit: 100,
        daily_cost_limit_krw: "0",
        prompt_profile_id: promptProfileId,
        prompt_version: null,
        output_schema_version: role === "CORE" ? "agent-core-v1" : "agent-assessment-v1",
        temperature_override: parameters.temperature ?? null,
        top_p_override: parameters.topP ?? null,
        max_output_tokens_override: parameters.maxOutputTokens ?? null,
        reasoning_effort_override: parameters.reasoningEffort ?? null,
        seed_override: parameters.seed ?? null,
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
  assignments(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: LlmRoleAssignment[] }>(
      "/api/v1/ai/role-assignments",
      { signal },
    );
  },
  previewAssignments(csrfToken: string, routeIds: Record<string, string>) {
    return request<{ target_action: string; target_id: string; routes: LlmRoleRoute[] }>(
      "/api/v1/ai/role-assignments/activation-preview",
      {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ schema_version: "1.0", route_ids: routeIds }),
      },
    );
  },
  activateAssignments(
    csrfToken: string,
    routeIds: Record<string, string>,
  ) {
    return request<{ routes: LlmRoleRoute[] }>("/api/v1/ai/role-assignments/activate", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        route_ids: routeIds,
      }),
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

export const agentApi = {
  list(signal?: AbortSignal) {
    return request<{ schema_version: "1.0"; request_id: string; items: AgentRunData[] }>(
      "/api/v1/ai/agent-runs",
      { signal },
    );
  },
  diagnostic(
    csrfToken: string,
    symbol: string,
    market: "KRX" | "NXT",
    routeIds: Record<string, string>,
  ) {
    return request<AgentRunData>("/api/v1/ai/agent-runs/diagnostic", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify({
        schema_version: "1.0",
        symbol,
        market,
        route_ids: routeIds,
      }),
    });
  },
  invocationOutput(runId: string, invocationId: string, signal?: AbortSignal) {
    return request<AgentInvocationOutputData>(
      `/api/v1/ai/agent-runs/${encodeURIComponent(runId)}/invocations/${encodeURIComponent(invocationId)}/output`,
      { signal },
    );
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
