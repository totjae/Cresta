# 판단 실행 및 승인 오케스트레이션 명세

## 1. 목적

검증된 `purpose=TRADING` Decision 또는 Guard 규칙 신호를 행동별 실행 권한, Guard 검사, 사용자 승인과 주문 상태 머신에 안전하게 연결한다. 판단 생성 구조 자체에는 주문 권한을 부여하지 않으며 동일 판단·승인·재시도에서 중복 주문이 생기지 않는 영속 경계를 정의한다.

## 2. 적용 범위

- 거래 목적 판단과 진단 목적 판단의 분리
- `purpose=TRADING` Decision·Guard 신호의 실행 행동 정규화
- `AUTOMATIC`, `MANUAL_APPROVAL`, `DISABLED` 분기
- 승인 생성·만료·거절·무효화·승인 후 재검사
- Guard 평가와 주문 의도·주문 생성 트랜잭션
- 멱등성, 동시성, 감사와 장애 복구

첫 구현은 `MOCK` 환경의 `BUY`, `PARTIAL_SELL`, `FULL_SELL`, `FIXED_STOP`만 실행 대상으로 한다. `TAKE_PROFIT`, `TRAILING_STOP`, `END_OF_DAY_LIQUIDATION`, `EMERGENCY_EXIT`은 각 trigger와 운영 시험이 구현되기 전 `ACTION_NOT_IMPLEMENTED`로 안전 차단한다. `/decisions/mock-evaluate` 같은 판단 진단용 HTTP API는 계속 주문과 승인을 만들지 않는다. Broker diagnostic order는 EXE-209~210의 별도 privileged 경계로만 취급한다.

Phase 10D.1B는 향후 BUY Guard가 읽을 `kt00001` account funds와 exact request-bound `kt00010` capacity의 persistence foundation만 추가한다. 이번 단계는 DecisionExecution, GuardEvaluation, Approval, OrderIntent, TradingOrder, fixed-stop 또는 Broker pre-send 권한과 상태 전이를 변경하지 않는다.

## 3. 상세 명세

### 3.1 책임과 입력 경계

