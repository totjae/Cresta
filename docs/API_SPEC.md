# HTTP 및 WebSocket API 명세 (v1)

기본 경로는 `/api/v1`이며 로그인 시작·완료 API를 제외한 변경 요청은 항상 인증 세션을 요구하고 필요한 경우 `Idempotency-Key`도 요구한다. 시간은 UTC ISO 8601, 비율은 퍼센트 단위로 명시한다. 인증 정책은 [인증 및 보안 명세](SECURITY_SPEC.md)를 따른다.

제품 동작은 [제품 요구사항](PRODUCT_REQUIREMENTS.md), [거래 세션 명세](TRADING_SESSION_SPEC.md), [주문 실행 명세](ORDER_EXECUTION_SPEC.md), [주문 상태 머신 명세](ORDER_STATE_MACHINE_SPEC.md), [판단 실행 및 승인 명세](DECISION_EXECUTION_SPEC.md), [Guard 명세](GUARD_RISK_SPEC.md), [재동기화 명세](RECONCILIATION_SPEC.md), [키움 Broker Adapter 명세](KIWOOM_BROKER_SPEC.md), [인증 및 보안 명세](SECURITY_SPEC.md)를 따른다.

## 1. 공통 계약

| ID | 요구사항 |
| --- | --- |
| API-001 | 요청·응답 본문은 UTF-8 JSON을 사용하고 모든 객체에 명시된 `schema_version`을 적용한다. |
| API-002 | 금액·가격·비율은 JSON number가 아닌 단위가 명시된 문자열로 전송해 클라이언트 부동소수점 손실을 방지한다. 수량은 정수다. |
| API-003 | 모든 응답은 `request_id`를 포함하고 상태 변경 결과는 생성·변경된 resource의 ID와 현재 version을 반환한다. |
| API-004 | 목록 API는 결정론적 정렬과 서버측 상한을 사용한다. 현재 endpoint별 `limit` 상한은 응답 계약에 명시하며, cursor pagination은 대용량 주문·감사 이력에 필요해질 때 해당 endpoint version에서 도입한다. |
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
| GET/POST | `/settings/execution-policy*` | 행동별 자동·승인·비활성 정책 조회·초안·검증·활성화·이력 |
| GET/POST | `/settings/risk-policy*` | Guard 사용자 기본 위험 설정 조회·초안·검증·활성화·이력 |
| GET/PATCH | `/settings/execution-stage` | SHADOW·승인형·MOCK 자동 실행 단계 조회/변경 |
| GET/PATCH | `/settings/trading-session` | 감시·분석·신규매수·장 마감 시간 조회/수정 |
| GET/PATCH | `/settings/overnight-policy` | 익일 보유 정책 조회/수정 |
| GET/PATCH | `/settings/order-policy` | 가격, 승인 범위, 미체결·재호가 정책 조회/수정 |
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
| GET | `/approvals/{id}` | 승인 scope, Guard 결과, 상태·version 조회 |
| GET | `/guard/evaluations/{id}` | 비밀 없는 Guard rule 결과 조회 |
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
| GET | `/risk/emergency-stop` | 현재 계좌의 영속 `PAUSE_ENTRY` 상태 조회 |
| GET | `/system/health` | 데이터·브로커·큐·DB 상태 |
| GET | `/system/broker` | 키움 환경, 연결, 토큰 만료 예정, Active worker와 호출 제한 상태 |
| POST | `/system/broker/mock-order-test` | MOCK·KRX 매수 1주 연결 시험 주문 대기열 생성; 현재는 세션·CSRF·확인문구를 요구하고 TOTP 재인증은 API-DEV 정책에 따라 보류 |
| GET | `/system/stop-triggers` | 고정 손절 trigger 최근 이력(상태·손절가·trigger가·차단 reason). 읽기 전용, 인증 세션 필요, mutation 없음 |
| GET | `/system/risk-events` | 범용 위험 이벤트 원장 최근 이력(scope·rule_code·severity·state·resolution). 읽기 전용, 인증 세션 필요, mutation 없음 |

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

