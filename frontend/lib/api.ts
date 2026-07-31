export type SessionData = {
  request_id: string;
  login_id: string;
  expires_at: string;
  csrf_token: string;
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
      body: JSON.stringify({ login_id: loginId, password }),
    });
  },
  totp(challengeId: string, code: string) {
    return request<SessionData>("/api/v1/auth/login/totp", {
      method: "POST",
      body: JSON.stringify({ challenge_id: challengeId, totp_code: code }),
    });
  },
  logout(csrfToken: string) {
    return request<{ status: string }>("/api/v1/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
  },
};
