# Cresta Backend

FastAPI 기반 Backend다. 현재 범위는 health endpoint, ID·비밀번호·TOTP 서버 세션 인증, Paper Broker 주문 상태 머신, 감시 종목 API와 Watch quote·1분봉·1차 지표 영속 상태, 키움 MOCK 토큰·시세·10자리 계좌 일치, 읽기 전용 주문·체결·잔고 대조와 상시 WebSocket worker다. Worker는 활성 KRX 감시 종목의 `0B`·`0D`를 구독한다. Paper 주문·체결과 Watch 시세 주입은 내부 service이며 공개 Web API로 제공하지 않는다.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

키움 Docker secret이 준비된 MOCK 배포에서 전체 계좌를 출력하지 않고 토큰 귀속 계좌를 점검한다.

```bash
cresta-admin kiwoom-check
cresta-admin kiwoom-reconcile-check
cresta-admin kiwoom-worker-status
cresta-worker kiwoom
```

`cresta-worker kiwoom`은 PostgreSQL lease를 획득한 한 프로세스만 LOGIN·`00`/`04` 구독과 REST 재동기화를 수행한다. WebSocket과 clean 대조가 모두 정상일 때만 `READY`다. READY worker는 내부 키움 MOCK 계좌의 `CREATED` 주문을 한 건씩 polling하며, `UNKNOWN`이면 다음 주문을 중단하고 즉시 재동기화한다. 공개 주문 생성 경로는 아직 없고 외부 주문·포지션도 자동 편입하지 않는다.

운영 시작 전 `CRESTA_DATABASE_URL`, `CRESTA_TOTP_ENCRYPTION_KEY`와 HTTPS cookie 설정을 비밀 파일 또는 안전한 환경 주입으로 제공해야 한다. 운영 Origin은 `https://trade.mihoservice.xyz`이며 호스트 Nginx가 `127.0.0.1:7788`의 Compose gateway로 전달한다.

Compose 배포에서는 `migration` one-shot service만 `alembic upgrade head`를 소유한다. API와 모든 worker는 migration의 성공 완료 뒤 시작하며 애플리케이션 프로세스가 자체적으로 migration을 실행하지 않는다. `/healthz`는 프로세스 liveness만 확인하고 `/readyz`는 PostgreSQL 연결과 Alembic head `20260829_0044` 일치를 확인한다. DB 연결 실패나 head drift는 readiness `503`으로 fail-closed한다.

## Agent Worker v2

`POST /api/v1/ai/agent-runs/diagnostic`은 최신 영속 snapshot과 5개 ACTIVE SHADOW route로 Intel → Verify → 4 Scout → Core DAG를 영속 queue에 등록하고 즉시 반환한다. 이 수동 경로는 항상 DIAGNOSTIC이며 decision·approval·order를 생성하지 않는다. 열린 포지션의 scheduler는 결정론적 TRADING/POSITION 판단을 먼저 처리한 뒤 같은 입력에 묶인 `TRADING_ADVISORY`를 내부 생성할 수 있다. `cresta-worker agent`가 stage를 claim·lease·fencing으로 비동기 실행하고, 서버 소유 fusion 정책이 검증된 고신뢰 청산 위험만 기존 판단보다 강한 새 TRADING Decision으로 승격한다. 외부 Core는 직접 주문하지 않으며 승격된 판단도 일반 실행 권한·Guard·승인/자동 주문 경계를 거친다. 조회 endpoint는 `/api/v1/ai/agent-runs`와 `/api/v1/ai/agent-runs/{run_id}`다.

`cresta-worker sourced-handoff`는 `CRESTA_V7_SOURCED_HANDOFF_ENABLED=true`일 때만 committed v7 sourced ENTRY Decision을 기존 execution reconciliation helper에 인계한다. 기본값은 false이며 Finalizer transaction이나 Broker 전송을 소유하지 않는다.

역할별 모델 배정은 `/api/v1/ai/role-assignments`에서 조회한다. 등록된 model profile을 여러 역할에서 재사용하고 route별 generation parameter override를 검증한 뒤, activation preview의 canonical target ID와 TOTP proof로 5개 역할을 원자 활성화한다. 외부 Adapter와 credential 등록은 아직 차단돼 있다.