첫 Console 연동은 MOCK 거래 원장의 상태와 결과를 읽기 전용으로 제공한다. `/system/health`는 DB 연결, Paper Broker 사용 가능 여부, 거래 게이트와 키움·시장데이터 준비 상태를 반환한다. `/orders`와 `/orders/{id}`는 주문·체결·상태 이벤트와 미체결 정책·timeout·재호가 횟수·다음 자동처리 시각을 반환한다. `/positions`와 `/positions/{symbol}`은 legacy `PAPER` 원장과 Broker 기준 `KIWOOM_MOCK_PRIMARY` projection을 함께 조회하며, 같은 종목이 두 원장에 모두 존재하면 상세 조회는 키움 projection을 우선한다. 포지션은 총수량·매도가능수량·Broker 평균단가·Cresta 관리수량·관리평균단가·외부수량을 제공하고 `origin`은 `CRESTA_MANAGED`, `EXTERNAL`, `MIXED` 중 하나다.

| ID | 요구사항 |
| --- | --- |
| API-080 | Paper 조회 응답은 `MOCK` 환경과 `PAPER` 계좌를 명시하고 샘플 주문·포지션을 생성하지 않는다. |
| API-081 | 시스템 상태는 `STARTING`, `RECONCILING`, `READY`, `DEGRADED`, `HALTED` 거래 게이트와 차단 사유·version을 그대로 제공한다. |
| API-082 | 주문 목록은 수량 불변조건을 구성하는 주문·체결·취소·잔량과 `UNKNOWN`·`RECONCILING` 상태를 생략하지 않는다. |
| API-083 | 주문 상세는 체결과 상태 이벤트를 시간순으로 제공하고 원주문·정정 관계 식별자를 유지한다. |
| API-158 | 주문 상세 이벤트는 정제된 `broker_result_code`와 `broker_result_message`를 nullable 필드로 제공한다. API는 `payload_json`과 Broker 원문, 계좌번호, credential 또는 authorization 값을 반환하지 않는다. |
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
| API-155 | `POST /venue-selections/diagnostic`은 로그인·CSRF를 요구하고 서버의 최신 KRX/NXT snapshot으로 `KRX/NXT/SOR/WAIT`를 평가한다. 클라이언트는 호가·평가시각·실행시장을 제출할 수 없으며 응답은 항상 `SHADOW`, `order_creation_allowed=false`다. |
| API-156 | `GET /venue-selections`은 현재 사용자의 평가 이력만 최신순으로 반환하며 종목과 개수를 제한할 수 있다. |
| API-157 | 거래시장 평가 응답은 서버가 계산한 `calendar_policy_version`, `trading_day_status`, `calendar_reason`을 반환한다. 휴장일에는 장중 시각이어도 `CLOSED/WAIT`이며 클라이언트가 이를 재해석하지 않는다. |
| API-093 | 시스템 상태의 시장데이터 값은 stream이 없으면 `NOT_STARTED`, 갭이 있으면 `DEGRADED`, 정상 stream이 모두 오래됐으면 `STALE`, 최신 정상 stream이 있으면 `AVAILABLE`로 표시한다. |
| API-106 | 공개 또는 인증된 HTTP mutation으로 fixture·quote·stream 상태를 주입하지 않는다. |

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

WebSocket `/api/v1/stream`은 `quote.updated`, `decision.created`, `decision.execution_updated`, `approval.requested`, `approval.updated`, `risk.evaluated`, `risk.triggered`, `order.updated`, `order.reconciliation_required`, `position.updated`, `system.health_changed` 이벤트를 제공한다. 이벤트에는 증가하는 sequence와 발생 시각을 포함하며, 누락 감지 시 REST snapshot을 다시 조회한다.

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

- 현재 API 기준은 이 문서와 FastAPI가 생성하는 `/openapi.json`이다. 정적 OpenAPI 파일과 CI drift 검사는 승인·주문 공개 API를 활성화하기 전 도입하며, 도입 전에는 존재하는 것으로 표시하지 않는다.
- WebSocket replay 이벤트는 10분 보존하고 범위를 벗어나면 REST snapshot을 요구한다.
- Console은 배포 시점의 Chrome·Edge·Safari 최신 2개 주요 버전을 지원한다. HTTPS 응답은 Nginx에서 gzip 또는 Brotli를 사용하되 실시간 이벤트는 지연 우선으로 압축을 강제하지 않는다.

