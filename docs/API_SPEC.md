# HTTP 및 WebSocket API 명세 (v1)

기본 경로는 `/api/v1`이며 로그인 시작·완료 API를 제외한 변경 요청은 항상 인증 세션을 요구하고 필요한 경우 `Idempotency-Key`도 요구한다. 시간은 UTC ISO 8601, 비율은 퍼센트 단위로 명시한다. 인증 정책은 [인증 및 보안 명세](SECURITY_SPEC.md)를 따른다.

제품 동작은 [제품 요구사항](PRODUCT_REQUIREMENTS.md), [거래 세션 명세](TRADING_SESSION_SPEC.md), [주문 실행 명세](ORDER_EXECUTION_SPEC.md), [주문 상태 머신 명세](ORDER_STATE_MACHINE_SPEC.md), [재동기화 명세](RECONCILIATION_SPEC.md), [키움 Broker Adapter 명세](KIWOOM_BROKER_SPEC.md), [인증 및 보안 명세](SECURITY_SPEC.md)를 따른다.

## 1. 공통 계약

| ID | 요구사항 |
| --- | --- |
| API-001 | 요청·응답 본문은 UTF-8 JSON을 사용하고 모든 객체에 명시된 `schema_version`을 적용한다. |
| API-002 | 금액·가격·비율은 JSON number가 아닌 단위가 명시된 문자열로 전송해 클라이언트 부동소수점 손실을 방지한다. 수량은 정수다. |
| API-003 | 모든 응답은 `request_id`를 포함하고 상태 변경 결과는 생성·변경된 resource의 ID와 현재 version을 반환한다. |
| API-004 | 목록 API는 cursor pagination, 기본 50건·최대 200건을 사용하며 정렬 기준과 방향을 고정한다. |
| API-005 | 알 수 없는 요청 필드와 지원하지 않는 enum은 묵시적으로 무시하지 않고 `VALIDATION_ERROR`로 거부한다. |
| API-006 | 서버는 클라이언트가 보낸 사용자·계좌·Guard 결과·주문 상태를 신뢰하지 않고 세션과 서버 상태에서 다시 계산한다. |

### 1.1 동시성·멱등성

| ID | 요구사항 |
| --- | --- |
| API-010 | 주문·승인·설정 활성화·비상정지·복구 명령은 `Idempotency-Key`를 필수로 요구한다. |
| API-011 | 같은 사용자·endpoint·멱등성 키와 같은 payload는 최초 결과를 반환하고 다른 payload는 `IDEMPOTENCY_CONFLICT`로 거부한다. |
| API-012 | 멱등성 결과는 최소 24시간 보존하며 주문 관련 키는 주문 감사 보존기간 동안 중복 방지 식별자로 남긴다. |
| API-013 | 수정 API는 `If-Match` 또는 body의 expected version을 요구하고 불일치는 `VERSION_CONFLICT`로 반환한다. |
| API-014 | 요청 timeout이나 연결 종료는 실패 확정을 의미하지 않으며 클라이언트는 같은 멱등성 키로 결과를 조회한다. |

## 2. Endpoint

