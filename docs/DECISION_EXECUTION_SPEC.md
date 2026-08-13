# 판단 실행 및 승인 오케스트레이션 명세

## 1. 목적

검증된 Core 판단 또는 Guard 규칙 신호를 행동별 실행 권한, Guard 검사, 사용자 승인과 주문 상태 머신에 안전하게 연결한다. AI 판단 자체에는 주문 권한을 부여하지 않으며 동일 판단·승인·재시도에서 중복 주문이 생기지 않는 영속 경계를 정의한다.

## 2. 적용 범위

- 거래 목적 판단과 진단 목적 판단의 분리
- Core·Guard 신호의 실행 행동 정규화
- `AUTOMATIC`, `MANUAL_APPROVAL`, `DISABLED` 분기
- 승인 생성·만료·거절·무효화·승인 후 재검사
- Guard 평가와 주문 의도·주문 생성 트랜잭션
- 멱등성, 동시성, 감사와 장애 복구

첫 구현은 `MOCK` 환경의 `BUY`, `PARTIAL_SELL`, `FULL_SELL`, `FIXED_STOP`만 실행 대상으로 한다. `TAKE_PROFIT`, `TRAILING_STOP`, `END_OF_DAY_LIQUIDATION`, `EMERGENCY_EXIT`은 각 trigger와 운영 시험이 구현되기 전 `ACTION_NOT_IMPLEMENTED`로 안전 차단한다. 진단용 Mock HTTP API는 계속 주문과 승인을 만들지 않는다.

## 3. 상세 명세

### 3.1 책임과 입력 경계

```text
불변 market snapshot
→ Scout/Core 또는 Guard 실시간 trigger
→ execution action 정규화
→ 활성 실행 권한 조회
→ Guard 평가
→ 승인 또는 주문 생성
→ 기존 Broker worker가 CREATED 주문 처리
```

| ID | 요구사항 |
| --- | --- |
| EXE-001 | 오케스트레이터는 모델을 호출하거나 Broker API를 직접 호출하지 않고, 검증된 판단·규칙 신호를 승인 또는 영속 주문으로 변환한다. |
| EXE-002 | 판단은 `purpose=DIAGNOSTIC | TRADING`을 가지며 `TRADING`만 실행 라우팅 대상이다. 공개 `/decisions/mock-evaluate`가 만든 `DIAGNOSTIC` 판단은 어떤 설정에서도 승인·주문을 생성하지 않는다. |
| EXE-003 | 입력에는 decision 또는 rule trigger ID, 사용자, 계좌 alias, symbol·market, snapshot, position, 실행 권한·위험·전략 설정 버전과 correlation ID가 필요하다. |
| EXE-004 | 입력 snapshot·판단·설정 중 하나가 없거나 만료·상위 schema·비활성 버전이면 `FAILED_SAFE`로 종료하고 주문을 만들지 않는다. |
| EXE-005 | 실행 권한과 Guard는 같은 사용자·계좌·종목에 대해 서버가 다시 조회하며 호출자가 전달한 mode·한도·Guard 결과를 신뢰하지 않는다. |

### 3.2 행동 정규화

| 입력 | 정규 실행 행동 | 처리 |
| --- | --- | --- |
| Core `BUY` | `BUY` | 실행 권한 분기 대상 |
| Core `PARTIAL_SELL` | `PARTIAL_SELL` | `sell_ratio`로 매도 가능 수량 계산 |
| Core `FULL_SELL` | `FULL_SELL` | 매도 가능 잔량 전부 |
| Core `EMERGENCY_EXIT` | `EMERGENCY_EXIT` | 첫 구현은 안전 차단 |
| Core `WAIT`, `REJECT`, `RISK_BLOCK`, `HOLD` | 없음 | `NO_ACTION` 기록 |
| Core `TIGHTEN_STOP` | 없음 | 첫 구현은 `DEFERRED_NOT_IMPLEMENTED`; 주문으로 변환 금지 |
| 목표수익 규칙 | `TAKE_PROFIT` | 첫 구현은 안전 차단 |
| 고정손절 규칙 | `FIXED_STOP` | AI 없이 실행 권한 분기 대상 |
| 추적손절 규칙 | `TRAILING_STOP` | 첫 구현은 안전 차단 |
| 장 마감 규칙 | `END_OF_DAY_LIQUIDATION` | 첫 구현은 안전 차단 |
| 비상청산 명령 | `EMERGENCY_EXIT` | 첫 구현은 안전 차단 |

