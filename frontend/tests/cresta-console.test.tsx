import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CrestaConsole } from "../components/cresta-console";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const healthResponse = {
  schema_version: "1.0",
  request_id: "health-1",
  environment: "MOCK",
  live_trading_enabled: false,
  execution_stage: "SHADOW",
  decision_execution_status: "SHADOW_ONLY",
  buy_execution_ready: false,
  buy_execution_block_reason: "ORDER_SIZE_NOT_CONFIGURED",
  analysis_scheduler: {
    state: "RUNNING",
    lease_valid: true,
    last_heartbeat_at: "2026-08-05T01:00:00Z",
    last_tick_at: "2026-08-05T01:00:00Z",
    last_completed_at: "2026-08-05T01:00:01Z",
    next_due_at: "2026-08-05T01:05:00Z",
    processed_count: 1,
    decision_count: 1,
    skipped_count: 0,
    failed_count: 0,
    last_error_code: null,
  },
  database_status: "CONNECTED",
  paper_broker_status: "AVAILABLE",
  kiwoom_broker_status: "NOT_CONFIGURED",
  market_data_status: "AVAILABLE",
  trading_gate: {
    account_alias: "PAPER",
    environment: "MOCK",
    status: "STARTING",
    reason: "INITIAL_RECONCILIATION_REQUIRED",
    version: 1,
    updated_at: "2026-08-01T01:00:00Z",
  },
  counts: { orders: 0, active_orders: 0, open_positions: 0 },
};

describe("CrestaConsole authentication", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("does not expose the protected console to an anonymous visitor", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({}, 401)));
    render(<CrestaConsole />);
    expect(await screen.findByRole("heading", { name: "관리자 로그인" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "대시보드" })).not.toBeInTheDocument();
  });

  it("requires password challenge and TOTP before showing the console", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => jsonResponse({}, 401))
      .mockImplementationOnce(() => jsonResponse({ request_id: "req-1", challenge_id: "challenge-1", expires_at: "2026-07-31T01:05:00Z" }))
      .mockImplementationOnce(() => jsonResponse({ request_id: "req-2", login_id: "admin", expires_at: "2026-07-31T09:00:00Z", csrf_token: "csrf-memory-only" }))
      .mockImplementationOnce(() => jsonResponse(healthResponse));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.type(await screen.findByLabelText("사용자 ID"), "admin");
    await user.type(screen.getByLabelText("비밀번호"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: /계속/ }));
    expect(await screen.findByRole("heading", { name: "인증 앱 확인" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "대시보드" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("TOTP 인증 코드"), "123456");
    await user.click(screen.getByRole("button", { name: /Console 접속/ }));
    expect(await screen.findByRole("heading", { name: "대시보드" })).toBeInTheDocument();
    expect(await screen.findByText("Paper Broker 조회 연결")).toBeInTheDocument();
    expect(await screen.findByText("시장 데이터")).toBeInTheDocument();
    expect((await screen.findAllByText("AVAILABLE")).length).toBeGreaterThanOrEqual(2);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      schema_version: "1.0",
      login_id: "admin",
      password: "correct horse battery staple",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({
      schema_version: "1.0",
      challenge_id: "challenge-1",
      totp_code: "123456",
    });
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("restores an active session and logs out with the in-memory CSRF token", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-3", login_id: "admin", expires_at: "2026-07-31T09:00:00Z", csrf_token: "csrf-logout" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/auth/logout") return jsonResponse({ status: "LOGGED_OUT" });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: "로그아웃" }));
    expect(await screen.findByRole("heading", { name: "관리자 로그인" })).toBeInTheDocument();
    const logoutCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/auth/logout");
    expect(logoutCall?.[1]).toEqual(expect.objectContaining({
      method: "POST",
      headers: { "X-CSRF-Token": "csrf-logout" },
    }));
  });

  it("manages the persistent KRX watchlist from the console", async () => {
    const emptyWatchlist = { schema_version: "1.0", request_id: "watch-1", limit: 3, remaining_slots: 3, items: [] };
    const populatedWatchlist = {
      schema_version: "1.0", request_id: "watch-2", limit: 3, remaining_slots: 2,
      items: [{
        id: "watch-item-1", symbol: "005930", market: "KRX", data_status: "AVAILABLE",
        quote: { last_price: "70000.0000", cumulative_volume: 12345, quality: "NORMAL", age_seconds: "0.200", is_fresh: true, received_at: "2026-08-04T01:00:00Z" },
        indicators: { calculator_version: "watch-indicators-v1", vwap: "69900.0000", sma5: "69800.0000", session_high: "70500.0000", drawdown_from_high_pct: "-0.709220", spread_pct: "0.142857", minute_bar_count: 5, calculated_at: "2026-08-04T01:00:00Z" },
        created_at: "2026-08-04T01:00:00Z",
      }],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "session-watch", login_id: "admin", expires_at: "2026-08-04T09:00:00Z", csrf_token: "csrf-watch" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/watchlist" && init?.method === "POST") return jsonResponse(populatedWatchlist, 201);
      if (path === "/api/v1/watchlist") return jsonResponse(emptyWatchlist);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: "감시 종목" }));
    expect(await screen.findByRole("heading", { name: "감시 종목" })).toBeInTheDocument();
    expect(await screen.findByText("등록된 감시 종목이 없습니다.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("종목코드"), "005930");
    await user.click(screen.getByRole("button", { name: "감시 등록" }));
    expect(await screen.findByRole("heading", { name: "005930" })).toBeInTheDocument();
    expect(screen.getByText("69,900원")).toBeInTheDocument();
    expect(screen.getByText("5개")).toBeInTheDocument();
    const createCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/watchlist" && init?.method === "POST");
    expect(createCall?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-watch" }),
    }));
  });

  it("shows persisted Paper read models without order creation controls", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-4", login_id: "admin", expires_at: "2026-08-01T09:00:00Z", csrf_token: "csrf-paper" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/orders") return jsonResponse({ schema_version: "1.0", request_id: "orders-1", items: [] });
      if (path === "/api/v1/positions") return jsonResponse({ schema_version: "1.0", request_id: "positions-1", items: [] });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    expect(await screen.findByText("Paper Broker 조회 연결")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /승인·주문/ }));
    expect(await screen.findByText("Paper 주문이 없습니다")).toBeInTheDocument();
    expect(screen.getByText("운영 화면에서 주문·체결을 생성하지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /주문 생성/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /보유 포지션/ }));
    expect(await screen.findByText("Paper 포지션이 없습니다")).toBeInTheDocument();
  });
});