| Method | Path | 용도 |
|---|---|---|
| POST | `/auth/login/password` | 사용자 ID·비밀번호 검증 및 단기 TOTP challenge 발급 |
| POST | `/auth/login/totp` | challenge와 TOTP 검증 후 서버 세션 발급 |
| POST | `/auth/reauth/totp` | 고위험 행동에 결합된 단기 재인증 증명 발급 |
| POST | `/auth/logout` | 현재 세션과 연결 폐기 |
| GET | `/auth/session` | 현재 세션, 만료 예정과 최근 재인증 상태 조회 |
| GET | `/dashboard` | 계좌, 포지션, 시스템 상태 요약 |
| GET | `/quotes/{symbol}` | 시장별 최신 시세·품질·기준시각 조회 |
| GET/POST | `/watchlist` | 감시 종목 조회/등록 |
| DELETE | `/watchlist/{id}` | 감시 해제 |
| GET/PATCH | `/settings/execution-policy` | 행동별 자동·승인·비활성 정책 조회/수정 |
| GET/PATCH | `/settings/trading-session` | 감시·분석·신규매수·장 마감 시간 조회/수정 |
| GET/PATCH | `/settings/overnight-policy` | 익일 보유 정책 조회/수정 |
| GET/PATCH | `/settings/order-policy` | 가격, 승인 범위, 미체결·재호가 정책 조회/수정 |
| GET/PATCH | `/settings/risk-policy` | 투자·손실·손절·데이터 위험 설정 조회/초안 수정 |
| GET/PATCH | `/settings/emergency-policy` | 비상정지 기본 동작·확인·해제 정책 조회/초안 수정 |
| POST | `/settings/validate` | 설정 조합 서버 검증 및 영향 미리보기 |
| POST | `/settings/{version}/activate` | 검증된 설정 버전 즉시·예약 활성화 |
| POST | `/settings/{version}/rollback` | 이전 설정을 새 버전으로 복원 |
| GET | `/settings/history` | 설정 버전·차이·적용 결과와 감사 이력 조회 |
| GET | `/positions` | 실제 계좌와 동기화된 포지션 |
| GET | `/positions/{symbol}` | 포지션, 손절, 판단, 주문 요약 조회 |
| POST | `/positions/{symbol}/sell` | 사용자 부분·전량매도 의도 생성 |
| POST | `/positions/{symbol}/stop` | 손절 변경 요청 및 영향 검증 |
| GET | `/decisions` | 필터 가능한 AI 판단 기록 |
| GET | `/decisions/{id}` | 입력 snapshot·모델·검증·실행 결과 조회 |
| GET | `/approvals` | 대기·완료·만료 승인 조회 |
| GET | `/orders` | 주문 및 체결 기록 |
| GET | `/orders/{id}` | 주문 상태, 수량, 원주문·정정 관계와 이벤트 조회 |
| GET | `/reconciliation/status` | 현재 거래 게이트, 최근 대조 실행과 미해결 불일치 조회 |
| GET | `/reconciliation/mismatches` | 불일치 코드·심각도·해결 상태 조회 |
| POST | `/reconciliation/run` | 계좌 전체 또는 종목 재동기화 요청 |
| POST | `/reconciliation/external/{id}/adopt` | 외부 포지션을 수동 관리 포지션으로 편입 |
| POST | `/reconciliation/external/{id}/keep-halted` | 외부 주문·포지션을 격리 상태로 유지 |
| POST | `/approvals/{id}/approve` | 유효한 판단 승인 |
| POST | `/approvals/{id}/reject` | 승인 요청 거절 |
| POST | `/risk/emergency-stop` | 비상정지 활성화 및 미체결 취소 요청 |
| POST | `/risk/emergency-stop/release` | 재인증 후 비상정지 해제 |
| GET | `/system/health` | 데이터·브로커·큐·DB 상태 |
| GET | `/system/broker` | 키움 환경, 연결, 토큰 만료 예정, Active worker와 호출 제한 상태 |
| POST | `/system/broker/mock-order-test` | TOTP 재인증 후 MOCK·KRX 매수 1주 연결 시험 주문 대기열 생성 |

인증 API는 계정 존재·비밀번호 오류·TOTP 오류를 구분하지 않는 공통 오류를 반환한다. TOTP challenge와 재인증 증명은 1회용이며 URL이나 WebSocket query string으로 전달하지 않는다. 비밀번호·TOTP·복구 코드는 응답, 감사 이벤트와 애플리케이션 로그에 포함하지 않는다.

## 3. 리소스 계약

### 3.1 주문 의도 생성

```json
{
  "schema_version": "1.0",
  "sell_type": "PARTIAL",
  "quantity": 5,
  "reason": "USER_REQUEST",
  "expected_position_version": 12
}
```

응답은 주문 성공을 뜻하지 않는다. `execution_mode`에 따라 승인 리소스 또는 주문 의도를 반환한다.

```json
{
  "request_id": "01J...",
  "result_type": "APPROVAL_CREATED",
  "approval_id": "01J...",
  "order_intent_id": null,
  "guard_result": "PASSED",
  "expires_at": "2026-07-31T01:00:30Z"
}
```

| ID | 요구사항 |
| --- | --- |
| API-020 | UI는 응답의 `result_type`을 주문 체결로 표시하지 않는다. |
| API-021 | 서버는 최신 포지션 수량·version, 실행 모드, 시세와 Guard를 다시 검사한다. |
| API-022 | 부분매도 수량이 실제 매도 가능 수량을 넘으면 생성 전에 거부한다. |

### 3.2 승인

승인 요청은 body에 `expected_approval_version`, `reauth_proof_id`를 포함한다. 승인 응답은 `APPROVED`, `INVALIDATED` 또는 생성된 `order_intent_id`를 명시한다.

| ID | 요구사항 |
| --- | --- |
| API-030 | 승인·거절은 한 번만 적용되고 만료·무효화된 승인은 되살리지 않는다. |
| API-031 | 승인은 대상 결정·가격범위·수량·설정 버전에 결합하며 변경 시 무효화한다. |
| API-032 | 주문 승인에는 보안 명세의 대상 결합 TOTP 재인증 증명을 요구한다. |