```text
검증된 purpose=TRADING Decision 또는 Guard rule trigger
→ execution action 정규화
→ ExecutionStage gate
→ 활성 action policy 조회
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
| EXE-006 | `position-agent-fusion-v1` 판단은 다른 TRADING 판단과 동일한 실행 경로만 사용한다. Agent run·Core·Decision Agent·Arbiter·Provider는 승인 또는 주문을 직접 만들 수 없으며 필수 provenance가 없거나 해당 AI 계약 검증에 실패한 판단은 실행하지 않는다. |

### 3.2 행동 정규화

| 입력 | 정규 실행 행동 | 처리 |
| --- | --- | --- |
| TRADING Decision `BUY` | `BUY` | 실행 권한 분기 대상 |
| TRADING Decision `PARTIAL_SELL` | `PARTIAL_SELL` | `sell_ratio`로 매도 가능 수량 계산 |
| TRADING Decision `FULL_SELL` | `FULL_SELL` | 매도 가능 잔량 전부 |
| TRADING Decision `EMERGENCY_EXIT` | `EMERGENCY_EXIT` | 첫 구현은 안전 차단 |
| TRADING Decision `WAIT`, `REJECT`, `RISK_BLOCK`, `UNKNOWN`, `HOLD` | 없음 | `NO_ACTION` 기록 |
| TRADING Decision `TIGHTEN_STOP` | 없음 | 첫 구현은 `DEFERRED_NOT_IMPLEMENTED`; 주문으로 변환 금지 |
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
| EXE-015 | 판단 기반 `PARTIAL_SELL`·`FULL_SELL`은 Broker 총수량이 아니라 `min(managed_quantity, available_quantity)`에서 활성·불명 SELL 주문 예약수량을 뺀 수량만 사용한다. 순수 외부 보유분은 승인·자동 모드 모두 주문하지 않는다. |
| EXE-016 | 판단 기반 SELL의 첫 주문가는 판단 snapshot의 최우선 매수호가를 `MARKETABLE_LIMIT`로 사용한다. 승인 시에는 최신 정상 snapshot의 최우선 매수호가와 포지션 version을 다시 검사하며, 임의 호가단위 계산·시장가 전환·재호가는 하지 않는다. |

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
  phase: PRE_ORDER | APPROVAL_REVALIDATION | BROKER_SEND
  subject_type: DECISION_EXECUTION | STOP_TRIGGER
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
| EXE-057 | `PARTIAL_SELL`·`FULL_SELL` Guard는 포지션 존재·OPEN 상태·Cresta 관리수량·Broker 매도가능수량·포지션 version·활성/불명 주문·최신 정상 시세·거래상태·Broker gate·재동기화·MOCK 환경을 검사한다. 신규진입 전용 한도와 `PAUSE_ENTRY`는 청산을 막지 않는다. |

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

### 3.9 ExecutionStage 우선 안전 게이트

ExecutionStage는 행동별 mode보다 상위 gate다. 행동별 mode가 더 넓더라도 현재 stage가 허용하지 않는 Approval·Order 생성 또는 Broker 송신으로 확대할 수 없다.

| ID | 요구사항 |
| --- | --- |
| EXE-200 | ExecutionStage는 행동별 `AUTOMATIC | MANUAL_APPROVAL | DISABLED`보다 먼저 평가하는 상위 gate다. |
| EXE-201 | `SHADOW`에서는 어떤 행동 mode라도 Approval 또는 신규 Order를 생성할 수 없다. |
| EXE-202 | `APPROVAL_ONLY`에서는 `MANUAL_APPROVAL`만 허용하며 `AUTOMATIC`은 fail-closed 한다. |
| EXE-203 | 승인 없는 자동 MOCK 주문은 `MOCK_AUTOMATIC`에서만 허용한다. |
| EXE-204 | Approval 승인 transaction은 현재 ExecutionStage를 서버에서 다시 조회한다. |
| EXE-205 | Approval 생성 후 stage가 `SHADOW` 또는 해당 Approval을 허용하지 않는 상태로 downgrade되면 Approval을 `INVALIDATED`로 끝내고 Order를 생성하지 않는다. |
| EXE-206 | Broker worker는 `CREATED` 주문 송신 직전에 현재 ExecutionStage와 주문 provenance를 다시 검사한다. |
| EXE-207 | 주문 생성 후 ExecutionStage가 downgrade되어 현재 단계에서 해당 주문 송신이 허용되지 않으면 Broker API를 호출하지 않는다. |
| EXE-208 | worker는 source execution, 생성 당시 execution stage, action mode와 필수 provenance가 검증되지 않은 `CREATED` 주문을 송신하지 않는다. |
| EXE-209 | Broker diagnostic order 기능은 production decision execution 경로와 분리하고 별도의 privileged diagnostic gate를 사용한다. |
| EXE-210 | 진단 주문 기능의 준비 상태를 일반 SHADOW 또는 운영 자동매매가 가능한 상태로 표시하거나 해석하지 않는다. |
| EXE-211 | ExecutionStage 상위 gate는 AI Decision뿐 아니라 Guard rule trigger와 `FIXED_STOP`을 포함한 모든 주문 생성 원인에 동일하게 적용한다. |
| EXE-212 | `APPROVAL_ONLY`에서 `FIXED_STOP`을 포함한 Guard 기반 행동은 승인 없는 TradingOrder를 생성할 수 없다. 행동 mode가 `MANUAL_APPROVAL`이면 Guard 통과 후 고우선 Approval을 생성할 수 있고, `AUTOMATIC`이면 주문을 만들지 않은 채 `EXIT_PENDING` 위험과 고우선 경보를 유지한다. |
| EXE-213 | `FIXED_STOP`의 승인 없는 자동 MOCK 주문은 `MOCK_AUTOMATIC`에서만 허용한다. |
| EXE-214 | v7 TRADING activation gate는 Decision Finalizer admission을 제어하며 ExecutionStage를 대체하지 않는다. activation gate가 `OPEN`이어도 현재 ExecutionStage가 실행을 허용하지 않으면 Approval 또는 Order를 만들 수 없다. |
| EXE-215 | ExecutionStage 변경은 v7 activation gate 상태를 자동 변경하지 않는다. Finalization 허가와 주문 실행 허가는 독립적으로 평가하며 두 조건이 모두 필요한 경로에서는 둘 모두를 통과해야 한다. |
| EXE-216 | `sourced-entry-decision-v1`을 포함해 `Decision.action=WAIT | REJECT | UNKNOWN`은 모두 `NO_ACTION`이며 Guard의 BUY-like flow, Approval, OrderIntent, TradingOrder 또는 Broker 호출로 보내지 않는다. |
| EXE-217 | Decision Finalizer는 Execution Orchestrator가 아니며 BUY를 포함한 모든 action에서 `execution_mode=null`, `execution_outcome=null`, DecisionExecution·Approval·OrderIntent·TradingOrder 0건으로 종료한다. |
| EXE-218 | Activation Gate의 `target=MOCK`과 `OPEN | CLOSED`를 `SHADOW | APPROVAL_ONLY | MOCK_AUTOMATIC`, 행동별 mode 또는 Decision execution field로 변환하지 않는다. |
| EXE-219 | 후속 Execution Orchestrator만 finalized TRADING Decision을 별도 current ExecutionStage·행동 mode·Guard로 평가하며 Finalizer 성공이나 Gate OPEN 자체는 주문 권한이 아니다. |
| EXE-220 | sourced v7 Decision의 legacy `execution_mode`와 `execution_outcome`은 불변 null로 유지하고 후속 mode/outcome은 기존 `decision_executions`에만 기록한다. |

### 3.10 sourced ENTRY execution authority 계약

`sourced-entry-decision-v1`은 평가 결과이고 주문 허가가 아니다. server-owned Execution
Orchestrator는 Finalizer transaction 밖에서 Phase 9 persisted lineage validator를 재사용해
Decision→ENTRY_ARBITER→DecisionContext→C/B/A 전체를 검증한 뒤에만 별도 실행 lifecycle을
만든다. WAIT·REJECT·UNKNOWN도 재처리 disposition을 남기기 위해 lifecycle을 정확히 하나
만들지만 Guard 또는 주문 권한은 갖지 않는다.

Canonical `entry-execution-identity-v1`은 다음 exact 두 field만 가진다.

```yaml
schema_version: entry-execution-identity-v1
decision_id: <sourced Decision UUID>
```

UTF-8, `ensure_ascii=false`, key 사전순, compact separator로 canonicalize한 bytes의 SHA-256
lowercase hex 앞에 `v7exe-`를 붙인 `v7exe-<64 lowercase hex>`가 `execution_key`다. current
Execution/Risk Policy, ExecutionStage, source output hash 또는 재검증 결과를 identity에 넣지
않는다. sourced lifecycle discriminator는 `contract_version=sourced-entry-execution-v1`이다.

ExecutionStage와 행동 mode는 서로 다른 authority 차원이다. stage authority 순서는
`SHADOW < APPROVAL_ONLY < MOCK_AUTOMATIC`, action mode authority 순서는
`DISABLED < MANUAL_APPROVAL < AUTOMATIC`이다. execution 생성 시 stage version ID/hash와
행동별 policy version/mode를 freeze하고, 이후 current authority와 frozen authority 중 더
제한적인 값을 적용한다. current 설정이 더 permissive해져도 기존 execution은 승격되지
않는다. current action mode가 AUTOMATIC에서 MANUAL_APPROVAL로 내려간 경우 아직 Approval이나
Order가 없을 때만 manual path로 축소할 수 있고, 이미 CREATED automatic Order가 있으면 새
Approval을 합성하지 않고 unsent invalidation한다.

| effective stage | DISABLED | MANUAL_APPROVAL | AUTOMATIC |
| --- | --- | --- | --- |
| SHADOW | `DISABLED` | Guard 후 `SHADOW_RECORDED` 또는 `GUARD_BLOCKED`; Approval/Order 0 | Guard 후 `SHADOW_RECORDED` 또는 `GUARD_BLOCKED`; Approval/Order 0 |
| APPROVAL_ONLY | `DISABLED` | Guard PASS 후 Approval PENDING; 승인 전 Order 0 | `FAILED_SAFE / AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY`; Approval/Order 0 |
| MOCK_AUTOMATIC | `DISABLED` | Guard PASS 후 Approval PENDING | Guard PASS 후 MOCK Order 후보 |

Decision `valid_until`은 broker external submission 직전까지 hard authority bound다. external
side effect 전 만료·stage/mode downgrade·PAUSE_ENTRY·provenance failure가 발견되면 Broker
호출 없이 아직 보내지 않은 Order를 `INVALIDATED`로 끝낸다. `SUBMITTING` commit 이후에는
Decision expiry만으로 주문을 되돌리지 않고 기존 ACK/UNKNOWN/cancel/fill/reconciliation
lifecycle을 따른다.

Safety result/event taxonomy는 다음 exact code를 사용한다.

| 의미 | exact code/state or event |
| --- | --- |
| sourced authority 또는 persisted lineage invalid | `SOURCE_AUTHORITY_INVALID` |
| Decision 만료 | `DECISION_EXPIRED` |
| current stage 부재·ambiguous·malformed·expired | `EXECUTION_STAGE_UNAVAILABLE` |
| current stage가 frozen authority보다 낮음 | `EXECUTION_STAGE_DOWNGRADED` |
| action mode disabled | 기존 `ACTION_DISABLED` |
| current action mode가 frozen mode보다 낮아 기존 resource authority 상실 | `EXECUTION_MODE_DOWNGRADED` |
| APPROVAL_ONLY automatic 금지 | `AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY` |
| Guard block | `GUARD_BLOCKED`; `result_code`는 첫 deterministic blocking rule code |
| Approval authority 변경 무효화 | `EXECUTION_AUTHORITY_REVOKED` |
| broker pre-send authority 상실 | `EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND` |
| BUY PAUSE_ENTRY | 기존 `EMERGENCY_STOP_ACTIVE` |
| unknown/unclassified order source | `ORDER_SOURCE_UNCLASSIFIED` |
| unsent order terminal event | `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` |

| ID | 요구사항 |
| --- | --- |
| EXE-221 | sourced execution admission은 Phase 9 full persisted lineage와 immutable Decision representation을 검증한다. row shape만 맞거나 임의로 만든 TRADING BUY에는 execution authority를 부여하지 않는다. |
| EXE-222 | sourced Decision 하나는 `entry-execution-identity-v1`과 `v7exe-<sha256>` key로 authoritative DecisionExecution을 정확히 하나만 가진다. policy·stage 변경과 retry는 새 lifecycle을 만들지 않으며 새 평가가 필요하면 새 Decision을 만든다. |
| EXE-223 | WAIT·REJECT·UNKNOWN은 stage/policy authority를 선택하지 않고 같은 exact-one lifecycle에 `mode=null`, `stage=null`, `state=NO_ACTION`, `result_code=<원 Decision action>`을 저장한다. GuardEvaluation·Approval·OrderIntent·TradingOrder·Broker side effect는 0이다. |
| EXE-224 | concurrent handoff의 winner만 sourced lifecycle을 insert하고 loser와 ambiguous retry는 unique identity로 authoritative row를 재조회해 반환한다. legacy execution identity는 소급 변경하지 않는다. |
| EXE-225 | sourced BUY가 handoff 전에 만료됐으면 Guard와 config selection 없이 exact-one `mode=null`, `stage=null`, `FAILED_SAFE / DECISION_EXPIRED`를 저장하고 Approval·Order를 만들지 않는다. |
| EXE-226 | sourced execution은 `contract_version=sourced-entry-execution-v1`으로 legacy lifecycle과 구분하고 Decision의 action·reason·validation·source·execution null field를 절대 변경하지 않는다. |
| EXE-227 | Finalizer와 AgentRun은 execution 성공·실패로 다시 열거나 수정하지 않는다. `FINALIZATION_SUCCEEDED`는 Decision 확정 성공만 의미한다. |
| EXE-228 | authoritative current ExecutionStage는 `CONFIGURATION_SPEC.md`의 `SYSTEM / MOCK / V7_ENTRY_EXECUTION_STAGE` ACTIVE ConfigurationVersion 하나에서만 선택한다. Settings 값은 legacy compatibility 또는 bootstrap 표시값일 뿐 sourced authority가 아니다. |
| EXE-229 | DecisionExecution은 선택한 stage, stage ConfigurationVersion ID와 canonical payload hash, 실행·위험 policy version과 effective action mode를 immutable provenance로 freeze한다. |
| EXE-230 | effective stage와 action mode는 각각 frozen/current authority의 minimum이다. current version ID 교체만으로 cancel하지 않지만 initial stage가 missing·ambiguous·invalid·expired이면 exact-one `mode=null`, `stage=null`, `FAILED_SAFE / EXECUTION_STAGE_UNAVAILABLE`로 terminal 처리한다. 선택 후 current control failure는 기존 resource authority를 회수하며, 더 permissive한 current 설정은 frozen authority를 확대하지 않는다. |
| EXE-231 | Approval 생성·승인, Order 생성과 broker pre-send는 current stage와 current action policy를 다시 읽는다. current authority downgrade는 이미 보내지 않은 권한에 즉시 적용한다. |
| EXE-232 | current SHADOW는 PENDING Approval을 `INVALIDATED / EXECUTION_AUTHORITY_REVOKED`, unsent CREATED Order를 `INVALIDATED`와 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` event로 끝내며 Broker 호출은 0이다. |
| EXE-233 | APPROVAL_ONLY+AUTOMATIC은 `FAILED_SAFE / AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY`이고 Approval·Order를 만들지 않는다. historical direct-order 동작은 normative behavior가 아니다. |
| EXE-234 | current/frozen action mode가 DISABLED이면 새 resource 전에는 `DISABLED / ACTION_DISABLED`; pending Approval 또는 CREATED Order 뒤 downgrade면 각각 authority invalidation을 사용한다. |
| EXE-235 | 별도 ExecutionStage lifecycle table을 기본으로 만들지 않는다. ConfigurationVersion은 current control-plane, DecisionExecution은 frozen stage provenance와 lifecycle의 source of truth다. |
| EXE-236 | SHADOW는 모든 mode에서 Guard 기록만 허용하고 Approval 생성, Order 생성과 unsent Broker send를 금지한다. |
| EXE-237 | APPROVAL_ONLY는 effective MANUAL_APPROVAL+Guard PASS에만 Approval을 허용한다. AUTOMATIC과 FIXED_STOP은 direct Order를 만들 수 없다. |
| EXE-238 | MOCK_AUTOMATIC은 target/account/environment/adapter가 모두 MOCK이고 effective AUTOMATIC, current Guard PASS인 경우에만 승인 없는 Order 후보를 허용한다. LIVE automatic은 없다. |
| EXE-239 | effective MANUAL_APPROVAL은 APPROVAL_ONLY와 MOCK_AUTOMATIC에서 같은 Approval contract를 사용하며 유효 승인 transaction 전에는 OrderIntent/Order를 만들지 않는다. |
| EXE-240 | automatic execution이 current MANUAL_APPROVAL로 downgrade된 경우 아직 authority resource가 없으면 manual path만 허용하고, CREATED Order가 있으면 Approval로 변환하지 않고 unsent invalidation한다. |
| EXE-241 | Decision validity, source authority, stage/action authority와 PAUSE_ENTRY를 initial Guard, Approval authorization, Order creation과 broker pre-send에서 다시 검사한다. SUBMITTING commit 이후 Decision expiry는 기존 Order lifecycle을 변경하지 않는다. |
| EXE-242 | BUY Guard는 거래 session/status, current 정상 snapshot·quote freshness, buying power, sizing/max notional, 노출/포지션 한도, 같은 account/symbol의 활성·UNKNOWN·RECONCILING 주문, Broker gate/worker safety를 모두 검사한다. 필수 input missing·stale·unknown·conflicted·DB failure는 fail-closed다. |
| EXE-243 | BUY 수량·가격·order type은 server-owned Risk/Order Policy와 authoritative quote만 결정한다. frozen Risk Policy와 current Risk Policy를 모두 평가하고 둘 중 하나라도 차단하면 실패하며 current 완화는 frozen quantity/authority를 확대하지 않는다. |
| EXE-244 | PAUSE_ENTRY는 BUY initial/approval/order/pre-send authority를 차단하지만 risk-reduction SELL/FIXED_STOP을 같은 이유로 차단하지 않는다. account-wide liquidation halt는 이번 계약에 추가하지 않는다. |
| EXE-245 | Guard phase exact enum은 `PRE_ORDER | APPROVAL_REVALIDATION | BROKER_SEND`다. Approval 생성 전 initial evaluation은 PRE_ORDER이며 각 권위 boundary는 새 immutable GuardEvaluation을 남긴다. |
| EXE-246 | Guard BLOCK은 Decision BUY를 RISK_BLOCK로 바꾸지 않고 GuardEvaluation과 DecisionExecution result에만 기록한다. |
| EXE-247 | Approval 생성은 effective stage가 APPROVAL_ONLY 또는 MOCK_AUTOMATIC, effective mode가 MANUAL_APPROVAL, PRE_ORDER Guard PASS, Decision valid, BUY PAUSE_ENTRY clear일 때만 가능하며 execution·Guard·Approval·audit을 한 transaction으로 commit한다. 내부 helper commit은 금지한다. |
| EXE-248 | approve/reject caller는 Approval.user_id와 일치하고 expected_version CAS를 통과해야 한다. APPROVE는 `target_action=APPROVE_ORDER`, `target_id=<approval_id>:<expected_version>`에 결합된 미사용·미만료 one-time reauth proof를 같은 transaction에서 소비한다. REJECT는 reauth 없이 owner+expected_version+CSRF+idempotency를 요구한다. |
| EXE-249 | APPROVE transaction은 PENDING/owner/version/proof, full sourced authority, Decision validity, frozen/current stage와 action mode, PAUSE_ENTRY, latest Guard, 가격편차와 position/order 상태를 잠금 후 재검증한다. 실패하면 Order 0이고 Approval은 사유에 따라 EXPIRED 또는 INVALIDATED다. |
| EXE-250 | Approval authorization transaction은 proof 소비, GuardEvaluation, OrderIntent/CREATED Order, Approval/DecisionExecution 전이와 audit을 함께 commit하거나 모두 rollback한다. |
| EXE-251 | FIXED_STOP은 STOP_TRIGGER source다. SHADOW는 SHADOW_RECORDED/blocked와 Order 0, APPROVAL_ONLY는 synthetic user/Approval 없이 EXIT_PENDING과 고우선 risk event를 유지, MOCK_AUTOMATIC+effective AUTOMATIC+sell Guard PASS에서만 MOCK automatic Order를 허용한다. |
| EXE-252 | FIXED_STOP도 versioned stage/action policy를 사용하며 safe-default AUTOMATIC을 account-scoped trigger의 암묵적 권한으로 사용하지 않는다. |
| EXE-253 | OrderIntent는 source_type/source_id, DecisionExecution 또는 StopTrigger, GuardEvaluation, optional Approval, execution/risk policy와 stage version/hash, stable authority key를 보존한다. TradingOrder는 intent를 통해 이 chain을 결정적으로 추적한다. |
| EXE-254 | order source exact enum은 `DECISION_EXECUTION | STOP_TRIGGER | BROKER_DIAGNOSTIC | LEGACY_EXECUTION | BROKER_IMPORTED`다. unknown/null source의 CREATED Order는 send하지 않고 `ORDER_SOURCE_UNCLASSIFIED`로 unsent invalidation한다. |
| EXE-255 | BROKER_DIAGNOSTIC은 별도 privileged MOCK 1주 validator와 PAUSE_ENTRY를 통과할 때만 send 가능하며 ExecutionStage나 production readiness를 뜻하지 않는다. BROKER_IMPORTED는 이미 관측된 외부 주문이라 신규 send 대상이 아니다. |
| EXE-256 | Order 생성 직전 source/Decision validity, effective stage/mode, latest Guard, required APPROVED Approval, emergency, MOCK target과 conflicting authoritative order 부재를 같은 transaction에서 확인한다. execution별 Approval과 initial authority key별 OrderIntent/Order는 각각 최대 하나다. |
| EXE-257 | Broker worker는 source_type별 shared validator로 source chain, current stage/action authority, BROKER_SEND Guard, Approval, Decision expiry, PAUSE_ENTRY와 MOCK target을 SUBMITTING 직전에 검사한다. 실패하면 send 0, Order INVALIDATED, authority event/audit을 원자 기록한다. |
| EXE-258 | broker pre-send DB transaction은 authority 검사와 SUBMITTING/fencing commit으로 끝내고 network call 동안 DB lock을 유지하지 않는다. 이후 ACK/REJECTED/UNKNOWN result transaction과 no-blind-resend reconciliation을 유지한다. |
| EXE-259 | Finalized sourced Decision과 authoritative execution이 없는 row를 찾는 idempotent reconciliation/sweep가 WAIT·REJECT·UNKNOWN을 포함해 crash window를 복구한다. production scheduler handoff는 전체 authority acceptance 뒤에만 활성화한다. |
| EXE-260 | migration 이전 unclassified CREATED Order는 source를 추측하거나 backfill하지 않는다. pre-send/reconciliation에서 `INVALIDATED / ORDER_SOURCE_UNCLASSIFIED`로 닫고, 이미 SUBMITTING 이상인 주문은 기존 ambiguous-send/reconciliation lifecycle을 유지한다. |

