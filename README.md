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
- Web UI 감시 종목 최대 3개 관리, 키움 MOCK KRX `0B`·`0D` 실시간 구독, 1분봉과 VWAP·SMA5·상대 거래량·실현 변동성·고점 하락률·호가 spread 영속·조회
- 서버 소유 KRX/NXT snapshot과 KST 세션·호가·유동성으로 `KRX/NXT/SOR/WAIT`를 계산하고 주문 없이 이력을 남기는 SHADOW 거래시장 선택 진단
- 키움 모의투자 전용 메모리 토큰·REST client와 `ka10001` 복구 시세 정규화 기반
- 키움 `ka00001` 10자리 계좌 일치 검증과 비밀 마스킹 `kiwoom-check` 점검 명령
- 키움 `ka10075`·`ka10076`·`kt00018` 연속조회 정규화와 읽기 전용 DB 대조 `kiwoom-reconcile-check`
- PostgreSQL 단일 lease·fencing, 키움 WebSocket LOGIN·`00`/`04` 구독, PING echo와 주기·이벤트 기반 재동기화를 수행하는 별도 Broker worker
- KST 평일 08:00~20:00에 감시 종목을 5분·10분 슬롯으로 평가하고 TRADING 판단을 SHADOW Guard에 인계하는 별도 AI scheduler
- ACTIVE 역할 배정 snapshot으로 DIAGNOSTIC DAG를 등록하고 stage claim·lease·fencing·만료 복구를 수행하는 별도 Agent worker
- Agent 호출별 Adapter 추출 구조화 JSON을 검증 전 단계에서 제한적으로 보관하고 Console에서 필요할 때만 조회하는 응답 이력
- `USER_DEFAULT / RISK_POLICY`의 진입금액·투자한도·보유/진입 횟수·고정손절·시세·spread·가격편차를 버전으로 검증·활성화하는 Guard 위험 설정 UI/API
- `scout-input-v1` canonical 입력·hash와 지표 provenance를 저장하고 이를 사용하는 `deterministic-mock-v2` Scout/Core
- 인증된 `GET /api/v1/system/broker`와 `kiwoom-worker-status` 안전 상태 조회
- N100·16GiB 서버용 Docker Compose 자원 제한 초안

Console의 주문 생성·승인 화면, 주문가격 산정, 전체 Guard·외부 AI provider와 키움 실시간 체결의 주문 원장 반영은 아직 구현되지 않았습니다. Active worker의 영속 주문 polling과 중복 방지·`UNKNOWN` 즉시 재동기화는 구현됐지만 주문 생성·공개 승인 경로가 없어 정상 운영에서는 키움 주문이 생성되지 않습니다. 외부 주문·포지션도 자동 편입하거나 수정하지 않습니다.

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

현재 AI 실험 경로는 외부 Provider route와 선택형 OpenDART·KRX·NAVER API HUB 증거 수집을 사용하는 Agent Runtime v6를 포함한다. Web Console의 AI 판단 화면에서 5개 role route 준비도를 확인하고 ENTRY/POSITION context, 서버 계산 포지션 위험값과 검증된 Market Context가 고정된 DIAGNOSTIC DAG를 실행할 수 있다. Market Context가 없으면 시장·업종 값을 추정하지 않고, 불완전한 필수 Scout가 있으면 Core Provider 호출 없이 `WAIT/UNKNOWN`으로 축소한다. 승인·주문은 생성하지 않는다.

AI 설정은 Provider·Model 카탈로그와 역할별 배정을 분리한다. 등록한 모델은 여러 Scout·Core에서 재사용할 수 있고 역할별 generation parameter override와 이력, 5개 역할의 원자적 일괄 활성화를 지원한다.

Agent 응답 이력은 Provider 원문이 아니라 Adapter가 추출한 서버 검증 전 구조화 JSON이다. 목록 조회에는 포함하지 않으며 Console에서 개별 호출의 `구조화 응답 보기`를 선택할 때만 가져온다. 64 KiB를 넘거나 민감한 key 이름을 포함한 응답은 저장하지 않고 해당 호출을 fail-closed 처리한다.

