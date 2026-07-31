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
  authApi,
  orderApi,
  OrderDetail,
  OrderSummary,
  positionApi,
  PositionSummary,
  SessionData,
  systemApi,
  SystemHealth,
} from "../lib/api";

type Screen = "boot" | "credentials" | "totp" | "console";
type ConsolePage = "dashboard" | "positions" | "orders";

const SAFE_AUTH_ERROR = "인증 정보를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.";

const navigation = [
  ["dashboard", "대시보드", LayoutDashboard, true],
  ["watchlist", "감시 종목", Eye, false],
  ["positions", "보유 포지션", WalletCards, true],
  ["orders", "승인·주문", ListChecks, true],
  ["decisions", "AI 판단", Bot, false],
  ["settings", "전략·설정", Settings2, false],
  ["risk", "리스크", ShieldCheck, false],
  ["system", "시스템 상태", Activity, false],
  ["audit", "이력·감사", History, false],
] as const;

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
          {navigation.map(([id, label, Icon, enabled]) => (
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
          {page === "orders" && <OrdersPage onSessionExpired={onSessionExpired} />}
          {page === "positions" && <PositionsPage onSessionExpired={onSessionExpired} />}
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
      <StatusCard icon={Activity} tone="wait" title="시장 데이터" status={health?.market_data_status ?? "LOADING"} description="Watch 서비스는 후속 구현 단계입니다." />
    </section>
    <section className="dashboard-grid">
      <article className="panel guard-panel"><div className="panel-head"><div><ShieldCheck size={18} /><span>Cresta Guard</span></div><span className="status-pill ok">ENFORCED</span></div><div className="guard-body"><div className="shield-visual"><ShieldCheck size={36} /></div><div><h3>거래 게이트 우선</h3><p>Paper Broker가 조회 가능해도 게이트가 READY가 아니면 신규 주문은 생성되지 않습니다.</p></div></div><div className="policy-row"><span>거래 게이트</span><b>{health?.trading_gate?.status ?? "초기화 전"}</b></div><div className="policy-row"><span>차단 사유</span><b>{health?.trading_gate?.reason ?? "없음"}</b></div></article>
      <article className="panel"><div className="panel-head"><div><ListChecks size={18} /><span>Paper 원장 요약</span></div><span className="status-pill neutral">READ ONLY</span></div><div className="metric-list"><div><span>전체 주문</span><strong>{health?.counts.orders ?? "—"}</strong></div><div><span>진행 주문</span><strong>{health?.counts.active_orders ?? "—"}</strong></div><div><span>보유 포지션</span><strong>{health?.counts.open_positions ?? "—"}</strong></div></div></article>
      <article className="panel activity-panel"><div className="panel-head"><div><Activity size={18} /><span>현재 연동 범위</span></div></div><div className="empty-state"><Radio size={26} /><h3>Paper 조회 전용</h3><p>주문·체결·포지션 조회만 활성화했습니다. 운영 Web에서는 임의 주문이나 체결을 만들 수 없습니다.</p></div></article>
    </section>
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
  const risk = status === "UNKNOWN" || status === "RECONCILING";
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
