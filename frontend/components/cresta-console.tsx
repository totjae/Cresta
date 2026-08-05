"use client";

import {
  Activity,
  Bell,
  Bot,
  ChevronRight,
  CircleAlert,
  Clock3,
  Database,
  Eye,
  Gauge,
  History,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  Radio,
  ReceiptText,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Smartphone,
  UserRound,
  WalletCards,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  agentApi,
  AgentRunData,
  authApi,
  BrokerStatus,
  decisionApi,
  DecisionData,
  ExecutionMode,
  ExecutionPolicy,
  llmApi,
  LlmModelProfile,
  LlmProviderProfile,
  LlmRoleRoute,
  orderApi,
  OrderDetail,
  OrderSummary,
  positionApi,
  PositionSummary,
  SessionData,
  settingsApi,
  systemApi,
  SystemHealth,
  watchlistApi,
  WatchlistData,
} from "../lib/api";

type Screen = "boot" | "credentials" | "totp" | "console";
type ConsolePage = "dashboard" | "watchlist" | "positions" | "orders" | "decisions" | "settings" | "system";

const SAFE_AUTH_ERROR = "인증 정보를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.";

const navigation = [
  ["dashboard", "대시보드", LayoutDashboard, true],
  ["watchlist", "감시 종목", Eye, true],
  ["positions", "보유 포지션", WalletCards, true],
  ["orders", "승인·주문", ListChecks, true],
  ["decisions", "AI 판단", Bot, false],
  ["settings", "전략·설정", Settings2, false],
  ["risk", "리스크", ShieldCheck, false],
  ["system", "시스템 상태", Activity, false],
  ["audit", "이력·감사", History, false],
] as const;