### 3.3 설정

PATCH는 활성 설정을 직접 수정하지 않고 초안 version을 생성한다. validate 응답은 `errors`, `warnings`, `impacts`, `preview_version`과 기준 계좌 snapshot을 반환한다.

| ID | 요구사항 |
| --- | --- |
| API-040 | 설정 활성화는 검증된 `preview_version`과 기준 snapshot version을 요구한다. |
| API-041 | 위험 완화 변경은 `reauth_proof_id`와 변경 사유를 요구한다. |
| API-042 | 활성화·롤백은 새 불변 설정 버전을 반환하고 기존 버전을 변경하지 않는다. |

### 3.4 인증

`/auth/login/password`는 성공 시 5분짜리 1회용 `challenge_id`만 반환하고 세션을 만들지 않는다. `/auth/login/totp` 성공 시 HttpOnly 세션 쿠키와 별도 CSRF token 전달 절차를 사용한다. 인증 입력은 응답에 반영하지 않는다.

| ID | 요구사항 |
| --- | --- |
| API-050 | 인증 실패 응답은 ID·비밀번호·TOTP·잠금 중 어느 단계가 실패했는지 구분하지 않는다. |
| API-051 | 로그인·재인증 endpoint에는 멱등성 키 대신 인증 실패 제한과 challenge 1회 사용을 적용한다. |
| API-052 | CSRF token은 쿠키와 다른 채널의 header로 검증하고 상태 변경 GET endpoint를 만들지 않는다. |

### 3.5 Paper Broker 조회 모델

첫 Console 연동은 Paper Broker의 상태와 결과를 읽기 전용으로 제공한다. `/system/health`는 DB 연결, Paper Broker 사용 가능 여부, 거래 게이트와 키움·시장데이터 준비 상태를 반환한다. `/orders`와 `/orders/{id}`는 주문·체결·상태 이벤트를, `/positions`와 `/positions/{symbol}`은 실제 Paper 체결로 생성된 포지션만 반환한다.

| ID | 요구사항 |
| --- | --- |
| API-080 | Paper 조회 응답은 `MOCK` 환경과 `PAPER` 계좌를 명시하고 샘플 주문·포지션을 생성하지 않는다. |
| API-081 | 시스템 상태는 `STARTING`, `RECONCILING`, `READY`, `DEGRADED`, `HALTED` 거래 게이트와 차단 사유·version을 그대로 제공한다. |
| API-082 | 주문 목록은 수량 불변조건을 구성하는 주문·체결·취소·잔량과 `UNKNOWN`·`RECONCILING` 상태를 생략하지 않는다. |
| API-083 | 주문 상세는 체결과 상태 이벤트를 시간순으로 제공하고 원주문·정정 관계 식별자를 유지한다. |
| API-084 | 포지션 목록은 수량 0의 종료 포지션을 `state`로 구분하며 평균단가·version·기준시각을 제공한다. |
| API-085 | 운영 Web API에는 Paper 체결·게이트를 임의 생성하거나 변경하는 endpoint를 제공하지 않는다. 단, API-094~098의 제한된 키움 MOCK 연결 시험은 예외다. |
| API-086 | `kiwoom_broker_status`는 기능 비활성 또는 secret 미준비 시 `NOT_CONFIGURED`, secret 파일 준비 시 `CONFIGURED`를 반환한다. 실제 인증과 연결 확인 전에는 `CONNECTED`를 반환하지 않는다. |
| API-087 | `GET /system/broker`는 `KIWOOM_MOCK_PRIMARY`의 gate, worker 상태, lease 유효 여부, WebSocket·구독 상태와 최근 heartbeat·재동기화 시각을 반환한다. |
| API-088 | Broker 상태 응답에는 worker owner ID, token, 전체 계좌번호, 자격증명, 원본 오류 메시지와 원본 WebSocket payload를 포함하지 않는다. |
| API-089 | worker 레코드가 없거나 heartbeat가 lease 만료 기준을 넘으면 응답은 `READY`를 추론하지 않고 `NOT_STARTED` 또는 `STALE`을 표시한다. |
| API-094 | MOCK 주문 시험은 `live_trading_enabled=false`, 키움 구성 `CONFIGURED`, worker·gate·lease·WebSocket·구독 전체 READY일 때만 `CREATED`를 생성한다. |
| API-095 | 시험 주문은 `BUY`, `KRX`, 수량 1로 고정하고 6자리 종목코드와 `MARKET | LIMIT` 계약만 받는다. |
| API-096 | 시험 요청은 `KIWOOM_MOCK_ORDER_TEST` 행동과 `test_request_id`에 결합된 1회용 TOTP 재인증 증명, CSRF header와 고정 확인문구를 요구한다. |
| API-097 | 동일 시험 ID 재사용과 동일 종목의 활성 주문 보유 중 추가 시험을 거부한다. |
| API-098 | 응답의 `ORDER_QUEUED/CREATED`는 주문 전송·접수·체결 성공을 의미하지 않으며 UI는 주문 원장을 다시 조회한다. |