describe("Kiwoom MOCK browser diagnostic", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("queues one share only after TOTP reauthentication", async () => {
    const brokerResponse = {
      schema_version: "1.0", request_id: "broker-1", environment: "MOCK",
      account_alias: "KIWOOM_MOCK_PRIMARY", state: "READY", gate_status: "READY",
      gate_reason: "WORKER_HEALTHY", fencing_token: 8, lease_valid: true,
      websocket_connected: true, subscriptions_ready: true,
      last_heartbeat_at: "2026-08-04T01:00:00Z", last_reconciliation_at: "2026-08-04T01:00:00Z",
      last_reconciliation_run_id: "run-1", last_error_code: null,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-5", login_id: "admin", expires_at: "2026-08-04T09:00:00Z", csrf_token: "csrf-mock-order" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/system/broker") return jsonResponse(brokerResponse);
      if (path === "/api/v1/auth/reauth/totp") return jsonResponse({ reauth_proof: "proof-memory-only", expires_at: "2026-08-04T01:05:00Z" });
      if (path === "/api/v1/system/broker/mock-order-test") return jsonResponse({
        schema_version: "1.0", request_id: "mock-1", result_type: "ORDER_QUEUED",
        order_id: "order-1", status: "CREATED", environment: "MOCK",
        account_alias: "KIWOOM_MOCK_PRIMARY", symbol: "005930", side: "BUY", requested_quantity: 1,
      });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /시스템 상태/ }));
    expect(await screen.findByRole("heading", { name: "시스템 상태" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "모의주문 확인" }));
    expect(await screen.findByRole("dialog", { name: "키움 모의주문 1주 확인" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("현재 TOTP 코드"), "123456");
    await user.click(screen.getByRole("button", { name: "모의주문 실행" }));
    expect(await screen.findByText(/CREATED 상태로 등록/)).toBeInTheDocument();

    const reauthCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/auth/reauth/totp");
    const orderCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/system/broker/mock-order-test");
    expect(reauthCall?.[1]).toEqual(expect.objectContaining({ method: "POST", headers: expect.objectContaining({ "X-CSRF-Token": "csrf-mock-order" }) }));
    expect(orderCall?.[1]).toEqual(expect.objectContaining({ method: "POST", headers: expect.objectContaining({ "X-CSRF-Token": "csrf-mock-order" }) }));
    expect(JSON.parse(String(orderCall?.[1]?.body))).toEqual(expect.objectContaining({
      symbol: "005930", order_type: "MARKET", limit_price: null,
      confirmation: "KIWOOM_MOCK_ONE_SHARE", reauth_proof: "proof-memory-only",
    }));
  });
});

