# Cresta Guard 리스크 및 비상정지 명세

## 1. 목적

투자 한도, 손실 제한, 손절, 데이터·연결 위험, 거래 중지와 비상정지를 정의하고 사용자가 Web UI에서 설정·검토할 수 있는 범위를 명확히 한다.

## 2. 적용 범위

- 계좌·종목·주문별 투자 한도
- 일일 손실 및 연속 손실 제한
- 고정·추적·본전·시간 손절
- 시세·WebSocket·Broker·서버 시각 이상
- 종목·진입·계좌·시스템 중지
- Web UI 설정, 변경 영향 미리보기와 감사 기록

## 3. 상세 명세

### 3.1 Web UI 설정 원칙

| ID | 요구사항 |
| --- | --- |
| GRD-001 | 이 문서의 모든 거래 리스크 정책은 Web UI에서 조회할 수 있어야 한다. |
| GRD-002 | 투자·손실·손절·데이터 위험·비상정지 정책은 Web UI에서 수정할 수 있어야 한다. |
| GRD-003 | 수정은 초안 저장, 서버 검증, 영향 미리보기와 사용자 확정 후 활성화한다. |
| GRD-004 | 변경 전·후 값, 사용자, 시각, 적용 범위, 이유와 설정 버전을 감사 로그에 기록한다. |
| GRD-005 | 종목별 설정이 사용자 기본값을 덮어쓸 수 있어야 한다. |
| GRD-006 | 현재 포지션·미체결 주문에 영향을 주는 변경은 예상 결과를 표시한다. |

### 3.2 투자 한도

초기 권장값은 모의투자 검증용이며 사용자가 Web UI에서 변경할 수 있다.

```yaml
risk_limits:
  entry_order_amount: null
  max_position_amount_per_symbol: 1_000_000
  max_total_position_amount: 3_000_000
  max_single_order_amount: 1_000_000
  max_open_positions: 3
  max_daily_entries: 5
```

첫 버전 서버 허용 범위:

| 필드 | 최소 | 최대 |
| --- | ---: | ---: |
| 1회·종목당·전체 투자금 | 10,000원 | 100,000,000원 |
| 최대 동시 보유 종목 | 1 | 3 |
| 일일 신규진입 횟수 | 1 | 20 |
| 고정 손절폭 | 0.1% | 20.0% |
| 일일 손실 한도 | 0.1% | 20.0% |
| 연속 손실 횟수 | 1 | 20 |
| 추적 손절 활성 수익률·거리 | 0.1% | 20.0% |
| 시간 손절 | 1분 | 600분 |

금액 상한은 사용자 설정 상한이며 실제 주문은 예수금·계좌·종목 한도 중 가장 작은 값으로 제한한다. 상한 변경은 코드와 명세 변경 및 회귀시험을 요구한다.

| ID | 요구사항 |
| --- | --- |
| GRD-010 | 종목당 최대 투자금, 전체 최대 투자금과 1회 최대 주문금액을 설정할 수 있어야 한다. |
| GRD-011 | 최대 동시 보유 종목과 일일 신규진입 횟수를 설정할 수 있어야 한다. |
| GRD-012 | `1회 주문금액 ≤ 종목 한도 ≤ 전체 한도` 관계를 위반하는 설정은 거부한다. |
| GRD-013 | 현재 노출이 새 한도를 초과하면 즉시 강제매도하지 않고 신규매수 차단과 경고를 기본으로 한다. |
| GRD-014 | 외부 관리 포지션도 전체 투자한도 계산에 포함한다. |
| GRD-015 | 수치 설정은 서버 허용 범위를 벗어나면 자동 보정하지 않고 거부한다. |
| GRD-016 | `entry_order_amount`는 신규진입 목표금액이며 10,000원 이상, `max_single_order_amount` 이하여야 한다. AI confidence나 Guard 한도 자체를 목표금액으로 사용하지 않는다. |
| GRD-017 | 첫 구현은 `entry_order_amount` 시스템 기본값을 제공하지 않는다. 사용자가 Web UI에서 위험 설정을 검증·활성화하기 전에는 신규매수를 `ORDER_SIZE_NOT_CONFIGURED`로 차단한다. |