### 8.1 실행 권한 설정 API 1차 구현 계약

| ID | 요구사항 |
| --- | --- |
| API-043 | 실행 권한은 `GET /settings/execution-policy`, `POST /settings/execution-policy/drafts`, `POST /settings/execution-policy/{id}/validate`, `POST /settings/execution-policy/{id}/activate`, `GET /settings/execution-policy/history`로 관리한다. |
| API-044 | 위험 설정은 `GET /settings/risk-policy`, `POST /settings/risk-policy/drafts`, `POST /settings/risk-policy/{id}/validate`, `POST /settings/risk-policy/{id}/activate`, `GET /settings/risk-policy/history`로 관리한다. write 요청은 로그인 세션·CSRF를 요구한다. |
| API-045 | 활성 위험 설정이 없으면 조회는 `source=SAFE_DEFAULT`, `active_version_id=null`, `entry_order_amount=null`을 반환한다. 검증·활성화 응답은 version ID·sequence·state·정규화 policy·reason·시각을 반환한다. |
| API-046 | 실행 권한 활성화 요청은 CSRF와 공백이 아닌 변경 사유를 요구한다. 현재 개발 단계의 별도 TOTP 재인증 보류는 API-DEV-001~003을 따른다. |
| API-047 | 실행 권한 API는 활성 버전이 없을 때 안전 기본값과 `active_version_id=null`을 반환하며 이를 영속 활성화로 표현하지 않는다. |

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
| API-105 | 감시 종목 항목은 최신 snapshot과 같은 입력에 결합된 현재 `watch-indicators-v2` 요약을 선택적으로 포함한다. 기존 v1 행도 계산 버전을 표시해 조회할 수 있고, 지표가 아직 없으면 null이며 시세 대기와 구분한다. |

### 8.4 판단 실행·Guard·승인 API 계약

판단 상세의 실행 요약은 다음 필드를 제공한다.

`SHADOW`에서 Guard를 통과한 경우 상태는 `SHADOW_RECORDED`, 차단된 경우 `GUARD_BLOCKED`다. 공개 Mock 진단의 `execution`은 항상 `null`이다.

```json
{
  "purpose": "TRADING",
  "execution": {
    "execution_id": "01J...",
    "action": "BUY",
    "mode": "MANUAL_APPROVAL",
    "stage": "APPROVAL_ONLY",
    "state": "APPROVAL_PENDING",
    "guard_evaluation_id": "01J...",
    "approval_id": "01J...",
    "order_intent_id": null
  }
}
```