| ID | 요구사항 |
| --- | --- |
| EXE-010 | 허용된 표 이외의 입력·행동 조합은 주문으로 묵시 변환하지 않는다. |
| EXE-011 | `PARTIAL_SELL` 수량은 `floor(매도가능수량 × sell_ratio)`이며 1주 미만이면 주문을 만들지 않고 `QUANTITY_BELOW_ONE`을 기록한다. 전량매도로 자동 승격하지 않는다. |
| EXE-012 | `FULL_SELL`과 `FIXED_STOP`은 보유수량에서 이미 미체결 매도 주문에 예약된 수량을 뺀 매도가능수량만 사용한다. |
| EXE-013 | `BUY` 수량은 AI confidence가 아니라 활성 전략의 `entry_order_amount`와 Guard 기준가격으로 계산한다. 설정이 없으면 `ORDER_SIZE_NOT_CONFIGURED`, 계산 결과가 1주 미만이면 `QUANTITY_BELOW_ONE`으로 차단한다. |
| EXE-014 | 첫 구현 미지원 행동은 실행 권한이 `AUTOMATIC`이어도 `ACTION_NOT_IMPLEMENTED`이며 다른 행동으로 대체하지 않는다. |

### 3.3 실행 상태와 멱등성

판단 실행 상태는 다음 값만 사용한다.

```text
ROUTING
→ NO_ACTION | DISABLED | GUARD_BLOCKED | SHADOW_RECORDED
→ APPROVAL_PENDING → EXPIRED | REJECTED | INVALIDATED | ORDER_CREATED
→ ORDER_CREATED
→ FAILED_SAFE
```

`ORDER_CREATED`는 내부 `CREATED` 주문이 영속됐다는 뜻이며 Broker 접수·체결을 뜻하지 않는다.

| ID | 요구사항 |
| --- | --- |
| EXE-020 | `decision_id + execution_action + execution_policy_version_id`로 안정된 execution key를 만들고 DB unique 제약으로 실행 레코드를 하나만 허용한다. |
| EXE-021 | 같은 실행 요청의 재처리는 기존 실행·승인·주문 식별자를 반환하며 새 리소스를 만들지 않는다. |
| EXE-022 | 실행 레코드 상태는 기대 version 조건부 갱신으로 한 번만 전이하고 terminal 상태를 되살리지 않는다. |
| EXE-023 | 판단 실행, Guard 평가, 승인 또는 주문 의도 생성과 감사 로그는 정의된 단일 DB 트랜잭션으로 commit한다. commit 결과가 불명확하면 재호출에서 execution key로 결과를 조회한다. |
| EXE-024 | 판단 원문과 구조화 출력은 불변으로 유지하고 후속 상태는 별도 `decision_executions`에 저장한다. |
| EXE-025 | `SHADOW` 단계에서 Guard를 통과한 실행은 `SHADOW_RECORDED`로 종료한다. 이 상태에서는 승인과 주문을 생성하지 않으며, Guard가 차단하면 `GUARD_BLOCKED`를 사용한다. |

### 3.4 실행 권한 분기

| mode | 결과 |
| --- | --- |
| `DISABLED` | `DISABLED`; Guard·승인·주문 없음 |
| `MANUAL_APPROVAL` | 승인 생성 전 Guard 통과 시 `PENDING` 승인 하나 생성 |
| `AUTOMATIC` | 주문 직전 Guard 통과 시 주문 의도와 `CREATED` 주문 생성 |

