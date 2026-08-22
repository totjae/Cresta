# 주문 가격 및 미체결 재처리 명세

## 1. 목적

AI가 임의의 주문 가격을 생성하지 않도록 가격 산정 방식을 제한하고, 미체결·부분체결·취소·재주문을 중복 주문 없이 처리하는 규칙을 정의한다.

## 2. 적용 범위

- 신규매수, 부분매도, 일반 전량매도
- 목표수익 매도, 고정·추적 손절과 긴급청산
- 장 마감 청산
- 지정가, 시장성 지정가, 시장가와 사용자 고정 지정가
- 미체결·부분체결 재처리

## 3. 상세 명세

### 3.1 가격 정책

- `PASSIVE_LIMIT`: 동일 방향 최우선 호가 또는 전략 목표가격에 대기
- `MARKETABLE_LIMIT`: 반대편 최우선 호가를 기준으로 즉시 체결 가능한 지정가 제출
- `MARKET`: 시장가 제출
- `FIXED_LIMIT`: 사용자가 직접 지정한 유효 호가단위 가격
- `DISABLED`: 해당 가격 정책 사용 금지

| ID | 요구사항 |
| --- | --- |
| ORD-001 | AI 출력은 자유 주문 가격을 포함하지 않고 제한된 행동 코드만 포함한다. |
| ORD-002 | 실제 주문 가격은 Broker가 최신 호가와 설정된 가격 정책으로 계산한다. |
| ORD-003 | 모든 계산 가격은 해당 종목의 유효 호가단위로 보정한다. |
| ORD-004 | 신규매수는 최대 허용 가격편차를 넘으면 체결을 포기하고 재판단한다. |
| ORD-005 | 손절·긴급청산은 신규매수보다 체결 우선 정책을 사용할 수 있다. |

호가단위 보정은 키움에서 조회한 해당 종목·시장·시점의 유효 호가단위 표를 사용한다. 이미 수신한 최우선 호가는 유효 가격으로 간주하되 백분율로 계산한 경계 가격은 다음처럼 보수적으로 보정한다.

- 매수 최대가격은 다음 낮은 유효 호가로 내림해 승인·한도를 넘지 않는다.
- 매도 최소가격은 다음 높은 유효 호가로 올림해 승인·한도를 넘지 않는다.
- 즉시 체결용 매수는 유효 매도 1호가, 즉시 체결용 매도는 유효 매수 1호가를 기본값으로 사용한다.
- 가격제한폭을 벗어나거나 호가단위 표를 확인할 수 없으면 주문을 만들지 않는다.

| ID | 요구사항 |
| --- | --- |
| ORD-006 | 호가단위 표에는 적용 거래일·시장·버전을 저장하고 주문 감사 기록에서 참조한다. |
| ORD-007 | 경계 가격 보정이 사용자 승인 범위를 넓히는 방향으로 이루어져서는 안 된다. |

권장 기본값:

| 행동 | 가격 정책 | 최종 처리 |
| --- | --- | --- |
| 신규매수 | `MARKETABLE_LIMIT` | 미체결 취소 |
| 부분·일반 매도 | `MARKETABLE_LIMIT` | 승인 범위 내 재호가 |
| 목표수익 매도 | `PASSIVE_LIMIT` | 목표가 대기 |
| 고정손절 | `MARKETABLE_LIMIT` | 제한 횟수 후 시장가 |
| 긴급청산 | `MARKET` | 체결 확인까지 추적 |
| 장 마감 청산 | `MARKETABLE_LIMIT` | 단계적 재호가 |

### 3.2 승인 범위

```yaml
approval_scope:
  valid_seconds: 30
  max_price_deviation_pct: 0.30
  allow_cancel_replace: true
  max_reprice_attempts: 1
```

| ID | 요구사항 |
| --- | --- |
| ORD-010 | 승인에는 불변 reference snapshot ID, 기준가격, 정확한 수량, 가격 상·하한, 유효시간과 허용 재호가 횟수를 기록한다. |
| ORD-011 | 승인 범위를 벗어난 가격으로 주문하지 않고 새 판단 또는 승인을 요청한다. |
| ORD-012 | 최신 시세 지연·품질 저하, 세션 변경, 거래정지, 한도 변경 시 기존 승인을 무효화한다. 정상적인 stream snapshot 갱신과 ID 변경만으로는 무효화하지 않는다. |
| ORD-013 | 자동 주문도 결정 시점의 가격 기준과 허용편차를 감사 로그에 남긴다. |
| ORD-014 | 승인 주문가격은 승인 transaction이 잠근 최신 정상 stream snapshot에서 계산하고 reference 가격과 편차를 검사한다. 허용 범위 안에서도 승인 수량을 새 가격에 맞춰 늘리지 않는다. |

### 3.3 미체결 정책