| ID | 요구사항 |
| --- | --- |
| API-110 | 판단 목록·상세는 `purpose`, execution action·mode·stage·state와 연결된 Guard·승인·주문 ID를 반환하되 진단 판단에는 execution이 null이어야 한다. |
| API-111 | 운영 HTTP API는 기존 판단을 임의로 `TRADING`으로 승격하거나 라우팅하는 endpoint를 제공하지 않는다. 거래 판단 생성·인계는 내부 scheduler와 영속 작업만 수행한다. |
| API-112 | `GET /approvals`는 기본적으로 현재 사용자의 `PENDING`을 만료시각 오름차순으로 반환하고 상태·종목·행동 필터와 안정 cursor를 지원한다. |
| API-113 | 승인 상세는 수량, 판단 기준가격·현재가격, 허용 가격범위, snapshot·position·설정 version, 만료시각, Core reason과 Guard rule 결과를 제공한다. |
| API-114 | `POST /approvals/{id}/approve`는 `Idempotency-Key`, CSRF, expected approval version과 승인 ID·version에 결합된 `APPROVE_ORDER` TOTP proof를 요구한다. |
| API-115 | 승인 요청은 최신 상태 Guard 재검사와 주문 생성까지 성공했을 때 `APPROVED`와 `order_intent_id`, `order_id`, `order_status=CREATED`를 반환한다. Broker 접수·체결 성공 문구를 반환하지 않는다. |
| API-116 | `POST /approvals/{id}/reject`는 `Idempotency-Key`, CSRF와 expected version을 요구하지만 TOTP proof는 요구하지 않는다. 성공 시 `REJECTED`만 반환한다. |
| API-117 | 만료 승인은 `410 APPROVAL_EXPIRED`, 이미 처리되거나 version이 바뀐 승인은 `409 APPROVAL_STATE_CONFLICT`를 반환하고 새 주문을 만들지 않는다. |
| API-118 | 가격범위·snapshot·position·설정·세션 변경 또는 Guard 차단은 승인을 원자적으로 `INVALIDATED`로 만들고 안정된 reason code를 반환한다. |
| API-119 | Guard 차단 응답은 evaluation ID, `BLOCKED`, rule code·severity·scope와 재시도 가능 여부를 제공하되 내부 계산 원문·전체 계좌·비밀을 포함하지 않는다. |
| API-120 | `GET /guard/evaluations/{id}`는 해당 판단·승인·주문을 볼 수 있는 사용자에게만 평가 요약을 제공한다. |
| API-121 | 상태 변경 API가 timeout되면 UI는 같은 Idempotency-Key로 재조회하며 새 승인·주문 요청을 만들지 않는다. |
| API-122 | 실행 단계 `SHADOW | APPROVAL_ONLY | MOCK_AUTOMATIC` 조회와 변경은 설정 API에서 제공하고 단계 확대는 TOTP proof·변경 사유·서버 시험 gate를 요구한다. |
| API-123 | `/system/health`는 현재 실행 단계와 `BUY` 기능 gate의 준비·차단 reason을 반환한다. |
| API-124 | `approval.requested`, `approval.updated`, `decision.execution_updated`, `risk.evaluated` 이벤트는 REST resource ID·version을 포함하고 주문 이벤트와 구분한다. |
| API-125 | `/system/health`는 scheduler의 `NOT_STARTED | RUNNING | IDLE | DEGRADED | STALE | STOPPED`, lease 유효 여부, 최근 heartbeat·tick·완료 시각, 다음 예정 시각과 최근 집계만 반환한다. owner ID와 fencing token은 반환하지 않는다. |
| API-126 | 판단 목록·상세는 `decision_input_id`, 입력 schema·hash, 연결된 indicator snapshot ID와 calculator version을 반환한다. canonical 입력 JSON과 사용자 소유권 metadata는 이 API에서 직접 반환하지 않는다. |

### 8.5 다중 에이전트·LLM Provider API 계약

상세 endpoint와 canonical payload는 [LLM Provider 및 Gateway 명세](LLM_PROVIDER_GATEWAY_SPEC.md)를 따른다.

