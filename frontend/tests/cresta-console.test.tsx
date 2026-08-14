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
  pause_entry_active: false,
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

  it("activates persistent PAUSE_ENTRY from the Guard dashboard", async () => {
    let active = false;
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(window, "prompt").mockReturnValue("모의 신규매수 즉시 중지");
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "session-stop", login_id: "admin", expires_at: "2026-08-12T09:00:00Z", csrf_token: "csrf-stop" });
      if (path === "/api/v1/system/health") return jsonResponse({ ...healthResponse, pause_entry_active: active, buy_execution_block_reason: active ? "EMERGENCY_STOP_ACTIVE" : "ORDER_SIZE_NOT_CONFIGURED" });
      if (path === "/api/v1/risk/emergency-stop" && init?.method === "POST") {
        active = true;
        return jsonResponse({ schema_version: "1.0", request_id: "stop-1", stop_id: "stop-id", account_alias: "KIWOOM_MOCK_PRIMARY", level: "PAUSE_ENTRY", state: "ACTIVE", reason: "모의 신규매수 즉시 중지", version: 1, activated_at: "2026-08-12T01:00:00Z", released_at: null });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: "신규 매수 즉시 중지" }));
    expect(await screen.findByText("EMERGENCY_STOP_ACTIVE")).toBeInTheDocument();
    expect(await screen.findByText("PAUSE_ENTRY가 활성화되었습니다.")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/risk/emergency-stop" && init?.method === "POST");
    expect(call?.[1]?.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "csrf-stop", "Idempotency-Key": expect.stringContaining("pause-entry-") }));
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
    const venueSelection = {
      schema_version: "1.0", request_id: "venue-1", selection_id: "selection-1",
      policy_version: "venue-selection-v2", execution_stage: "SHADOW",
      order_creation_allowed: false, environment: "MOCK", symbol: "005930", side: "BUY",
      quantity: 1, order_type: "LIMIT", urgency: "NORMAL", session: "DUAL_CONTINUOUS",
      trading_day_status: "OPEN", calendar_reason: "WEEKDAY", calendar_policy_version: "krx-calendar-v2", calendar_override_id: null,
      nxt_eligible: true, nxt_eligibility_status: "VERIFIED", sor_supported: false,
      selected_venue: "NXT", state: "SELECTED", reason_codes: ["BETTER_EXECUTABLE_PRICE_NXT"],
      quotes: {
        KRX: { market: "KRX", snapshot_id: "krx-1", bid_price: "70000.0000", bid_quantity: 100, ask_price: "70100.0000", ask_quantity: 100, event_at: "2026-08-12T01:00:00Z", valid: true },
        NXT: { market: "NXT", snapshot_id: "nxt-1", bid_price: "70050.0000", bid_quantity: 80, ask_price: "70090.0000", ask_quantity: 90, event_at: "2026-08-12T01:00:00Z", valid: true },
      },
      input_hash: "a".repeat(64), evaluated_at: "2026-08-12T01:00:00Z", created_at: "2026-08-12T01:00:00Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "session-watch", login_id: "admin", expires_at: "2026-08-04T09:00:00Z", csrf_token: "csrf-watch" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/watchlist" && init?.method === "POST") return jsonResponse(populatedWatchlist, 201);
      if (path === "/api/v1/watchlist") return jsonResponse(emptyWatchlist);
      if (path === "/api/v1/venue-selections/diagnostic" && init?.method === "POST") return jsonResponse(venueSelection);
      if (path === "/api/v1/venue-selections?limit=20") return jsonResponse({ schema_version: "1.0", request_id: "venue-list", items: [venueSelection] });
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
    expect(await screen.findByText("거래시장 SHADOW 평가")).toBeInTheDocument();
    expect(screen.getAllByText("SHADOW · 주문 없음").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("BETTER_EXECUTABLE_PRICE_NXT")).toBeInTheDocument();
    expect(screen.getByText("OPEN · WEEKDAY")).toBeInTheDocument();
    expect(screen.getByText("krx-calendar-v2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "SHADOW 평가" }));
    const createCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/watchlist" && init?.method === "POST");
    expect(createCall?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-watch" }),
    }));
    const venueCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/venue-selections/diagnostic" && init?.method === "POST");
    expect(JSON.parse(String(venueCall?.[1]?.body))).toEqual({
      schema_version: "1.0", symbol: "005930", side: "BUY", quantity: 1,
      order_type: "LIMIT", urgency: "NORMAL",
    });
    expect(screen.queryByRole("button", { name: /주문 생성/ })).not.toBeInTheDocument();
  });

  it("shows persisted Paper read models without order creation controls", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-4", login_id: "admin", expires_at: "2026-08-01T09:00:00Z", csrf_token: "csrf-paper" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/orders") return jsonResponse({ schema_version: "1.0", request_id: "orders-1", items: [{ id: "order-1", order_group_id: "group-1", parent_order_id: null, symbol: "005930", market: "KRX", side: "BUY", order_type: "LIMIT", limit_price: "269000.0000", requested_quantity: 2, filled_quantity: 1, cancelled_quantity: 0, remaining_quantity: 1, status: "PARTIALLY_FILLED", environment: "MOCK", client_order_id: "client-1", broker_order_id: "1234567", replacement_sequence: 0, unfilled_policy: "CANCEL", fill_timeout_seconds: 10, max_reprice_attempts: 0, reprice_attempts: 0, next_action_at: "2026-08-14T00:01:10Z", trading_date: "2026-08-14", version: 2, created_at: "2026-08-14T00:01:00Z", updated_at: "2026-08-14T00:01:01Z" }] });
      if (path === "/api/v1/positions") return jsonResponse({ schema_version: "1.0", request_id: "positions-1", items: [{ id: "position-1", account_alias: "KIWOOM_MOCK_PRIMARY", environment: "MOCK", market: "KRX", symbol: "005930", quantity: 1, available_quantity: 1, average_price: "269000.0000", managed_quantity: 0, managed_average_price: "0.0000", external_quantity: 1, state: "OPEN", origin: "EXTERNAL", version: 1, created_at: "2026-08-14T00:00:00Z", updated_at: "2026-08-14T00:01:00Z" }] });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    expect(await screen.findByText("Paper Broker 조회 연결")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /승인·주문/ }));
    expect(await screen.findByText("잔량 취소")).toBeInTheDocument();
    expect(screen.getByText("2 / 1 / 0 / 1")).toBeInTheDocument();
    expect(screen.getByText("운영 화면에서 주문·체결을 생성하지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /주문 생성/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /보유 포지션/ }));
    expect(await screen.findByText("005930")).toBeInTheDocument();
    expect(screen.getByText("총 보유 수량")).toBeInTheDocument();
    expect(screen.getByText("매도 가능")).toBeInTheDocument();
    expect(screen.getByText("Cresta 관리")).toBeInTheDocument();
    expect(screen.getByText("외부 보유")).toBeInTheDocument();
    expect(screen.getByText("키움 외부 보유")).toBeInTheDocument();
  });
});