| ID | 요구사항 |
| --- | --- |
| EXE-030 | 활성 실행 권한 버전이 없으면 제품 안전 기본값을 사용하되 출처와 `policy_version_id=null`을 기록한다. |
| EXE-031 | `DISABLED`는 위험 신호를 숨기지 않지만 승인·주문을 만들지 않는다. 고정손절 비활성 상태는 별도 위험 경고를 유지한다. |
| EXE-032 | `MANUAL_APPROVAL`은 승인 생성 시점 Guard가 차단하면 승인 자체를 만들지 않고 `GUARD_BLOCKED`로 종료한다. |
| EXE-033 | `AUTOMATIC`은 주문 의도와 주문을 만들기 직전 같은 트랜잭션에서 Guard를 통과해야 한다. |
| EXE-034 | 자동매매 활성화에 사용한 TOTP는 개별 자동 주문에 재사용하지 않는다. 자동 주문은 활성 설정 버전과 해당 활성화 감사 기록을 참조한다. |
| EXE-035 | `BUY`의 자동·승인 실행은 최소한 신규진입 Guard, 고정손절 trigger와 `PAUSE_ENTRY` 비상정지가 구현·활성 상태일 때만 기능 gate를 열 수 있다. |

### 3.5 승인 생명주기

승인 상태는 `PENDING | APPROVED | REJECTED | EXPIRED | INVALIDATED`만 사용한다. 기본 유효시간은 30초다.

| ID | 요구사항 |
| --- | --- |
| EXE-040 | 승인은 execution, 판단과 그 불변 reference snapshot, 행동, 정확한 수량, 기준가격·허용범위, 설정 버전, Guard 평가와 만료시각에 결합한다. reference snapshot과 기준가격·수량은 승인 대기 중 새 시세가 들어와도 바꾸지 않는다. |
| EXE-041 | 승인은 사용자 소유권, CSRF, Idempotency-Key, expected version과 승인 ID·version에 결합된 1회용 TOTP 재인증 proof를 요구한다. |
| EXE-042 | 거절은 TOTP 재인증 없이 가능하지만 인증 세션, CSRF, Idempotency-Key와 expected version을 요구하며 주문을 만들지 않는다. |
| EXE-043 | 만료, 가격범위 이탈, 최신 snapshot의 부재·지연·품질 저하, position·설정 version 변경, 거래 세션 변경 또는 Guard 차단은 승인을 `INVALIDATED` 또는 `EXPIRED`로 끝내고 새 판단을 요구한다. 정상적인 실시간 snapshot 갱신과 ID 변경만으로 승인을 무효화하지 않는다. |
| EXE-044 | 승인 처리 transaction은 proof 소비, 승인 상태 전이, 최신 Guard 평가, 주문 의도·`CREATED` 주문과 감사를 원자적으로 저장한다. 하나라도 실패하면 전부 rollback한다. |
| EXE-045 | `APPROVED`는 주문 생성 transaction이 성공했을 때만 기록한다. 승인만 완료되고 주문이 없는 중간 상태를 commit하지 않는다. |
| EXE-046 | 승인 처리 transaction은 해당 `market + symbol`의 `market_stream_states` 행을 잠그고 그 행이 가리키는 최신 snapshot 하나를 승인 시점 snapshot으로 선택한다. Guard의 freshness·품질·spread와 주문가격은 이 snapshot으로 평가하며 Guard evaluation에는 이 최신 snapshot ID를 기록한다. |
| EXE-047 | 가격편차는 승인에 고정된 reference 가격과 승인 시점 최신 snapshot의 주문 기준가격을 비교한다. 허용 범위 안이면 승인에 고정된 수량으로 주문하고, 범위를 벗어나면 수량이나 가격을 자동 보정하지 않고 `PRICE_DEVIATION_EXCEEDED`로 무효화한다. |

### 3.6 Guard 평가 계약

Guard 평가는 불변 레코드이며 다음 필드를 가진다.

```yaml
guard_evaluation:
  schema_version: "1.0"
  evaluation_id:
  phase: APPROVAL_CREATION | PRE_ORDER | BROKER_SEND
  subject_type: DECISION_EXECUTION | ORDER
  subject_id:
  result: PASSED | BLOCKED
  rule_results: []
  halt_scope: null | SYMBOL_HALT | ENTRY_HALT | ACCOUNT_HALT | SYSTEM_HALT
  snapshot_id:
  position_version:
  execution_policy_version_id:
  risk_policy_version_id:
  evaluated_at:
  valid_until:
```

첫 구현 차단 reason code:

```text
ACTION_NOT_IMPLEMENTED | ENVIRONMENT_NOT_MOCK | CONFIG_MISSING
DECISION_EXPIRED | SNAPSHOT_MISSING | MARKET_DATA_STALE | MARKET_DATA_DEGRADED
MARKET_SESSION_CLOSED | ENTRY_WINDOW_CLOSED | INSTRUMENT_NOT_TRADABLE
SYMBOL_NOT_WATCHED | BROKER_NOT_READY | RECONCILING | HALT_ACTIVE
EMERGENCY_STOP_ACTIVE | ACTIVE_ORDER_EXISTS | UNCERTAIN_ORDER_EXISTS
POSITION_ALREADY_OPEN | POSITION_NOT_FOUND | POSITION_VERSION_CONFLICT
ORDER_SIZE_NOT_CONFIGURED | QUANTITY_BELOW_ONE | SELL_QUANTITY_EXCEEDED
INSUFFICIENT_BUYING_POWER | SINGLE_ORDER_LIMIT | SYMBOL_EXPOSURE_LIMIT
TOTAL_EXPOSURE_LIMIT | MAX_OPEN_POSITIONS | MAX_DAILY_ENTRIES
SPREAD_TOO_WIDE | PRICE_DEVIATION_EXCEEDED | DATABASE_UNAVAILABLE
```

| ID | 요구사항 |
| --- | --- |
| EXE-050 | Guard는 규칙을 고정된 순서로 모두 평가해 복수 결과를 반환하되 blocking 결과가 하나라도 있으면 전체 결과는 `BLOCKED`다. |
| EXE-051 | `BUY`는 MOCK 환경, 거래·신규진입 시간, 등록 종목, 정상 최신 시세, tradable 상태, Broker READY, 재동기화 없음, 비상정지·중지 없음, 중복·불명 주문 없음, 잔고·금액·노출·포지션·횟수·spread·가격편차를 검사한다. |
| EXE-052 | 매도는 실제 포지션·version·매도가능수량, 활성·불명 주문, Broker·재동기화·거래 가능 상태를 검사한다. 신규매수 전용 한도는 청산을 막는 데 사용하지 않는다. |
| EXE-053 | 고정손절 trigger는 최신 정상 시세에서 한 번 발생하면 데이터 단절로 지우지 않고 `EXIT_PENDING` 위험 이벤트를 유지한다. 주문 실행 불가 사유를 경보하고 Broker 복구 후 최신 상태를 재검사한다. |
| EXE-054 | Guard 기준가격·수량·노출 계산은 Decimal/정수 연산을 사용하고 수수료·세금 포함 정책과 이미 예약된 미체결 금액·수량을 반영한다. |
| EXE-055 | `BROKER_SEND` 단계는 worker의 현재 lease·fencing, gate, 주문 상태·계좌·수량 불변조건을 다시 검사한다. 실패하면 송신하지 않고 주문을 안전 상태 또는 재동기화로 전환한다. |
| EXE-056 | Guard 차단은 decision 실행 결과, `risk_events`와 감사 로그에 reason code·scope·입력 version을 남기며 비밀값과 전체 계좌번호를 저장하지 않는다. 고정 손절 trigger의 차단 기록은 `stop_triggers` 상태(`EXIT_PENDING`)와 `risk_events`(scope=`FIXED_STOP`)에 함께 기록하며, 일일손실·spread·연결위험은 후속에서 같은 `risk_events` 원장을 재사용한다. |

### 3.7 주문 생성 경계

| ID | 요구사항 |
| --- | --- |
| EXE-060 | Guard 통과 후 `order_intent`와 첫 `orders(status=CREATED)`를 같은 transaction에서 생성한다. |
| EXE-061 | 주문은 `source_type`, source decision/rule trigger, execution ID, Guard evaluation ID, 실행·위험·전략 설정 버전을 참조한다. |
| EXE-062 | 가격은 주문 실행 명세의 정책으로 서버가 계산하고 AI·브라우저가 보낸 자유 가격을 사용하지 않는다. |
| EXE-063 | 주문 생성 후 전송·ACK·체결·UNKNOWN은 기존 Broker worker와 주문 상태 머신만 변경한다. 오케스트레이터는 직접 재전송하지 않는다. |
| EXE-064 | 한 종목의 활성 주문 또는 `SUBMITTING`, `UNKNOWN`, `RECONCILING`이 있으면 다른 판단의 주문 생성도 차단한다. |