| ID | 요구사항 |
| --- | --- |
| API-130 | `/ai/providers`, `/ai/models`, `/ai/routes` 조회는 secret 원문과 Authorization header를 반환하지 않고 profile 상태·capability·검증시각·version만 제공한다. |
| API-131 | Provider 생성·수정과 credential 교체는 세션·CSRF·expected version을 요구하며 credential 교체와 외부 endpoint 변경은 대상에 결합된 TOTP proof를 요구한다. |
| API-132 | Provider 연결 시험은 `test_id`, 단계별 상태, latency, 안전한 오류 code와 확인된 capability만 반환하고 model route를 자동 활성화하지 않는다. |
| API-133 | model discovery와 capability validation은 별도 endpoint이며 발견된 model ID를 사용자의 확인 없이 저장·활성화하지 않는다. |
| API-134 | role route는 draft·validate·activate endpoint를 분리하고 활성화에 회귀시험 ID, 변경 사유, TOTP proof와 expected version을 요구한다. |
| API-135 | `/ai/agent-runs` 목록·상세는 run·stage·입력/출력 schema·provider/model·상태·지연·비용·evidence 참조를 반환하되 raw prompt·원문 응답·credential은 제외한다. |
| API-136 | Provider·model·route mutation과 연결 시험은 `Idempotency-Key`를 지원하고 timeout 후 같은 key의 결과 조회를 허용한다. |
| API-137 | Provider health·circuit·rate/cost limit과 agent run 상태 변경은 resource ID·version을 포함한 실시간 이벤트로 전달하고 주문·체결 이벤트와 구분한다. |
| API-138 | 역할 배정 조회는 역할별 current assignment, draft, 선택 가능한 검증 model과 model 기본값·role override·최종 generation parameter를 구분해 반환한다. |
| API-139 | 역할별 draft 저장은 `expected_version`과 `Idempotency-Key`를 요구하며 같은 역할의 편집 저장이 무제한 신규 행 생성으로 보이지 않도록 현재 draft를 교체한다. |
| API-140 | 역할 배정 활성화는 대상 route·role·version에 결합된 TOTP proof를 요구하고 기존 활성 배정의 `SUPERSEDED` 전환과 새 배정의 `ACTIVE` 전환을 원자 수행한다. |
| API-141 | model capability가 지원하지 않는 generation parameter, 범위 밖 값과 중복 활성 배정은 안정된 4xx 오류로 거부하며 Adapter 호출을 수행하지 않는다. |
| API-142 | 역할 배정 일괄 활성화 preview는 선택 route map의 canonical hash를 TOTP `target_id`로 반환한다. activate 요청은 같은 map·hash에 결합된 proof를 소비하고 전 역할 변경을 한 transaction으로 처리한다. |
| API-143 | `POST /api/v1/ai/agent-runs/diagnostic`은 비동기 admission API다. 신규 run은 `CREATED`와 8개 `PENDING` stage를 반환하며 동일 멱등 입력은 기존 run을 반환한다. 내부 `EVIDENCE_CANDIDATE_AUDITOR`는 외부 invocation 없이 Scout 이후 Core 전에 실행한다. |
| API-144 | Agent run 조회 응답은 stage별 `attempt_count`, `max_attempts`, `fencing_token`, `lease_expires_at`, `timeout_at`을 포함하되 worker owner 식별자는 노출하지 않는다. |
| API-145 | role route 생성은 `failure_policy=FAIL_STOP|FAILOVER`와 선택적 `fallback_model_profile_id`를 받는다. `FAILOVER`는 기본 모델과 다른 검증 모델 하나를 요구하며 run 조회는 stage의 기본·예비 invocation을 시도 순서, 실제 모델, 상태와 안전한 오류 코드로 반환한다. |
| API-146 | Agent runtime은 역할별 JSON Schema의 `reason_codes.items`를 현재 reason code 정책 enum으로 제한하고 같은 목록과 정책 버전을 구조화 입력에 제공한다. 미등록 code는 안전한 `LLM_REASON_CODE_NOT_ALLOWED`로 조회할 수 있다. |
| API-147 | 인증된 run 소유자는 `GET /api/v1/ai/agent-runs/{run_id}/invocations/{invocation_id}/output`으로 해당 invocation의 캡처된 구조화 model output과 hash·검증 상태를 조회한다. 목록 API에는 output을 포함하지 않는다. |
| API-148 | model output이 없거나 금지·크기 초과로 폐기된 경우 output 조회는 `output_available=false`와 안전한 상태·오류만 반환하며 Provider 원문이나 prompt로 대체하지 않는다. |

Foundation v1의 기존 수동 profile API는 호환 목적으로 유지한다. 현행 Console은 Provider 등록·모델 동기화·역할별 후보 검증·5개 역할 원자 활성화 API를 사용하며 외부 모델은 `DIAGNOSTIC/SHADOW`에서만 실행한다. Provider·route 변경의 현재 인증 경계는 세션·CSRF·변경 사유이고 TOTP 추가 재인증은 API-DEV 정책에 따라 보류한다.

