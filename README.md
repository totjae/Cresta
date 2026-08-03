# Cresta

**Cresta — AI-Assisted Intraday Trading System**은 사용자가 선택한 국내 주식의 진입과 청산을 분석하고, 규칙 기반 리스크 엔진을 통해 주문을 통제하는 개인용 단기매매 시스템입니다.

이 저장소는 제품 명세, FastAPI Backend와 Next.js Console을 포함합니다. 첫 제품 버전은 키움 REST API 모의투자 주문 연결을 목표로 하며 실거래는 비활성화합니다.

## 현재 구현 범위

- FastAPI 기본 애플리케이션과 최소 공개 health endpoint
- PostgreSQL용 Alembic 인증 기반 migration
- ID·비밀번호·TOTP 로그인
- 서버측 session, HttpOnly cookie, CSRF·Origin 검사와 로그아웃
- 계정·IP 실패 제한, TOTP replay 차단과 고위험 행동 재인증 proof
- 비밀번호 Argon2id hash, TOTP secret AES-GCM 암호화와 감사 로그
- Next.js ID·비밀번호·TOTP 로그인, 세션 복구와 로그아웃 UI
- 보호된 반응형 MOCK Console, 실제 Paper 시스템 상태·주문 상세·포지션 조회 화면
- 결정론적 Paper Broker 주문·부분체결·취소·정정·응답유실 상태 머신
- 주문·체결·포지션 원장과 인증된 주문 조회 API
- Watch quote 정규화·중복/역순/갭 처리, PostgreSQL snapshot과 인증된 최신 시세 조회 API
- 키움 모의투자 전용 메모리 토큰·REST client와 `ka10001` 복구 시세 정규화 기반
- 키움 `ka00001` 10자리 계좌 일치 검증과 비밀 마스킹 `kiwoom-check` 점검 명령
- 키움 `ka10075`·`ka10076`·`kt00018` 연속조회 정규화와 읽기 전용 DB 대조 `kiwoom-reconcile-check`
- PostgreSQL 단일 lease·fencing, 키움 WebSocket LOGIN·`00`/`04` 구독, PING echo와 주기·이벤트 기반 재동기화를 수행하는 별도 Broker worker
- 인증된 `GET /api/v1/system/broker`와 `kiwoom-worker-status` 안전 상태 조회
- N100·16GiB 서버용 Docker Compose 자원 제한 초안

Console의 주문 생성·승인·설정 화면, 주문가격 산정, 전체 Guard·분봉·지표·AI와 키움 주문 송신·실시간 이벤트 직접 원장 반영은 아직 구현되지 않았습니다. 키움 계좌 대조와 Broker worker는 읽기 전용이며 외부 주문·포지션을 자동 편입하거나 수정하지 않습니다.

## Backend 개발 실행

```bash
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

DB migration과 관리자 생성은 TOTP 암호화 키 파일과 DB 연결을 안전하게 설정한 뒤 수행합니다.

```bash
alembic upgrade head
cresta-admin create-admin --login-id <사용자ID>
cresta-admin kiwoom-check
cresta-admin kiwoom-reconcile-check
cresta-admin kiwoom-worker-status
cresta-worker kiwoom
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend는 Node.js 24 기준으로 실행합니다.

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

`deploy/.env.example`은 키 이름과 비밀 파일 경로만 제공하며 실제 값은 `secrets/`에 생성해야 합니다. secret 생성 또는 교체 후 `sudo deploy/prepare-secrets.sh`를 실행해 비밀값을 출력하지 않고 API 고정 UID/GID `10001:10001`과 읽기 전용 권한을 적용합니다. 이 준비 없이 migration이나 API를 시작하지 않습니다.

운영 Web 진입점은 `https://trade.mihoservice.xyz`이며, Compose gateway는 `127.0.0.1:7788`에만 바인딩됩니다. 호스트 Nginx 예시는 `deploy/host-nginx.example.conf`에 있으며 7788 포트는 인터넷에 직접 개방하지 않습니다.

## 문서

- [제품 요구사항](docs/PRODUCT_REQUIREMENTS.md)
- [거래 세션 및 감시 운영 명세](docs/TRADING_SESSION_SPEC.md)
- [주문 가격 및 미체결 재처리 명세](docs/ORDER_EXECUTION_SPEC.md)
- [주문 상태 머신 및 키움 매핑 명세](docs/ORDER_STATE_MACHINE_SPEC.md)
- [계좌·주문 재동기화 명세](docs/RECONCILIATION_SPEC.md)
- [키움 Broker Adapter 명세](docs/KIWOOM_BROKER_SPEC.md)
- [Guard 리스크 및 비상정지 명세](docs/GUARD_RISK_SPEC.md)
- [사용자 설정 및 적용 명세](docs/CONFIGURATION_SPEC.md)
- [Web UI 명세](docs/WEB_UI_SPEC.md)
- [인증 및 보안 명세](docs/SECURITY_SPEC.md)
- [시장데이터 및 Watch 명세](docs/MARKET_DATA_SPEC.md)
- [Scout·Core AI 판단 계약](docs/AI_DECISION_SPEC.md)
- [데이터베이스 및 영속성 명세](docs/DATABASE_SPEC.md)
- [배포·운영·장애복구 명세](docs/OPERATIONS_RUNBOOK.md)
- [구현 착수 준비도 검토](docs/IMPLEMENTATION_READINESS_REVIEW.md)
- [MVP 제품 및 시스템 설계](docs/SYSTEM_DESIGN.md)
- [HTTP 및 WebSocket API 명세](docs/API_SPEC.md)
- [구현 상태](IMPLEMENTATION_STATUS.md)
- [테스트 계획](TEST_PLAN.md)

## 안전 원칙

실거래보다 분석 전용 → 자체 모의매매 → 증권사 모의투자 → 승인형 실거래 순서로 검증합니다. AI의 출력은 주문이 아니라 제한된 행동 제안이며, 모든 주문은 Cresta Guard의 결정론적 검사를 통과해야 합니다.