### 3.11 금융 freshness와 initial Order authority identity

| ID | 요구사항 |
| --- | --- |
| EXE-261 | PRE_ORDER와 APPROVAL_REVALIDATION의 financial freshness 및 refresh/reselect authority는 GRD-107~116과 CFG-121~126을 따른다. Broker network call 동안 Guard/Approval/Order authority transaction이나 DB row lock을 유지하지 않는다. |
| EXE-262 | Approval authorization은 PRE_ORDER funds/capacity를 blind reuse하지 않고 current intended price·quantity와 exact `kt00010` request context로 새 APPROVAL_REVALIDATION evidence를 평가한다. refresh 실패, future timestamp, required NULL 또는 stale evidence는 OrderIntent/Order 0건이다. |
| EXE-263 | `OrderIntent.authority_key`는 하나의 source execution authority가 initial authoritative OrderIntent를 만들 수 있는 stable identity이며 exact-order request나 Broker submission idempotency가 아니다. canonical schema는 `order-authority-key-v1`이다. |
| EXE-264 | canonical material은 exact 네 field `schema_version`, `source_type`, `source_id`, `approval_id`만 가진 JSON object다. 앞의 세 값은 string이고 `approval_id`는 string 또는 null이다. ID는 persisted primary-key string을 trim·case 변환 없이 사용한다. key 사전순, compact separators, UTF-8, explicit JSON null로 직렬화하며 whitespace와 추가 field는 identity에 관여할 수 없다. |
| EXE-265 | canonical bytes의 SHA-256 digest를 64 lowercase hex로 만들고 stored key를 `ordauth-<digest>`로 저장한다. 총 길이는 72자이며 기존 128자 column으로 충분하다. |
| EXE-266 | manual approved sourced BUY material은 `schema_version="order-authority-key-v1"`, `source_type="DECISION_EXECUTION"`, `source_id=<DecisionExecution.id>`, `approval_id=<Approval.id>`다. Approval은 execution의 기존 exact-one APPROVED authority와 일치해야 한다. |
| EXE-267 | future MOCK_AUTOMATIC sourced BUY는 같은 schema와 DecisionExecution source를 사용하되 `approval_id`를 explicit null로 둔다. synthetic Approval이나 가짜 Approval ID를 만들지 않는다. |
| EXE-268 | fixed-stop initial authority material은 `source_type="STOP_TRIGGER"`, `source_id=<StopTrigger.id>`, `approval_id=null`이다. Phase 10E runtime은 validated MOCK_AUTOMATIC, explicit versioned fixed-stop AUTOMATIC policy, typed sell Guard와 strict MOCK authority를 모두 통과할 때만 이 key로 initial CREATED MOCK SELL을 정확히 하나 만든다. |
| EXE-269 | authority material에는 price, quantity, notional, GuardEvaluation ID, Stage version/hash, Risk/Execution Policy version, current policy/stage, market snapshot/quote와 Decision source hash를 넣지 않는다. 이 값의 재검증·변화는 같은 source에 두 번째 authority를 만들 수 없다. |
| EXE-270 | 같은 canonical material의 retry는 같은 authority_key로 existing OrderIntent를 조회·재사용한다. 같은 key에 다른 immutable intent/order terms가 제시되면 새 key를 만들지 않고 fail-closed conflict로 끝낸다. |
| EXE-271 | authority_key는 `request_hash`, TradingOrder `idempotency_key`, `client_order_id`, replacement metadata와 별개다. 전자는 authority grant identity이고 후자는 exact request/submission/fencing lifecycle이며 어느 것도 다른 것을 대체하지 않는다. |
| EXE-272 | Stage/Risk/Execution Policy 변화는 같은 execution의 새 authority_key를 만들지 않는다. live authority는 아직 전송되지 않은 기존 order를 revoke할 수 있으나 두 번째 intent를 mint할 수 없고, historical missing key는 authority를 추측해 backfill하지 않는다. |
| EXE-273 | `source_type`은 EXE-254의 Phase 10B taxonomy를 그대로 사용한다. 이번 key material 계약은 DECISION_EXECUTION과 future STOP_TRIGGER만 정의하며 BROKER_DIAGNOSTIC·LEGACY_EXECUTION·BROKER_IMPORTED에 신규 sending authority나 guessed identity를 부여하지 않는다. |