간편 Provider 등록 API는 `GET /ai/provider-catalog`, `POST /ai/provider-registrations/preview`, `POST /ai/provider-registrations`, `POST /ai/providers/{id}/models/sync`, `POST /ai/models/{id}/disable`을 제공한다. 등록 응답에는 credential 원문이 없고 실제 모델 목록 조회가 성공한 경우에만 Provider와 발견 모델을 반환한다.

현행 Agent Runtime v4는 `GET /ai/agent-runs`, `GET /ai/agent-runs/{id}`, `POST /ai/agent-runs/diagnostic`을 제공한다. 생성 요청은 market·symbol과 5개 필수 role의 활성 SHADOW route를 기준으로 8개 stage를 생성하고, 서버가 ENTRY/POSITION context와 position snapshot을 고정한다. 응답은 `created`로 멱등 신규·기존 반환을 구분하며 이 endpoint는 decision·execution·approval·order를 생성하지 않는다. 기존 v1~v3 run 조회는 nullable v4 field로 호환한다.
## 외부 LLM credential (Native Adapter Foundation v2)

```text
POST /api/v1/ai/providers/{provider_id}/credential-preview
POST /api/v1/ai/providers/{provider_id}/credential
```

- preview는 향후 선택적 재인증에 사용할 수 있는 `LLM_PROVIDER_CREDENTIAL_SET` 대상 ID를 반환한다.
- 현재 credential 등록은 세션·CSRF를 요구하고 1회용 TOTP proof는 API-DEV 정책에 따라 요구하지 않는다.
- 응답은 `credential_configured`만 반환하며 credential 원문과 secret ref를 반환하지 않는다.
- Provider test는 외부 생성 호출 없이 Adapter 계약과 secret 가독성만 검증한다.
# LLM Provider catalog additions (2026-08-07)

- `GET /api/v1/ai/provider-catalog` returns `template_id`, canonical `adapter_type`, registration availability, support level, and non-secret configuration fields.
- Registration preview and registration accept `template_id` and `configuration`; legacy native `adapter_type` remains accepted during migration.
- `POST /api/v1/ai/providers/{provider_id}/delete-preview` returns an auditable `LLM_PROVIDER_DELETE` target for the selected Provider.
- `DELETE /api/v1/ai/providers/{provider_id}` currently requires session and CSRF, tombstones the Provider, and returns `204`; reauthentication proof consumption is deferred by API-DEV policy.

## Prompt management API (2026-08-08)

- `GET /api/v1/ai/prompts?role=...` lists the authenticated owner's prompt versions.
- `POST /api/v1/ai/prompts` creates an immutable DRAFT for one Agent role and assigns its next server-side version number.
- `POST /api/v1/ai/prompts/{prompt_id}/validate` validates safety and moves a DRAFT to VALIDATED.
- Route creation accepts `prompt_profile_id`; the server derives `prompt_version` from the referenced profile and rejects role/state mismatches.

## 2026-08-08 개발 단계 재인증 정책

- `API-DEV-001`: 로그인 이후 현재 구현된 설정·Provider·역할 배정·MOCK 주문 시험 mutation은 세션과 CSRF를 요구하지만 request body의 `reauth_proof`를 요구하지 않는다.
- `API-DEV-002`: `/auth/reauth/totp` 기반시설은 향후 선택적 고위험 행동 재인증을 위해 유지할 수 있으나 현재 Console 흐름에서는 사용하지 않는다.
- `API-DEV-003`: API-046·096·131·134·140·142의 TOTP proof 부분은 서비스 완성 후 재도입 전까지 보류한다. 변경 사유, validation, idempotency, 원자성, 상태 gate와 감사 요구는 유지한다.
- `API-DEV-004`: Provider 삭제 후 `/ai/providers`와 `/ai/models`는 tombstone 대상을 제외하고, `/ai/routes`는 보존된 `SUPERSEDED` 이력을 계속 반환한다. `/ai/role-assignments`는 삭제된 Provider route를 현재 배정이나 후보로 반환하지 않는다.

## LLM route timeout and service tier (2026-08-10)