### 3.8 단계적 활성화

```text
SHADOW: Guard 평가·실행 결과만 저장, 승인·주문 0건
APPROVAL_ONLY: MANUAL_APPROVAL만 허용, AUTOMATIC은 안전 차단
MOCK_AUTOMATIC: 명세된 4개 행동의 키움 모의투자 자동 실행 허용
```

| ID | 요구사항 |
| --- | --- |
| EXE-070 | 첫 배포 기본 gate는 `SHADOW`이며 Web UI에 현재 단계와 차단 이유를 표시한다. |
| EXE-071 | `APPROVAL_ONLY` 전환은 Guard·승인·주문 멱등성 자동시험과 서버 TOTP 승인 수동시험 통과를 요구한다. |
| EXE-072 | `MOCK_AUTOMATIC` 전환은 고정손절·비상정지·재시작·UNKNOWN·부분체결 장애시험과 사용자 재인증을 요구한다. |
| EXE-073 | 단계 전환은 `MOCK`에서만 가능하며 실거래 활성화를 의미하지 않는다. |

## 4. 오류·예외 또는 경계 조건

- 판단 유효시간과 승인 유효시간 중 더 이른 시각을 최종 만료로 사용한다.
- 승인 화면을 여러 탭에서 열어도 조건부 상태 전이로 한 요청만 성공한다.
- 승인 직전 새 시세가 도착하면 최신 정상 snapshot으로 Guard를 다시 평가한다. 단순 snapshot ID 변경은 허용하되 가격범위 이탈·지연·품질 저하·세션 또는 설정·position 변경은 기존 승인을 자동 보정하지 않고 무효화한다.
- DB commit 결과가 불명확하면 Broker에 보내지 않고 execution key와 주문 원장을 먼저 조회한다.
- Guard 서비스 예외·timeout·알 수 없는 reason code는 통과로 간주하지 않고 `FAILED_SAFE`와 `DATABASE_UNAVAILABLE` 또는 내부 안전 오류로 기록한다.
- 거래 종료 행동이 차단돼도 포지션을 `CLOSED`로 표시하지 않고 위험·운영 경보를 유지한다.

## 5. 검증·인수 조건

- 같은 판단을 동시에 여러 번 라우팅해도 승인 또는 주문이 최대 하나다.
- 진단 판단과 미지원 행동은 실행 권한과 무관하게 주문·승인 0건이다.
- `DISABLED`, `MANUAL_APPROVAL`, `AUTOMATIC`이 각각 기록만, 승인, Guard 통과 주문으로 분기된다.
- 승인 대기 중 최신 정상 snapshot으로 바뀌어도 가격편차 안이면 reference 수량으로 주문이 하나 생성된다.
- 승인 만료·가격 이탈·최신 snapshot stale/degraded·position version 변경·Guard 차단 시 주문이 생성되지 않는다.
- 승인 성공 transaction에서 proof·승인·Guard·주문·감사가 함께 commit되거나 모두 rollback된다.
- Guard 차단 reason과 판단 reference snapshot·승인 시점 최신 snapshot·설정·포지션 version으로 결과를 재현할 수 있다.
- 자동 주문도 사용자 승인 주문과 동일한 주문 상태 머신·Broker worker·UNKNOWN 재동기화를 사용한다.
- `SHADOW → APPROVAL_ONLY → MOCK_AUTOMATIC` 단계가 시험 근거 없이 확대되지 않는다.

## 6. 미결정·보류 항목

- 외부 AI 모델 제공자와 실거래 활성화는 범위 밖이다.
- 목표수익·추적손절·장 마감청산·긴급청산 trigger의 구현 순서는 첫 4개 행동 검증 후 정한다.
- 첫 버전은 `entry_order_amount` 시스템 기본값을 제공하지 않으며 사용자가 위험 설정에서 명시적으로 활성화해야 한다.