describe("Kiwoom MOCK browser diagnostic", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("queues one share after an authenticated confirmation", async () => {
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
    await user.click(screen.getByRole("button", { name: "모의주문 실행" }));
    expect(await screen.findByText(/CREATED 상태로 등록/)).toBeInTheDocument();

    const orderCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/system/broker/mock-order-test");
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/auth/reauth/totp")).toBe(false);
    expect(orderCall?.[1]).toEqual(expect.objectContaining({ method: "POST", headers: expect.objectContaining({ "X-CSRF-Token": "csrf-mock-order" }) }));
    expect(JSON.parse(String(orderCall?.[1]?.body))).toEqual(expect.objectContaining({
      symbol: "005930", order_type: "MARKET", limit_price: null,
      confirmation: "KIWOOM_MOCK_ONE_SHARE",
    }));
  });
});

describe("execution policy settings", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("validates and activates independent action modes without reauthentication", async () => {
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
      if (path.endsWith("/activate")) { active = true; return jsonResponse({ version_id: "policy-1", sequence: 1, state: "ACTIVE", policy: safePolicy, reason: "모의 자동화", created_at: "2026-08-04T01:00:00Z", validated_at: "2026-08-04T01:01:00Z", activated_at: "2026-08-04T01:02:00Z" }); }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect((await screen.findAllByText("안전 기본값 · 미저장")).length).toBeGreaterThan(0);
    await user.selectOptions(screen.getByLabelText("일반 매수 실행 모드"), "AUTOMATIC");
    await user.type(screen.getByLabelText("변경 사유"), "모의 자동화");
    await user.click(screen.getByRole("button", { name: "변경안 검증" }));
    expect(await screen.findByRole("dialog", { name: "실행 권한 활성화" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "활성화" }));
    expect(await screen.findByText(/새 활성 버전으로 적용/)).toBeInTheDocument();
    expect(await screen.findByText("policy-1")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/auth/reauth/totp")).toBe(false);
  });

  it("validates and activates Guard risk settings without opening the BUY gate", async () => {
    const safePolicy = {
      buy: "MANUAL_APPROVAL", partial_sell: "MANUAL_APPROVAL", full_sell: "MANUAL_APPROVAL",
      take_profit: "MANUAL_APPROVAL", fixed_stop_loss: "AUTOMATIC", trailing_stop: "AUTOMATIC",
      end_of_day_liquidation: "AUTOMATIC", emergency_exit: "AUTOMATIC",
    };
    const riskPolicy = {
      entry_order_amount: null, max_single_order_amount: 1000000,
      max_position_amount_per_symbol: 1000000, max_total_position_amount: 3000000,
      max_open_positions: 3, max_daily_entries: 5, fixed_stop_loss_pct: "-2.0",
      quote_stale_seconds: 2, max_spread_pct: "0.30", max_price_deviation_pct: "0.50",
      daily_loss_limit_pct: "5.0", daily_loss_basis: "REALIZED_PLUS_UNREALIZED", max_consecutive_losses: 3,
    };
    let active = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-risk", login_id: "admin", expires_at: "2026-08-11T09:00:00Z", csrf_token: "csrf-risk" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/settings/execution-policy") return jsonResponse({ active_version_id: null, source: "SAFE_DEFAULT", policy: safePolicy });
      if (path === "/api/v1/settings/risk-policy" && !init?.method) return jsonResponse({ active_version_id: active ? "risk-1" : null, source: active ? "USER_DEFAULT" : "SAFE_DEFAULT", policy: { ...riskPolicy, entry_order_amount: active ? 500000 : null } });
      if (path === "/api/v1/settings/risk-policy/drafts") return jsonResponse({ version_id: "risk-1", sequence: 1, state: "DRAFT", policy: { ...riskPolicy, entry_order_amount: 500000 }, reason: "모의 위험 설정", created_at: "2026-08-11T01:00:00Z", validated_at: null, activated_at: null });
      if (path === "/api/v1/settings/risk-policy/risk-1/validate") return jsonResponse({ version_id: "risk-1", sequence: 1, state: "VALIDATED", policy: { ...riskPolicy, entry_order_amount: 500000 }, reason: "모의 위험 설정", created_at: "2026-08-11T01:00:00Z", validated_at: "2026-08-11T01:01:00Z", activated_at: null });
      if (path === "/api/v1/settings/risk-policy/risk-1/activate") { active = true; return jsonResponse({ version_id: "risk-1", sequence: 1, state: "ACTIVE", policy: { ...riskPolicy, entry_order_amount: 500000 }, reason: "모의 위험 설정", created_at: "2026-08-11T01:00:00Z", validated_at: "2026-08-11T01:01:00Z", activated_at: "2026-08-11T01:02:00Z" }); }
      if (path.startsWith("/api/v1/ai/")) return jsonResponse({ items: [] });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect(await screen.findByText(/진입금액 미설정/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("신규진입 목표금액"), "500000");
    await user.type(screen.getByLabelText("위험 설정 변경 사유"), "모의 위험 설정");
    await user.click(screen.getByRole("button", { name: "위험 설정 검증" }));
    expect(await screen.findByRole("dialog", { name: "Guard 위험 설정 활성화" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "활성화" }));
    expect(await screen.findByText(/Guard 위험 설정이 새 활성 버전/)).toBeInTheDocument();
    expect(await screen.findByText("risk-1")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/auth/reauth/totp")).toBe(false);
  });

  it("creates and revokes a fail-closed calendar override without reauthentication", async () => {
    const safePolicy = {
      buy: "MANUAL_APPROVAL", partial_sell: "MANUAL_APPROVAL", full_sell: "MANUAL_APPROVAL",
      take_profit: "MANUAL_APPROVAL", fixed_stop_loss: "AUTOMATIC", trailing_stop: "AUTOMATIC",
      end_of_day_liquidation: "AUTOMATIC", emergency_exit: "AUTOMATIC",
    };
    const riskPolicy = {
      entry_order_amount: null, max_single_order_amount: 1000000,
      max_position_amount_per_symbol: 1000000, max_total_position_amount: 3000000,
      max_open_positions: 3, max_daily_entries: 5, fixed_stop_loss_pct: "-2.0",
      quote_stale_seconds: 2, max_spread_pct: "0.30", max_price_deviation_pct: "0.50",
      daily_loss_limit_pct: "5.0", daily_loss_basis: "REALIZED_PLUS_UNREALIZED", max_consecutive_losses: 3,
    };
    let item: Record<string, unknown> | null = null;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-calendar", login_id: "admin", expires_at: "2026-08-12T09:00:00Z", csrf_token: "csrf-calendar" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/settings/execution-policy") return jsonResponse({ active_version_id: null, source: "SAFE_DEFAULT", policy: safePolicy });
      if (path === "/api/v1/settings/risk-policy") return jsonResponse({ active_version_id: null, source: "SAFE_DEFAULT", policy: riskPolicy });
      if (path.startsWith("/api/v1/ai/")) return jsonResponse({ items: [] });
      if (path === "/api/v1/venue-selections/calendar-overrides?limit=100") return jsonResponse({ schema_version: "1.0", request_id: "calendar-list", items: item ? [item] : [] });
      if (path === "/api/v1/venue-selections/calendar-overrides" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        item = { schema_version: "1.0", request_id: "calendar-create", override_id: "override-1", market_date: body.market_date, override_type: "OPERATIONAL_CLOSURE", state: "ACTIVE", reason: body.reason, source_reference: body.source_reference, created_at: "2026-08-12T01:00:00Z", revoked_at: null };
        return jsonResponse(item, 201);
      }
      if (path === "/api/v1/venue-selections/calendar-overrides/override-1" && init?.method === "DELETE") {
        item = { ...item, state: "REVOKED", revoked_at: "2026-08-12T01:05:00Z" };
        return jsonResponse(item);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect(await screen.findByText("거래 캘린더 운영 휴장")).toBeInTheDocument();
    await user.type(screen.getByLabelText("운영 휴장 날짜"), "2026-08-13");
    await user.type(screen.getByLabelText("운영 휴장 사유"), "거래소 임시 휴장 공지");
    await user.type(screen.getByLabelText("운영 휴장 출처"), "KRX notice 2026-test");
    await user.click(screen.getByRole("button", { name: "운영 휴장 등록" }));
    expect(await screen.findByText(/운영 휴장이 활성화/)).toBeInTheDocument();
    expect(await screen.findByText("2026-08-13")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "해제" }));
    expect(await screen.findByText(/과거 평가 이력은 그대로 유지/)).toBeInTheDocument();
    expect(await screen.findByText("해제됨")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/auth/reauth/totp")).toBe(false);
    const createCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/venue-selections/calendar-overrides" && init?.method === "POST");
    expect(createCall?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-calendar" }),
    }));
  });

  it("registers a provider after confirmation and successful model discovery", async () => {
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
      if (path === "/api/v1/ai/provider-catalog") return jsonResponse({ items: [{ template_id: "openai", adapter_type: "OPENAI_RESPONSES", label: "OpenAI", can_register: true, support_level: "compatible", configuration_fields: [] }] });
      if (path.endsWith("/provider-registrations/preview")) return jsonResponse({ target_action: "LLM_PROVIDER_REGISTER", target_id: "registration-target" });
      if (path.endsWith("/provider-registrations") && init?.method === "POST") return jsonResponse({ provider: { id: "provider-1" }, models: [{ id: "model-1" }] }, 201);
      if (path === "/api/v1/ai/providers") return jsonResponse({ schema_version: "1.0", request_id: "providers-1", items: [] });
      if (path === "/api/v1/ai/models") return jsonResponse({ schema_version: "1.0", request_id: "models-1", items: [] });
      if (path === "/api/v1/ai/prompts") return jsonResponse({ schema_version: "1.0", request_id: "prompts-1", items: [] });
      if (path === "/api/v1/ai/routes") return jsonResponse({ schema_version: "1.0", request_id: "routes-1", items: [] });
      if (path === "/api/v1/ai/role-assignments") return jsonResponse({ schema_version: "1.0", request_id: "assignments-1", items: [] });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect(await screen.findByText("SHADOW ONLY")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Models" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Provider" }));
    await user.click(screen.getByRole("button", { name: "Provider 추가" }));
    await user.type(screen.getByLabelText("연결 이름"), "openai-primary");
    await user.type(screen.getByLabelText("API 키"), "secret-provider-key");
    await user.click(screen.getAllByRole("button", { name: "연결 시험 및 등록" }).at(-1)!);
    expect(await screen.findByRole("dialog", { name: "Provider 연결 시험 및 등록" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "연결 시험 및 등록" }).at(-1)!);
    expect(await screen.findByText(/1개 모델을 확인/)).toBeInTheDocument();

    const createCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/v1/ai/provider-registrations" && init?.method === "POST");
    expect(createCall?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-llm" }),
    }));
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual(expect.objectContaining({
      name: "openai-primary", template_id: "openai", configuration: {}, credential: "secret-provider-key",
    }));
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/auth/reauth/totp")).toBe(false);
  });

  it("selects one reusable model per role and activates the set atomically", async () => {
    const roles = ["TECHNICAL_SCOUT", "NEWS_DISCLOSURE_SCOUT", "MARKET_SECTOR_SCOUT", "POSITION_RISK_SCOUT", "CORE"];
    const route = (role: string, index: number, state = "VALIDATED") => ({
      id: `assignment-route-${index}`, role, primary_model_profile_id: "model-shared", primary_model_alias: "shared-model",
      failure_policy: "FAIL_STOP", fallback_model_profile_id: null, fallback_model_alias: null, execution_stage: "SHADOW", timeout_ms: 10000, service_tier: "DEFAULT", web_search_enabled: false, max_attempts: 1,
      daily_call_limit: 100, daily_cost_limit_krw: "0", prompt_version: `${role}-v1`,
      output_schema_version: role === "CORE" ? "agent-core-v1" : "agent-assessment-v1",
      temperature_override: "0.1", top_p_override: null, max_output_tokens_override: 512,
      reasoning_effort_override: null, seed_override: 11,
      effective_parameters: {
        temperature: "0.100", temperature_source: "ROLE_OVERRIDE", top_p: "0.900",
        top_p_source: "MODEL_DEFAULT", max_output_tokens: 512, max_output_tokens_source: "ROLE_OVERRIDE",
        reasoning_effort: null, reasoning_effort_source: "ADAPTER_DEFAULT", seed: 11, seed_source: "ROLE_OVERRIDE",
      },
      state, reason: "fixture", validated_at: "2026-08-06T01:00:00Z", version: 2,
      created_at: `2026-08-06T00:0${index}:00Z`,
    });
    const routes = roles.map((roleName, index) => route(roleName, index));
    let active = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return jsonResponse({ request_id: "req-assign", login_id: "admin", expires_at: "2026-08-06T09:00:00Z", csrf_token: "csrf-assign" });
      if (path === "/api/v1/system/health") return jsonResponse(healthResponse);
      if (path === "/api/v1/settings/execution-policy") return jsonResponse({ active_version_id: null, source: "SAFE_DEFAULT", policy: { buy: "MANUAL_APPROVAL", partial_sell: "MANUAL_APPROVAL", full_sell: "MANUAL_APPROVAL", take_profit: "MANUAL_APPROVAL", fixed_stop_loss: "AUTOMATIC", trailing_stop: "AUTOMATIC", end_of_day_liquidation: "AUTOMATIC", emergency_exit: "AUTOMATIC" } });
      if (path === "/api/v1/ai/provider-catalog") return jsonResponse({ items: [] });
      if (path === "/api/v1/ai/providers") return jsonResponse({ items: [] });
      if (path === "/api/v1/ai/models") return jsonResponse({ items: [] });
      if (path === "/api/v1/ai/prompts") return jsonResponse({ items: [] });
      if (path === "/api/v1/ai/routes") return jsonResponse({ items: active ? routes.map((item) => ({ ...item, state: "ACTIVE" })) : routes });
      if (path === "/api/v1/ai/role-assignments" && !init?.method) return jsonResponse({ items: roles.map((role, index) => ({ role, current: active ? { ...routes[index], state: "ACTIVE" } : null, candidates: active ? [] : [routes[index]], history_count: 1, status: active ? "ACTIVE" : "CANDIDATE" })) });
      if (path.endsWith("/activation-preview")) return jsonResponse({ target_action: "LLM_ROLE_ASSIGNMENT_ACTIVATE", target_id: "assignment-target", routes });
      if (path.endsWith("/role-assignments/activate")) { active = true; return jsonResponse({ routes: routes.map((item) => ({ ...item, state: "ACTIVE" })) }); }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /전략·설정/ }));
    expect(await screen.findByText("역할별 현재 모델")).toBeInTheDocument();
    expect(screen.getByText("0/5 ACTIVE")).toBeInTheDocument();
    expect(screen.getAllByText(/timeout 10s/)).toHaveLength(5);
    const timeoutInputs = screen.getAllByLabelText(/^전체 응답 제한\(초\)/);
    const maxOutputInputs = screen.getAllByLabelText("max output");
    expect(screen.getAllByLabelText("서비스 티어")[0]).toHaveValue("DEFAULT");
    expect(timeoutInputs[0]).toHaveValue(120);
    expect(maxOutputInputs).toHaveLength(5);
    expect(maxOutputInputs.every((input) => (input as HTMLInputElement).value === "8192")).toBe(true);
    await user.click(screen.getByRole("button", { name: "현재 배정 적용" }));
    expect(await screen.findByRole("dialog", { name: "역할별 모델 배정 적용" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "5개 역할 적용" }));
    expect(await screen.findByText(/원자적으로 적용/)).toBeInTheDocument();
    expect(await screen.findByText("5/5 ACTIVE")).toBeInTheDocument();

    expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/auth/reauth/totp")).toBe(false);
    const activationCall = fetchMock.mock.calls.find(([path]) => path === "/api/v1/ai/role-assignments/activate");
    expect(JSON.parse(String(activationCall?.[1]?.body)).route_ids).toEqual(
      Object.fromEntries(roles.map((role, index) => [role, `assignment-route-${index}`])),
    );
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