```yaml
order_policy:
  buy:
    price_policy: MARKETABLE_LIMIT
    fill_timeout_seconds: 10
    max_reprice_attempts: 1
    final_fallback: CANCEL
    partial_fill_policy: KEEP_PARTIAL

  normal_sell:
    price_policy: MARKETABLE_LIMIT
    fill_timeout_seconds: 10
    max_reprice_attempts: 2
    final_fallback: MANUAL_REVIEW

  stop_loss:
    price_policy: MARKETABLE_LIMIT
    fill_timeout_seconds: 2
    max_reprice_attempts: 3
    final_fallback: MARKET

  emergency_exit:
    price_policy: MARKET
    max_reprice_attempts: 0
```

| ID | 요구사항 |
| --- | --- |
| ORD-020 | 신규매수는 가격 추격보다 진입 포기를 우선하며 기본적으로 시장가 전환하지 않는다. |
| ORD-021 | 부분체결 후 잔량만 취소·정정·재주문한다. |
| ORD-022 | 기본 부분매수 정책은 체결 수량을 포지션으로 인정하고 잔량을 취소하는 `KEEP_PARTIAL`이다. |
| ORD-023 | 손절 잔량은 포지션이 0이 될 때까지 손절 상태를 유지한다. |
| ORD-024 | 재호가 횟수와 허용 가격 범위를 넘으면 자동 처리를 중단하고 정책별 최종 처리를 실행한다. |

첫 버전은 종목 유동성별 자동 프로필을 사용하지 않고 위의 단일 기본 timeout·재호가 값을 사용한다. 손절 재호가도 주문 전 Guard의 `max_price_deviation_pct` 안에서만 수행하며 범위를 벗어나면 마지막 fallback인 시장가 전환 전에 현재 호가·상하한가·거래상태를 다시 검사하고 경고 이벤트를 남긴다.

### 3.4 취소·재주문

재주문은 다음 순서를 지켜야 한다.

```text
기존 주문 취소 요청
→ 취소 또는 추가 체결 확인
→ 실제 잔량 조회
→ 새 client_order_id와 idempotency_key 생성
→ 잔량만 재주문
```

| ID | 요구사항 |
| --- | --- |
| ORD-030 | 취소 확인 전에 대체 주문을 보내지 않는다. |
| ORD-031 | 개별 키움 주문은 새로운 `client_order_id`를 사용하고 하나의 `order_group_id`로 원래 의도에 연결한다. |
| ORD-032 | 응답이 불명확한 주문은 재전송하지 않고 먼저 재동기화한다. |
| ORD-033 | 재주문 제한 초과 시 자동 처리를 중단하고 사용자에게 알린다. |
| ORD-034 | 키움 주문 송신은 내부 주문을 `SUBMITTING`으로 commit한 뒤 정확히 한 번만 수행하며, 같은 주문의 후속 호출은 저장된 상태만 반환한다. |
| ORD-035 | 숫자 7자리 주문번호가 확인된 성공 응답만 `ACKNOWLEDGED`, 명시적 업무 거절만 `REJECTED`, HTTP·전송·응답 형식이 불명확한 결과는 `UNKNOWN`으로 분류한다. |
| ORD-036 | 첫 키움 송신 단계는 worker 내부 서비스로만 제공하며 공개 주문 생성 경로와 실제 모의주문 통합시험은 별도 승인 전까지 활성화하지 않는다. |
| ORD-037 | worker polling은 키움 MOCK 계좌의 `CREATED` 주문만 생성시각 순으로 한 건씩 처리하고, 다른 계좌·환경·상태 주문은 선택하지 않는다. |
| ORD-038 | `UNKNOWN` 발생 cycle에서는 다음 주문을 처리하지 않고 즉시 전체 계좌 재동기화로 전환한다. |
| ORD-044 | 첫 미체결 자동처리 단계는 신규매수 `BUY`에만 적용한다. Broker 접수 후 10초가 지나도 잔량이 있으면 해당 잔량 전부의 취소를 한 번 요청하며 시장가 전환이나 재호가를 하지 않는다. |
| ORD-045 | 자동 취소 전 주문 row를 잠그고 `CANCEL_PENDING`을 commit한 뒤 `kt10003`을 정확히 한 번 호출한다. 취소 요청 결과가 불명확하면 주문을 `UNKNOWN`으로 전환하고 거래 gate를 `RECONCILING/ORDER_CANCEL_OUTCOME_UNKNOWN`으로 닫는다. |
| ORD-046 | 명시적 취소 업무 거절은 원주문의 체결·잔량을 변경하지 않는다. 주문은 Broker 대조가 필요한 `RECONCILING`으로 전환하고 다음 주문 송신을 중지한다. |
| ORD-047 | 이 단계의 호가단위 검사는 KRX가 공표한 시장별 주식 호가표 version과 상품 구분을 명시적으로 받은 계산 가격에만 적용한다. Broker 최우선 호가에서 직접 가져온 가격은 수신 Adapter가 유효 호가로 보장하며, 시장·상품 구분 없이 가격만 보고 임의 보정하지 않는다. |
| ORD-048 | 판단 기반 `PARTIAL_SELL`·`FULL_SELL` 1차는 최신 Broker 최우선 매수호가를 그대로 사용하는 LIMIT 주문만 만든다. 미체결 재호가·시장가 fallback은 별도 정책과 장중 시험 전까지 `NONE`으로 유지한다. |
| ORD-049 | 키움이 HTTP 200과 `return_code != 0`으로 주문 또는 취소를 명시적으로 거절하면 Adapter는 canonical 오류 `KIWOOM_ORDER_REJECTED`와 함께 정규화한 `broker_result_code`, 안전하게 정제한 `broker_result_message`를 반환한다. Broker 응답 원문 전체는 저장하지 않는다. |
| ORD-050 | 결과 메시지는 제어문자와 연속 공백을 정규화하고 200자로 제한하며 Bearer token, app key·secret·authorization·token 값과 8~12자리 연속 숫자를 제거한다. 정제 후 비면 사용자 화면에는 일반 거절 문구만 표시한다. |
| ORD-051 | 명시적 신규주문 거절은 `ORDER_REJECTED`, 명시적 취소 거절은 `ORDER_CANCEL_REJECTED` 이벤트 payload에 정제된 두 필드만 기록한다. 전송 실패·timeout·5xx·파싱 실패에는 Broker 결과를 추정해 기록하지 않는다. |

