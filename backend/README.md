# Cresta Backend

FastAPI 기반 Backend다. 현재 범위는 health endpoint, ID·비밀번호·TOTP 서버 세션 인증, Paper Broker 주문 상태 머신, Watch quote 정규화·영속 상태, 키움 MOCK 토큰·시세·10자리 계좌 일치, 읽기 전용 주문·체결·잔고 대조와 상시 WebSocket worker다. Paper 주문·체결과 Watch 시세 주입은 내부 service이며 공개 Web API로 제공하지 않는다.

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

`cresta-worker kiwoom`은 PostgreSQL lease를 획득한 한 프로세스만 LOGIN·`00`/`04` 구독과 REST 재동기화를 수행한다. WebSocket과 clean 대조가 모두 정상일 때만 `READY`다. 외부 주문·포지션을 자동 편입하지 않으며 실제 주문도 전송하지 않는다.

운영 시작 전 `CRESTA_DATABASE_URL`, `CRESTA_TOTP_ENCRYPTION_KEY`와 HTTPS cookie 설정을 비밀 파일 또는 안전한 환경 주입으로 제공해야 한다. 운영 Origin은 `https://trade.mihoservice.xyz`이며 호스트 Nginx가 `127.0.0.1:7788`의 Compose gateway로 전달한다.
