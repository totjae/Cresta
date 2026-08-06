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

## Agent Worker v2

`POST /api/v1/ai/agent-runs/diagnostic`은 최신 영속 snapshot과 5개 ACTIVE SHADOW Mock route로 Intel → Verify → 4 Scout → Core DAG를 영속 queue에 등록하고 즉시 반환한다. `cresta-worker agent`가 stage를 claim·lease·fencing으로 비동기 실행한다. 결과는 항상 DIAGNOSTIC이며 Core는 `WAIT`만 반환하고 decision·approval·order를 생성하지 않는다. 조회 endpoint는 `/api/v1/ai/agent-runs`와 `/api/v1/ai/agent-runs/{run_id}`다.

역할별 모델 배정은 `/api/v1/ai/role-assignments`에서 조회한다. 등록된 model profile을 여러 역할에서 재사용하고 route별 generation parameter override를 검증한 뒤, activation preview의 canonical target ID와 TOTP proof로 5개 역할을 원자 활성화한다. 외부 Adapter와 credential 등록은 아직 차단돼 있다.
