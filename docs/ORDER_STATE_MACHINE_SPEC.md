# 주문 상태 머신 및 키움 매핑 명세

## 1. 목적

Cresta 내부 주문 상태와 키움 REST·WebSocket·조회 결과의 매핑을 정의한다. 응답 유실, 부분체결과 취소·정정 경쟁 상황에서도 중복 주문과 잘못된 포지션 종료를 방지하는 것이 목적이다.

## 2. 적용 범위

- 키움 국내주식 매수·매도·정정·취소 주문
- 실시간 주문체결 이벤트
- 미체결·체결·계좌 조회를 통한 재동기화
- Cresta 주문 의도, 개별 증권사 주문과 체결 상태

키움 실시간 이벤트의 정확한 필드명과 값은 모의투자 캡처 전까지 잠정 매핑으로 취급한다.

## 3. 상세 명세

### 3.1 승인과 주문 상태 분리

승인 상태는 다음 값만 사용한다.

```text
PENDING | APPROVED | REJECTED | EXPIRED | INVALIDATED
```

주문 상태는 다음 값만 사용한다.

```text
CREATED | VALIDATING | SUBMITTING | ACKNOWLEDGED | OPEN
PARTIALLY_FILLED | FILLED
CANCEL_PENDING | CANCELLED
REPLACE_PENDING | REPLACED
REJECTED | UNKNOWN | RECONCILING
```

| ID | 요구사항 |
| --- | --- |
| STM-001 | 사용자 승인 완료를 주문 접수 또는 체결로 간주하지 않는다. |
| STM-002 | Guard 재검사는 승인 후 주문 생성 직전에 다시 수행한다. |
| STM-003 | 키움 REST 성공 응답은 체결 완료가 아니라 주문 접수 확인으로 처리한다. |

### 3.2 정상 상태 전이

```text
CREATED
→ VALIDATING
→ SUBMITTING
→ ACKNOWLEDGED
→ OPEN
→ PARTIALLY_FILLED
→ FILLED
```

- `CREATED`: 내부 주문 의도와 멱등성 키 생성
- `VALIDATING`: Guard가 잔고, 한도, 가격, 시세와 중복 주문 검사
- `SUBMITTING`: 키움 요청 전송을 시작했으나 결과가 확정되지 않음
- `ACKNOWLEDGED`: 키움 주문번호 확인
- `OPEN`: 체결 없이 미체결 잔량 존재
- `PARTIALLY_FILLED`: 누적 체결과 미체결 잔량이 모두 존재
- `FILLED`: 주문수량 전부 체결

### 3.3 취소와 정정 상태 전이

```text
OPEN 또는 PARTIALLY_FILLED
→ CANCEL_PENDING
→ CANCELLED

OPEN 또는 PARTIALLY_FILLED
→ REPLACE_PENDING
→ REPLACED
```

| ID | 요구사항 |
| --- | --- |
| STM-010 | 취소·정정 요청 중 발생한 추가 체결을 먼저 반영한다. |
| STM-011 | 취소 전에 전량 체결되면 `CANCEL_PENDING`에서 `FILLED`로 전이할 수 있다. |
| STM-012 | 정정 전 원주문과 정정 후 주문을 별도 레코드로 보존하고 부모 관계로 연결한다. |
| STM-013 | `CANCELLED`는 체결수량이 0임을 의미하지 않으며 취소된 잔량이 있음을 의미한다. |

### 3.4 불명확한 결과

```text
SUBMITTING 또는 CANCEL_PENDING 또는 REPLACE_PENDING
→ UNKNOWN
→ RECONCILING
→ 실제 확인된 상태
```

| ID | 요구사항 |
| --- | --- |
| STM-020 | 네트워크 시간초과와 응답 유실은 `REJECTED`로 처리하지 않는다. |
| STM-021 | `UNKNOWN` 상태에서는 같은 매매 의도를 다시 전송하지 않는다. |
| STM-022 | `UNKNOWN` 주문이 있는 종목은 추가 주문을 잠그고 키움 조회로 대조한다. |
| STM-023 | 재동기화가 끝나지 않으면 `RECONCILING`을 유지하고 사용자에게 경보한다. |

### 3.5 키움 API 매핑

현재 공식 API 기준 연동 대상은 다음과 같다.

| 목적 | 키움 API/실시간 유형 | Cresta 사용 |
| --- | --- | --- |
| 주식 매수 | `kt10000` | 주문 제출 |
| 주식 매도 | `kt10001` | 주문 제출 |
| 주식 정정 | `kt10002` | 잔량 정정 |
| 주식 취소 | `kt10003` | 잔량 취소 |
| 미체결 조회 | `ka10075` | OPEN·PARTIALLY_FILLED 대조 |
| 체결 조회 | `ka10076` | 체결 누락 복구 |
| 계좌별 주문체결 상세 | `kt00007` | 재동기화 스냅샷 |
| 계좌별 주문체결 현황 | `kt00009` | 재동기화 스냅샷 |
| 실시간 주문체결 | `00` | 주문·체결 이벤트 우선 반영 |
| 실시간 잔고 | `04` | 포지션 변화 보조 확인 |