### 3.12 Broker pre-send authority와 미송신 회수

| ID | 요구사항 |
| --- | --- |
| EXE-274 | worker는 잠근 `CREATED` Order의 exact Intent/source를 분류한 뒤 shared persisted source validator, frozen/current stage·action·Risk Policy minimum, source별 `BROKER_SEND` Guard와 worker lease/fencing/gate를 모두 통과한 경우에만 `SUBMITTING`을 commit한다. `V7_ENTRY_ACTIVATION`은 이 경계에서 조회하지 않는다. |
| EXE-275 | semantic authority 상실은 Order를 `INVALIDATED / ORDER_AUTHORITY_REVOKED_BEFORE_SEND`로 원자 종료한다. DecisionExecution은 `FAILED_SAFE / EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND`, manual Approval은 `INVALIDATED / EXECUTION_AUTHORITY_REVOKED`로 전이하며 Decision·AgentRun·OrderIntent의 immutable 내용을 변경하지 않는다. |
| EXE-276 | stage/config DB retryable failure와 authority transaction commit 실패는 semantic 회수가 아니다. transaction 전체를 rollback하고 Order를 `CREATED`로 유지하며 Broker 호출을 하지 않는다. |
| EXE-277 | STOP_TRIGGER Order 회수 시 trigger를 `FULFILLED`로 남기지 않는다. 같은 trigger의 실제 청산 위험은 기존 `EXIT_PENDING`으로 복귀하고 연계 RiskEvent는 ACTIVE를 유지하거나 없으면 생성한다. 자동 수량 축소, replacement Order 생성과 PAUSE_ENTRY 차단은 하지 않는다. |
| EXE-278 | `BROKER_DIAGNOSTIC`은 persisted privileged MOCK diagnostic request identity, 1주, exact account/environment, PAUSE_ENTRY clear를 검증할 때만 송신한다. `LEGACY_EXECUTION`은 별도 typed grant가 규범적으로 증명되지 않으면 송신 authority가 없고, `BROKER_IMPORTED`는 관측 projection이므로 항상 신규 송신을 금지한다. |
| EXE-279 | pre-send BUY financial 검사는 Broker refresh 없이 persisted exact funds/capacity만 사용한다. source Order 자신은 active-order conflict에서 제외하고 다른 active/UNKNOWN/RECONCILING order는 차단한다. |
| EXE-280 | pre-send PASS transaction은 `BROKER_SEND` Guard, 최종 lease/fencing/gate 검증과 `SUBMITTING` 전이를 함께 commit한다. 외부 MOCK submit은 commit 뒤 transaction 밖에서 정확히 한 번 호출하며 결과는 기존 ACK/REJECTED/UNKNOWN transaction으로 처리한다. |
| EXE-281 | internal unsent reconciliation helper는 `CREATED`만 검사하고 valid row는 유지, revoked row는 exactly-once invalidation, 이미 INVALIDATED/SUBMITTING 이상은 no-op한다. Phase 10F에서는 startup/scheduler/periodic activation을 연결하지 않는다. |