### 3.6 Watch snapshot 조회 모델

`GET /quotes/{symbol}?market=KRX`는 인증된 사용자에게 Watch가 마지막으로 확정한 정상 snapshot과 현재 stream 품질을 제공한다. snapshot이 없으면 `QUOTE_NOT_FOUND`를 반환한다.

| ID | 요구사항 |
| --- | --- |
| API-090 | quote 응답은 가격을 문자열, 수량을 정수, 시각을 UTC ISO 8601로 제공한다. |
| API-091 | 응답은 `quality`, `age_seconds`, `is_fresh`를 분리해 제공하며 `is_fresh`만으로 주문 가능 여부를 표현하지 않는다. |
| API-092 | KRX와 NXT 조회는 명시적인 `market`으로 분리하고 지원하지 않는 시장은 검증 오류로 거부한다. |
| API-093 | 시스템 상태의 시장데이터 값은 stream이 없으면 `NOT_STARTED`, 갭이 있으면 `DEGRADED`, 정상 stream이 모두 오래됐으면 `STALE`, 최신 정상 stream이 있으면 `AVAILABLE`로 표시한다. |
| API-094 | 공개 또는 인증된 HTTP mutation으로 fixture·quote·stream 상태를 주입하지 않는다. |

## 4. 공통 오류

```json
{
  "error": {
    "code": "STALE_MARKET_DATA",
    "message": "신규 매수가 차단되었습니다.",
    "correlation_id": "01J...",
    "retryable": true
  }
}
```

표준 HTTP 상태:

| 상태 | 사용 |
| --- | --- |
| `400` | JSON·스키마·enum 오류 |
| `401` | 인증 없음·만료·일반화된 로그인 실패 |
| `403` | 인증됐으나 작업 권한·CSRF·재인증 부족 |
| `404` | 접근 가능한 리소스 없음 |
| `409` | 상태·version·멱등성 충돌 |
| `410` | 승인·challenge 만료 |
| `422` | 의미적 정책·Guard 검증 실패 |
| `429` | 로그인 또는 API 요청 제한 |
| `503` | DB·Broker·재동기화·데이터 상태로 안전 실행 불가 |

| ID | 요구사항 |
| --- | --- |
| API-060 | 오류 응답은 내부 stack trace, 비밀값, 전체 계좌번호와 키움 원문 인증 오류를 포함하지 않는다. |
| API-061 | `retryable=true`여도 상태 변경 요청은 새 멱등성 키로 임의 재시도하지 않는다. |
| API-062 | Guard 차단은 성공 응답으로 숨기지 않고 안정된 오류 코드와 차단 범위를 반환한다. |

## 5. 실시간 이벤트

WebSocket `/api/v1/stream`은 `quote.updated`, `decision.created`, `approval.requested`, `order.updated`, `order.reconciliation_required`, `position.updated`, `risk.triggered`, `system.health_changed` 이벤트를 제공한다. 이벤트에는 증가하는 sequence와 발생 시각을 포함하며, 누락 감지 시 REST snapshot을 다시 조회한다.

재동기화 관련 이벤트는 `reconciliation.started`, `reconciliation.mismatch_detected`, `reconciliation.completed`, `reconciliation.failed`를 추가로 제공한다. 외부 주문 취소나 포지션 청산은 위 재동기화 API에서 직접 실행하지 않고 별도 승인·주문 API를 사용한다.

| ID | 요구사항 |
| --- | --- |
| API-070 | 이벤트는 `schema_version`, `event_id`, `sequence`, `event_type`, `occurred_at`, `resource_id`, `resource_version`과 payload를 포함한다. |
| API-071 | sequence는 사용자 stream 단위 단조 증가하며 누락 시 클라이언트가 REST snapshot을 다시 조회한다. |
| API-072 | WebSocket 재연결은 `last_sequence` 이후 replay를 요청할 수 있고 보존 범위를 벗어나면 `SNAPSHOT_REQUIRED`를 반환한다. |
| API-073 | 이벤트 전달은 at-least-once로 간주하며 클라이언트는 `event_id`와 resource version으로 중복을 제거한다. |
| API-074 | WebSocket으로 주문·설정 변경 명령을 받지 않고 상태 이벤트만 전송한다. |

