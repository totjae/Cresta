# 키움 Broker Adapter 명세

## 1. 목적

키움 REST API와 Cresta 내부 주문·계좌 인터페이스 사이의 인증, 연결, 단일 실행권한, 환경 분리와 배포 기준을 정의한다. 이 문서는 키움 고유 규격이 Core·Guard·Console로 확산되지 않도록 Adapter 경계를 관리한다.

## 2. 적용 범위

- 키움 국내주식 REST API와 WebSocket
- 키움 모의투자 계좌 1개
- Ubuntu Server와 Docker Compose 배포
- 인증정보·접근토큰 관리
- 계좌별 단일 Broker worker
- 모의·실거래 환경 분리

첫 버전은 키움 모의투자 주문만 활성화한다. 실거래 자격증명은 배포하지 않는다.

## 3. 상세 명세

### 3.1 확정 배포 기준

| ID | 항목 | 값 | 상태 |
| --- | --- | --- | --- |
| KIW-001 | 키움 등록 고정 공인 IP | `180.68.4.149` | 2026-08-03 실제 서버 출구·MOCK 인증 확인 |
| KIW-002 | 서버 사용자 홈 | `/home/totquf4171` | 확정 |
| KIW-003 | Cresta 배포 루트 | `/home/totquf4171/cresta` | 확정 |
| KIW-004 | 초기 Broker 환경 | `MOCK` | 확정 |
| KIW-005 | 초기 계좌 수 | 모의투자 계좌 1개 | 계좌 식별값은 비밀로 주입 |

`180.68.4.149`가 서버 NIC에 직접 할당된 주소인지와 무관하게, 키움 API 요청이 외부로 나갈 때 보이는 실제 출구 IP가 이 값과 같아야 한다. 2026-08-03 운영 서버에서 출구 IP와 키움 MOCK 인증·`ka10001` 조회를 확인했다.

### 3.2 권장 디렉터리 구조

```text
/home/totquf4171/cresta/
├── app/                 배포할 애플리케이션 소스 또는 이미지 메타데이터
├── deploy/              Docker Compose와 배포 스크립트
├── config/              비밀이 아닌 환경별 설정
├── secrets/             키움·DB 등 비밀 원본 파일, Git 제외
├── data/
│   ├── postgres/        PostgreSQL bind mount
│   └── redis/           Redis 영속 데이터가 필요할 때 사용
├── logs/                서비스 로그
├── backups/             암호화된 백업
└── artifacts/           진단·시험 결과, 비밀정보 제외
```

| ID | 요구사항 |
| --- | --- |
| KIW-010 | 배포 파일은 `/home/totquf4171/cresta` 아래에 배치한다. |
| KIW-011 | `secrets/`, `data/`, `logs/`, `backups/`는 Git 저장소에 포함하지 않는다. |
| KIW-012 | Docker 컨테이너에는 필요한 비밀만 읽기 전용 파일로 마운트한다. |
| KIW-013 | 데이터 디렉터리와 백업 디렉터리는 애플리케이션 이미지 교체로 삭제되지 않아야 한다. |

### 3.3 모의투자 계좌 식별

문서와 일반 설정에는 실제 계좌번호를 기록하지 않고 다음 별칭만 사용한다.

```yaml
broker_account_alias: KIWOOM_MOCK_PRIMARY
broker_environment: MOCK
```

실제 계좌 식별값은 `/home/totquf4171/cresta/secrets/`의 비밀 파일에서 Docker secret으로 주입한다.

```text
kiwoom_mock_app_key
kiwoom_mock_app_secret
kiwoom_mock_account_id
```

실제 파일 내용은 문서, 로그, 오류 메시지와 테스트 fixture에 포함하지 않는다.

### 3.4 인증정보 저장과 접근토큰

키움은 실전·모의 App Key를 별도로 관리하며, OAuth Client Credentials로 유효기간 24시간의 접근토큰을 발급한다. 공식 자료 확인 기준일은 2026-07-31이다.

참고 자료:

- <https://openapi.kiwoom.com/intro/serviceInfo>
- <https://openapi.kiwoom.com/guide/apiguide?dummyVal=0>