describe("execution policy settings", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("validates and activates independent action modes with TOTP", async () => {
    const safePolicy = {
      buy: "MANUAL_APPROVAL", partial_sell: "MANUAL_APPROVAL", full_sell: "MANUAL_APPROVAL",
      take_profit: "MANUAL_APPROVAL", fixed_stop_loss: "AUTOMATIC", trailing_stop: "AUTOMATIC",
      end_of_day_liquidation: "AUTOMATIC", emergency_exit: "AUTOMATIC",
    };
    let active = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-6", login_id: "admin", expires_at: "2026-08-04T09:00:00Z", csrf_token: "csrf-policy" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/settings/execution-policy" && !init?.method) return jsonResponse({ active_version_id: active ? "policy-1" : null, source: active ? "USER_DEFAULT" : "SAFE_DEFAULT", policy: { ...safePolicy, buy: active ? "AUTOMATIC" : "MANUAL_APPROVAL" } });
      if (path.endsWith("/drafts")) return jsonResponse({ version_id: "policy-1", sequence: 1, state: "DRAFT", policy: safePolicy, reason: "모의 자동화", created_at: "2026-08-04T01:00:00Z", validated_at: null, activated_at: null });
      if (path.endsWith("/validate")) return jsonResponse({ version_id: "policy-1", sequence: 1, state: "VALIDATED", policy: safePolicy, reason: "모의 자동화", created_at: "2026-08-04T01:00:00Z", validated_at: "2026-08-04T01:01:00Z", activated_at: null });
      if (path === "/api/v1/auth/reauth/totp") return jsonResponse({ reauth_proof: "policy-proof", expires_at: "2026-08-04T01:05:00Z" });
      if (path.endsWith("/activate")) { active = true; return jsonResponse({ version_id: "policy-1", sequence: 1, state: "ACTIVE", policy: safePolicy, reason: "모의 자동화", created_at: "2026-08-04T01:00:00Z", validated_at: "2026-08-04T01:01:00Z", activated_at: "2026-08-04T01:02:00Z" }); }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect(await screen.findByText("안전 기본값 · 미저장")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("일반 매수 실행 모드"), "AUTOMATIC");
    await user.type(screen.getByLabelText("변경 사유"), "모의 자동화");
    await user.click(screen.getByRole("button", { name: "변경안 검증" }));
    expect(await screen.findByRole("dialog", { name: "실행 권한 활성화" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("현재 TOTP 코드"), "123456");
    await user.click(screen.getByRole("button", { name: "활성화" }));
    expect(await screen.findByText(/새 활성 버전으로 적용/)).toBeInTheDocument();
    expect(await screen.findByText("policy-1")).toBeInTheDocument();
    const proofCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/auth/reauth/totp");
    expect(JSON.parse(String(proofCall?.[1]?.body))).toEqual(expect.objectContaining({
      target_action: "EXECUTION_POLICY_ACTIVATE", target_id: "policy-1",
    }));
  });

  it("creates only a credential-free Mock provider from the SHADOW foundation panel", async () => {
    const safePolicy = {
      buy: "MANUAL_APPROVAL", partial_sell: "MANUAL_APPROVAL", full_sell: "MANUAL_APPROVAL",
      take_profit: "MANUAL_APPROVAL", fixed_stop_loss: "AUTOMATIC", trailing_stop: "AUTOMATIC",
      end_of_day_liquidation: "AUTOMATIC", emergency_exit: "AUTOMATIC",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-llm", login_id: "admin", expires_at: "2026-08-05T09:00:00Z", csrf_token: "csrf-llm" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/settings/execution-policy") return jsonResponse({ active_version_id: null, source: "SAFE_DEFAULT", policy: safePolicy });
      if (path === "/api/v1/ai/providers" && init?.method === "POST") return jsonResponse({ id: "provider-1" }, 201);
      if (path === "/api/v1/ai/providers") return jsonResponse({ schema_version: "1.0", request_id: "providers-1", items: [] });
      if (path === "/api/v1/ai/models") return jsonResponse({ schema_version: "1.0", request_id: "models-1", items: [] });
      if (path === "/api/v1/ai/routes") return jsonResponse({ schema_version: "1.0", request_id: "routes-1", items: [] });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect(await screen.findByText("SHADOW ONLY")).toBeInTheDocument();
    expect(screen.queryByLabelText(/credential/i)).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("Provider 이름"));
    await user.type(screen.getByLabelText("Provider 이름"), "foundation-mock");
    await user.click(screen.getAllByRole("button", { name: "초안 생성" })[0]);
    expect(await screen.findByText("Mock Provider 초안이 생성되었습니다.")).toBeInTheDocument();

    const createCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/ai/providers" && init?.method === "POST");
    expect(createCall?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-llm" }),
    }));
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      schema_version: "1.0",
      name: "foundation-mock",
      adapter_type: "MOCK",
      endpoint: null,
      credential_secret_ref: null,
      data_policy: "NONE",
    });
  });
});