- `POST /api/v1/ai/routes` accepts `timeout_ms` from `1000` through `600000` and `service_tier` as `DEFAULT`, `PRIORITY`, or `FLEX`.
- `GET /api/v1/ai/routes` and role-assignment responses return both fields for every immutable route version.
- `POST /api/v1/ai/routes` accepts `web_search_enabled` with default `false`; validation permits `true` only for supported SHADOW Scout roles whose primary and fallback models declare web-search capability.
- Agent-run invocation responses expose `runtime_context_at` and `web_search_enabled` for audit without exposing prompts, credentials, or raw provider responses.
- 신규 route 요청은 120초와 `DEFAULT`를 기본값으로 사용한다. 기존 row는 설정된 timeout과 migration 당시 부여된 `DEFAULT` tier를 유지한다.

## Agent SHADOW 판단 계약 v2 API

| ID | 요구사항 |
| --- | --- |
| API-149 | `POST /api/v1/ai/agent-runs/diagnostic`은 서버가 결정한 `analysis_context`, 고정된 position snapshot reference와 현행 `dag_version=agent-dag-v6`를 응답한다. 클라이언트가 context나 position 존재 여부를 임의 지정하지 않는다. 기존 v4/v5 run은 당시 계약과 idempotency key로 조회·처리한다. |
| API-150 | run 상세는 stage의 `NOT_APPLICABLE`을 별도 상태로 반환하고 Core의 `action=WAIT`와 nullable `shadow_assessment`를 분리해 반환한다. 기존 v1~v3 응답은 기존 필드 의미를 유지한다. |
| API-151 | 목록·상세 응답은 사용한 assessment/core/score policy version을 노출하되 position 원문, prompt, credential과 Provider raw response는 노출하지 않는다. 신규 API는 판단·승인·주문 mutation을 제공하지 않는다. |

### Agent 서버 입력 provenance

| ID | 요구사항 |
| --- | --- |
| API-152 | Agent run 목록·상세 응답은 nullable `server_input_policy_version`, `market_context_snapshot_id`, `market_context_snapshot_hash`를 제공한다. position 원문 snapshot과 Risk Policy payload는 응답하지 않는다. |
| API-153 | v5 run은 `agent-server-input-v1`과 선택된 Market Context reference를 반환하고, context가 없으면 ID·hash를 null로 반환한다. null은 API 오류가 아니라 Scout 결측 입력을 뜻한다. |
| API-154 | Market Context를 외부에서 생성·수정하는 운영 HTTP endpoint는 제공하지 않는다. |

### POSITION Agent 결합 조회

| ID | 요구사항 |
| --- | --- |
| API-155 | Agent run 목록·상세는 `DIAGNOSTIC | TRADING_ADVISORY` purpose와 nullable `basis_decision_id`, `fusion_policy_version`, `fusion_state`, `fusion_reason_code`, `fusion_decision_id`를 반환한다. position 원문과 credential은 반환하지 않는다. |
| API-156 | `POST /ai/agent-runs/diagnostic`은 계속 `DIAGNOSTIC`만 생성한다. `TRADING_ADVISORY`를 생성·재실행·승격하는 공개 HTTP endpoint는 제공하지 않으며 scheduler만 내부 admission을 호출한다. |

## 거래 캘린더 운영 휴장 API

- `GET /api/v1/venue-selections/calendar-overrides`는 활성 override와 최근 해제 이력을 반환한다.
- `POST /api/v1/venue-selections/calendar-overrides`는 오늘부터 730일 이내 `market_date`, 5~200자 `reason`, 3~200자 `source_reference`를 받아 `OPERATIONAL_CLOSURE`를 생성한다.
- `DELETE /api/v1/venue-selections/calendar-overrides/{override_id}`는 행을 지우지 않고 `REVOKED`로 전이한다.
- 쓰기 요청은 CSRF를 요구하며 별도 TOTP 재인증은 요구하지 않는다. 중복 활성 날짜는 `409 CALENDAR_OVERRIDE_ALREADY_ACTIVE`, 없거나 이미 해제된 ID는 `404 CALENDAR_OVERRIDE_NOT_FOUND`다.
- 거래시장 평가 응답은 적용된 `calendar_override_id`를 반환하며 미적용 시 `null`이다.