### 3.13 sourced ENTRY production handoff worker

| ID | 요구사항 |
| --- | --- |
| EXE-282 | Finalizer는 immutable sourced TRADING/ENTRY Decision commit까지만 담당한다. Finalizer/Arbiter transaction 또는 synchronous callback에서 execution을 호출하지 않으며, 별도 `sourced-handoff` worker의 다음 sweep만 committed Decision을 처리한다. |
| EXE-283 | production handoff는 기존 `reconcile_sourced_entry_executions()`를 그대로 호출한다. worker가 eligibility, action, stage, Gate, policy 또는 Guard 규칙을 복제·재해석하거나 Decision을 변경하지 않는다. |
| EXE-284 | worker는 `CRESTA_V7_SOURCED_HANDOFF_ENABLED=true`일 때만 sweep한다. false/unset이면 정상 종료하며 manual/internal helper 호출과 Broker worker lifecycle에는 영향을 주지 않는다. 이 process setting은 trading authority가 아니다. |
| EXE-285 | sweep cadence는 기존 Agent worker poll setting을 재사용하고 batch는 helper의 bounded 100건, `created_at ASC, id ASC` ordering을 유지한다. 복수 process·반복 sweep의 exact-one은 in-memory checkpoint가 아니라 sourced execution partial unique, canonical identity와 unique-loser recovery가 보장한다. |
| EXE-286 | retryable DB failure는 transaction을 rollback해 Decision을 그대로 두고 phantom DecisionExecution을 만들지 않는다. worker는 failure를 기록하고 다음 허용 iteration에서 다시 시도한다. semantic terminal result는 새 lifecycle로 대체하지 않으며 unexpected error는 숨기지 않고 worker-level structured exception log로 격리한다. |
| EXE-287 | SIGINT/SIGTERM 또는 application stop은 stop event를 설정해 새 iteration을 금지하고 진행 중 helper 호출의 transaction 경계를 존중한 뒤 join한다. handoff worker는 Broker adapter나 external network를 호출하지 않으며 external MOCK submit은 기존 Broker worker만 소유한다. |
| EXE-288 | WAIT·REJECT·UNKNOWN은 background handoff에서도 exact-one `NO_ACTION`이며 Approval·OrderIntent·Order가 0이다. BUY는 기존 SHADOW/APPROVAL_ONLY/MOCK_AUTOMATIC authority path만 사용하고 Stage/Gate/Policy를 생성·seed하거나 Approval을 자동 승인하지 않는다. |
| EXE-289 | 활성화 시 historical persisted eligible Decision도 hidden cutoff 없이 helper contract에 따라 처리한다. uncommitted 또는 rolled-back Finalizer row는 별도 session에 보이지 않으며, committed execution/NO_ACTION 뒤 worker crash와 재시작은 duplicate downstream authority를 만들지 않는다. |