describe("Mock AI decisions", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows execution-policy routing without creating an order", async () => {
    const decision = {
      decision_id: "decision-1", evaluation_request_id: "evaluation-1", symbol: "005930", market: "KRX",
      input_snapshot_id: "snapshot-1", decision_input_id: "input-1",
      input_schema_version: "scout-input-v1", input_hash: "abcdef1234567890",
      indicator_snapshot_id: "indicator-1", indicator_calculator_version: "watch-indicators-v2",
      model_id: "deterministic-mock-v2", prompt_version: "mock-entry-indicators-v2",
      scout: { trend_state: "UPTREND", entry_score: 75, reason_codes: ["BREAKOUT_CONFIRMED"] },
      core: { action: "BUY", confidence: "0.75", risk_level: "MEDIUM", reason_codes: ["BREAKOUT_CONFIRMED"] },
      purpose: "DIAGNOSTIC", configuration_version_id: null, execution_mode: null,
      execution_outcome: "NO_ACTION", execution: null,
      valid_until: "2026-08-04T01:01:00Z", created_at: "2026-08-04T01:00:00Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-7", login_id: "admin", expires_at: "2026-08-04T09:00:00Z", csrf_token: "csrf-ai" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/decisions") return jsonResponse({ items: [] });
      if (path === "/api/v1/decisions/mock-evaluate") return jsonResponse(decision);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /AI 판단/ }));
    expect(await screen.findByText(/주문이나 승인을 생성하지 않습니다/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Mock 판단 실행" }));
    expect(await screen.findByText(/진단 판단이 BUY/)).toBeInTheDocument();
    expect((await screen.findAllByText("DIAGNOSTIC")).length).toBeGreaterThan(0);
    expect(await screen.findByText("scout-input-v1")).toBeInTheDocument();
    expect(await screen.findByText("watch-indicators-v2")).toBeInTheDocument();
    expect(await screen.findByText("abcdef123456")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes("/orders"))).toBe(false);
  });
});