`deploy/.env.example`은 키 이름과 비밀 파일 경로만 제공하며 실제 값은 `secrets/`에 생성해야 합니다. secret 생성 또는 교체 후 `sudo deploy/prepare-secrets.sh`를 실행해 비밀값을 출력하지 않고 API 고정 UID/GID `10001:10001`과 읽기 전용 권한을 적용합니다. 이 준비 없이 migration이나 API를 시작하지 않습니다.

OpenDART 공시, KRX 전 거래일 공식 시장 증거와 NAVER API HUB 뉴스 수집은 기본적으로 비활성화되어 있다. DART·KRX 키와 NAVER API HUB Client ID·Secret을 각 `secrets/` 파일에 준비한 뒤 선택형 `deploy/compose.dart.yaml`, `deploy/compose.krx.yaml`, `deploy/compose.naver-news.yaml`을 적용해야 활성화된다. DART·KRX 검증 메타데이터는 PRIMARY, 최신 종목 연관 뉴스 메타데이터는 SECONDARY로 저장한다. 활성 source가 모두 정상 완료되고 최신 KRX 증거가 있을 때만 Bundle을 `VERIFIED`로 승격한다.

운영 Web 진입점은 `https://trade.mihoservice.xyz`이며, Compose gateway는 `127.0.0.1:7788`에만 바인딩됩니다. 호스트 Nginx 예시는 `deploy/host-nginx.example.conf`에 있으며 7788 포트는 인터넷에 직접 개방하지 않습니다.

서버 재부팅 후 Compose 스택을 자동 복구하려면 최초 1회 `deploy/cresta-boot.service`를 systemd에 설치하고 활성화합니다. `deploy/boot-reconcile.sh`가 비어 있지 않은 DART·KRX secret과 완전한 NAVER credential 쌍을 감지해 선택 overlay를 자동 포함한다. unit 변경이 포함된 업데이트 뒤에는 파일을 다시 설치하고 `daemon-reload` 및 실제 재부팅 인수시험을 해야 하며, 정확한 절차는 [배포·운영·장애복구 명세](docs/OPERATIONS_RUNBOOK.md)의 3.2절과 4절을 따릅니다.

## 문서

- [제품 요구사항](docs/PRODUCT_REQUIREMENTS.md)
- [거래 세션 및 감시 운영 명세](docs/TRADING_SESSION_SPEC.md)
- [거래시장 자동 선택 명세](docs/VENUE_SELECTION_SPEC.md)
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
- [다중 에이전트 오케스트레이션 명세](docs/MULTI_AGENT_ORCHESTRATION_SPEC.md)
- [LLM Provider 및 Gateway 명세](docs/LLM_PROVIDER_GATEWAY_SPEC.md)
- [판단 실행 및 승인 오케스트레이션 명세](docs/DECISION_EXECUTION_SPEC.md)
- [데이터베이스 및 영속성 명세](docs/DATABASE_SPEC.md)
- [배포·운영·장애복구 명세](docs/OPERATIONS_RUNBOOK.md)
- [구현 착수 준비도 검토](docs/IMPLEMENTATION_READINESS_REVIEW.md)
- [MVP 제품 및 시스템 설계](docs/SYSTEM_DESIGN.md)
- [HTTP 및 WebSocket API 명세](docs/API_SPEC.md)
- [구현 상태](IMPLEMENTATION_STATUS.md)
- [테스트 계획](TEST_PLAN.md)

## 안전 원칙

실거래보다 분석 전용 → 자체 모의매매 → 증권사 모의투자 → 승인형 실거래 순서로 검증합니다. AI의 출력은 주문이 아니라 제한된 행동 제안이며, 모든 주문은 Cresta Guard의 결정론적 검사를 통과해야 합니다.