### 3.3 손실 제한

```yaml
loss_limits:
  default_stop_loss_pct: -2.0
  daily_loss_limit_pct: -3.0
  daily_loss_basis: REALIZED_PLUS_UNREALIZED
  include_fees_and_taxes: true
  max_consecutive_losses: 3
  halt_release: NEXT_TRADING_DAY
```

| ID | 요구사항 |
| --- | --- |
| GRD-020 | 기본 손절률, 일일 최대 손실과 연속 손실 횟수를 Web UI에서 설정할 수 있어야 한다. |
| GRD-021 | 일일 손실 기준은 `REALIZED_ONLY`와 `REALIZED_PLUS_UNREALIZED` 중 선택할 수 있어야 한다. |
| GRD-022 | 수수료·세금 포함 여부를 설정할 수 있어야 한다. |
| GRD-023 | 일일 손실 한도 도달 시 기본적으로 계좌 전체 신규매수를 중지한다. |
| GRD-024 | 중지 해제 정책은 `MANUAL`, `NEXT_TRADING_DAY`, `SCHEDULED` 중 선택할 수 있어야 한다. |
| GRD-025 | 손실 한도를 완화하거나 중지를 해제하는 장중 변경은 재인증과 추가 확인을 요구한다. |

### 3.4 손절 정책

지원 손절 방식:

- `FIXED_STOP`: 평균 매입가 기준 고정 손절
- `TRAILING_STOP`: 활성화 이후 고점 대비 하락폭 기준
- `BREAK_EVEN_STOP`: 수익 발생 후 손절선을 매입가 이상으로 상향
- `TIME_STOP`: 제한 시간 동안 기대 움직임이 없으면 청산 판단

```yaml
stop_policy:
  fixed_stop:
    mode: AUTOMATIC
    stop_loss_pct: -2.0
  trailing_stop:
    mode: AUTOMATIC
    activation_profit_pct: 2.0
    distance_pct: 1.0
  break_even_stop:
    mode: AUTOMATIC
    activation_profit_pct: 1.5
  time_stop:
    mode: MANUAL_APPROVAL
    elapsed_minutes: 60
```

| ID | 요구사항 |
| --- | --- |
| GRD-030 | 각 손절 방식의 활성화, 실행 모드와 기준값을 종목별로 설정할 수 있어야 한다. |
| GRD-031 | 설정 변경으로 현재가가 새 손절 조건을 즉시 충족하면 적용 전에 이를 명확히 경고한다. |
| GRD-032 | 즉시 손절 가능 변경은 `현재 포지션에 즉시 적용` 또는 `다음 신규 포지션부터 적용`을 선택하게 한다. |
| GRD-033 | 손절 조건 충족 후 주문 처리는 주문 실행 명세의 손절 가격·미체결 정책을 따른다. |
| GRD-034 | 손절 기능을 모두 비활성화할 수는 있지만 위험 경고와 명시적 확인을 요구한다. |

### 3.5 데이터·연결 위험

```yaml
data_risk:
  quote_stale_seconds: 2
  websocket_disconnected_seconds: 5
  broker_unhealthy_seconds: 10
  clock_drift_limit_seconds: 1
```

서버 허용 범위는 시세 지연 1~30초, WebSocket 단절 1~60초, Broker 이상 2~120초, 서버 시각 오차 0.2~5초다.

| ID | 요구사항 |
| --- | --- |
| GRD-040 | 시세 지연, WebSocket 단절, Broker 이상과 서버 시각 오차 기준을 Web UI에서 설정할 수 있어야 한다. |
| GRD-041 | 각 기준은 시스템이 허용한 유효 범위 안에서만 변경할 수 있다. |
| GRD-042 | 시세 지연 또는 WebSocket 단절 시 기본적으로 신규매수를 차단한다. |
| GRD-043 | 연결 이상 시 기존 주문·포지션 처리 여부와 REST fallback 정책을 화면에 표시한다. |
| GRD-044 | 연결 정상화 후 재동기화가 끝나기 전 신규매수를 재개하지 않는다. |
| GRD-045 | 데이터·연결 임계값은 서버 허용 범위 밖으로 완화할 수 없다. |