`SOURCE_AUTHORITY_INVALID`, `DECISION_EXPIRED`, `EXECUTION_STAGE_UNAVAILABLE`로 생성된
pre-selection `FAILED_SAFE` lifecycle은 terminal이다. 이후 lineage/config가 달라져도 같은
Decision을 재활성화하거나 두 번째 execution을 만들지 않으며, 새 평가가 필요하면 새 immutable
Decision을 생성한다.

## 4. 오류·예외 또는 경계 조건

- 판단 유효시간과 승인 유효시간 중 더 이른 시각을 최종 만료로 사용한다.
- 승인 화면을 여러 탭에서 열어도 조건부 상태 전이로 한 요청만 성공한다.
- 승인 직전 새 시세가 도착하면 최신 정상 snapshot으로 Guard를 다시 평가한다. 단순 snapshot ID 변경은 허용하되 가격범위 이탈·지연·품질 저하·세션 또는 설정·position 변경은 기존 승인을 자동 보정하지 않고 무효화한다.
- DB commit 결과가 불명확하면 Broker에 보내지 않고 execution key와 주문 원장을 먼저 조회한다.
- Guard 서비스 예외·timeout·알 수 없는 reason code는 통과로 간주하지 않고 `FAILED_SAFE`와 `DATABASE_UNAVAILABLE` 또는 내부 안전 오류로 기록한다.
- 거래 종료 행동이 차단돼도 포지션을 `CLOSED`로 표시하지 않고 위험·운영 경보를 유지한다.