참고 자료: <https://openapi.kiwoom.com/m/guide/apiguide>

키움 이벤트는 다음 정규화 필드로 변환한다.

```yaml
normalized_broker_event:
  broker_order_id:
  original_broker_order_id:
  symbol:
  side:
  market:
  event_type:
  order_quantity:
  event_fill_quantity:
  cumulative_fill_quantity:
  remaining_quantity:
  cancelled_quantity:
  order_price:
  fill_price:
  broker_event_at:
  rejection_code:
  raw_event_hash:
```

잠정 매핑 규칙:

| 키움에서 확인한 사실 | Cresta 상태 |
| --- | --- |
| 정상 응답과 키움 주문번호 존재 | `ACKNOWLEDGED` |
| 누적 체결 0, 미체결 잔량 존재 | `OPEN` |
| 누적 체결 > 0, 미체결 잔량 > 0 | `PARTIALLY_FILLED` |
| 누적 체결 = 주문수량 | `FILLED` |
| 잔량 취소 확인 | `CANCELLED` |
| 정정 주문 관계 확인 | 원주문 `REPLACED`, 새 주문 추적 |
| 명확한 API·업무 거부 | `REJECTED` |
| 결과를 판별할 수 없음 | `UNKNOWN` |

### 3.6 주문 의도와 개별 주문

하나의 사용자 의도는 여러 키움 주문으로 구성될 수 있다.

```text
Order Intent / Order Group
├─ 최초 주문
├─ 정정 주문
└─ 잔량 재주문
```

필수 관계 필드:

```yaml
order_group_id:
client_order_id:
parent_order_id:
broker_order_id:
original_broker_order_id:
replacement_sequence:
idempotency_key:
```

### 3.7 수량 불변조건

```text
requested_quantity
= filled_quantity + cancelled_quantity + remaining_quantity
```

| ID | 요구사항 |
| --- | --- |
| STM-030 | 모든 수량은 0 이상이어야 한다. |
| STM-031 | `FILLED`이면 `remaining_quantity`는 0이다. |
| STM-032 | `OPEN`이면 `remaining_quantity`가 0보다 크다. |
| STM-033 | `PARTIALLY_FILLED`이면 체결수량과 잔량이 모두 0보다 크다. |
| STM-034 | 동일 체결 이벤트를 두 번 반영하지 않는다. |
| STM-035 | 체결 저장과 주문 누적수량·포지션 갱신은 원자적으로 처리한다. |

### 3.8 상태 변경의 진실 공급원

우선순위는 다음과 같다.

```text
키움에서 확인된 실제 체결
→ 키움 미체결·주문 조회 스냅샷
→ 키움 실시간 주문 접수·취소·정정 이벤트
→ REST 제출 응답
→ Cresta 내부 예상 상태
```

WebSocket은 빠른 반영 경로이며 단독 진실 공급원이 아니다. 재시작, 재연결, 응답 시간초과와 계좌 불일치 때는 REST 조회와 실제 잔고로 대조한다.

구체적인 스냅샷·버퍼·불일치 처리와 거래 게이트는 [계좌·주문 재동기화 명세](RECONCILIATION_SPEC.md)를 따른다.

## 4. 오류·예외 또는 경계 조건

- 취소 요청과 전량 체결이 경쟁하면 체결을 우선하고 취소 실패를 시스템 장애로 보지 않는다.
- 늦게 도착한 체결은 종료 상태 주문에도 추가될 수 있으며 포지션을 다시 계산한다.
- 키움 주문번호가 없는 성공 응답은 `ACKNOWLEDGED`로 승격하지 않는다.
- 조회에서 주문이 즉시 발견되지 않아도 전송 실패로 단정하지 않고 반영 지연을 고려한다.
- 외부 키움 앱 주문은 Cresta 주문과 구분해 수동 외부 주문으로 가져오거나 재동기화 예외로 표시한다.
- 키움 모의투자는 공식 문서상 KRX만 지원하므로 NXT/SOR 매핑은 실거래 전 별도 검증한다.

## 5. 검증·인수 조건

- 정상 접수, 전량체결, 부분체결, 취소와 정정의 모든 전이를 재현한다.
- 취소 요청 직후 추가 체결되어도 수량 불변조건이 유지된다.
- 주문 제출 응답 유실 시 중복 주문이 발생하지 않는다.
- WebSocket 이벤트 누락 후 조회 API로 상태와 포지션을 복원한다.
- 동일 이벤트 재수신 시 체결과 포지션이 중복 반영되지 않는다.
- 재시작 후 키움 주문·체결·잔고와 내부 상태가 일치하기 전 신규매수가 차단된다.
- 모의투자에서 캡처한 실시간 이벤트로 잠정 필드 매핑을 계약 테스트로 고정한다.

## 6. 미결정·보류 항목

- 키움 모의투자 `주문체결(00)`의 실제 필드명·코드와 이벤트 순서
- 정정주문 시 키움 주문번호가 교체되는 정확한 규칙
- 체결 고유번호의 제공 및 안정성 여부
- 주문 조회 반영 지연을 고려한 재동기화 대기·재시도 시간
