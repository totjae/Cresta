import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CrestaConsole } from "../components/cresta-console";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

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
      .mockImplementationOnce(() => jsonResponse({ request_id: "req-2", login_id: "admin", expires_at: "2026-07-31T09:00:00Z", csrf_token: "csrf-memory-only" }));
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
    expect(screen.getByText("Console 인증 완료")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("restores an active session and logs out with the in-memory CSRF token", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => jsonResponse({ request_id: "req-3", login_id: "admin", expires_at: "2026-07-31T09:00:00Z", csrf_token: "csrf-logout" }))
      .mockImplementationOnce(() => jsonResponse({ status: "LOGGED_OUT" }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CrestaConsole />);

    await user.click(await screen.findByRole("button", { name: "로그아웃" }));
    expect(await screen.findByRole("heading", { name: "관리자 로그인" })).toBeInTheDocument();
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/logout");
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({
      method: "POST",
      headers: { "X-CSRF-Token": "csrf-logout" },
    }));
  });
});
