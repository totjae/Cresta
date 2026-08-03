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