| ID | 요구사항 |
| --- | --- |
| KIW-020 | 비밀 디렉터리는 권한 `0700`, 개별 비밀 파일은 `0600` 이하로 제한한다. |
| KIW-021 | App Key·Secret·계좌 식별값은 환경변수 평문 목록보다 Docker secret 파일 주입을 우선한다. |
| KIW-022 | 접근토큰은 Broker worker 메모리에만 보관하고 재시작 시 재발급한다. |
| KIW-023 | 접근토큰 원문을 PostgreSQL, Redis, 로그에 저장하지 않는다. |
| KIW-024 | 토큰 만료 60분 전부터 갱신 가능 상태로 보고 단일 인증 관리자가 갱신한다. |
| KIW-025 | 토큰 갱신 실패 시 신규매수를 차단하고 기존 주문·포지션 상태를 보존한다. |

권장 사전 점검:

```text
07:25 토큰 확인·필요 시 발급
→ 키움 인증 및 계좌조회 시험
→ 07:30 재동기화·장 전 점검
```

### 3.5 고정 IP 변경 정책

초기 정책은 fail-closed다.

| ID | 요구사항 |
| --- | --- |
| KIW-030 | 기대 출구 IP는 비밀이 아닌 설정 `expected_egress_ip=180.68.4.149`로 관리한다. |
| KIW-031 | 출구 IP 불일치 또는 키움 IP 인증 오류 시 신규 API 세션과 신규매수를 중지한다. |
| KIW-032 | IP 불일치 중 주문을 다른 네트워크로 우회하지 않는다. |
| KIW-033 | IP 변경 후 키움 등록, 인증, 계좌조회와 재동기화를 통과해야 `READY`로 복귀한다. |
| KIW-034 | IP 불일치는 `BROKER_IP_MISMATCH` 경보와 감사 로그를 생성한다. |

### 3.6 계좌별 단일 실행 프로세스

한 계좌의 주문 권한을 가진 Broker worker는 동시에 하나만 허용한다.

```text
API/Scout/Core/Guard
→ 내부 명령 큐
→ Active Broker Worker 1개
→ 키움 API
```

| ID | 요구사항 |
| --- | --- |
| KIW-040 | 동일 계좌에 둘 이상의 주문 가능 Broker worker가 동시에 활성화되지 않게 한다. |
| KIW-041 | Active worker는 계좌 별칭을 키로 한 분산 lease를 획득해야 주문할 수 있다. |
| KIW-042 | lease 상실 즉시 신규 주문을 중단하고 진행 중 주문을 재동기화 대상으로 표시한다. |
| KIW-043 | 대기 worker는 조회·주문을 키움에 직접 보내지 않고 Active worker 승계 전까지 대기한다. |
| KIW-044 | 승계한 worker는 계좌 전체 재동기화가 끝난 후에만 주문 권한을 얻는다. |

DB의 계좌 소유권 레코드와 만료 가능한 lease를 기본으로 사용한다. Redis는 빠른 상태 공유에 사용할 수 있지만 Redis 잠금만으로 계좌 소유권을 확정하지 않는다.

### 3.7 모의·실거래 환경 분리

```yaml
broker:
  environment: MOCK
  account_alias: KIWOOM_MOCK_PRIMARY
  live_trading_enabled: false
```

| ID | 요구사항 |
| --- | --- |
| KIW-050 | 모의·실거래 도메인, App Key, 계좌 별칭과 설정 파일을 분리한다. |
| KIW-051 | `MOCK` 환경에서 실거래 자격증명을 로드하면 서비스 시작을 거부한다. |
| KIW-052 | 첫 버전 배포에는 실거래 App Key·Secret을 넣지 않는다. |
| KIW-053 | 모든 Console 화면과 감사 로그에 `MOCK` 환경을 명확히 표시한다. |
| KIW-054 | 실거래 전환은 단순 환경변수 한 개 변경으로 수행할 수 없게 별도 승인 절차를 둔다. |

### 3.8 KRX·NXT·SOR 처리

키움 공식 안내에 따르면 모의투자 주문·계좌조회는 KRX만 지원하지만 NXT 시세는 제공될 수 있다. 공식 자료 확인 기준일은 2026-07-31이다.