const activeNavigation = navigation.map((item) =>
  item[0] === "system" || item[0] === "settings" || item[0] === "decisions"
    ? ([item[0], item[1], item[2], true] as const)
    : item,
);

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "brand-compact" : ""}`} aria-label="Cresta">
      <span className="brand-mark" aria-hidden="true">C</span>
      {!compact && (
        <span>
          <strong>Cresta</strong>
          <small>AI-ASSISTED TRADING</small>
        </span>
      )}
    </div>
  );
}

function LoginPanel({
  screen,
  busy,
  error,
  onPassword,
  onTotp,
  onBack,
}: {
  screen: "credentials" | "totp";
  busy: boolean;
  error: string;
  onPassword: (loginId: string, password: string) => Promise<void>;
  onTotp: (code: string) => Promise<void>;
  onBack: () => void;
}) {
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const totpRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (screen === "totp") totpRef.current?.focus();
  }, [screen]);

  async function submitCredentials(event: FormEvent) {
    event.preventDefault();
    const submittedPassword = password;
    setPassword("");
    await onPassword(loginId, submittedPassword);
  }

  async function submitTotp(event: FormEvent) {
    event.preventDefault();
    const submittedCode = totp;
    setTotp("");
    await onTotp(submittedCode);
  }

  return (
    <main className="login-page">
      <div className="login-atmosphere" aria-hidden="true" />
      <section className="login-intro">
        <Brand />
        <div className="intro-copy">
          <span className="eyebrow"><Radio size={14} /> KIWOOM MOCK ENVIRONMENT</span>
          <h1>판단은 신중하게.<br />위험 통제는 단호하게.</h1>
          <p>선택한 국내주식의 흐름을 감시하고, 정해진 원칙 안에서 판단과 주문을 관리합니다.</p>
        </div>
        <div className="trust-row">
          <span><ShieldCheck size={16} /> Guard 우선 통제</span>
          <span><Database size={16} /> 모든 판단 기록</span>
          <span><Clock3 size={16} /> 실시간 위험 감시</span>
        </div>
      </section>

      <section className="login-card" aria-labelledby="login-title">
        <div className="mobile-brand"><Brand /></div>
        <div className="step-indicator" aria-label="로그인 진행 단계">
          <span className="active">1</span><i className={screen === "totp" ? "active" : ""} />
          <span className={screen === "totp" ? "active" : ""}>2</span>
        </div>

        {screen === "credentials" ? (
          <form onSubmit={submitCredentials}>
            <span className="section-kicker">SECURE CONSOLE</span>
            <h2 id="login-title">관리자 로그인</h2>
            <p className="form-help">등록된 계정으로 Cresta Console에 접속합니다.</p>
            <label htmlFor="login-id">사용자 ID</label>
            <div className="input-shell"><UserRound size={18} /><input id="login-id" value={loginId} onChange={(event) => setLoginId(event.target.value)} autoComplete="username" required autoFocus /></div>
            <label htmlFor="password">비밀번호</label>
            <div className="input-shell"><ShieldCheck size={18} /><input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></div>
            <AuthError message={error} />
            <button className="primary-button" disabled={busy} type="submit">{busy ? "확인 중…" : <>계속 <ChevronRight size={18} /></>}</button>
          </form>
        ) : (
          <form onSubmit={submitTotp}>
            <span className="section-kicker">SECOND FACTOR</span>
            <h2 id="login-title">인증 앱 확인</h2>
            <p className="form-help">인증 앱에 표시된 현재 6자리 코드를 입력하세요.</p>
            <div className="totp-icon"><Smartphone size={28} /></div>
            <label htmlFor="totp">TOTP 인증 코드</label>
            <input ref={totpRef} className="totp-input" id="totp" value={totp} onChange={(event) => setTotp(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" pattern="[0-9]{6}" autoComplete="one-time-code" maxLength={6} required aria-describedby="totp-hint" />
            <small id="totp-hint" className="field-hint">코드는 30초마다 변경됩니다.</small>
            <AuthError message={error} />
            <button className="primary-button" disabled={busy || totp.length !== 6} type="submit">{busy ? "인증 중…" : <>Console 접속 <ChevronRight size={18} /></>}</button>
            <button className="text-button" type="button" onClick={onBack} disabled={busy}>처음부터 다시 입력</button>
          </form>
        )}
        <div className="security-note"><ShieldCheck size={15} /> 비밀번호와 인증 코드는 저장되지 않습니다.</div>
      </section>
    </main>
  );
}

function AuthError({ message }: { message: string }) {
  return <div className={`auth-error ${message ? "visible" : ""}`} role="alert" aria-live="polite">{message && <><CircleAlert size={16} /> {message}</>}</div>;
}

function ConsoleShell({
  session,
  onLogout,
  onSessionExpired,
  logoutBusy,
  error,
}: {
  session: SessionData;
  onLogout: () => Promise<void>;
  onSessionExpired: () => void;
  logoutBusy: boolean;
  error: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState<ConsolePage>("dashboard");
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [healthError, setHealthError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    systemApi.health(controller.signal).then(setHealth).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (reason instanceof ApiError && reason.status === 401) onSessionExpired();
      else setHealthError("시스템 준비 상태를 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, [onSessionExpired]);

  function selectPage(nextPage: ConsolePage) {
    setPage(nextPage);
    setMenuOpen(false);
  }

  const gateStatus = health?.trading_gate?.status ?? "NOT INITIALIZED";
  return (
    <div className="console-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="sidebar-head"><Brand /><button className="mobile-close" onClick={() => setMenuOpen(false)} aria-label="메뉴 닫기"><X /></button></div>
        <nav aria-label="주요 메뉴">
          {activeNavigation.map(([id, label, Icon, enabled]) => (
            <button
              key={id}
              className={page === id ? "selected" : ""}
              disabled={!enabled}
              title={!enabled ? "후속 구현 예정" : undefined}
              onClick={() => enabled && selectPage(id as ConsolePage)}
            >
              <Icon size={19} /><span>{label}</span>{!enabled && <small>준비 중</small>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot"><div className="avatar">{session.login_id.slice(0, 1).toUpperCase()}</div><div><strong>{session.login_id}</strong><small>관리자 · MOCK</small></div></div>
      </aside>
      {menuOpen && <button className="scrim" aria-label="메뉴 닫기" onClick={() => setMenuOpen(false)} />}

      <main className="console-main">
        <header className="topbar">
          <div className="topbar-left"><button className="menu-button" onClick={() => setMenuOpen(true)} aria-label="메뉴 열기"><Menu /></button><div className="market-state"><span className="status-dot amber" /><span className="market-label">키움 모의투자</span><b>{health?.kiwoom_broker_status ?? "확인 중"}</b></div><div className="top-divider" /><div className="market-state"><span className="status-dot muted" /><span className="market-label">Paper Gate</span><b>{gateStatus}</b></div></div>
          <div className="top-actions"><span className="mock-badge">MOCK</span><button aria-label="알림" disabled><Bell size={19} /></button><button className="logout-button" onClick={onLogout} disabled={logoutBusy}><LogOut size={17} /> {logoutBusy ? "종료 중" : "로그아웃"}</button></div>
        </header>

        <div className="content">
          {(error || healthError) && <div className="console-alert" role="alert"><CircleAlert size={17} /> {error || healthError}</div>}
          {page === "dashboard" && <DashboardPage session={session} health={health} />}
          {page === "watchlist" && <WatchlistPage session={session} onSessionExpired={onSessionExpired} />}
          {page === "orders" && <OrdersPage onSessionExpired={onSessionExpired} />}
          {page === "positions" && <PositionsPage onSessionExpired={onSessionExpired} />}
          {page === "decisions" && <DecisionsPage session={session} onSessionExpired={onSessionExpired} />}
          {page === "settings" && <SettingsPage session={session} onSessionExpired={onSessionExpired} />}
          {page === "system" && <SystemPage session={session} onSessionExpired={onSessionExpired} />}
        </div>
      </main>
      <nav className="mobile-nav" aria-label="모바일 메뉴"><button className={page === "dashboard" ? "active" : ""} onClick={() => selectPage("dashboard")}><LayoutDashboard /><span>대시보드</span></button><button className={page === "positions" ? "active" : ""} onClick={() => selectPage("positions")}><WalletCards /><span>포지션</span></button><button className={page === "orders" ? "active" : ""} onClick={() => selectPage("orders")}><ListChecks /><span>주문</span></button><button onClick={() => setMenuOpen(true)}><Menu /><span>전체</span></button></nav>
    </div>
  );
}

function PageHeading({ kicker, title, description }: { kicker: string; title: string; description: string }) {
  return <div className="page-heading"><div><span className="eyebrow"><Gauge size={14} /> {kicker}</span><h1>{title}</h1><p>{description}</p></div></div>;
}

function DashboardPage({ session, health }: { session: SessionData; health: SystemHealth | null }) {
  const paperReady = health?.paper_broker_status === "AVAILABLE";
  const readiness = paperReady ? 2 : 1;
  return <>
    <div className="page-heading"><div><span className="eyebrow"><Gauge size={14} /> CONTROL CENTER</span><h1>대시보드</h1><p>시스템 연결과 거래 준비 상태를 확인합니다.</p></div><div className="sync-stamp"><Clock3 size={15} /> 세션 활성 · {new Date(session.expires_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 만료</div></div>
    <section className="readiness-hero">
      <div><span className="card-label">SYSTEM READINESS</span><h2>{paperReady ? "Paper Broker 조회 연결" : "Console 인증 완료"}</h2><p>운영 화면은 실제 저장된 Paper 데이터만 표시하며 키움 주문 연결은 아직 비활성 상태입니다.</p></div>
      <div className="readiness-score"><strong>{readiness}<span>/4</span></strong><small>준비 단계</small></div>
    </section>
    <section className="status-grid" aria-label="시스템 준비 상태">
      <StatusCard icon={ShieldCheck} tone="ok" title="Web 보안" status="READY" description="ID·비밀번호·TOTP 세션이 활성화되었습니다." />
      <StatusCard icon={Database} tone={health?.database_status === "CONNECTED" ? "ok" : "wait"} title="데이터베이스" status={health?.database_status ?? "LOADING"} description="Backend가 확인한 영속 저장소 상태입니다." />
      <StatusCard icon={ReceiptText} tone={paperReady ? "ok" : "wait"} title="Paper Broker" status={health?.paper_broker_status ?? "LOADING"} description={`거래 게이트: ${health?.trading_gate?.status ?? "초기화 전"}`} />
      <StatusCard icon={Activity} tone={health?.market_data_status === "AVAILABLE" ? "ok" : "wait"} title="시장 데이터" status={health?.market_data_status ?? "LOADING"} description="Watch가 확인한 stream 최신성과 품질 상태입니다." />
      <StatusCard icon={Bot} tone={health?.analysis_scheduler?.lease_valid ? "ok" : "wait"} title="AI Scheduler" status={health?.analysis_scheduler?.state ?? "LOADING"} description={`다음 판단: ${health?.analysis_scheduler?.next_due_at ? formatDateTime(health.analysis_scheduler.next_due_at) : "대기"}`} />
    </section>
    <section className="dashboard-grid">
      <article className="panel guard-panel"><div className="panel-head"><div><ShieldCheck size={18} /><span>Cresta Guard</span></div><span className="status-pill ok">ENFORCED</span></div><div className="guard-body"><div className="shield-visual"><ShieldCheck size={36} /></div><div><h3>거래 게이트 우선</h3><p>Paper Broker가 조회 가능해도 게이트가 READY가 아니면 신규 주문은 생성되지 않습니다.</p></div></div><div className="policy-row"><span>판단 실행 단계</span><b>{health?.execution_stage ?? "LOADING"} · {health?.decision_execution_status ?? "UNKNOWN"}</b></div><div className="policy-row"><span>BUY 기능 게이트</span><b>{health?.buy_execution_ready ? "READY" : health?.buy_execution_block_reason ?? "BLOCKED"}</b></div><div className="policy-row"><span>거래 게이트</span><b>{health?.trading_gate?.status ?? "초기화 전"}</b></div><div className="policy-row"><span>차단 사유</span><b>{health?.trading_gate?.reason ?? "없음"}</b></div></article>
      <article className="panel"><div className="panel-head"><div><ListChecks size={18} /><span>Paper 원장 요약</span></div><span className="status-pill neutral">READ ONLY</span></div><div className="metric-list"><div><span>전체 주문</span><strong>{health?.counts.orders ?? "—"}</strong></div><div><span>진행 주문</span><strong>{health?.counts.active_orders ?? "—"}</strong></div><div><span>보유 포지션</span><strong>{health?.counts.open_positions ?? "—"}</strong></div></div></article>
      <article className="panel activity-panel"><div className="panel-head"><div><Activity size={18} /><span>현재 연동 범위</span></div></div><div className="empty-state"><Radio size={26} /><h3>Paper 조회 전용</h3><p>주문·체결·포지션 조회만 활성화했습니다. 운영 Web에서는 임의 주문이나 체결을 만들 수 없습니다.</p></div></article>
    </section>
  </>;
}

function WatchlistPage({ session, onSessionExpired }: { session: SessionData; onSessionExpired: () => void }) {
  const [data, setData] = useState<WatchlistData | null>(null);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await watchlistApi.list());
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) onSessionExpired();
      else setError("감시 종목을 불러오지 못했습니다.");
    }
  }, [onSessionExpired]);

  useEffect(() => { void load(); }, [load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!/^\d{6}$/.test(symbol)) return;
    setBusy(true);
    try {
      setData(await watchlistApi.create(session.csrf_token, symbol));
      setSymbol("");
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) onSessionExpired();
      else if (caught instanceof ApiError && caught.status === 409) setError("이미 등록된 종목입니다.");
      else if (caught instanceof ApiError && caught.status === 422) setError("등록 한도 또는 모의투자 시장 조건을 확인해 주세요.");
      else setError("감시 종목을 등록하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(itemId: string) {
    setBusy(true);
    try {
      await watchlistApi.remove(session.csrf_token, itemId);
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) onSessionExpired();
      else setError("감시 종목을 해제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return <>
    <PageHeading kicker="CRESTA WATCH" title="감시 종목" description="키움 모의투자 KRX 실시간 체결·호가를 수집할 종목을 관리합니다." />
    {error && <div className="console-alert"><CircleAlert size={15} />{error}</div>}
    <section className="ledger-panel watchlist-panel">
      <div className="ledger-toolbar">
        <div>
          <span className="status-pill neutral">{data?.items.length ?? 0} / 3</span>
          <small>남은 슬롯 {data?.remaining_slots ?? "-"}개 · MOCK에서는 KRX만 지원</small>
        </div>
        <button className="secondary-button" disabled={busy} onClick={() => void load()}><RefreshCw size={14} />새로고침</button>
      </div>
      <form className="watchlist-form" onSubmit={submit}>
        <label htmlFor="watch-symbol">종목코드</label>
        <input id="watch-symbol" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="예: 005930" value={symbol} onChange={(event) => setSymbol(event.target.value.replace(/\D/g, "").slice(0, 6))} required />
        <span className="watch-market">KRX · 키움 모의투자</span>
        <button className="primary-button" type="submit" disabled={busy || symbol.length !== 6 || (data?.remaining_slots ?? 0) === 0}>감시 등록</button>
      </form>
      {!data ? <div className="empty-state ledger-empty"><span className="loader small" /></div> : data.items.length === 0 ?
        <div className="empty-state ledger-empty"><Eye size={28} /><h3>등록된 감시 종목이 없습니다.</h3><p>숫자 6자리 종목코드를 등록하면 worker가 5초 이내에 실시간 구독을 동기화합니다.</p></div> :
        <div className="watchlist-grid">{data.items.map((item) => <article className="watch-card" key={item.id}>
          <div className="watch-card-head"><div><span>{item.market} · MOCK</span><h2>{item.symbol}</h2></div><span className={`order-status ${item.data_status === "AVAILABLE" ? "complete" : item.data_status === "DEGRADED" ? "risk" : "neutral"}`}>{item.data_status === "WAITING_FOR_DATA" ? "시세 대기" : item.data_status}</span></div>
          <dl>
            <div><dt>현재가</dt><dd>{item.quote ? `${Number(item.quote.last_price).toLocaleString("ko-KR")}원` : "-"}</dd></div>
            <div><dt>누적 거래량</dt><dd>{item.quote ? item.quote.cumulative_volume.toLocaleString("ko-KR") : "-"}</dd></div>
            <div><dt>데이터 품질</dt><dd>{item.quote?.quality ?? "WAITING"}</dd></div>
            <div><dt>수신 경과</dt><dd>{item.quote ? `${item.quote.age_seconds}초` : "시세 수신 전"}</dd></div>
            <div><dt>VWAP</dt><dd>{item.indicators ? `${Number(item.indicators.vwap).toLocaleString("ko-KR")}원` : "-"}</dd></div>
            <div><dt>SMA5</dt><dd>{item.indicators?.sma5 ? `${Number(item.indicators.sma5).toLocaleString("ko-KR")}원` : "-"}</dd></div>
            <div><dt>고점 대비</dt><dd>{item.indicators ? `${item.indicators.drawdown_from_high_pct}%` : "-"}</dd></div>
            <div><dt>호가 spread</dt><dd>{item.indicators?.spread_pct ? `${item.indicators.spread_pct}%` : "-"}</dd></div>
            <div><dt>1분봉</dt><dd>{item.indicators ? `${item.indicators.minute_bar_count}개` : "-"}</dd></div>
          </dl>
          <button className="secondary-button watch-remove" disabled={busy} onClick={() => void remove(item.id)}>감시 해제</button>
        </article>)}</div>}
    </section>
  </>;
}

const executionActions: Array<[keyof ExecutionPolicy, string, string]> = [
  ["buy", "일반 매수", "AI BUY 판단의 신규 진입"],
  ["partial_sell", "부분매도", "보유 수량 일부 청산"],
  ["full_sell", "전량매도", "일반 전량 청산"],
  ["take_profit", "목표수익 매도", "목표수익 도달 시 청산"],
  ["fixed_stop_loss", "고정 손절", "손절 가격 도달 시 즉시 청산"],
  ["trailing_stop", "추적 손절", "고점 대비 하락 시 수익 보호"],
  ["end_of_day_liquidation", "장 마감 청산", "익일 보유 금지 정책 청산"],
  ["emergency_exit", "긴급 청산", "Guard 긴급 위험 청산"],
];

function SettingsPage({ session, onSessionExpired }: { session: SessionData; onSessionExpired: () => void }) {
  const [policy, setPolicy] = useState<ExecutionPolicy | null>(null);
  const [source, setSource] = useState("");
  const [activeVersion, setActiveVersion] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [pendingVersion, setPendingVersion] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const loadPolicy = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await settingsApi.executionPolicy(signal);
      setPolicy(result.policy); setSource(result.source); setActiveVersion(result.active_version_id);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("실행 권한 설정을 불러오지 못했습니다.");
    }
  }, [onSessionExpired]);

  useEffect(() => { const controller = new AbortController(); void loadPolicy(controller.signal); return () => controller.abort(); }, [loadPolicy]);

  async function validateChanges(event: FormEvent) {
    event.preventDefault();
    if (!policy) return;
    setBusy(true); setMessage("");
    try {
      const draft = await settingsApi.createDraft(session.csrf_token, policy, reason);
      const validated = await settingsApi.validate(session.csrf_token, draft.version_id);
      setPendingVersion(validated.version_id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("변경안을 검증하지 못했습니다. 변경 사유와 설정값을 확인해 주세요.");
    } finally { setBusy(false); }
  }

  async function activate(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const proof = await authApi.reauthTotp(session.csrf_token, totp, "EXECUTION_POLICY_ACTIVATE", pendingVersion);
      await settingsApi.activate(session.csrf_token, pendingVersion, proof.reauth_proof);
      setPendingVersion(""); setTotp(""); setReason("");
      await loadPolicy();
      setMessage("행동별 실행 권한이 새 활성 버전으로 적용되었습니다.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("설정을 활성화하지 못했습니다. TOTP 또는 버전 충돌을 확인해 주세요.");
      setTotp("");
    } finally { setBusy(false); }
  }

  return <>
    <PageHeading kicker="EXECUTION POLICY" title="전략·설정" description="AI 행동마다 자동·승인·비활성 실행 권한을 독립적으로 관리합니다." />
    {message && <div className="console-alert" role="status"><CircleAlert size={17} /> {message}</div>}
    <section className="panel execution-policy-panel">
      <div className="panel-head"><div><Settings2 size={18} /><span>행동별 실행 권한</span></div><span className={`status-pill ${source === "USER_DEFAULT" ? "ok" : "neutral"}`}>{source || "LOADING"}</span></div>
      <div className="policy-version-note">활성 버전: <b className="mono">{activeVersion ?? "안전 기본값 · 미저장"}</b></div>
      {policy && <form onSubmit={validateChanges}>
        <div className="execution-policy-list">{executionActions.map(([key, label, description]) => <div className="execution-policy-row" key={key}><div><strong>{label}</strong><small>{description}</small></div><select aria-label={`${label} 실행 모드`} value={policy[key]} onChange={(event) => setPolicy({ ...policy, [key]: event.target.value as ExecutionMode })}><option value="AUTOMATIC">자동 실행</option><option value="MANUAL_APPROVAL">사용자 승인</option><option value="DISABLED">비활성</option></select></div>)}</div>
        <label className="reason-field" htmlFor="execution-policy-reason">변경 사유<input id="execution-policy-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} required placeholder="자동화 범위를 변경하는 이유" /></label>
        <button className="primary-button" disabled={busy || !reason.trim()}>{busy ? "검증 중" : "변경안 검증"}</button>
      </form>}
    </section>
    <LlmFoundationPanel session={session} onSessionExpired={onSessionExpired} />
    {pendingVersion && <div className="modal-backdrop" role="presentation"><section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="policy-confirm-title"><span className="section-kicker">TOTP REAUTHENTICATION</span><h2 id="policy-confirm-title">실행 권한 활성화</h2><p>검증된 버전만 운영에 적용됩니다. 자동 실행 항목은 이후 건별 TOTP 없이 Guard 검사 후 실행됩니다.</p><form onSubmit={activate}><label htmlFor="policy-totp">현재 TOTP 코드</label><input id="policy-totp" className="totp-input" value={totp} onChange={(event) => setTotp(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" pattern="[0-9]{6}" autoComplete="one-time-code" required autoFocus /><div className="modal-actions"><button type="button" className="secondary-button" onClick={() => { setPendingVersion(""); setTotp(""); }} disabled={busy}>취소</button><button type="submit" className="primary-button" disabled={busy || totp.length !== 6}>{busy ? "적용 중" : "활성화"}</button></div></form></section></div>}
  </>;
}

const llmRoles = [
  "INTEL_COLLECTOR",
  "EVIDENCE_VERIFIER",
  "TECHNICAL_SCOUT",
  "NEWS_DISCLOSURE_SCOUT",
  "MARKET_SECTOR_SCOUT",
  "POSITION_RISK_SCOUT",
  "CORE",
] as const;

function LlmFoundationPanel({
  session,
  onSessionExpired,
}: {
  session: SessionData;
  onSessionExpired: () => void;
}) {
  const [providers, setProviders] = useState<LlmProviderProfile[]>([]);
  const [models, setModels] = useState<LlmModelProfile[]>([]);
  const [routes, setRoutes] = useState<LlmRoleRoute[]>([]);
  const [providerName, setProviderName] = useState("cresta-mock");
  const [modelProviderId, setModelProviderId] = useState("");
  const [modelAlias, setModelAlias] = useState("deterministic-shadow-v1");
  const [providerModelId, setProviderModelId] = useState("deterministic-mock-v2");
  const [routeRole, setRouteRole] = useState<(typeof llmRoles)[number]>("TECHNICAL_SCOUT");
  const [routeModelId, setRouteModelId] = useState("");
  const [routeReason, setRouteReason] = useState("Foundation SHADOW 검증");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [providerResult, modelResult, routeResult] = await Promise.all([
        llmApi.providers(signal),
        llmApi.models(signal),
        llmApi.routes(signal),
      ]);
      setProviders(providerResult.items);
      setModels(modelResult.items);
      setRoutes(routeResult.items);
      setModelProviderId((current) => current || providerResult.items[0]?.id || "");
      setRouteModelId((current) => current || modelResult.items.find((item) => item.state === "VALIDATED")?.id || "");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("LLM Foundation 설정을 불러오지 못했습니다.");
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function perform(action: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("");
    try {
      await action();
      await load();
      setMessage(success);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("요청을 처리하지 못했습니다. 현재 상태와 입력값을 확인해 주세요.");
    } finally { setBusy(false); }
  }

  const validatedModels = models.filter((item) => item.state === "VALIDATED");

  return <section className="panel execution-policy-panel" aria-labelledby="llm-foundation-title">
    <div className="panel-head"><div><Bot size={18} /><span id="llm-foundation-title">LLM Provider · Agent Route</span></div><span className="status-pill neutral">SHADOW ONLY</span></div>
    <div className="console-alert decision-warning" role="note"><ShieldCheck size={17} /> 외부 API와 주문 경로는 연결되지 않았습니다. Mock Provider로 profile·model·route 계약만 검증합니다.</div>
    {message && <div className="console-alert" role="status"><CircleAlert size={17} /> {message}</div>}

    <div className="execution-policy-list">
      <div className="execution-policy-row"><div><strong>Provider Profile</strong><small>credential 입력 없이 외부 통신이 없는 Mock profile을 생성합니다.</small></div><form className="diagnostic-form" onSubmit={(event) => { event.preventDefault(); void perform(() => llmApi.createMockProvider(session.csrf_token, providerName), "Mock Provider 초안이 생성되었습니다."); }}><label htmlFor="llm-provider-name">Provider 이름</label><input id="llm-provider-name" value={providerName} onChange={(event) => setProviderName(event.target.value.replace(/[^A-Za-z0-9_-]/g, ""))} required /><button className="secondary-button" disabled={busy || !providerName}>초안 생성</button></form></div>
      {providers.map((provider) => <div className="execution-policy-row" key={provider.id}><div><strong>{provider.name}</strong><small>{provider.adapter_type} · {provider.data_policy} · credential {provider.credential_configured ? "설정됨" : "없음"}</small></div><div><span className={`status-pill ${provider.health_status === "READY" ? "ok" : "neutral"}`}>{provider.state} · {provider.health_status}</span> <button className="secondary-button" disabled={busy || provider.adapter_type !== "MOCK"} onClick={() => void perform(() => llmApi.testProvider(session.csrf_token, provider.id), "Mock Provider 연결 계약을 검증했습니다.")}>연결 시험</button></div></div>)}

      <div className="execution-policy-row"><div><strong>Model Profile</strong><small>Mock capability fixture를 검증하며 운영 모델로 활성화하지 않습니다.</small></div><form className="diagnostic-form" onSubmit={(event) => { event.preventDefault(); void perform(() => llmApi.createMockModel(session.csrf_token, modelProviderId, modelAlias, providerModelId), "Model Profile 초안이 생성되었습니다."); }}><label htmlFor="llm-model-provider">Provider</label><select id="llm-model-provider" value={modelProviderId} onChange={(event) => setModelProviderId(event.target.value)} required><option value="">선택</option>{providers.filter((item) => item.adapter_type === "MOCK").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><label htmlFor="llm-model-alias">Model 별칭</label><input id="llm-model-alias" value={modelAlias} onChange={(event) => setModelAlias(event.target.value.replace(/[^A-Za-z0-9_.-]/g, ""))} required /><label htmlFor="llm-provider-model-id">Provider model ID</label><input id="llm-provider-model-id" value={providerModelId} onChange={(event) => setProviderModelId(event.target.value)} required /><button className="secondary-button" disabled={busy || !modelProviderId}>초안 생성</button></form></div>
      {models.map((model) => <div className="execution-policy-row" key={model.id}><div><strong>{model.alias}</strong><small>{model.provider_model_id} · structured {model.capabilities.structured_output ? "지원" : "미지원"} · local {model.capabilities.local_execution ? "예" : "아니오"}</small></div><div><span className={`status-pill ${model.state === "VALIDATED" ? "ok" : "neutral"}`}>{model.state}</span> <button className="secondary-button" disabled={busy || model.state !== "DRAFT"} onClick={() => void perform(() => llmApi.validateModel(session.csrf_token, model.id), "Model capability를 검증했습니다.")}>모델 검증</button></div></div>)}

      <div className="execution-policy-row"><div><strong>Agent Role Route</strong><small>fallback 없는 SHADOW route만 생성하고 활성화 endpoint는 제공하지 않습니다.</small></div><form className="diagnostic-form" onSubmit={(event) => { event.preventDefault(); void perform(() => llmApi.createShadowRoute(session.csrf_token, routeRole, routeModelId, routeReason), "SHADOW Route 초안이 생성되었습니다."); }}><label htmlFor="llm-route-role">Agent 역할</label><select id="llm-route-role" value={routeRole} onChange={(event) => setRouteRole(event.target.value as (typeof llmRoles)[number])}>{llmRoles.map((role) => <option key={role}>{role}</option>)}</select><label htmlFor="llm-route-model">Primary model</label><select id="llm-route-model" value={routeModelId} onChange={(event) => setRouteModelId(event.target.value)} required><option value="">검증된 모델 선택</option>{validatedModels.map((model) => <option key={model.id} value={model.id}>{model.alias}</option>)}</select><label htmlFor="llm-route-reason">Route 변경 사유</label><input id="llm-route-reason" value={routeReason} onChange={(event) => setRouteReason(event.target.value)} required /><button className="secondary-button" disabled={busy || !routeModelId || !routeReason.trim()}>초안 생성</button></form></div>
      {routes.map((route) => <div className="execution-policy-row" key={route.id}><div><strong>{route.role}</strong><small>{route.primary_model_alias} · {route.execution_stage} · fallback {route.fallback_policy}</small></div><div><span className={`status-pill ${route.state === "VALIDATED" ? "ok" : "neutral"}`}>{route.state}</span> <button className="secondary-button" disabled={busy || route.state !== "DRAFT"} onClick={() => void perform(() => llmApi.validateRoute(session.csrf_token, route.id), "SHADOW Route 계약을 검증했습니다.")}>Route 검증</button></div></div>)}
    </div>
  </section>;
}

function DecisionsPage({ session, onSessionExpired }: { session: SessionData; onSessionExpired: () => void }) {
  const [items, setItems] = useState<DecisionData[]>([]);
  const [symbol, setSymbol] = useState("005930");
  const [market, setMarket] = useState<"KRX" | "NXT">("KRX");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    try { setItems((await decisionApi.list(signal)).items); }
    catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("AI 판단 기록을 불러오지 못했습니다.");
    }
  }, [onSessionExpired]);

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);

  async function evaluate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const decision = await decisionApi.mockEvaluate(session.csrf_token, symbol, market);
      setItems((current) => [decision, ...current.filter((item) => item.decision_id !== decision.decision_id)]);
      setMessage(`진단 판단이 ${decision.core.action}으로 기록되었습니다. 주문·승인은 생성되지 않습니다.`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("최신 영속 시세 snapshot이 없거나 판단 입력이 안전 기준을 충족하지 못했습니다.");
    } finally { setBusy(false); }
  }

  return <>
    <PageHeading kicker="MOCK SCOUT · CORE" title="AI 판단" description="결정론적 Mock 모델로 판단 계약과 실행 권한 분기를 검증합니다." />
    <div className="console-alert decision-warning" role="note"><ShieldCheck size={17} /> 이 화면의 판단은 주문이나 승인을 생성하지 않습니다. AUTOMATIC도 Guard 미구현 상태에서는 차단됩니다.</div>
    {message && <div className="console-alert" role="status"><CircleAlert size={17} /> {message}</div>}
    <AgentRuntimePanel session={session} onSessionExpired={onSessionExpired} />
    <section className="panel decision-control"><div className="panel-head"><div><Bot size={18} /><span>최신 snapshot 진단</span></div><span className="status-pill neutral">deterministic-mock-v2</span></div><form className="diagnostic-form" onSubmit={evaluate}><label htmlFor="decision-symbol">종목코드</label><input id="decision-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value.replace(/\D/g, "").slice(0, 6))} pattern="[0-9]{6}" required /><label htmlFor="decision-market">시장</label><select id="decision-market" value={market} onChange={(event) => setMarket(event.target.value as "KRX" | "NXT")}><option value="KRX">KRX</option><option value="NXT">NXT</option></select><button className="primary-button" disabled={busy || symbol.length !== 6}>{busy ? "판단 중" : "Mock 판단 실행"}</button></form></section>
    <section className="decision-grid">{items.length === 0 ? <article className="panel empty-state"><Bot size={26} /><h3>저장된 AI 판단이 없습니다</h3><p>최신 시세 snapshot이 준비된 종목으로 진단을 실행하세요.</p></article> : items.map((item) => <article className="panel decision-card" key={item.decision_id}><div className="panel-head"><div><Bot size={17} /><span>{item.symbol} · {item.market}</span></div><OrderStatus status={item.execution?.state ?? item.purpose} /></div><div className="decision-action"><strong>{item.core.action}</strong><span>confidence {Number(item.core.confidence).toFixed(2)}</span></div><dl><div><dt>Scout</dt><dd>{item.scout.trend_state} · {item.scout.entry_score}점</dd></div><div><dt>판단 목적</dt><dd>{item.purpose}</dd></div><div><dt>실행 단계</dt><dd>{item.execution?.stage ?? "진단 전용"}</dd></div><div><dt>실행 결과</dt><dd>{item.execution?.state ?? "실행 없음"}</dd></div><div><dt>입력 schema</dt><dd>{item.input_schema_version ?? "legacy"}</dd></div><div><dt>지표 버전</dt><dd>{item.indicator_calculator_version ?? "미준비"}</dd></div><div><dt>입력 hash</dt><dd className="mono">{item.input_hash ? item.input_hash.slice(0, 12) : "없음"}</dd></div><div><dt>snapshot</dt><dd className="mono">{item.input_snapshot_id}</dd></div></dl><p className="reason-codes">{item.core.reason_codes.join(" · ")}</p><small>{formatDateTime(item.created_at)} · {item.model_id}</small></article>)}</section>
  </>;
}

const agentRuntimeRoles = [
  "TECHNICAL_SCOUT",
  "NEWS_DISCLOSURE_SCOUT",
  "MARKET_SECTOR_SCOUT",
  "POSITION_RISK_SCOUT",
  "CORE",
] as const;

function AgentRuntimePanel({
  session,
  onSessionExpired,
}: {
  session: SessionData;
  onSessionExpired: () => void;
}) {
  const [runs, setRuns] = useState<AgentRunData[]>([]);
  const [routes, setRoutes] = useState<LlmRoleRoute[]>([]);
  const [symbol, setSymbol] = useState("005930");
  const [market, setMarket] = useState<"KRX" | "NXT">("KRX");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [runResult, routeResult] = await Promise.all([
        agentApi.list(signal),
        llmApi.routes(signal),
      ]);
      setRuns(runResult.items);
      setRoutes(routeResult.items);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("Agent Runtime 상태를 불러오지 못했습니다.");
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const routeIds = Object.fromEntries(agentRuntimeRoles.map((role) => [
    role,
    routes.find((route) => route.role === role && route.state === "VALIDATED")?.id ?? "",
  ]));
  const missingRoles = agentRuntimeRoles.filter((role) => !routeIds[role]);

  async function runDiagnostic(event: FormEvent) {
    event.preventDefault();
    if (missingRoles.length) return;
    setBusy(true); setMessage("");
    try {
      const run = await agentApi.diagnostic(session.csrf_token, symbol, market, routeIds);
      setRuns((current) => [run, ...current.filter((item) => item.run_id !== run.run_id)]);
      setMessage(run.created ? "DIAGNOSTIC Agent run을 생성했습니다. 주문은 생성되지 않았습니다." : "같은 입력의 기존 Agent run을 반환했습니다.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onSessionExpired();
      else setMessage("최신 snapshot과 검증된 SHADOW route를 확인해 주세요.");
    } finally { setBusy(false); }
  }

  return <section className="panel execution-policy-panel" aria-labelledby="agent-runtime-title">
    <div className="panel-head"><div><Bot size={18} /><span id="agent-runtime-title">Agent Runtime v1</span></div><span className="status-pill neutral">DIAGNOSTIC · SHADOW · 주문 없음</span></div>
    <div className="console-alert decision-warning" role="note"><ShieldCheck size={17} /> Intel → Verify → 4 Scout → Core 고정 DAG를 Mock Provider로만 실행합니다. 외부 웹·LLM·승인·주문은 연결되지 않습니다.</div>
    <form className="diagnostic-form" onSubmit={runDiagnostic}>
      <label htmlFor="agent-symbol">Agent 종목코드</label><input id="agent-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value.replace(/\D/g, "").slice(0, 6))} pattern="[0-9]{6}" required />
      <label htmlFor="agent-market">Agent 시장</label><select id="agent-market" value={market} onChange={(event) => setMarket(event.target.value as "KRX" | "NXT")}><option value="KRX">KRX</option><option value="NXT">NXT</option></select>
      <button className="primary-button" disabled={busy || symbol.length !== 6 || missingRoles.length > 0}>{busy ? "DAG 실행 중" : "DIAGNOSTIC DAG 실행"}</button>
    </form>
    <p className="policy-version-note">Route 준비: {agentRuntimeRoles.length - missingRoles.length}/{agentRuntimeRoles.length}{missingRoles.length ? ` · 누락 ${missingRoles.join(", ")}` : " · READY"}</p>
    {message && <div className="console-alert" role="status"><CircleAlert size={17} /> {message}</div>}
    <div className="decision-grid">{runs.map((run) => <article className="panel decision-card" key={run.run_id}>
      <div className="panel-head"><div><Bot size={17} /><span>{run.symbol} · {run.market}</span></div><OrderStatus status={run.state} /></div>
      <div className="decision-action"><strong>{run.core_action ?? "-"}</strong><span>{run.dag_version}</span></div>
      <dl><div><dt>목적</dt><dd>{run.purpose}</dd></div><div><dt>실행 경계</dt><dd>{run.execution_stage} · 주문 없음</dd></div><div><dt>증거</dt><dd>{run.evidence_bundle?.state ?? "없음"}</dd></div><div><dt>Stage</dt><dd>{run.stages.length}개</dd></div><div><dt>Mock 호출</dt><dd>{run.stages.filter((stage) => stage.invocation).length}개</dd></div><div><dt>입력 hash</dt><dd className="mono">{run.input_hash.slice(0, 12)}</dd></div></dl>
      <p className="reason-codes">{run.stages.map((stage) => `${stage.role}: ${stage.state}`).join(" · ")}</p>
      <small>{formatDateTime(run.created_at)} · {run.run_id}</small>
    </article>)}</div>
  </section>;
}

function SystemPage({ session, onSessionExpired }: { session: SessionData; onSessionExpired: () => void }) {
  const [broker, setBroker] = useState<BrokerStatus | null>(null);
  const [symbol, setSymbol] = useState("005930");
  const [orderType, setOrderType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [targetId, setTargetId] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [createdOrderId, setCreatedOrderId] = useState("");

  const loadBroker = useCallback(async (signal?: AbortSignal) => {
    try { setBroker(await systemApi.broker(signal)); }
    catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (reason instanceof ApiError && reason.status === 401) onSessionExpired();
      else setMessage("키움 Broker 상태를 불러오지 못했습니다.");
    }
  }, [onSessionExpired]);

  useEffect(() => {
    const controller = new AbortController();
    void loadBroker(controller.signal);
    return () => controller.abort();
  }, [loadBroker]);

  const ready = broker?.state === "READY"
    && broker.gate_status === "READY"
    && broker.lease_valid
    && broker.websocket_connected
    && broker.subscriptions_ready;

  function requestConfirmation(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    setCreatedOrderId("");
    setTargetId(globalThis.crypto.randomUUID());
  }

  async function submitMockOrder(event: FormEvent) {
    event.preventDefault();
    if (!targetId) return;
    setBusy(true); setMessage("");
    try {
      const reauth = await authApi.reauthTotp(
        session.csrf_token,
        totp,
        "KIWOOM_MOCK_ORDER_TEST",
        targetId,
      );
      const result = await systemApi.mockOrderTest(session.csrf_token, {
        test_request_id: targetId,
        symbol,
        order_type: orderType,
        limit_price: orderType === "LIMIT" ? limitPrice : null,
        reauth_proof: reauth.reauth_proof,
      });
      setCreatedOrderId(result.order_id);
      setMessage("모의주문 1주가 CREATED 상태로 등록되었습니다. Worker가 전송 결과를 처리합니다.");
      setTargetId(""); setTotp("");
      await loadBroker();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onSessionExpired();
      else setMessage("모의주문을 등록하지 못했습니다. Broker 상태와 TOTP를 확인해 주세요.");
      setTotp("");
    } finally { setBusy(false); }
  }

  return <>
    <PageHeading kicker="BROKER DIAGNOSTICS" title="시스템 상태" description="키움 모의투자 연결과 주문 전달 경로를 점검합니다." />
    {message && <div className="console-alert" role="status"><CircleAlert size={17} /> {message}{createdOrderId && <span className="mono"> 주문 ID: {createdOrderId}</span>}</div>}
    <section className="dashboard-grid system-diagnostics">
      <article className="panel">
        <div className="panel-head"><div><Radio size={18} /><span>키움 Broker</span></div><OrderStatus status={broker?.state ?? "LOADING"} /></div>
        <div className="metric-list">
          <div><span>거래 Gate</span><strong>{broker?.gate_status ?? "—"}</strong></div>
          <div><span>WebSocket</span><strong>{broker?.websocket_connected ? "CONNECTED" : "DISCONNECTED"}</strong></div>
          <div><span>계좌 구독</span><strong>{broker?.subscriptions_ready ? "READY" : "NOT READY"}</strong></div>
          <div><span>Lease</span><strong>{broker?.lease_valid ? "VALID" : "INVALID"}</strong></div>
        </div>
        <button className="secondary-button" onClick={() => void loadBroker()}><RefreshCw size={15} /> 상태 새로고침</button>
      </article>
      <article className="panel mock-order-panel">
        <div className="panel-head"><div><ShieldCheck size={18} /><span>모의주문 연결 시험</span></div><span className="status-pill neutral">MOCK · 1주</span></div>
        <p className="panel-description">실거래가 아닌 키움 모의투자 계좌에 KRX 매수 1주를 전송합니다. 주문은 체결이 아니라 CREATED 등록부터 시작합니다.</p>
        <form className="diagnostic-form" onSubmit={requestConfirmation}>
          <label htmlFor="mock-symbol">종목코드</label>
          <input id="mock-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value.replace(/\D/g, "").slice(0, 6))} pattern="[0-9]{6}" required />
          <label htmlFor="mock-order-type">주문 유형</label>
          <select id="mock-order-type" value={orderType} onChange={(event) => setOrderType(event.target.value as "MARKET" | "LIMIT")}>
            <option value="MARKET">시장가</option><option value="LIMIT">지정가</option>
          </select>
          {orderType === "LIMIT" && <><label htmlFor="mock-limit-price">지정가</label><input id="mock-limit-price" inputMode="numeric" value={limitPrice} onChange={(event) => setLimitPrice(event.target.value.replace(/\D/g, ""))} min="1" required /></>}
          <div className="diagnostic-summary"><span>환경 <b>MOCK</b></span><span>시장 <b>KRX</b></span><span>방향 <b>BUY</b></span><span>수량 <b>1주</b></span></div>
          <button className="primary-button" type="submit" disabled={!ready || symbol.length !== 6}>모의주문 확인</button>
          {!ready && <small className="field-hint">Worker, Gate, WebSocket과 구독이 모두 READY일 때만 실행할 수 있습니다.</small>}
        </form>
      </article>
    </section>
    {targetId && <div className="modal-backdrop" role="presentation"><section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="mock-confirm-title">
      <span className="section-kicker">TOTP REAUTHENTICATION</span><h2 id="mock-confirm-title">키움 모의주문 1주 확인</h2>
      <p>{symbol} 종목을 {orderType === "MARKET" ? "시장가" : `${Number(limitPrice).toLocaleString("ko-KR")}원 지정가`}로 1주 매수합니다.</p>
      <form onSubmit={submitMockOrder}><label htmlFor="mock-totp">현재 TOTP 코드</label><input id="mock-totp" className="totp-input" value={totp} onChange={(event) => setTotp(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" pattern="[0-9]{6}" autoComplete="one-time-code" required autoFocus />
        <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => { setTargetId(""); setTotp(""); }} disabled={busy}>취소</button><button type="submit" className="primary-button" disabled={busy || totp.length !== 6}>{busy ? "등록 중" : "모의주문 실행"}</button></div>
      </form>
    </section></div>}
  </>;
}

function OrdersPage({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  async function loadOrders(signal?: AbortSignal) {
    setLoading(true); setMessage("");
    try { setOrders((await orderApi.list(signal)).items); }
    catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (reason instanceof ApiError && reason.status === 401) onSessionExpired();
      else setMessage("주문 원장을 불러오지 못했습니다.");
    } finally { setLoading(false); }
  }

  useEffect(() => { const controller = new AbortController(); void loadOrders(controller.signal); return () => controller.abort(); }, []);

  async function selectOrder(orderId: string) {
    setMessage("");
    try { setDetail(await orderApi.detail(orderId)); }
    catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onSessionExpired();
      else setMessage("주문 상세를 불러오지 못했습니다.");
    }
  }

  return <>
    <PageHeading kicker="PAPER LEDGER" title="승인·주문" description="실제 저장된 Paper 주문, 체결과 상태 이벤트를 조회합니다." />
    {message && <div className="console-alert" role="alert"><CircleAlert size={17} /> {message}</div>}
    <section className="ledger-panel">
      <div className="ledger-toolbar"><div><span className="status-pill neutral">READ ONLY</span><small>운영 화면에서 주문·체결을 생성하지 않습니다.</small></div><button className="secondary-button" onClick={() => void loadOrders()} disabled={loading}><RefreshCw size={15} /> 새로고침</button></div>
      {loading ? <LoadingState label="주문 원장 불러오는 중" /> : orders.length === 0 ? <EmptyLedger icon={ReceiptText} title="Paper 주문이 없습니다" description="시험 또는 내부 Broker 흐름에서 주문이 생성되면 여기에 표시됩니다." /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>시각</th><th>종목</th><th>구분</th><th>유형·가격</th><th>요청/체결/취소/잔량</th><th>상태</th></tr></thead><tbody>{orders.map((order) => <tr key={order.id} className={detail?.id === order.id ? "active-row" : ""} onClick={() => void selectOrder(order.id)}><td>{formatDateTime(order.created_at)}</td><td><strong>{order.symbol}</strong><small>{order.market} · {order.environment}</small></td><td><span className={`side-label ${order.side.toLowerCase()}`}>{order.side}</span></td><td>{order.order_type}<small>{order.limit_price ? formatWon(order.limit_price) : "가격 없음"}</small></td><td className="mono">{order.requested_quantity} / {order.filled_quantity} / {order.cancelled_quantity} / {order.remaining_quantity}</td><td><OrderStatus status={order.status} /></td></tr>)}</tbody></table></div>}
    </section>
    {detail && <OrderDetailPanel detail={detail} onClose={() => setDetail(null)} />}
  </>;
}

function PositionsPage({ onSessionExpired }: { onSessionExpired: () => void }) {
  const [positions, setPositions] = useState<PositionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  async function loadPositions(signal?: AbortSignal) {
    setLoading(true); setMessage("");
    try { setPositions((await positionApi.list(signal)).items); }
    catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (reason instanceof ApiError && reason.status === 401) onSessionExpired();
      else setMessage("포지션 원장을 불러오지 못했습니다.");
    } finally { setLoading(false); }
  }

  useEffect(() => { const controller = new AbortController(); void loadPositions(controller.signal); return () => controller.abort(); }, []);

  return <>
    <PageHeading kicker="PAPER POSITIONS" title="보유 포지션" description="Paper 체결로 생성된 실제 수량과 평균단가를 조회합니다." />
    {message && <div className="console-alert" role="alert"><CircleAlert size={17} /> {message}</div>}
    <section className="ledger-panel">
      <div className="ledger-toolbar"><div><span className="status-pill neutral">NO MARKET PRICE</span><small>시세 연동 전이므로 평가손익은 계산하지 않습니다.</small></div><button className="secondary-button" onClick={() => void loadPositions()} disabled={loading}><RefreshCw size={15} /> 새로고침</button></div>
      {loading ? <LoadingState label="포지션 원장 불러오는 중" /> : positions.length === 0 ? <EmptyLedger icon={WalletCards} title="Paper 포지션이 없습니다" description="Paper 매수 체결이 원장에 반영되면 실제 보유 수량이 표시됩니다." /> : <div className="position-grid">{positions.map((position) => <article className="position-card" key={position.id}><div><span>{position.market} · {position.account_alias}</span><h2>{position.symbol}</h2></div><OrderStatus status={position.state} /><dl><div><dt>보유 수량</dt><dd>{position.quantity.toLocaleString("ko-KR")}주</dd></div><div><dt>평균단가</dt><dd>{formatWon(position.average_price)}</dd></div><div><dt>원장 version</dt><dd>v{position.version}</dd></div><div><dt>갱신 시각</dt><dd>{formatDateTime(position.updated_at)}</dd></div></dl></article>)}</div>}
    </section>
  </>;
}

function OrderDetailPanel({ detail, onClose }: { detail: OrderDetail; onClose: () => void }) {
  return <section className="detail-panel" aria-label={`${detail.symbol} 주문 상세`}><div className="detail-head"><div><span className="card-label">ORDER DETAIL</span><h2>{detail.symbol} · {detail.side}</h2></div><button className="icon-button" onClick={onClose} aria-label="주문 상세 닫기"><X /></button></div><div className="detail-summary"><div><span>주문 그룹</span><b>{detail.order_group_id}</b></div><div><span>Broker 주문번호</span><b>{detail.broker_order_id ?? "미발급"}</b></div><div><span>부모 주문</span><b>{detail.parent_order_id ?? "없음"}</b></div><div><span>상태</span><OrderStatus status={detail.status} /></div></div><div className="detail-columns"><div><h3>체결</h3>{detail.fills.length === 0 ? <p className="detail-empty">체결 없음</p> : detail.fills.map((fill) => <div className="timeline-row" key={fill.id}><span>{formatDateTime(fill.filled_at)}</span><b>{fill.quantity}주 · {formatWon(fill.price)}</b></div>)}</div><div><h3>상태 이벤트</h3>{detail.events.map((event) => <div className="timeline-row" key={event.id}><span>{formatDateTime(event.occurred_at)}</span><b>{event.event_type} · {event.source}</b></div>)}</div></div></section>;
}

function OrderStatus({ status }: { status: string }) {
  const risk = status === "UNKNOWN" || status === "RECONCILING" || status === "GUARD_BLOCKED";
  const complete = status === "FILLED" || status === "OPEN";
  return <span className={`order-status ${risk ? "risk" : complete ? "complete" : "neutral"}`}>{status}</span>;
}

function LoadingState({ label }: { label: string }) { return <div className="empty-state"><div className="loader small" /><h3>{label}</h3></div>; }

function EmptyLedger({ icon: Icon, title, description }: { icon: typeof Activity; title: string; description: string }) { return <div className="empty-state ledger-empty"><Icon size={28} /><h3>{title}</h3><p>{description}</p></div>; }

function formatWon(value: string) { return `${Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 4 })}원`; }

function formatDateTime(value: string) { return new Date(value).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

function StatusCard({ icon: Icon, tone, title, status, description }: { icon: typeof Activity; tone: "ok" | "wait"; title: string; status: string; description: string }) {
  return <article className={`status-card ${tone}`}><div className="status-icon"><Icon size={20} /></div><div><span>{title}</span><strong>{status}</strong><p>{description}</p></div></article>;
}

export function CrestaConsole() {
  const [screen, setScreen] = useState<Screen>("boot");
  const [session, setSession] = useState<SessionData | null>(null);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const sessionExpired = useCallback(() => {
    setSession(null);
    setChallengeId(null);
    setError("세션이 만료되었습니다. 다시 로그인해 주세요.");
    setScreen("credentials");
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    authApi.session(controller.signal).then((value) => { setSession(value); setScreen("console"); }).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setScreen("credentials");
      if (!(reason instanceof ApiError && reason.status === 401)) setError("Console 서비스에 연결할 수 없습니다.");
    });
    return () => controller.abort();
  }, []);

  async function passwordLogin(loginId: string, password: string) {
    setBusy(true); setError("");
    try {
      const challenge = await authApi.password(loginId, password);
      setChallengeId(challenge.challenge_id); setScreen("totp");
    } catch { setError(SAFE_AUTH_ERROR); }
    finally { setBusy(false); }
  }

  async function totpLogin(code: string) {
    if (!challengeId) { setScreen("credentials"); return; }
    setBusy(true); setError("");
    try {
      const activeSession = await authApi.totp(challengeId, code);
      setChallengeId(null); setSession(activeSession); setScreen("console");
    } catch { setError(SAFE_AUTH_ERROR); }
    finally { setBusy(false); }
  }

  async function logout() {
    if (!session) return;
    setBusy(true); setError("");
    try {
      await authApi.logout(session.csrf_token);
      setSession(null); setChallengeId(null); setScreen("credentials");
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) { setSession(null); setScreen("credentials"); }
      else setError("로그아웃을 완료하지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.");
    } finally { setBusy(false); }
  }

  if (screen === "boot") return <main className="boot-screen"><Brand /><div className="loader" /><p>보안 세션 확인 중</p></main>;
  if (screen === "console" && session) return <ConsoleShell session={session} onLogout={logout} onSessionExpired={sessionExpired} logoutBusy={busy} error={error} />;
  return <LoginPanel screen={screen === "totp" ? "totp" : "credentials"} busy={busy} error={error} onPassword={passwordLogin} onTotp={totpLogin} onBack={() => { setChallengeId(null); setError(""); setScreen("credentials"); }} />;
}