## 6. 오류·예외 또는 경계 조건

- API 응답 유실 후 UI는 상태를 추정하지 않고 같은 멱등성 키 결과 또는 리소스 snapshot을 조회한다.
- 클라이언트 schema version이 서버 지원 범위를 벗어나면 거래 명령을 거부하고 업그레이드 필요 상태를 반환한다.
- 목록 조회 중 데이터가 바뀌어도 cursor는 중복·누락을 최소화하는 안정된 `(created_at, id)` 정렬을 사용한다.
- 인증 세션 만료와 WebSocket 단절은 진행 중 Broker 주문을 취소하지 않는다.

## 7. 검증·인수 조건

- 모든 상태 변경 endpoint에 인증·CSRF·version·멱등성 요구가 정책대로 적용된다.
- 응답 유실 후 동일 키 재조회에서 중복 주문·설정 버전이 생성되지 않는다.
- 승인 응답과 주문 접수·체결 상태를 UI가 구분할 수 있다.
- WebSocket sequence 누락·중복·재연결 후 REST snapshot으로 일관성을 복구한다.
- 오류 응답과 로그에 비밀값·내부 stack trace가 노출되지 않는다.

## 8. 미결정·보류 항목

- OpenAPI 기준 파일은 `docs/generated/openapi-v1.json`에 생성하며 CI에서 구현과 차이를 검사한다. 첫 버전은 별도 SDK를 배포하지 않고 Console 내부 TypeScript client만 생성한다.
- WebSocket replay 이벤트는 10분 보존하고 범위를 벗어나면 REST snapshot을 요구한다.
- Console은 배포 시점의 Chrome·Edge·Safari 최신 2개 주요 버전을 지원한다. HTTPS 응답은 Nginx에서 gzip 또는 Brotli를 사용하되 실시간 이벤트는 지연 우선으로 압축을 강제하지 않는다.

### 8.1 실행 권한 설정 API 1차 구현 계약

| ID | 요구사항 |
| --- | --- |
| API-043 | 실행 권한은 `GET /settings/execution-policy`, `POST /settings/execution-policy/drafts`, `POST /settings/execution-policy/{id}/validate`, `POST /settings/execution-policy/{id}/activate`, `GET /settings/execution-policy/history`로 관리한다. |
| API-044 | 실행 권한 활성화 요청은 CSRF, 공백이 아닌 변경 사유, 대상 버전에 결합된 TOTP 재인증 증명을 요구한다. |
| API-045 | API는 활성 버전이 없을 때 안전 기본값과 `active_version_id=null`을 반환하며 이를 영속 활성화로 표현하지 않는다. |

### 8.2 Mock AI 진단 API 계약

| ID | 요구사항 |
| --- | --- |
| API-099 | `POST /decisions/mock-evaluate`는 CSRF와 고유 요청 ID를 요구하고 최신 영속 snapshot으로 진단 판단 하나를 생성한다. |
| API-100 | `GET /decisions`와 `GET /decisions/{id}`는 모델·snapshot·설정 버전·Scout/Core 출력·실행 모드와 안전 차단 결과를 반환한다. |
| API-101 | Mock 진단 API는 주문·승인·설정·시장 snapshot을 변경하지 않으며 시세가 없거나 오래되면 주문 가능 행동을 반환하지 않는다. |

### 8.3 감시 종목 API 계약

| ID | 요구사항 |
| --- | --- |
| API-102 | `GET /watchlist`는 현재 사용자의 활성 감시 종목과 각 종목의 최신 snapshot 요약을 반환한다. snapshot이 없어도 종목은 `WAITING_FOR_DATA`로 반환한다. |
| API-103 | `POST /watchlist`는 CSRF, `schema_version=1.0`, 숫자 6자리 종목코드와 `market=KRX`를 요구한다. 중복은 `409`, 활성 3개 초과와 MOCK 미지원 시장은 `422`로 거부한다. |
| API-104 | `DELETE /watchlist/{id}`는 CSRF와 소유권을 검사하고 DB에서 삭제한다. WebSocket worker는 늦어도 설정된 동기화 주기 안에 구독을 해제한다. |
| API-105 | 감시 종목 항목은 최신 snapshot과 같은 입력에 결합된 `watch-indicators-v1` 요약을 선택적으로 포함한다. 지표가 아직 없으면 null이며 시세 대기와 구분한다. |