참고 자료: <https://openapi.kiwoom.com/intro/mockInvestInfo?dummyVal=0>

MVP 정책:

```yaml
mock_market_policy:
  executable_market: KRX
  order_price_quote_source: KRX
  guard_quote_source: KRX
  nxt_quote_usage: DISPLAY_AND_ANALYSIS_ONLY
  sor_enabled: false
```

| ID | 요구사항 |
| --- | --- |
| KIW-060 | 모의투자 주문은 KRX로만 전송한다. |
| KIW-061 | 모의주문 가격과 Guard 체결 가능성 검사는 KRX 호가를 사용한다. |
| KIW-062 | NXT 시세를 KRX 모의주문의 실행 가격으로 사용하지 않는다. |
| KIW-063 | NXT·SOR 주문 기능은 인터페이스만 예약하고 실거래 검증 전 활성화하지 않는다. |

### 3.9 초기 타임아웃과 재시도

아래 값은 모의투자 측정 전 초기값이다.

```yaml
http_policy:
  connect_timeout_seconds: 2
  query_timeout_seconds: 5
  order_timeout_seconds: 3
  cancel_timeout_seconds: 3
  token_timeout_seconds: 5
```

| 요청 | 재시도 정책 |
| --- | --- |
| 조회 요청 네트워크 오류 | 제한적 지수 백오프 재시도 |
| 호출 제한 | 서버 지시에 따라 지연 후 큐 재진입 |
| 명확한 인증 실패 | 토큰 1회 갱신 후 조회 재시도 |
| 명확한 업무 거부 | 재시도하지 않고 표준 오류로 변환 |
| 주문·취소·정정 응답 시간초과 | 자동 재전송 금지, `UNKNOWN` 및 재동기화 |

### 3.10 호출 제한과 우선순위

키움 공식 안내 기준 국내주식 실전 주문·조회는 계좌·토큰별 각각 초당 5회이며, 모의투자는 계좌·토큰·TR별 초당 1회다. 제한은 변경될 수 있으므로 설정값과 공식 문서를 배포 전에 재확인한다.

참고 자료: <https://openapi.kiwoom.com/intro?dummyVal=0>

요청 우선순위:

```text
긴급청산·손절
→ 취소·정정
→ UNKNOWN 주문 확인·재동기화
→ 일반 매도
→ 승인된 매수
→ 계좌 조회
→ 일반 시세·과거 데이터 조회
```

| ID | 요구사항 |
| --- | --- |
| KIW-070 | 모든 키움 REST 호출은 중앙 rate limiter와 요청 큐를 통과한다. |
| KIW-071 | limiter는 계좌·토큰·TR과 주문/조회 분류를 구분한다. |
| KIW-072 | 손절·취소 요청을 위해 주문 호출 용량을 예약한다. |
| KIW-073 | 호출 제한 오류로 요청 순서가 바뀌어도 동일 주문을 중복 생성하지 않는다. |

### 3.11 WebSocket 소유와 재연결

```yaml
websocket_policy:
  owner: ACTIVE_BROKER_WORKER
  reconnect_backoff_seconds: [1, 2, 5, 10, 30]
  block_new_buy_when_disconnected: true
  reconcile_after_reconnect: true
  resubscribe_automatically: true
```

| ID | 요구사항 |
| --- | --- |
| KIW-080 | WebSocket 세션은 Active Broker worker가 단독 소유한다. |
| KIW-081 | 재연결 후 주문체결·잔고·시세 구독을 복원한다. |
| KIW-082 | 단절 구간은 REST 미체결·체결·잔고 조회로 복구한다. |
| KIW-083 | WebSocket 단절 중 신규매수를 차단한다. |
| KIW-084 | 재구독과 재동기화 완료 전 `READY`로 복귀하지 않는다. |

### 3.12 Broker 인터페이스

```text
authenticate
get_health
get_quote
subscribe_quotes
subscribe_order_events
subscribe_balance_events
get_positions
get_available_cash
get_open_orders
get_fills
place_order
cancel_order
replace_order
reconcile_account
```

Core·Guard·Console은 키움 TR 코드나 원본 필드에 직접 의존하지 않는다. Adapter는 키움 응답을 Cresta 표준 주문·체결·계좌 모델과 표준 오류 코드로 변환한다.