describe("Agent Runtime v1", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("runs only when five validated SHADOW routes are ready and shows no-order boundary", async () => {
    const roles = ["TECHNICAL_SCOUT", "NEWS_DISCLOSURE_SCOUT", "MARKET_SECTOR_SCOUT", "POSITION_RISK_SCOUT", "CORE"];
    const routes = roles.map((role, index) => ({
      id: `route-${index}`, role, primary_model_profile_id: "model-1", primary_model_alias: "agent-runtime-v1",
      fallback_policy: "NONE", execution_stage: "SHADOW", timeout_ms: 10000, max_attempts: 1,
      daily_call_limit: 100, daily_cost_limit_krw: "0", prompt_version: `${role}-v1`,
      output_schema_version: "agent-assessment-v1", state: "VALIDATED", reason: "fixture",
      validated_at: "2026-08-06T01:00:00Z", version: 2, created_at: "2026-08-06T00:00:00Z",
    }));
    const run = {
      schema_version: "1.0", request_id: "agent-1", run_id: "run-1", created: true,
      purpose: "DIAGNOSTIC", execution_stage: "SHADOW", market: "KRX", symbol: "005930",
      market_snapshot_id: "snapshot-1", input_hash: "a".repeat(64), dag_version: "agent-dag-v1",
      route_versions: {}, state: "PARTIAL", core_action: "WAIT", valid_until: "2026-08-06T01:01:00Z",
      stages: roles.map((role, index) => ({
        stage_run_id: `stage-${index}`, role, sequence: index + 1, dependencies: [], route_id: `route-${index}`,
        state: role === "NEWS_DISCLOSURE_SCOUT" ? "INSUFFICIENT_DATA" : "SUCCEEDED",
        input_hash: "b".repeat(64), output: {}, output_hash: "c".repeat(64), error_code: null,
        invocation: { invocation_id: `inv-${index}`, state: "SUCCEEDED", actual_provider: "CRESTA_MOCK", actual_model: "deterministic-mock-v2", latency_ms: 0, validation_status: "PASSED", error_code: null },
        started_at: "2026-08-06T01:00:00Z", completed_at: "2026-08-06T01:00:00Z",
      })),
      evidence_bundle: { bundle_id: "bundle-1", state: "PARTIAL", policy_version: "fixture-none-v1", evidence_ids: [], reason_codes: ["NO_EXTERNAL_EVIDENCE_FIXTURE"], bundle_hash: "d".repeat(64), as_of: "2026-08-06T01:00:00Z" },
      created_at: "2026-08-06T01:00:00Z", completed_at: "2026-08-06T01:00:00Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-agent", login_id: "admin", expires_at: "2026-08-06T09:00:00Z", csrf_token: "csrf-agent" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/decisions") return jsonResponse({ items: [] });
      if (path === "/api/v1/ai/agent-runs" && !init?.method) return jsonResponse({ schema_version: "1.0", request_id: "runs-1", items: [] });
      if (path === "/api/v1/ai/routes") return jsonResponse({ schema_version: "1.0", request_id: "routes-1", items: routes });
      if (path === "/api/v1/ai/agent-runs/diagnostic") return jsonResponse(run, 201);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /AI 판단/ }));
    expect(await screen.findByText("DIAGNOSTIC · SHADOW · 주문 없음")).toBeInTheDocument();
    expect(await screen.findByText(/Route 준비: 5\/5/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "DIAGNOSTIC DAG 실행" }));
    expect(await screen.findByText(/주문은 생성되지 않았습니다/)).toBeInTheDocument();
    expect(await screen.findByText("WAIT")).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([path]) => path === "/api/v1/ai/agent-runs/diagnostic");
    expect(call?.[1]).toEqual(expect.objectContaining({ headers: expect.objectContaining({ "X-CSRF-Token": "csrf-agent" }) }));
    expect(JSON.parse(String(call?.[1]?.body)).route_ids).toEqual(Object.fromEntries(roles.map((role, index) => [role, `route-${index}`])));
  });
});