## 5. 검증·인수 조건

- 같은 판단을 동시에 여러 번 라우팅해도 승인 또는 주문이 최대 하나다.
- `purpose=DIAGNOSTIC` 판단과 미지원 행동은 실행 권한과 무관하게 주문·승인 0건이다. 별도 Broker diagnostic order는 운영 판단으로 승격되지 않는다.
- `DISABLED`, `MANUAL_APPROVAL`, `AUTOMATIC`이 각각 기록만, 승인, Guard 통과 주문으로 분기된다.
- 승인 대기 중 최신 정상 snapshot으로 바뀌어도 가격편차 안이면 reference 수량으로 주문이 하나 생성된다.
- 승인 만료·가격 이탈·최신 snapshot stale/degraded·position version 변경·Guard 차단 시 주문이 생성되지 않는다.
- 승인 성공 transaction에서 proof·승인·Guard·주문·감사가 함께 commit되거나 모두 rollback된다.
- Guard 차단 reason과 판단 reference snapshot·승인 시점 최신 snapshot·설정·포지션 version으로 결과를 재현할 수 있다.
- 자동 주문도 사용자 승인 주문과 동일한 주문 상태 머신·Broker worker·UNKNOWN 재동기화를 사용한다.
- FIXED_STOP trigger도 현재 ExecutionStage를 우회하지 않으며 `APPROVAL_ONLY`에서는 승인 없는 주문을 만들지 않는다.
- 부분·전량매도의 승인 범위에는 `position_id`, `position_version`, 정확한 수량과 reference snapshot·가격을 고정하며 승인 시 하나라도 달라져 안전한 주문을 보장할 수 없으면 `INVALIDATED`로 종료한다.
- `SHADOW → APPROVAL_ONLY → MOCK_AUTOMATIC` 단계가 시험 근거 없이 확대되지 않는다.

## 6. 미결정·보류 항목

- 외부 AI 모델 제공자와 실거래 활성화는 범위 밖이다.
- 목표수익·추적손절·장 마감청산·긴급청산 trigger의 구현 순서는 첫 4개 행동 검증 후 정한다.
- 첫 버전은 `entry_order_amount` 시스템 기본값을 제공하지 않으며 사용자가 위험 설정에서 명시적으로 활성화해야 한다.