### 3.13 감사와 비밀 마스킹

기록 대상:

- TR 코드, 요청·응답 시각과 지연시간
- 키움 결과 코드와 마스킹된 결과 메시지
- 키움 주문번호와 `correlation_id`
- 재시도 횟수와 호출 제한 대기시간
- WebSocket 연결·단절·재구독
- 정규화 전 원본 응답 해시

기록 금지:

- App Key·App Secret
- 접근토큰과 Authorization 헤더
- 전체 계좌번호
- 비밀 파일 경로의 실제 내용

### 3.14 모의투자 인증·REST 시세 계약

2026-08-01 기준 키움증권 공식 REST API 저장소와 API 명세 JSON으로 다음 계약을 확인했다.

참고 자료:

- <https://github.com/Kiwoom-Securities/Kiwoom-REST-API>
- <https://openapi.kiwoom.com/guide/apiguide?dummyVal=0>

| ID | 요구사항 |
| --- | --- |
| KIW-090 | 모의투자 REST base URL은 `https://mockapi.kiwoom.com`, WebSocket base URL은 `wss://mockapi.kiwoom.com:10000`으로 고정하며 첫 버전에서 설정값으로 운영 URL로 바꿀 수 없게 한다. |
| KIW-091 | 접근토큰은 `POST /oauth2/token`, API ID `au10001`, JSON body의 `grant_type=client_credentials`, `appkey`, `secretkey`로 발급한다. |
| KIW-092 | 토큰 응답의 `expires_dt`는 `yyyyMMddHHmmss` 한국시간으로 해석하고, 토큰은 프로세스 메모리에만 보관하며 만료 60분 전부터 한 번만 갱신한다. |
| KIW-093 | 일반 REST 요청은 `Content-Type: application/json;charset=UTF-8`, `api-id`, `authorization: Bearer <token>` 헤더를 사용한다. 인증 실패 시 토큰을 폐기하고 최대 한 번만 재발급·재요청한다. |
| KIW-094 | 복구용 기본 시세 snapshot은 `POST /api/dostk/stkinfo`, API ID `ka10001`, body `{"stk_cd":"종목코드"}`를 사용한다. 부호가 포함된 가격 문자열은 방향 정보와 분리해 절대 가격으로 정규화한다. |
| KIW-095 | `return_code`가 0이 아니거나 필수 필드가 없거나 JSON이 아니면 해당 응답을 정상 시세로 저장하지 않는다. 오류 응답에는 토큰·App Key·Secret을 포함하지 않는다. |
| KIW-096 | 자격증명이 없거나 Broker가 비활성인 경우 API 서버는 계속 기동할 수 있지만 상태는 `NOT_CONFIGURED`이다. 자격증명 파일이 읽기 가능하면 외부 호출 전 상태는 `CONFIGURED`이며 실제 인증 성공 전 `CONNECTED`로 표시하지 않는다. |

첫 REST 기반 단계는 토큰 수명주기, 공통 REST client, `ka10001` 정규화와 구성 상태를 포함했다. 이번 계좌 부트스트랩 단계는 `ka00001` 일치 점검까지 확장하며, WebSocket 상시 연결, 주문 송신, 재동기화와 Active worker lease는 후속 단계로 유지한다.

### 3.15 모의투자 계좌조회와 부트스트랩 점검

2026-08-03 기준 키움 공식 API 가이드에서 다음 계약을 확인했다.

- API ID: `ka00001`
- Method·URL: `POST /api/dostk/acnt`
- 요청 body: 빈 JSON object
- 응답 계좌 필드: `acctNo`
- 계좌 형식: 뒤 2자리 분류값을 포함한 숫자 10자리

참고 자료: <https://openapi.kiwoom.com/m/guide/apiguide?jobTpCode=08>