### 3.5 주문 전 안전검사

초기 권장값이며 모의투자 결과로 조정한다.

```yaml
order_guard:
  max_spread_pct: 0.30
  max_price_deviation_pct: 0.50
  max_quote_age_seconds: 2
  max_orderbook_consumption_pct: 20
  require_tradable_status: true
```

주문시장과 참조 호가시장은 일치해야 한다. SOR 사용 시 실제 주문시장과 체결시장을 함께 저장한다.

주문시장은 사용자 종목 설정이 아니라 [거래시장 자동 선택 명세](VENUE_SELECTION_SPEC.md)의 평가 결과로 정한다. KRX/NXT 양 시장 운영시간에는 Broker SOR 또는 서버가 영속화한 양 시장 호가 비교를 사용하며, 선택 결과가 없거나 stale이면 주문을 만들지 않는다.

| ID | 요구사항 |
| --- | --- |
| ORD-039 | 주문 전 안전검사는 [판단 실행 및 승인 오케스트레이션 명세](DECISION_EXECUTION_SPEC.md)의 불변 Guard evaluation을 생성하고 주문이 해당 evaluation ID를 참조하게 한다. |
| ORD-040 | 자동 실행은 `PRE_ORDER`, 승인 실행은 `APPROVAL_CREATION`과 승인 시 새 `PRE_ORDER` 평가를 통과해야 한다. 이전 평가를 최신 검사로 재사용하지 않는다. |
| ORD-041 | Guard 통과, 주문 의도, 첫 `CREATED` 주문과 감사 로그는 하나의 transaction에서 생성하며 중간 상태를 Broker polling에 노출하지 않는다. |
| ORD-042 | `BUY` 수량은 활성 `entry_order_amount`를 기준가격으로 나눈 정수 주식 수이며 AI confidence·한도 최대값·브라우저 입력으로 확대하지 않는다. |
| ORD-043 | 매도 수량은 실제 포지션에서 활성 매도 주문 예약수량을 뺀 값을 넘을 수 없고 position version 변경 시 기존 승인을 무효화한다. |

## 4. 오류·예외 또는 경계 조건

- 시장가와 시장성 지정가는 목표 손실률 또는 표시가격 체결을 보장하지 않는다.
- 취소 처리 중 추가 체결될 수 있으므로 취소 요청 시점의 잔량을 확정 잔량으로 간주하지 않는다.
- 데이터가 오래됐거나 호가가 비정상인 경우 신규매수는 차단한다.
- 거래정지·VI·하한가에서는 손절 주문이 체결되지 않을 수 있으며 `EXIT_PENDING` 경보를 유지한다.
- 주문 응답 시간초과는 `REJECTED`가 아니라 `UNKNOWN`으로 처리한다.

## 5. 검증·인수 조건

- 신규매수가 가격편차를 넘으면 자동 취소되고 시장가로 전환되지 않는다.
- 부분체결 후 재주문 수량이 실제 잔량과 일치한다.
- 취소와 체결이 경쟁하는 상황에서 초과 매수·매도가 발생하지 않는다.
- 손절 미체결 잔량이 사라질 때까지 포지션을 `CLOSED`로 처리하지 않는다.
- 같은 idempotency key로 주문 레코드가 중복 생성되지 않는다.
- 오래된 호가나 허용 스프레드 초과 시 신규매수가 차단된다.

## 6. 미결정·보류 항목

- 키움 SOR 사용 시 주문 가격 정책과 체결시장 기록 방식