describe("Agent Worker v2", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("admits only when five ACTIVE SHADOW routes are ready and shows no-order boundary", async () => {
    const roles = ["TECHNICAL_SCOUT", "NEWS_DISCLOSURE_SCOUT", "MARKET_SECTOR_SCOUT", "POSITION_RISK_SCOUT", "CORE"];
    const routes = roles.map((role, index) => ({
      id: `route-${index}`, role, primary_model_profile_id: "model-1", primary_model_alias: "agent-runtime-v1",
      failure_policy: "FAIL_STOP", fallback_model_profile_id: null, fallback_model_alias: null, execution_stage: "SHADOW", timeout_ms: 10000, service_tier: "DEFAULT", web_search_enabled: false, max_attempts: 1,
      daily_call_limit: 100, daily_cost_limit_krw: "0", prompt_version: `${role}-v1`,
      output_schema_version: "agent-assessment-v1", state: "ACTIVE", reason: "fixture",
      validated_at: "2026-08-06T01:00:00Z", version: 2, created_at: "2026-08-06T00:00:00Z",
    }));
    const run = {
      schema_version: "1.0", request_id: "agent-1", run_id: "run-1", created: true,
      purpose: "DIAGNOSTIC", execution_stage: "SHADOW", market: "KRX", symbol: "005930",
      market_snapshot_id: "snapshot-1", input_hash: "a".repeat(64), dag_version: "agent-dag-v6",
      analysis_context: "ENTRY", position_snapshot_hash: "f".repeat(64),
      server_input_policy_version: "agent-server-input-v1", market_context_snapshot_id: null, market_context_snapshot_hash: null,
      assessment_schema_version: "agent-assessment-v2", core_schema_version: "agent-core-v2", score_policy_version: "score-policy-v1",
      route_versions: {}, state: "PARTIAL", core_action: "WAIT", shadow_assessment: "UNKNOWN", valid_until: "2026-08-06T01:01:00Z",
      stages: roles.map((role, index) => ({
        stage_run_id: `stage-${index}`, role, sequence: index + 1, dependencies: [], route_id: `route-${index}`,
        state: role === "NEWS_DISCLOSURE_SCOUT" ? "INSUFFICIENT_DATA" : role === "POSITION_RISK_SCOUT" ? "NOT_APPLICABLE" : "SUCCEEDED",
        input_hash: "b".repeat(64), output: {}, output_hash: "c".repeat(64), error_code: null,
        attempt_count: 1, max_attempts: 1, fencing_token: 1, lease_expires_at: null, timeout_at: "2026-08-06T01:00:10Z",
        invocation: role === "POSITION_RISK_SCOUT" ? null : { invocation_id: `inv-${index}`, attempt_number: 1, requested_model_profile_id: "model-1", state: "SUCCEEDED", actual_provider: "CRESTA_MOCK", actual_model: "deterministic-mock-v2", latency_ms: 0, validation_status: "PASSED", error_code: null, fallback_path: [], created_at: "2026-08-06T01:00:00Z" },
        invocations: role === "POSITION_RISK_SCOUT" ? [] : [{ invocation_id: `inv-${index}`, attempt_number: 1, requested_model_profile_id: "model-1", state: "SUCCEEDED", actual_provider: "CRESTA_MOCK", actual_model: "deterministic-mock-v2", latency_ms: 0, validation_status: "PASSED", error_code: null, fallback_path: [], created_at: "2026-08-06T01:00:00Z" }],
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
      if (path === "/api/v1/ai/agent-runs/run-1/invocations/inv-0/output") return jsonResponse({
        schema_version: "1.0", request_id: "output-1", run_id: "run-1", stage_run_id: "stage-0",
        invocation_id: "inv-0", state: "SUCCEEDED", validation_status: "PASSED", error_code: null,
        output_available: true, model_output: { assessment: "CAUTION", reason_codes: ["PRICE_BELOW_VWAP"] },
        model_output_hash: "e".repeat(64), captured_at: "2026-08-06T01:00:00Z",
      });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: /AI 판단/ }));
    expect(await screen.findByText("비동기 · SHADOW · 주문 없음")).toBeInTheDocument();
    expect(await screen.findByText(/Route 준비: 5\/5/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "DIAGNOSTIC DAG 등록" }));
    expect(await screen.findByText(/Worker가 비동기로 실행/)).toBeInTheDocument();
    expect(await screen.findByText("실행 WAIT")).toBeInTheDocument();
    expect(await screen.findByText("UNKNOWN")).toBeInTheDocument();
    expect(await screen.findByText(/agent-server-input-v1/)).toBeInTheDocument();
    expect(await screen.findByText(/POSITION_RISK_SCOUT: 해당 없음/)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "구조화 응답 보기" })[0]);
    expect(await screen.findByText(/Provider 원문이 아니라 Adapter가 추출한/)).toBeInTheDocument();
    expect(await screen.findByText(/PRICE_BELOW_VWAP/)).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([path]) => path === "/api/v1/ai/agent-runs/diagnostic");
    expect(call?.[1]).toEqual(expect.objectContaining({ headers: expect.objectContaining({ "X-CSRF-Token": "csrf-agent" }) }));
    expect(JSON.parse(String(call?.[1]?.body)).route_ids).toEqual(Object.fromEntries(roles.map((role, index) => [role, `route-${index}`])));
  });
});
