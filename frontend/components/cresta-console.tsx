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
  Settings2,
  ShieldCheck,
  Smartphone,
  UserRound,
  WalletCards,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";

import { ApiError, authApi, SessionData } from "../lib/api";

type Screen = "boot" | "credentials" | "totp" | "console";

const SAFE_AUTH_ERROR = "인증 정보를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.";

const navigation = [
  ["대시보드", LayoutDashboard, true],
  ["감시 종목", Eye, false],
  ["보유 포지션", WalletCards, false],
  ["승인·주문", ListChecks, false],
  ["AI 판단", Bot, false],
  ["전략·설정", Settings2, false],
  ["리스크", ShieldCheck, false],
  ["시스템 상태", Activity, false],
  ["이력·감사", History, false],
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

function ConsoleShell({ session, onLogout, logoutBusy, error }: { session: SessionData; onLogout: () => Promise<void>; logoutBusy: boolean; error: string }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="console-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="sidebar-head"><Brand /><button className="mobile-close" onClick={() => setMenuOpen(false)} aria-label="메뉴 닫기"><X /></button></div>
        <nav aria-label="주요 메뉴">
          {navigation.map(([label, Icon, enabled]) => <button key={label} className={enabled ? "selected" : ""} disabled={!enabled} title={!enabled ? "후속 구현 예정" : undefined}><Icon size={19} /><span>{label}</span>{!enabled && <small>준비 중</small>}</button>)}
        </nav>
        <div className="sidebar-foot"><div className="avatar">{session.login_id.slice(0, 1).toUpperCase()}</div><div><strong>{session.login_id}</strong><small>관리자 · MOCK</small></div></div>
      </aside>
      {menuOpen && <button className="scrim" aria-label="메뉴 닫기" onClick={() => setMenuOpen(false)} />}

      <main className="console-main">
        <header className="topbar">
          <div className="topbar-left"><button className="menu-button" onClick={() => setMenuOpen(true)} aria-label="메뉴 열기"><Menu /></button><div className="market-state"><span className="status-dot amber" /> 키움 모의투자 <b>연결 전</b></div><div className="top-divider" /><div className="market-state"><span className="status-dot muted" /> KRX/NXT <b>감시 대기</b></div></div>
          <div className="top-actions"><span className="mock-badge">MOCK</span><button aria-label="알림" disabled><Bell size={19} /></button><button className="logout-button" onClick={onLogout} disabled={logoutBusy}><LogOut size={17} /> {logoutBusy ? "종료 중" : "로그아웃"}</button></div>
        </header>

        <div className="content">
          <div className="page-heading"><div><span className="eyebrow"><Gauge size={14} /> CONTROL CENTER</span><h1>대시보드</h1><p>시스템 연결과 거래 준비 상태를 확인합니다.</p></div><div className="sync-stamp"><Clock3 size={15} /> 세션 활성 · {new Date(session.expires_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 만료</div></div>
          {error && <div className="console-alert" role="alert"><CircleAlert size={17} /> {error}</div>}

          <section className="readiness-hero">
            <div><span className="card-label">SYSTEM READINESS</span><h2>Console 인증 완료</h2><p>거래 기능을 활성화하기 전에 Broker 연결과 계좌 재동기화가 필요합니다.</p></div>
            <div className="readiness-score"><strong>1<span>/4</span></strong><small>준비 단계</small></div>
          </section>

          <section className="status-grid" aria-label="시스템 준비 상태">
            <StatusCard icon={ShieldCheck} tone="ok" title="Web 보안" status="READY" description="ID·비밀번호·TOTP 세션이 활성화되었습니다." />
            <StatusCard icon={Database} tone="ok" title="데이터베이스" status="CONNECTED" description="인증 세션 저장소가 정상 응답했습니다." />
            <StatusCard icon={Radio} tone="wait" title="키움 Broker" status="NOT CONNECTED" description="모의투자 자격증명 연동 전입니다." />
            <StatusCard icon={Activity} tone="wait" title="시장 데이터" status="NOT STARTED" description="Watch 서비스는 후속 구현 단계입니다." />
          </section>

          <section className="dashboard-grid">
            <article className="panel guard-panel"><div className="panel-head"><div><ShieldCheck size={18} /><span>Cresta Guard</span></div><span className="status-pill ok">ENFORCED</span></div><div className="guard-body"><div className="shield-visual"><ShieldCheck size={36} /></div><div><h3>리스크 규칙 우선</h3><p>Guard는 AI 판단과 사용자 명령보다 우선합니다. 현재 거래 기능은 연결 전이므로 신규 주문이 차단됩니다.</p></div></div><div className="policy-row"><span>실거래</span><b>강제 비활성</b></div><div className="policy-row"><span>운영 환경</span><b>키움 모의투자</b></div></article>
            <article className="panel"><div className="panel-head"><div><ListChecks size={18} /><span>구현 진행 상태</span></div><span className="status-pill neutral">FOUNDATION</span></div><ol className="milestones"><li className="done"><span>1</span><div><b>보안 Console</b><small>로그인·세션·CSRF</small></div></li><li><span>2</span><div><b>Paper Broker</b><small>주문 상태 머신·체결 모의</small></div></li><li><span>3</span><div><b>Guard & Watch</b><small>위험 규칙·시장 데이터</small></div></li><li><span>4</span><div><b>키움 모의투자</b><small>계좌·주문 연동</small></div></li></ol></article>
            <article className="panel activity-panel"><div className="panel-head"><div><Activity size={18} /><span>최근 시스템 활동</span></div></div><div className="empty-state"><Radio size={26} /><h3>이벤트 대기 중</h3><p>거래 서비스가 연결되면 판단·주문·위험 이벤트가 여기에 표시됩니다.</p></div></article>
          </section>
        </div>
      </main>
      <nav className="mobile-nav" aria-label="모바일 메뉴"><button className="active"><LayoutDashboard /><span>대시보드</span></button><button disabled><Eye /><span>감시</span></button><button disabled><ListChecks /><span>승인</span></button><button onClick={() => setMenuOpen(true)}><Menu /><span>전체</span></button></nav>
    </div>
  );
}

function StatusCard({ icon: Icon, tone, title, status, description }: { icon: typeof Activity; tone: "ok" | "wait"; title: string; status: string; description: string }) {
  return <article className={`status-card ${tone}`}><div className="status-icon"><Icon size={20} /></div><div><span>{title}</span><strong>{status}</strong><p>{description}</p></div></article>;
}

export function CrestaConsole() {
  const [screen, setScreen] = useState<Screen>("boot");
  const [session, setSession] = useState<SessionData | null>(null);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
  if (screen === "console" && session) return <ConsoleShell session={session} onLogout={logout} logoutBusy={busy} error={error} />;
  return <LoginPanel screen={screen === "totp" ? "totp" : "credentials"} busy={busy} error={error} onPassword={passwordLogin} onTotp={totpLogin} onBack={() => { setChallengeId(null); setError(""); setScreen("credentials"); }} />;
}