| ID | 요구사항 |
| --- | --- |
| KIW-097 | Adapter는 `ka00001`로 현재 토큰에 귀속된 계좌번호를 조회하며 `acctNo`가 정확히 숫자 10자리가 아니면 응답을 거부한다. |
| KIW-098 | `kiwoom_mock_account_id`에는 분류값을 포함한 10자리 전체 계좌 식별값을 저장하고 키움 응답과 상수시간 비교한다. 8자리 prefix 또는 부분 일치는 허용하지 않는다. |
| KIW-099 | 계좌가 다르면 `KIWOOM_ACCOUNT_MISMATCH`, secret 형식이 잘못되면 `KIWOOM_ACCOUNT_ID_INVALID`, 응답 형식이 잘못되면 `KIWOOM_INVALID_RESPONSE`로 실패하고 신규 주문을 금지한다. |
| KIW-100 | 로그·CLI·HTTP 응답에는 전체 계좌번호를 출력하지 않는다. 점검 성공 시 앞 8자리를 `*`로 가리고 마지막 분류 2자리만 표시할 수 있다. |
| KIW-101 | `cresta-admin kiwoom-check`는 MOCK 설정 확인→토큰 발급→계좌조회→계좌 일치 검증 순으로 실행하며 성공 시 `ACCOUNT_VERIFIED`를 반환한다. App Key·Secret·token·전체 계좌번호는 출력하지 않는다. |
| KIW-102 | 일회성 점검 프로세스의 `ACCOUNT_VERIFIED`는 해당 점검 시점의 결과다. 프로세스 종료 후 API 상태를 `AUTHENTICATED`나 `READY`로 유지하지 않는다. |
| KIW-103 | Broker 준비 상태 순서는 `NOT_CONFIGURED → CONFIGURED → AUTHENTICATED → ACCOUNT_VERIFIED → RECONCILING → READY`이며, 어느 단계에서든 오류에 따라 `DEGRADED` 또는 `HALTED`로 전환할 수 있다. 이번 단계는 `ACCOUNT_VERIFIED`까지만 구현한다. |

## 4. 오류·예외 또는 경계 조건

- 고정 IP가 맞더라도 키움 등록이 완료되지 않았으면 인증 성공으로 간주하지 않는다.
- 토큰 갱신 중 기존 토큰이 유효하더라도 두 worker가 동시에 갱신하지 않는다.
- Active worker가 비정상 종료되어 lease가 만료될 때까지 대기 worker는 주문하지 않는다.
- 키움 응답 시간초과는 주문 실패를 의미하지 않으므로 같은 주문을 즉시 재전송하지 않는다.
- 모의투자 NXT 시세를 표시할 때 `주문 불가·분석용`임을 명확히 표시한다.
- secret 파일 권한 또는 소유자가 예상과 다르면 Broker 서비스를 시작하지 않는다.
- `/home/totquf4171/cresta`가 없는 경우 자동으로 상위 홈 전체를 변경하지 않고 배포 절차에서 명시적으로 생성한다.

## 5. 검증·인수 조건

2026-08-03 운영 서버에서 고정 출구 IP, MOCK 인증, `ka10001`, `ka00001` 10자리 일치와 마스킹 출력을 확인했다. 전체 계좌번호와 자격증명은 시험 기록에 남기지 않았다.

- `180.68.4.149` 출구에서 모의투자 인증과 계좌 조회가 성공한다.
- 등록되지 않은 출구 IP에서 인증 실패 시 신규 주문이 차단된다.
- 실제 계좌번호와 비밀값이 로그·문서·오류 응답에 노출되지 않는다.
- 동일 계좌에서 두 Broker worker를 시작해도 하나만 Active가 된다.
- Active worker lease 상실 후 신규 주문이 즉시 중단된다.
- 모의투자 환경에서 NXT·SOR 주문이 생성되지 않는다.
- 주문 응답 시간초과 시 중복 주문 없이 재동기화로 전환된다.
- WebSocket 재연결 후 구독과 계좌 상태가 복원되기 전 신규매수가 차단된다.
- 호출 제한 상황에서도 손절·취소 요청 우선순위와 멱등성이 유지된다.

## 6. 미결정·보류 항목

- 모의투자 계좌에 사용할 내부 별칭 확정
- 원본 키움 요청·응답을 보관할 기간과 암호화 방식
- WebSocket heartbeat와 실제 단절 판정 시간
- 키움 JSON 명세 다운로드 후 전체 필드·오류 코드 매핑