### 3.6 비상정지 수준

```text
PAUSE_ENTRY
신규매수만 중지

CANCEL_OPEN_ORDERS
신규매수 중지 및 설정된 범위의 미체결 주문 취소

EMERGENCY_LIQUIDATE
신규매수 중지, 미체결 취소 및 보유 종목 청산 시도
```

```yaml
emergency_stop:
  default_action: CANCEL_OPEN_ORDERS
  require_confirmation: true
  require_reauthentication_to_release: true
  persist_across_restart: true
  preserve_exit_orders: true
```

| ID | 요구사항 |
| --- | --- |
| GRD-050 | Web UI에서 비상정지 수준을 선택하고 실행할 수 있어야 한다. |
| GRD-051 | 기본 비상정지 수준, 확인 여부, 재시작 후 유지 여부와 해제 인증 정책을 설정할 수 있어야 한다. |
| GRD-052 | 비상정지 실행 버튼과 비상정지 정책 편집 화면을 구분한다. |
| GRD-053 | `EMERGENCY_LIQUIDATE` 실행 전 예상 취소 주문과 청산 포지션을 표시한다. |
| GRD-054 | 비상정지 해제는 계좌 재동기화와 Guard 검사를 통과한 뒤 적용한다. |
| GRD-055 | 청산 주문 보존이 활성화된 경우 비상정지가 기존 손절·청산 주문을 취소하지 않는다. |

### 3.7 중지 범위

```text
SYMBOL_HALT | ENTRY_HALT | ACCOUNT_HALT | SYSTEM_HALT
```

| ID | 요구사항 |
| --- | --- |
| GRD-060 | 위험 규칙별 기본 중지 범위를 Web UI에서 설정할 수 있어야 한다. |
| GRD-061 | 특정 종목의 외부 포지션·주문 불일치는 기본적으로 `SYMBOL_HALT`를 사용한다. |
| GRD-062 | 일일 손실 한도 초과는 기본적으로 `ENTRY_HALT`를 사용한다. |
| GRD-063 | 계좌 전체 수량 불일치나 Broker 소유권 충돌은 `ACCOUNT_HALT` 이상을 사용한다. |
| GRD-064 | 중지 원인과 해제 조건을 설정 화면과 시스템 상태 화면에 표시한다. |

### 3.8 변경할 수 없는 무결성 규칙

다음 항목은 사용자 설정값이 아니라 시스템 무결성 규칙이므로 Web UI에서 비활성화할 수 없다.

- 중복 주문 방지와 idempotency
- 키움 주문번호·체결 중복 검증
- 재시작·재연결 후 재동기화
- `UNKNOWN` 주문의 즉시 재전송 금지
- 실제 계좌 수량 확인 없는 초과 매도 금지
- 비밀정보 마스킹과 권한 검사
- 모의·실거래 환경 분리
- 감사 로그 생성

| ID | 요구사항 |
| --- | --- |
| GRD-070 | Web UI는 변경 불가 안전장치의 현재 상태를 표시할 수 있지만 해제 기능은 제공하지 않는다. |
| GRD-071 | 변경 불가 규칙을 우회하는 API 요청은 서버에서 거부하고 감사한다. |

### 3.9 Guard 1차 평가 계약

Guard는 상태를 가진 주문 실행기가 아니라 같은 입력에서 같은 규칙 결과를 반환하는 결정론적 평가기다. 실행·승인 흐름과 평가 record 형식은 [판단 실행 및 승인 오케스트레이션 명세](DECISION_EXECUTION_SPEC.md)를 따른다.

첫 구현 범위:

```text
BUY
  환경·기능 단계·판단 만료·거래시간·감시종목·시세 품질
  Broker/gate/reconciliation·비상정지·halt·활성/불명 주문
  포지션·진입금액·예수금·종목/전체 노출·보유종목/일일진입 한도
  spread·가격편차

PARTIAL_SELL | FULL_SELL | FIXED_STOP
  판단/trigger 유효성·실제 포지션/version·매도가능수량
  Broker/gate/reconciliation·활성/불명 주문·거래 가능 상태
```

| ID | 요구사항 |
| --- | --- |
| GRD-080 | Guard는 `APPROVAL_CREATION`, `PRE_ORDER`, `BROKER_SEND` 단계에 필요한 검사를 구분하고 각 평가를 불변 record로 저장한다. |
| GRD-081 | `PASSED`는 blocking rule 결과가 하나도 없을 때만 가능하며 예외·timeout·알 수 없는 상태는 통과로 간주하지 않는다. |
| GRD-082 | 신규매수 검사는 현재 포지션과 미체결 매수의 예약금액, 외부 포지션, 예수금, 당일 신규진입 횟수를 같은 기준시각으로 합산한다. |
| GRD-083 | 매도·손절에는 신규매수 한도 초과를 차단 사유로 사용하지 않지만 실제 수량, 활성·불명 주문과 Broker 송신 가능성은 검사한다. |
| GRD-084 | 승인 생성 시 Guard 통과는 주문 권한이 아니며 승인 처리와 자동 주문 생성 직전에 최신 상태로 다시 평가한다. |
| GRD-085 | 고정손절 trigger는 평균단가, 활성 stop 설정 버전, 최신 정상 시세와 position version에 결합해 중복 생성하지 않는다. trigger 후 주문 불가 상태에서도 `EXIT_PENDING` 위험을 유지한다. |
| GRD-086 | Guard 결과에는 안정된 reason code, severity, halt scope, 사용한 snapshot·position·실행권한·위험설정 version과 유효시간을 기록한다. |
| GRD-087 | 첫 구현 미지원 trigger와 행동은 `ACTION_NOT_IMPLEMENTED`로 차단하며 다른 청산·보유 행동으로 자동 변환하지 않는다. |
| GRD-088 | 자동 또는 승인형 `BUY` 기능 gate는 신규진입 Guard, 고정손절 trigger, `PAUSE_ENTRY` 비상정지와 관련 장애시험이 모두 준비되기 전 열지 않는다. |

## 4. 오류·예외 또는 경계 조건

- Web UI가 잘못된 값을 보내도 서버가 동일한 검증을 다시 수행한다.
- 한도 축소로 현재 포지션이 초과 상태가 되어도 사용자가 별도로 선택하지 않은 강제매도를 생성하지 않는다.
- 손절 완화와 비상정지 해제는 연결·계좌 상태가 정상이어도 재인증 없이 적용하지 않는다.
- 설정 저장과 실제 활성화 사이에 상태가 바뀌면 영향 미리보기를 다시 계산한다.
- 두 브라우저가 같은 설정을 동시에 수정하면 설정 버전 충돌을 반환한다.

## 5. 검증·인수 조건

- 모든 리스크 정책을 Web UI에서 조회·수정할 수 있다.
- 잘못된 한도 관계와 유효 범위 밖의 값이 서버에서 거부된다.
- 설정 변경 전 현재 포지션·주문 영향이 표시된다.
- 손절 조건을 즉시 충족시키는 변경이 경고 없이 활성화되지 않는다.
- 장중 위험 완화와 비상정지 해제에 재인증이 요구된다.
- 변경 불가 안전장치는 UI와 API 양쪽에서 해제할 수 없다.
- 모든 변경과 비상정지 실행·해제가 감사 로그에 남는다.

## 6. 미결정·보류 항목

- 모의매매 결과에 따른 권장 기본값 조정 여부
- 실거래 단계에서 금액 상한을 더 낮출지 여부
