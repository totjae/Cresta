# Cresta AI 분석 및 의사결정 계약 명세

## 1. 목적

기존 Scout·Core와 Cresta v2 Decision Agent가 사용할 입력 snapshot, 제한된 출력 스키마, 호출 조건, 실패 처리와 감사 기준을 정의해 AI가 주문 권한이나 Guard 정책을 우회하지 못하게 한다.

## 계약 버전과 전환 범위

이 문서는 기존 Cresta Agent Runtime과 Cresta v2 ENTRY 판단 구조를 함께 관리한다.

기존 `agent-dag-v1`~`agent-dag-v6`, `agent-core-v1/v2`, `deterministic-mock-v2`, `deterministic-position-v1`, `position-agent-fusion-v1`의 과거 실행 의미와 저장된 결과는 소급 변경하지 않는다. 3~6절은 기존 Agent Runtime v1~v6와 기존 POSITION 호환 계약이며, Cresta v2 신규 ENTRY 판단에는 7절을 적용한다.

Cresta v2의 1차 의사결정 구조 변경은 `ENTRY` 판단에만 적용한다. `POSITION` 판단은 별도 migration이 명시되기 전까지 `deterministic-position-v1`, `TRADING_ADVISORY`, `position-agent-fusion-v1` 계약을 유지한다.

## 2. 적용 범위

- 감시 종목의 Scout 주기 분석
- 신규진입과 보유 포지션의 Core 판단
- Cresta v2 ENTRY의 DecisionContext, 세 Decision Agent와 deterministic Arbiter 판단
- 모델 입력 최소화, JSON Schema 검증과 유효시간
- 장애·비결정성·비용·지연 관리
- 판단 저장, 재현과 성과 평가

## 3. 상세 명세

### 3.1 역할과 호출 조건

| ID | 요구사항 |
| --- | --- |
| AI-001 | Scout는 주문하지 않고 구조화된 특징과 Core 검토 필요 여부만 출력한다. |
| AI-002 | Core는 제한된 행동 코드와 근거를 출력하며 수량·자유 가격·증권사 명령을 생성하지 않는다. |
| AI-003 | 신규진입 Core 호출은 사전 필수조건을 통과한 등록 종목에만 수행한다. |
| AI-004 | 보유 중 Core 호출은 정기 정책 또는 Scout 중요 경보로 수행하고 같은 입력 버전의 중복 호출을 억제한다. |
| AI-005 | AI가 `BUY`·매도를 제안해도 실행 모드, 승인과 Guard 검사를 별도로 통과해야 한다. |

### 3.2 공통 입력 snapshot

```yaml
decision_input:
  schema_version:
  purpose: DIAGNOSTIC | TRADING
  snapshot_id:
  symbol:
  market:
  observed_at:
  data_quality:
  session_state:
  quote:
  indicators:
  position:
  open_orders:
  account_risk_summary:
  market_context:
  strategy:
  configuration_version:
  prior_decision_summary:
```

| ID | 요구사항 |
| --- | --- |
| AI-010 | 입력은 불변 `snapshot_id`, 스키마 버전과 모든 데이터의 기준시각을 포함한다. |
| AI-011 | 계좌번호, 사용자 인증값, 키움 자격증명과 불필요한 개인정보를 모델에 전달하지 않는다. |
| AI-012 | 가격·수량·비율의 단위와 결측 상태를 명시하며 결측을 0으로 대체하지 않는다. |
| AI-013 | 입력 시세가 거래 행동별 최신성 기준을 넘거나 품질이 `DEGRADED` 이하면 주문 가능 판단을 요청하지 않는다. |
| AI-014 | 모델 입력에는 Guard의 변경 불가 규칙을 수정하거나 무시하라는 지시를 포함할 수 없다. |

### 3.3 Scout 출력

```yaml
scout_output:
  schema_version:
  symbol:
  snapshot_id:
  trend_state: UPTREND | UPTREND_WEAKENING | RANGE | DOWNTREND | UNKNOWN
  volume_state: STRENGTHENING | NORMAL | WEAKENING | UNKNOWN
  volatility_state: NORMAL | EXPANDING | EXTREME | UNKNOWN
  entry_score: 0..100
  exit_risk_score: 0..100
  core_review_required: true | false
  suggested_review: ENTRY | HOLDING | NONE
  reason_codes: []
  valid_until:
```

| ID | 요구사항 |
| --- | --- |
| AI-020 | Scout 출력은 위 열거형과 범위만 사용하고 자유 행동 코드를 만들지 않는다. |
| AI-021 | `reason_codes`는 버전 관리된 허용 목록만 사용하며 사용자 표시 문장은 서버가 코드에서 생성한다. |
| AI-022 | Scout 실패·시간초과·스키마 오류는 주문 신호로 변환하지 않고 `UNKNOWN` 분석 상태로 기록한다. |
| AI-023 | Core 검토 임계값은 사용자 전략 설정으로 관리하되 Guard 한도를 완화하지 않는다. |

첫 버전 허용 `reason_codes`:

```text
PRICE_STABLE | ABOVE_VWAP | BELOW_VWAP | VOLUME_STRENGTHENING
VOLUME_WEAKENING | BREAKOUT_CONFIRMED | BREAKDOWN_DETECTED
DRAWDOWN_FROM_HIGH | LOWER_LOW | SELL_PRESSURE_RISING
VOLATILITY_EXPANDING | MARKET_SUPPORTIVE | MARKET_WEAKENING
RISK_REWARD_ACCEPTABLE | CHASE_RISK | SPREAD_WIDE
TARGET_REACHED | STOP_RISK | TIME_DECAY | DATA_INSUFFICIENT
```

| AI-024 | 허용 목록에 없는 reason code는 출력 검증에서 거부하며 한국어 문장은 서버의 버전 관리 번역표로 생성한다. |

### 3.4 Core 출력

신규진입 행동:

```text
BUY | WAIT | REJECT | RISK_BLOCK
```

보유 중 행동:

```text
HOLD | TIGHTEN_STOP | PARTIAL_SELL | FULL_SELL | EMERGENCY_EXIT
```

```yaml
core_output:
  schema_version:
  symbol:
  snapshot_id:
  action:
  confidence: 0.0..1.0
  risk_level: LOW | MEDIUM | HIGH | CRITICAL
  sell_ratio: null | 0.01..1.0
  reason_codes: []
  valid_until:
```

| ID | 요구사항 |
| --- | --- |
| AI-030 | 행동은 현재 포지션 상태에 허용된 집합에서만 선택할 수 있다. |
| AI-031 | `PARTIAL_SELL`만 `sell_ratio`를 요구하고 그 외 행동에서는 null이어야 한다. 실제 수량은 Broker 앞의 주문 서비스가 보유수량과 호가단위로 계산한다. |
| AI-032 | `confidence`는 주문금액·Guard 한도·손절폭을 자동 확대하는 데 사용하지 않는다. |
| AI-033 | `valid_until`은 입력 시각과 행동별 최대 유효시간 안이어야 하며 기본 최대값은 신규진입 60초, 보유 판단 5분이다. |
| AI-034 | 허용되지 않은 필드·행동·범위, snapshot 불일치와 만료 출력은 실행하지 않고 `RISK_BLOCK` 또는 기존 포지션 `HOLD_WITH_ERROR` 내부 상태로 처리한다. |

### 3.5 모델 실행과 재현

| ID | 요구사항 |
| --- | --- |
| AI-040 | 모델 제공자, 모델 식별자, 프롬프트 버전, 스키마 버전, 생성 파라미터와 호출 지연을 기록한다. |
| AI-041 | 같은 판단의 자동 재시도는 네트워크 오류에 한해 최대 1회 허용하고 동일 `snapshot_id`와 요청 식별자를 사용한다. |
| AI-042 | 호출 시간초과 기본값은 Scout 10초, Core 15초이며 만료된 응답은 폐기한다. |
| AI-043 | 출력 원문과 검증 결과는 비밀정보를 제거해 저장하고 구조화 출력과 해시로 연결한다. |
| AI-044 | 모델 장애 중 신규매수는 생성하지 않으며 기존 포지션의 실시간 Guard 손절은 AI 없이 계속 작동한다. |

### 3.6 프롬프트·도구 안전

| ID | 요구사항 |
| --- | --- |
| AI-050 | 종목명·뉴스·공시 등 외부 텍스트는 명령이 아닌 비신뢰 데이터로 구분해 전달한다. |
| AI-051 | 첫 버전 Scout·Core에는 주문 API, 파일시스템, 네트워크와 비밀 저장소 도구를 제공하지 않는다. |
| AI-052 | 모델 출력의 자연어 설명은 주문 실행에 사용하지 않고 `action`과 검증된 구조 필드만 사용한다. |
| AI-053 | 프롬프트·모델 버전 변경은 설정 버전과 동일하게 검증·승인·회귀시험 후 활성화한다. |

### 3.7 성과 평가

| ID | 요구사항 |
| --- | --- |
| AI-060 | 판단 당시 알 수 없던 미래 데이터를 입력에 포함하지 않는다. |
| AI-061 | 판단 후 실제 수익률뿐 아니라 최대 유리·불리 움직임, 실행 여부, 거부 이유와 데이터 품질을 기록한다. |
| AI-062 | 승인 거절·Guard 차단·미체결을 모델 판단 실패와 구분해 평가한다. |
| AI-063 | 모델 변경 비교는 동일 기간·종목·비용·슬리피지 가정으로 수행한다. |

## 4. 오류·예외 또는 경계 조건

- Scout와 Core 판단이 충돌하면 Core 행동이 후보가 되지만 Guard와 실제 계좌 상태가 최종 우선한다.
- 기존 포지션에서 Core 응답이 없거나 잘못돼도 자동 보유 판단으로 간주하지 않고 오류 상태를 표시하며 Guard는 계속 작동한다.
- `sell_ratio`로 계산한 수량이 1주 미만이면 부분매도를 만들지 않고 정책에 따라 HOLD 또는 전량매도 재판단을 요청한다.
- 만료된 snapshot의 모델 응답을 최신 데이터에 재사용하지 않는다.

## 5. 검증·인수 조건

- 잘못된 행동·필드·범위·만료 출력이 주문으로 이어지지 않는다.
- 모델에 인증정보·계좌번호가 전달되지 않는다.
- 동일 snapshot 호출과 재시도가 중복 판단·주문을 만들지 않는다.
- 모델 장애 중 신규매수는 차단되고 Guard 실시간 손절은 유지된다.
- 저장된 snapshot·프롬프트·모델·설정 버전으로 판단 조건을 재구성할 수 있다.

## 6. 미결정·보류 항목

- 첫 Scout·Core 모델 제공자와 모델 식별자
- 실제 모의매매 결과에 따른 점수·검토 임계값
- 뉴스·공시는 첫 버전 AI 입력에서 제외하고 위험 경고를 위한 별도 신뢰 데이터 공급원이 정해진 뒤 추가한다.

### 6.1 결정론적 Mock 판단 1차 구현 계약

| ID | 요구사항 |
| --- | --- |
| AI-070 | 외부 모델 연결 전에는 versioned 결정론적 Mock을 사용하며 같은 불변 입력 snapshot과 정책 버전에서 같은 Scout·Core 출력을 생성한다. v1 판단은 감사 이력으로 유지하고 현재 생성 버전은 AI-090의 v2다. |
| AI-071 | 진단 판단은 `MarketStreamState.current_snapshot_id`가 가리키는 불변 snapshot만 입력으로 사용하고 품질·최신성·거래상태가 부적합하면 `RISK_BLOCK`을 기록한다. |
| AI-072 | 진단 판단은 최신 활성 실행 권한 버전을 읽어 `DISABLED`, `APPROVAL_REQUIRED`, `GUARD_BLOCKED`, `NO_ACTION` 중 하나의 실행 결과만 기록한다. |
| AI-073 | Guard와 승인 서비스가 구현되기 전에는 `AUTOMATIC`과 `MANUAL_APPROVAL` 모두 주문·승인 리소스를 생성하지 않으며 실행 결과로 미구현 안전 차단을 명시한다. |
| AI-074 | 같은 `evaluation_request_id`는 하나의 판단만 생성하며 모델·snapshot·설정 버전·구조화 출력·유효시간을 저장한다. |

### 6.2 거래 목적 판단의 실행 인계 계약

| ID | 요구사항 |
| --- | --- |
| AI-075 | 내부 scheduler가 만든 `purpose=TRADING` 판단만 [판단 실행 및 승인 오케스트레이션 명세](DECISION_EXECUTION_SPEC.md)에 인계할 수 있다. |
| AI-076 | `/decisions/mock-evaluate` 결과는 항상 `purpose=DIAGNOSTIC`이며 향후 Guard가 구현돼도 승인·주문 생성 경로에 인계하지 않는다. |
| AI-077 | 판단 저장과 실행 인계 작업 enqueue는 같은 transaction 또는 transactional outbox로 연결해 판단 유실과 중복 실행을 방지한다. |
| AI-078 | 실행 결과는 불변 판단을 수정하지 않고 별도 execution record로 연결한다. |
| AI-079 | 결정론적 Mock 모델은 내부 `TRADING` 판단에도 사용할 수 있지만 모델 식별자가 같다는 이유만으로 진단 판단을 거래 판단으로 승격하지 않는다. |
| AI-080 | 정기 AI scheduler는 API·Broker worker와 분리된 단일 장기 실행 프로세스이며 활성 감시 종목만 평가한다. 현재 구현은 `deterministic-mock-v2`를 사용한다. |
| AI-081 | scheduler가 만든 판단은 처음부터 `purpose=TRADING`으로 저장하며 공개 진단 판단을 승격하거나 복사하지 않는다. |
| AI-082 | evaluation request ID는 사용자·시장·종목·KST 분석 슬롯·모델·프롬프트 버전으로 결정론적으로 만들고 DB unique 제약으로 재시작·중복 tick을 억제한다. 같은 슬롯에서는 snapshot이 바뀌어도 최초 판단을 유지한다. |
| AI-083 | 활성 감시 종목에 현재 snapshot이 없으면 판단을 생성하지 않고 `SNAPSHOT_NOT_READY`로 scheduler 결과를 기록한다. snapshot이 존재하지만 stale·degraded이면 기존 판단 계약에 따라 `RISK_BLOCK` 판단을 저장할 수 있다. |
| AI-084 | 종목 하나의 평가 실패는 같은 tick의 다른 종목을 중단시키지 않는다. 판단·SHADOW 실행·Guard·감사는 종목별 transaction으로 commit하며 실패 종목은 rollback한다. |
| AI-085 | scheduler lease의 현재 owner만 tick을 실행한다. lease를 잃으면 새 판단 생성을 즉시 중단하며 다른 인스턴스가 만료 후 현재 슬롯을 멱등 재처리할 수 있다. |

### 6.2.1 보유 포지션 정기 판단

| ID | 요구사항 |
| --- | --- |
| AI-117 | scheduler는 평가 대상 종목에 `OPEN` 포지션이 있으면 ENTRY가 아니라 `decision_kind=POSITION` 판단을 생성한다. 같은 종목·슬롯의 ENTRY와 POSITION은 서로 다른 evaluation request ID를 사용하며 각각 한 번만 생성한다. |
| AI-118 | POSITION의 기준 판단은 서버 소유 `deterministic-position-v1` 정책을 사용한다. 공개·수동 `DIAGNOSTIC` Agent 결과는 TRADING 판단으로 복사하거나 승격하지 않는다. 활성 route가 모두 준비된 경우 scheduler만 같은 기준 판단에 결합된 별도 `TRADING_ADVISORY` run을 만들 수 있다. |
| AI-119 | POSITION 입력은 현재 market·indicator snapshot과 포지션 ID·version, 수량·평균단가·현재가·미실현손익률·고정손절 거리·고점 대비 하락률을 canonical JSON에 고정한다. 입력 생성 후 포지션이나 시세가 바뀌어도 기존 판단을 수정하지 않는다. |
| AI-120 | 데이터가 정상일 때 exit risk score는 `position-policy-v1`의 고정 가중치만 사용한다. 고정손절 도달 또는 90점 이상은 `FULL_SELL`, 70~89점은 `PARTIAL_SELL`과 `sell_ratio=0.5`, 그 외는 `HOLD`다. 모델 confidence는 수량이나 Guard 한도를 확대하지 않는다. |
| AI-121 | snapshot·지표·포지션 freshness가 불충분하거나 포지션 원가가 유효하지 않으면 `HOLD/DATA_INSUFFICIENT`로 축소한다. 데이터 오류를 매도 또는 보유 안전성의 근거로 추정하지 않으며 독립 Guard의 고정손절은 계속 동작한다. |
| AI-122 | POSITION 판단 유효시간은 최대 5분이다. 실행 시점에는 현재 포지션 version·관리수량·예약수량·최신 정상 시세와 Guard를 다시 검사하며 판단 입력의 수량을 그대로 주문수량으로 사용하지 않는다. |
| AI-123 | 단일계좌·단일사용자 MVP에서는 열린 계좌 포지션을 감시 종목 해제 여부와 무관하게 scheduler 대상에 포함한다. 활성 사용자가 둘 이상이어서 계좌 소유자를 유일하게 결정할 수 없으면 자동 귀속하지 않고 기존 사용자별 감시 대상만 처리한다. |

### 6.2.2 POSITION 외부 Agent 결합 정책 v1

외부 Agent는 주문 행동을 직접 반환하지 않는다. 서버가 검증된 `shadow_assessment`를 같은 입력의 결정론적 POSITION 판단과 비대칭적으로 결합해 별도의 최종 `TRADING` 판단을 만들 수 있다. 이 절이 결합 정책의 단일 기준이며 Agent·실행 문서는 이 절을 참조한다.

| ID | 요구사항 |
| --- | --- |
| AI-124 | scheduler만 `purpose=TRADING_ADVISORY`, `analysis_context=POSITION` run을 생성할 수 있다. 공개 진단 API는 계속 `DIAGNOSTIC`만 생성하며 advisory run을 요청하거나 기존 진단 run을 거래에 연결할 수 없다. |
| AI-125 | advisory run은 기준 `deterministic-position-v1` 판단 ID를 admission 시 고정한다. 기준 판단과 Agent run의 사용자·시장·종목·market snapshot ID·canonical position snapshot hash가 모두 같지 않으면 결합을 `FAILED_SAFE`로 종료한다. |
| AI-126 | 결합 입력은 완료된 v2 Core stage, `incomplete_roles=[]`, 모든 POSITION 필수 Scout의 `SUCCEEDED`, 허용 evidence reference 검사 통과와 `confidence >= 0.70`을 요구한다. 실패·timeout·schema 오류·`UNKNOWN`·불완전 Scout는 결합 판단을 만들지 않으며 기준 결정론 판단과 독립 Guard trigger는 그대로 유지한다. |
| AI-127 | `position-agent-fusion-v1`은 위험을 낮추지 않는 비대칭 정책이다. `HOLD_SUPPORTIVE | NEUTRAL`은 기준 행동을 유지하고, `EXIT_RISK_ELEVATED`는 기준보다 강한 경우에만 `PARTIAL_SELL(0.5)`, `EXIT_RISK_HIGH`는 기준보다 강한 경우에만 `FULL_SELL` 후보를 만든다. 기준 `FULL_SELL`을 낮추거나 LLM confidence로 수량을 확대하지 않는다. |
| AI-128 | 결합으로 행동이 상향될 때만 원본 기준 판단과 advisory run을 참조하는 새 불변 `purpose=TRADING`, `model_id=position-agent-fusion-v1` 판단을 만든다. 같은 기준 판단·run·정책 version은 최종 판단을 최대 하나만 만들며 원본 판단·Agent 출력은 수정하지 않는다. |
| AI-129 | 결합 판단은 기준 판단의 `valid_until`을 넘길 수 없다. 완료 시 이미 만료됐거나 현재 포지션 version이 바뀐 경우 새 판단을 만들지 않는다. 정상적인 최신 market snapshot 전진은 허용하되 생성된 판단은 기존 실행 권한과 Cresta Guard의 관리수량·예약수량·최신 시세·가격편차 검사를 동일하게 통과해야 한다. |
| AI-130 | 결합 실패는 `NO_ESCALATION | EXPIRED | FAILED_SAFE | ESCALATED` 상태와 안정적인 reason code로 Agent run에 기록한다. 결합 실패 때문에 기존 결정론 실행을 취소·되돌리거나 고정손절을 지연하지 않는다. |

### 6.3 Scout 입력 snapshot과 지표 기반 Mock 계약

| ID | 요구사항 |
| --- | --- |
| AI-086 | 모든 신규 진단·거래 판단은 모델 호출 전에 `scout-input-v1` 불변 입력 snapshot을 만들고 판단이 해당 입력 ID와 시장·지표 snapshot을 참조한다. |
| AI-087 | 입력 JSON은 기준시각, 품질·세션, 정규화 quote, `watch-indicators-v2` 지표와 명시적인 null 영역을 포함하며 사용자 ID·계좌번호·인증·Broker 자격증명을 포함하지 않는다. |
| AI-088 | 입력 JSON은 정렬된 key와 고정 Decimal 문자열로 canonicalize해 SHA-256 해시를 저장한다. 저장된 JSON의 재계산 해시가 다르면 판단 실행에 사용하지 않는다. |
| AI-089 | 현재 market snapshot에 연결된 v2 지표가 없거나 계산 버전이 다르면 Scout는 `UNKNOWN/DATA_INSUFFICIENT`, Core는 `RISK_BLOCK`을 반환한다. 결측 지표를 0으로 대체하지 않는다. |
| AI-090 | `deterministic-mock-v2`는 VWAP 위치, SMA5 방향, 상대 거래량, 실현 변동성, 고점 낙폭과 spread만으로 허용 reason code와 점수를 생성한다. 같은 입력 hash는 같은 출력을 만들어야 한다. |

### 6.4 다중 에이전트·외부 Provider 확장 계약

세부 역할·DAG·증거 형식은 [다중 에이전트 오케스트레이션 명세](MULTI_AGENT_ORCHESTRATION_SPEC.md), API Adapter·route·fallback은 [LLM Provider 및 Gateway 명세](LLM_PROVIDER_GATEWAY_SPEC.md)를 따른다.

| ID | 요구사항 |
| --- | --- |
| AI-091 | 기존 `scout-input-v1`은 `TECHNICAL_SCOUT`의 불변 입력으로 유지하고 복수 Scout 도입을 이유로 기존 입력 의미를 변경하지 않는다. |
| AI-092 | 외부 정보는 검증된 `EvidenceBundle`로만 뉴스·시장 Scout와 Core에 전달하고 원시 웹 문서를 Core에 직접 전달하지 않는다. |
| AI-093 | Core 호출에는 활성 DAG, role route, model profile, prompt, 입력·출력 schema version과 모든 필수 Scout stage ID를 포함한다. |
| AI-094 | 신규 provider·model·prompt·agent는 SHADOW로 시작하며 기존 실행 단계와 별개로 승인·주문을 생성할 수 없다. |
| AI-095 | Core route는 기본적으로 자동 재시도와 fallback을 허용하지 않는다. 실패·불명확·schema 오류는 신규매수 차단으로 변환한다. |
| AI-096 | provider가 구조화 출력을 지원해도 서버가 같은 JSON Schema, evidence reference와 상태별 행동을 다시 검증한다. |
| AI-097 | 모델과 Gateway가 반환한 실제 provider/model, request ID, 사용량, 지연, 비용과 fallback 경로를 비밀 제거 후 기록한다. |
| AI-098 | 복수 Scout의 confidence 평균이나 다수결만으로 행동을 정하지 않으며 Guard 우선순위와 기존 Core 행동 계약을 유지한다. |
| AI-099 | 외부 LLM이 비활성 또는 장애여도 결정론적 Mock·Guard·Broker의 현재 검증 경계를 깨지 않고 명시된 SHADOW/실패 상태를 기록한다. |
| AI-100 | 외부 Scout와 Core는 서버가 주입한 versioned 역할별 reason code allowlist만 출력할 수 있다. Provider의 structured output 성공 여부와 별도로 서버가 부분집합을 검사하며 미등록 code는 판단 근거로 저장하지 않는다. |
| AI-101 | OpenDART PRIMARY evidence는 공시 존재와 메타데이터의 검증 근거일 뿐 긍정·부정 방향을 규칙으로 추정하지 않는다. 방향 평가는 NEWS_DISCLOSURE_SCOUT가 허용 evidence를 사용해 수행한다. |

### 6.5 Agent SHADOW 판단 계약 v2

이 계약은 주문 연결이 아니라 SHADOW 판단의 의미와 평가 가능성을 완성한다. 기존 `agent-assessment-v1`, `agent-core-v1`과 `agent-dag-v3` 이력은 수정하지 않고 신규 run부터 versioned v2 계약을 사용한다.

| ID | 요구사항 |
| --- | --- |
| AI-102 | 서버는 run admission 시 열린 포지션 유무를 불변 snapshot으로 고정하고 analysis context를 `ENTRY` 또는 `POSITION`으로 결정한다. stage 실행 중 현재 포지션이 바뀌어도 이미 생성된 run의 context를 변경하지 않는다. |
| AI-103 | `ENTRY` context에서 열린 포지션이 없는 `POSITION_RISK_SCOUT`는 데이터 부족이 아니라 `NOT_APPLICABLE`, `stance=UNKNOWN`, `entry_score=null`, `exit_risk_score=null`, `OPEN_POSITION_NOT_FOUND`를 반환한다. 이 상태는 Core의 불완전 필수 역할로 계산하지 않는다. |
| AI-104 | `POSITION` context에서는 `POSITION_RISK_SCOUT`가 필수 역할이다. admission snapshot에는 포지션이 있었지만 평가 가능한 position snapshot이 누락·오염·만료된 경우에만 `INSUFFICIENT_DATA` 또는 `CONFLICTED`로 종료한다. |
| AI-105 | `agent-assessment-v2`는 `status != SUCCEEDED`이면 `entry_score`와 `exit_risk_score`를 모두 null로 강제한다. `NOT_APPLICABLE`은 성공이나 실패로 점수 통계에 포함하지 않고 별도 분모로 집계한다. |
| AI-106 | `score-policy-v1`은 0–24 `STRONGLY_ADVERSE`, 25–44 `ADVERSE`, 45–55 `MIXED`, 56–74 `SUPPORTIVE`, 75–100 `STRONGLY_SUPPORTIVE` 의미를 제공한다. 이 점수는 SHADOW 비교용이며 Guard 한도나 주문금액에 사용하지 않는다. 경계 변경은 replay 근거와 새 정책 version을 요구한다. |
| AI-107 | `agent-core-v2`의 실행 `action`은 계속 `WAIT`로 고정하고 별도 `shadow_assessment`를 기록한다. ENTRY는 `ENTRY_STRONG`, `ENTRY_SUPPORTIVE`, `NEUTRAL`, `ENTRY_ADVERSE`, `UNKNOWN`을 허용하고 POSITION은 `HOLD_SUPPORTIVE`, `NEUTRAL`, `EXIT_RISK_ELEVATED`, `EXIT_RISK_HIGH`, `UNKNOWN`을 허용한다. |
| AI-108 | 필수 역할이 불완전하거나 schema·evidence 검증이 실패하면 Core의 `shadow_assessment`는 `UNKNOWN`이어야 한다. `DIAGNOSTIC` 평가는 판단·승인·주문을 생성하지 않는다. scheduler 소유 `TRADING_ADVISORY`만 AI-124~130의 별도 서버 결합 정책에 입력될 수 있다. |
| AI-109 | 모델별 성능 비교는 `shadow_assessment`, schema 통과율, unsupported claim, latency, 비용과 판단 후 5분·10분·30분 수익률 및 MFE·MAE를 같은 입력 집합에서 측정한다. 모델 자동 교체는 구현하지 않으며 첫 거래 결합은 AI-124~130의 위험 상향 전용 정책으로 제한한다. |

### 6.6 서버 소유 판단 입력 v1

| ID | 요구사항 |
| --- | --- |
| AI-110 | 신규 `agent-dag-v5` run은 `agent-server-input-v1`을 사용한다. v4와 이전 run은 당시 입력 의미로 보존하며 worker는 이미 생성된 v4를 계속 v2 출력 계약으로 처리한다. |
| AI-111 | POSITION admission은 frozen position snapshot에 잔여 수량·평균단가·현재가, 평가금액, 원가, 미실현손익 금액·수익률, 세션 고점 대비 하락률, 추적 시작 후 경과시간, 고정 손절가격과 손절선 거리를 서버 계산값으로 저장한다. 모델이 반환한 같은 이름의 값으로 이를 덮어쓰지 않는다. |
| AI-112 | 손절 계산은 admission 당시 활성 사용자 Risk Policy를 사용하고 없으면 명시적 SAFE_DEFAULT를 사용한다. snapshot에는 정책 source, version ID 또는 null, payload hash와 계산 version을 남긴다. 종목별 전략 stop과 실제 보유 시작시각은 아직 없으므로 session high와 `Position.created_at`을 각각 명시적인 대체 provenance로 사용한다. |
| AI-113 | Position freshness는 position `updated_at`과 market snapshot 기준시각의 차이, 적용한 stale threshold와 상태를 함께 기록한다. stale·누락·hash 불일치는 점수를 만들지 않고 `INSUFFICIENT_DATA` 또는 `CONFLICTED`로 축소한다. |
| AI-114 | MARKET_SECTOR_SCOUT는 서버가 선택해 run에 고정한 `market-context-v1` snapshot만 사용한다. 유효한 snapshot이 없으면 개별 종목 quote를 시장·업종 흐름으로 오인하지 않고 `INSUFFICIENT_DATA`, null 점수와 `MARKET_DATA_INSUFFICIENT`를 반환한다. |
| AI-115 | 서버 입력의 모든 Decimal은 canonical 문자열로 직렬화하고 계산 version, source reference, observed/received/valid 시각과 freshness를 포함한다. 같은 원시 입력과 정책 version은 같은 canonical hash와 파생값을 생성해야 한다. |
| AI-116 | 신규 `agent-dag-v6`의 v2 Core 필수 Scout 목록에 `INSUFFICIENT_DATA`, `CONFLICTED`, 실패 또는 검증 불가 역할이 하나라도 있으면 Core Provider를 호출하지 않는다. 서버는 원본 Scout 상태를 바꾸거나 모델 출력을 치환하지 않고 `action=WAIT`, `shadow_assessment=UNKNOWN`, `confidence=0`, `risk_level=HIGH`와 정확한 `incomplete_roles`를 결정론적으로 기록한다. 기존 v5 run은 당시 실행 의미를 유지한다. |

## 7. Cresta v2 ENTRY Decision Architecture

### 7.1 역할 분리

Cresta v2 ENTRY 판단은 Scout Agent, 세 Decision Agent와 Deterministic Arbiter로 구성한다. Scout는 시장 상태를 평가한다. `CONSERVATIVE`, `BALANCED`, `AGGRESSIVE` Decision Agent는 동일한 불변 `DecisionContext`를 사용해 각 `PolicyProfile`에 따른 ENTRY 판단 후보를 독립적으로 생성한다. Arbiter는 세 Decision Agent 결과를 서버 소유 결정론적 정책으로 종합해 `ArbiterResult`를 생성한다.

`ArbiterResult`는 거래 판단 자체가 아니며, AI-226~235에 정의된 Decision Finalizer를 통과한 경우에만 `purpose=TRADING`, `decision_kind=ENTRY` Decision으로 확정할 수 있다.

| ID | 요구사항 |
| --- | --- |
| AI-200 | Scout는 `BUY`, 주문수량, 주문가격, Approval 또는 Broker 명령을 생성하지 않는다. |
| AI-201 | ENTRY의 투자 행동 후보는 `CONSERVATIVE`, `BALANCED`, `AGGRESSIVE` Decision Agent만 생성한다. |
| AI-202 | Decision Agent는 Approval, OrderIntent, TradingOrder 또는 Broker API를 호출하거나 생성하지 않는다. |
| AI-203 | Arbiter는 LLM Provider를 호출하지 않고 서버 소유 결정론적 정책만 사용한다. |
| AI-204 | Arbiter도 Approval, OrderIntent, TradingOrder 또는 Broker API를 직접 생성·호출하지 않는다. |

### 7.2 DecisionContext

DecisionContext는 세 Decision Agent가 공유하는 시장·증거·분석 입력만
포함하는 서버 소유 불변 객체다.

최소 다음을 참조한다.

- immutable market/scout input snapshot
- EvidenceBundle
- 완료된 Scout assessment IDs
- market context
- configuration version
- input hash
- provenance

Agent별 PolicyProfile은 DecisionContext에 포함하지 않는다.
DecisionContext는 `AgentRun`과 1:1인 별도 불변 reference manifest로 영속화하며 raw market·evidence·Scout output을 복제하지 않는다. 구체적인 identity, FK, canonical hash, freeze transaction과 보존 규칙은 [데이터베이스 및 영속성 명세](DATABASE_SPEC.md)의 `Cresta v2 ENTRY v7 영속성 계약`을 단일 기준으로 따른다.

| ID | 요구사항 |
| --- | --- |
| AI-205 | 세 Decision Agent는 동일한 `DecisionContext` ID와 hash를 사용한다. |
| AI-205A | 각 Decision Agent의 실제 입력은 동일한 `DecisionContext`와 해당 Agent에 고정된 하나의 `PolicyProfile`로 구성한다. |
| AI-205B | run admission 시 Conservative/Balanced/Aggressive 각각의 PolicyProfile version을 별도 version map으로 고정하고 실행 중 변경하지 않는다. |
| AI-206 | `DecisionContext`가 고정된 뒤 Decision Agent는 DB, 최신 시세, 웹 또는 외부 API를 독자적으로 다시 조회하지 않는다. |
| AI-207 | 한 Decision Agent가 다른 Decision Agent의 출력 또는 prompt를 입력으로 받을 수 없다. |
| AI-208 | `DecisionContext`의 필수 입력이 누락·만료·상충되거나 검증에 실패하면 `BUY` 후보를 생성하지 않는다. |

### 7.3 Decision Agent 출력

Decision Agent 출력은 model-owned `decision-agent-model-output-v1`과 server-owned
`decision-agent-result-v1`을 분리한다. exact field set, provenance, 상태별 점수와
failure mapping은 7.13.3~7.13.5를 단일 기준으로 적용한다.

`RISK_BLOCK`은 Decision Agent 행동이 아니다. Decision Agent는 투자 의견만 만들고 실행 위험 차단은 Guard가 담당한다.

| ID | 요구사항 |
| --- | --- |
| AI-209 | Decision Agent의 `action`은 `BUY | WAIT | REJECT | UNKNOWN`만 허용한다. |
| AI-210 | `status != SUCCEEDED`이면 `BUY`를 허용하지 않는다. 점수와 confidence의 상태별 필수·null 조건은 `decision-agent-result-v1` schema에 고정한다. |
| AI-211 | confidence는 주문금액·Guard 한도·수량을 변경하는 데 사용할 수 없다. |
| AI-212 | reason/evidence reference는 `DecisionContext`가 허용한 목록의 부분집합이어야 한다. |

### 7.4 PolicyProfile

PolicyProfile은 system-owned ConfigurationVersion이며 exact identity와
`policy-schema-v1.policy_parameters` field set은 7.13.2를 단일 기준으로 적용한다.
PromptProfile과 Route는 PolicyProfile과 별도 provenance로 고정하며 threshold를
Prompt에 복제하지 않는다.

| ID | 요구사항 |
| --- | --- |
| AI-213 | 세 Decision Agent의 차이는 입력 데이터가 아니라 versioned `PolicyProfile`이다. |
| AI-214 | 동일 Agent type의 정책 변경은 새로운 policy version으로 기록한다. |
| AI-215 | 과거 Decision Agent 결과는 이후 `PolicyProfile` 변경으로 재해석하지 않는다. |

정확한 profile별 threshold는 Phase 2 이후 fixture와 SHADOW 평가 근거로 확정하며 Phase 1에서 임의 숫자를 정하지 않는다.

### 7.5 역할 매핑과 실패 정책

| ID | 요구사항 |
| --- | --- |
| AI-216 | Decision Agent의 최종 LLM 실패를 `deterministic-mock-v2` BUY 판단으로 대체하지 않는다. |
| AI-217 | 필수 Decision Agent 결과를 모두 확보하지 못한 run에서는 Arbiter가 `BUY`를 생성할 수 없다. |
| AI-218 | 기존 Guard 기반 손절·비상 안전 규칙은 Decision Agent 장애와 독립적으로 계속 동작한다. |
| AI-219 | runtime role과 `DecisionAgentResult.agent_type`은 `CONSERVATIVE_DECISION → CONSERVATIVE`, `BALANCED_DECISION → BALANCED`, `AGGRESSIVE_DECISION → AGGRESSIVE`로 일대일 매핑한다. `ENTRY_ARBITER`는 Decision Agent가 아니므로 `agent_type`을 가지지 않는다. |

### 7.6 Arbiter 계약

```yaml
arbiter_result:
  schema_version: entry-consensus-v1
  decision_context_id:
  decision_context_hash:
  action: BUY | WAIT | REJECT | UNKNOWN
  policy_version: consensus-policy-v1
  input_result_ids: []
  input_results: []
  decision_pattern:
  reason_codes: []
  valid_until:
```

| ID | 요구사항 |
| --- | --- |
| AI-220 | Arbiter는 정확히 한 개씩의 `CONSERVATIVE`, `BALANCED`, `AGGRESSIVE` 결과만 입력받는다. |
| AI-221 | Arbiter 결과는 모든 입력 결과 ID와 consensus policy version을 보존한다. |
| AI-222 | 동일 입력과 동일 consensus policy version의 Arbiter 결과는 항상 동일해야 한다. |
| AI-223 | Arbiter는 Agent confidence 평균이나 단순 총점만으로 `BUY`를 결정하지 않는다. |

### 7.7 `consensus-policy-v1`

`BUY`는 세 Agent가 모두 `SUCCEEDED`이고 `BALANCED=BUY`이며 `CONSERVATIVE` 또는 `AGGRESSIVE` 중 하나 이상이 `BUY`이고 어느 Agent도 `REJECT`가 아닐 때만 생성한다. `REJECT`가 하나면 기본 `WAIT`, 둘 이상이면 `REJECT`다. 필수 Agent 실패 또는 `UNKNOWN`은 `UNKNOWN`이다. 나머지 조합은 `WAIT`다.

| Conservative | Balanced | Aggressive | 결과 |
| --- | --- | --- | --- |
| BUY | BUY | BUY | BUY |
| BUY | BUY | WAIT | BUY |
| WAIT | BUY | BUY | BUY |
| BUY | WAIT | BUY | WAIT |
| WAIT | BUY | WAIT | WAIT |
| 하나의 REJECT 포함 | 임의 | 임의 | WAIT |
| 둘 이상의 REJECT 포함 | 임의 | 임의 | REJECT |
| 필수 실패 또는 UNKNOWN 포함 | 임의 | 임의 | UNKNOWN |

| ID | 요구사항 |
| --- | --- |
| AI-224 | `consensus-policy-v1`은 위 조건과 truth table을 적용하며, 중첩되는 행은 `필수 실패 또는 UNKNOWN > 둘 이상의 REJECT > 하나의 REJECT > BUY 조건 > WAIT` 순서로 판정한다. |
| AI-225 | Arbiter는 자체 confidence를 새로 계산하지 않으며 최종 ENTRY Decision의 실행 가능 여부는 기존 Execution Orchestrator와 Guard가 별도로 판정한다. |

### 7.8 ENTRY Decision Finalization

ArbiterResult는 직접 Execution 입력이 아니다.

서버 소유 ENTRY Decision Finalizer가 검증된 ArbiterResult를
기존 불변 Decision 계약으로 변환한 경우에만
Execution Orchestrator에 전달할 수 있다.

| ID | 요구사항 |
| --- | --- |
| AI-226 | ArbiterResult 자체는 Execution Orchestrator의 입력으로 사용할 수 없다. |
| AI-227 | 서버 소유 Decision Finalizer만 검증된 ArbiterResult를 `purpose=TRADING`, `decision_kind=ENTRY` Decision으로 확정할 수 있다. |
| AI-228 | 최종 Decision은 DecisionContext, 세 DecisionAgentResult, ArbiterResult, consensus policy version과 provenance를 추적할 수 있어야 한다. |
| AI-229 | 동일 run·DecisionContext·ArbiterResult·policy version 조합에서는 최종 ENTRY Decision을 최대 하나만 생성한다. |
| AI-230 | Finalization 중 provenance 불일치, 만료, context hash 불일치 또는 DB 실패가 발생하면 TRADING Decision을 만들지 않고 fail-closed 한다. |
| AI-231 | Finalizer는 Arbiter의 action을 재해석하거나 confidence를 새로 계산하지 않는다. |
| AI-232 | 신규 agent-dag-v7의 최초 완결 decision slice는 SHADOW/DIAGNOSTIC으로 실행하며 ArbiterResult까지만 생성한다. 이에 앞선 Phase 4 upstream partial slice는 AI-251~255에 따라 DecisionContext Freeze에서 checkpoint한다. |
| AI-233 | SHADOW/DIAGNOSTIC v7 run은 `purpose=TRADING` Decision, Approval, OrderIntent 또는 TradingOrder를 생성할 수 없다. |
| AI-234 | DIAGNOSTIC ArbiterResult를 이후 TRADING Decision으로 승격·복사해 사용할 수 없다. |
| AI-235 | v7 TRADING finalization은 별도 서버 소유 activation gate와 관련 실행 안전 요구사항 시험을 통과한 뒤에만 허용한다. |

### 7.9 v7 Scheduler 책임

| ID | 요구사항 |
| --- | --- |
| AI-236 | Cresta v2 ENTRY Scheduler는 평가 run admission과 versioned pipeline 시작만 담당한다. Scheduler 자체는 `BUY | WAIT | REJECT | UNKNOWN` 판단 규칙, Decision Agent 정책, consensus policy 또는 Finalizer 정책을 소유하지 않는다. |
| AI-237 | v7 활성화 후 Scheduler는 `deterministic-mock-v2`의 score 또는 threshold를 production ENTRY BUY 결정에 사용할 수 없다. Scheduler는 versioned ENTRY pipeline을 호출하고 그 결과만 기록·인계한다. |

### 7.10 v7 TRADING Activation Gate

v7 TRADING Activation Gate는 SHADOW/DIAGNOSTIC으로 검증된 `agent-dag-v7` 결과가 실제 `purpose=TRADING`, `decision_kind=ENTRY` Decision으로 finalization될 수 있는지를 제어하는 서버 소유 admission gate다.

이 gate는 주문 실행 여부를 제어하는 `ExecutionStage`와 별개다.

```text
TRADING Activation Gate: ArbiterResult → Decision Finalizer 진입 권한
ExecutionStage:           TRADING Decision → Approval / Order 실행 권한
```

| ID | 요구사항 |
| --- | --- |
| AI-238 | v7 TRADING activation gate가 `OPEN`이 아니면 Decision Finalizer는 `purpose=TRADING`, `decision_kind=ENTRY` Decision을 생성할 수 없다. |
| AI-239 | activation gate 개방에는 활성 DAG version, consensus policy version, Decision Agent schema·prompt·PolicyProfile·model/route version과 해당 조합에 대한 필수 안전시험 근거가 필요하다. |
| AI-240 | 필수 안전시험 중 하나라도 미통과, 누락, 현재 활성 version과 불일치 또는 명시된 유효성 조건을 충족하지 못하면 activation gate를 열 수 없다. |
| AI-241 | gate 개방 판단은 하나의 일관된 version snapshot을 기준으로 수행한다. 검증 중 활성 DAG·consensus policy·Decision Agent schema·prompt·PolicyProfile·model/route version이 변경되면 개방을 거부하고 새 snapshot으로 다시 검증한다. |
| AI-242 | activation gate 개방은 `ExecutionStage`를 변경하거나 주문 권한을 부여하지 않는다. Finalizer가 생성한 TRADING Decision도 별도로 현재 ExecutionStage와 Guard를 통과해야 한다. |

### 7.11 v7 persistence와 lineage 계약

이 절은 AI-200~242의 행동 의미를 변경하지 않고 Phase 2에서 확정한 persistence mapping을 연결한다. 물리 컬럼·제약·canonical serialization의 단일 기준은 [데이터베이스 및 영속성 명세](DATABASE_SPEC.md)의 DB-157~182다.

| ID | 요구사항 |
| --- | --- |
| AI-243 | v7 evaluation root는 새 DecisionRun이 아니라 기존 AgentRun이며 `agent-dag-v7 + analysis_context=ENTRY`로 식별한다. 첫 slice는 admission부터 `DIAGNOSTIC`이고 production은 activation gate를 통과한 scheduler가 admission부터 `TRADING`으로 생성한다. DIAGNOSTIC run과 ArbiterResult의 승격·복사는 금지한다. |
| AI-244 | DecisionContext는 AgentRun당 최대 하나인 별도 불변 reference manifest다. 필수 Scout와 Candidate Audit의 명시적 terminal result, 같은-run EvidenceBundle과 snapshot provenance를 검증한 freeze transaction이 commit된 뒤에만 Decision Agent가 실행될 수 있다. stage 부재와 허용된 `NOT_APPLICABLE` result를 구분한다. |
| AI-245 | run admission은 Conservative/Balanced/Aggressive PolicyProfile의 정확히 세 ACTIVE ConfigurationVersion ID/version/hash를 DecisionContext 밖의 별도 canonical map으로 고정한다. 실행 중 ACTIVE policy 변경은 기존 run과 결과의 의미를 변경하지 않는다. |
| AI-246 | DecisionAgentResult는 각 Decision Agent AgentStageRun의 server-owned `output_json/output_hash`이며 별도 결과 table을 만들지 않는다. 결과는 같은 DecisionContext ID/hash와 해당 역할의 PolicyProfile identity/hash를 보존하고 stage role과 agent type을 일대일 검증한다. |
| AI-247 | ArbiterResult는 Provider route·prompt·model·invocation이 없는 `ENTRY_ARBITER` AgentStageRun의 `output_json/output_hash`다. 정확한 C/B/A result stage ID/hash와 consensus policy version을 보존하며 별도 ArbiterResult table을 만들지 않는다. |
| AI-248 | finalized v7 ENTRY Decision은 nullable source AgentRun FK, exact ENTRY_ARBITER stage FK와 검증한 output hash를 all-or-none으로 보존한다. 동일 source run과 source Arbiter stage는 finalized Decision을 각각 최대 하나만 가지며 legacy Decision은 source lineage를 backfill하지 않는다. |
| AI-249 | Activation Gate는 system-owned ConfigurationVersion manifest로 영속화한다. OPEN admission에는 test ID·requirement·code/build identity·spec version·실행·유효시각·evidence reference/hash를 가진 전체 safety evidence set 검증이 필요하며 단순 `passed=true`는 충분하지 않다. |
| AI-250 | v7 AgentRun ID가 evaluation lineage root이고 Decision의 source FK가 Execution·Order 방향의 역추적을 연결한다. 별도 상위 correlation ID나 DecisionRun·EvidenceSet을 만들지 않으며 operational correlation과 finalization idempotency를 구분한다. |

#### 7.11.1 Activation acceptance set

첫 v7 TRADING activation 후보의 필수 시험 근거는 최소 다음 범주를 포함한다. 구체적인 시험 ID 목록은 `TEST_PLAN.md`의 Cresta v2 ENTRY activation acceptance set을 따른다.

1. 동일 DecisionContext와 Decision Agent 독립성: `T-V2-AI-001`~`003`, `T-V2-MAO-002`
2. Decision Agent schema validation: `T-V2-AI-005`
3. Decision Agent 실패의 fail-closed 처리: `T-V2-AI-007`~`008`
4. Deterministic Arbiter truth table과 canonical contract: `T-V2-ARB-001`, `003`~`004`, `014`
5. Arbiter의 Provider invocation·외부 권한 0건: `T-V2-ARB-002`, `013`
6. Arbiter 구조 검증·expiry·fencing·멱등성과 Finalizer provenance: `T-V2-ARB-005`~`012`, `015`, `T-V2-AI-010`~`013`, `T-V2-AI-016`, `T-V2-DB-FIN-001`~`013`
7. SHADOW의 거래 resource 0건: `T-V2-MAO-004`, `T-V2-EXE-004`
8. DIAGNOSTIC 결과 승격 금지: `T-V2-MAO-005`
9. activation gate admission·freeze·live revalidation·audit: `T-V2-MAO-006`, `T-V2-ACT-002`~`012`
10. ExecutionStage/NO_ACTION 안전 회귀: `T-V2-EXE-001`, `T-V2-EXE-004`~`012`
11. Approval stage downgrade 차단: `T-V2-EXE-002`
12. Broker 송신 직전 global gate 검증: `T-V2-EXE-003`, `T-V2-EXE-006`, `T-V2-EXE-009`
13. DecisionContext identity·same-run·freeze ordering: `T-V2-DB-CTX-001`~`006`
14. Policy map·role·Finalizer source/API/lifecycle persistence: `T-V2-DB-POL-001`~`002`, `T-V2-DB-ROLE-001`~`002`, `T-V2-DB-FIN-001`~`013`, `T-V2-FIN-API-001`~`004`, `T-V2-FIN-LIFE-001`~`006`
15. Activation manifest·TOCTOU와 migration compatibility: `T-V2-DB-GATE-001`~`004`, `T-V2-DB-MIG-001`~`010`
16. v7 upstream stage set·checkpoint·legacy finalization: `T-V2-UPSTREAM-001`~`010`
17. `scout-input-v2` canonical input과 validity: `T-V2-INPUT-V2-001`~`004`
18. 역할별 Scout input hash 재현성: `T-V2-SCOUT-HASH-001`~`002`
19. v7 Evidence freshness fail-closed: `T-V2-EVIDENCE-V7-001`~`002`

Gate 영속 방식은 DB-177~180으로 확정됐다. API endpoint와 service/class 이름은 후속 구현 단계에서 이 계약을 축소하지 않는 범위로 결정한다.

### 7.12 v7 upstream Scout 입력 계약

Phase 4는 최종 `agent-dag-v7`의 Intel → Verify → 네 Scout → Candidate Audit → DecisionContext Freeze 구간만 `DIAGNOSTIC`으로 구현·검증한다. 이 upstream slice는 새 DAG version이나 완결된 7-stage DAG가 아니며, Decision Agent·Arbiter·Finalizer 또는 거래 실행 권한을 포함하지 않는다.

| ID | 요구사항 |
| --- | --- |
| AI-251 | v7 upstream의 네 Scout는 기존 `AgentAssessmentV2` 행동·상태·reason/evidence 검증 계약을 재사용하고 서버 소유 불변 `scout-input-v2`를 공통 base input으로 사용한다. 기존 `scout-input-v1`과 v1~v6 결과의 의미는 변경하지 않는다. |
| AI-252 | `scout-input-v2`는 user identity, `DIAGNOSTIC/ENTRY`, market·symbol, MarketSnapshot·IndicatorSnapshot·선택적 MarketContextSnapshot의 identity/hash/version/quality/validity provenance, 공통 configuration provenance, server-input policy version, `observed_at`, `valid_until`을 canonical JSON/hash로 고정한다. user identity는 내부 UUID만 허용하며 계좌번호·로그인 ID·인증·Broker 비밀을 포함하지 않는다. |
| AI-253 | `scout-input-v2`에는 EvidenceBundle, Scout output, DecisionContext, PolicyProfile, Decision Agent·Arbiter 또는 주문·실행 데이터를 포함하지 않는다. PolicyProfile 변경은 같은 source material의 Scout input JSON/hash를 변경하지 않아야 한다. |
| AI-254 | `scout-input-v2.valid_until`은 MarketSnapshot/session, Indicator input, 존재하는 MarketContext와 적용 가능한 공통 configuration의 versioned 유효 경계 중 최솟값이다. source가 직접 validity를 제공하지 않으면 명시된 server-input policy version의 규칙만 사용하고 임의 TTL을 만들지 않으며, 필수 경계를 계산할 수 없거나 이미 만료됐으면 admission을 거부한다. |
| AI-255 | `ENTRY`에 열린 포지션이 없더라도 Position Risk stage를 생략하지 않는다. Provider 호출 없이 `AgentAssessmentV2(status=NOT_APPLICABLE, stance=UNKNOWN, entry_score=null, exit_risk_score=null, reason_codes=[OPEN_POSITION_NOT_FOUND])`를 서버가 기록하며 stage 부재를 같은 의미로 해석하지 않는다. |

### 7.13 Decision Agent runtime 계약

이 절은 Phase 7 Decision Agent 구현의 단일 AI 행동 계약이다. Phase 3A~6의 persistence, DecisionContext와 Scout 결과를 변경하지 않으며 `CONSERVATIVE_DECISION`, `BALANCED_DECISION`, `AGGRESSIVE_DECISION`에만 적용한다.

#### 7.13.1 canonical Provider input

서버는 committed `DecisionContext`가 참조하는 불변 row를 Provider 호출 전에 역참조해 다음 exact top-level 구조의 `decision-agent-input-v1`을 만든다. Provider는 ID를 사용해 DB, API, 웹 또는 다른 도구를 조회하지 않는다.

```yaml
decision_agent_input:
  schema_version: decision-agent-input-v1
  decision_context:
    decision_context_id:
    decision_context_hash:
    run_id:
    analysis_context: ENTRY
    purpose: DIAGNOSTIC
    decision_input:
      snapshot_id:
      input_hash:
      schema_version: scout-input-v2
      material: {}
    evidence_bundle:
      bundle_id:
      bundle_hash:
      policy_version:
      state:
      verified_evidence: []
    scout_results: []
    candidate_audit:
      stage_run_id:
      output_hash:
      result: {}
    market_context: null
    observed_at:
    frozen_at:
    valid_until:
  agent:
    role: CONSERVATIVE_DECISION | BALANCED_DECISION | AGGRESSIVE_DECISION
    agent_type: CONSERVATIVE | BALANCED | AGGRESSIVE
  policy_profile:
    configuration_version_id:
    category:
    sequence:
    agent_type:
    payload_hash:
    schema_version: policy-schema-v1
    policy_parameters: {}
  allowed_evidence_refs: []
  valid_until:
```

`decision_input.material`은 저장된 canonical `scout-input-v2`를 역직렬화한 값에서 Provider 판단에 필요 없는 내부 `user_id`만 제거한 representation이다. 원본 snapshot과 hash는 변경하지 않는다. `verified_evidence[]`는 evidence ID 오름차순이며 각 원소의 exact field는 `evidence_id`, `source_type`, `source_tier`, `source_name`, `title`, decoded canonical `facts`, `content_hash`, `extraction_method`, nullable `published_at`, nullable `event_at`, `received_at`이다. URL은 포함하지 않는다. `scout_results[]`는 `TECHNICAL_SCOUT`, `NEWS_DISCLOSURE_SCOUT`, `MARKET_SECTOR_SCOUT`, `POSITION_RISK_SCOUT` 순서이고 각 원소는 `role`, `stage_run_id`, `output_hash`, decoded canonical `result`다. `market_context`는 참조가 없으면 null이고, 있으면 `snapshot_id`, `payload_hash`, `quality`, `observed_at`, `received_at`, `valid_until`, decoded canonical `material`을 가진다. Context `observed_at`은 frozen DecisionInputSnapshot의 `observed_at`, `frozen_at`은 저장된 DecisionContext freeze 시각이다.

canonical JSON은 UTF-8, `ensure_ascii=false`, key 사전순, 불필요한 공백 없는 separator를 사용한다. evidence는 ID, Scout는 위 고정 role 순서, reason/evidence ref는 중복 제거 후 문자열 오름차순이다. timestamp는 UTC ISO-8601 `+00:00`로 정규화하고 0인 소수 초는 생략한다. Decimal은 지수 표기 없는 base-10 문자열, null은 JSON null로 표현하며 JSON float로 금액·비율을 직렬화하지 않는다. 같은 Context와 같은 참조 row는 같은 resolved Context material을 만든다.

#### 7.13.2 PolicyProfile semantic schema

`policy-schema-v1.policy_parameters`의 exact field set은 다음과 같고 unknown/missing key를 거부한다. 실제 C/B/A 값은 ConfigurationVersion data이며 이 명세는 default나 profile별 숫자를 정하지 않는다.

| Field | Type/range | 의미 |
| --- | --- | --- |
| `minimum_confidence` | Decimal string, `0..1` | BUY 후보에 필요한 최소 모델 confidence |
| `minimum_entry_score` | integer, `0..100` | BUY 후보에 필요한 최소 entry score |
| `risk_tolerance_score` | integer, `0..100` | 허용 가능한 최대 risk score |
| `uncertainty_tolerance_ratio` | Decimal string, `0..1` | 허용 가능한 불확실성 비율 |
| `momentum_deterioration_tolerance_pct` | Decimal string, `0..100` | 허용 가능한 momentum deterioration percentage |
| `drawdown_tolerance_pct` | Decimal string, `0..100` | 허용 가능한 drawdown percentage |

Decimal string은 부호 없는 지수 비표기 canonical base-10이고 `-0`을 허용하지 않는다. Prompt에는 이 숫자를 복제하지 않는다. role mapping은 `CONSERVATIVE_DECISION→CONSERVATIVE`, `BALANCED_DECISION→BALANCED`, `AGGRESSIVE_DECISION→AGGRESSIVE`이며 다른 agent type의 profile은 fail-closed 한다. 실행은 admission 당시 map의 ConfigurationVersion ID/hash만 resolve하고 hash가 일치하는 `SUPERSEDED` historical profile을 허용한다.

#### 7.13.3 model-owned output과 server-owned result

Provider가 생성하는 `decision-agent-model-output-v1`은 다음 model-owned field만 허용한다. `status`는 `SUCCEEDED | INSUFFICIENT_DATA | CONFLICTED`만 허용하며 `TIMED_OUT | FAILED | INVALID_OUTPUT`은 서버가 runtime outcome으로만 생성한다.

```yaml
decision_agent_model_output:
  schema_version: decision-agent-model-output-v1
  status: SUCCEEDED | INSUFFICIENT_DATA | CONFLICTED
  action: BUY | WAIT | REJECT | UNKNOWN
  confidence: 0.0..1.0
  entry_score: 0..100 | null
  risk_score: 0..100 | null
  reason_codes: []
  positive_evidence_refs: []
  negative_evidence_refs: []
```

검증 후 서버는 다음 exact field set의 `decision-agent-result-v1`을 canonicalize해 해당 `AgentStageRun.output_json/output_hash`에 저장한다. 별도 table을 만들지 않는다. 기존 `model_id`는 삭제하지 않고 `requested_model_profile_id`와 같은 내부 ModelProfile UUID로 정의한다.

```yaml
decision_agent_result:
  schema_version: decision-agent-result-v1
  stage_run_id:
  role: CONSERVATIVE_DECISION | BALANCED_DECISION | AGGRESSIVE_DECISION
  decision_context_id:
  decision_context_hash:
  agent_type: CONSERVATIVE | BALANCED | AGGRESSIVE
  status: SUCCEEDED | INSUFFICIENT_DATA | CONFLICTED | TIMED_OUT | FAILED | INVALID_OUTPUT
  action: BUY | WAIT | REJECT | UNKNOWN
  confidence: 0.0..1.0
  entry_score: 0..100 | null
  risk_score: 0..100 | null
  reason_codes: []
  positive_evidence_refs: []
  negative_evidence_refs: []
  policy_profile_id:
  policy_profile_hash:
  policy_profile_version:
  policy_profile_category:
  route_id:
  route_version:
  route_version_hash:
  prompt_profile_id:
  prompt_version:
  prompt_hash:
  model_id:
  requested_model_profile_id:
  actual_provider: null
  actual_model: null
  fallback_used: false
  valid_until:
```

`policy_profile_id`는 frozen `configuration_version_id`, `policy_profile_version`은 ConfigurationVersion `sequence`다. `model_id`와 `requested_model_profile_id`는 같은 requested ModelProfile UUID다. Decision role은 validated PromptProfile을 필수로 가지므로 prompt provenance는 null일 수 없다. `actual_provider`와 `actual_model`은 Provider 호출 전에 실패한 server-owned result에서만 null일 수 있다. fallback이 실제 성공·실패 attempt에 사용됐으면 `fallback_used=true`이며 invocation attempt들은 기존 LlmInvocation provenance에 보존한다.

#### 7.13.4 status, score, evidence와 reason

| Result status | 허용 action | confidence | entry/risk score | AgentStageRun.state |
| --- | --- | --- | --- | --- |
| `SUCCEEDED` | `BUY | WAIT | REJECT` | required `0..1` | 각각 available하면 `0..100`, unavailable이면 null | `SUCCEEDED` |
| `INSUFFICIENT_DATA` | `UNKNOWN` | `0.0` | 둘 다 null | `INSUFFICIENT_DATA` |
| `CONFLICTED` | `UNKNOWN` | `0.0` | 둘 다 null | `CONFLICTED` |
| `TIMED_OUT` | `UNKNOWN` | `0.0` | 둘 다 null | `TIMED_OUT` |
| `FAILED` | `UNKNOWN` | `0.0` | 둘 다 null | `FAILED` |
| `INVALID_OUTPUT` | `UNKNOWN` | `0.0` | 둘 다 null | `INVALID_OUTPUT` |

`SUCCEEDED+UNKNOWN`, non-success의 BUY/WAIT/REJECT, `RISK_BLOCK`은 금지한다. Worker는 BUY/WAIT/REJECT threshold를 하드코딩하거나 모델이 만들지 않은 score를 생성하지 않는다.

positive/negative evidence namespace는 Context가 참조하는 frozen EvidenceBundle의 `evidence_ids_json`에 포함된 VERIFIED EvidenceItem ID뿐이다. 두 목록은 같은 allowlist의 부분집합이어야 하며 서로 겹칠 수 없다. Scout stage ID/hash, Candidate Audit candidate, UNRATED item, URL/title, cross-run item과 bundle 밖 ID는 거부한다.

세 역할은 다음 공통 model reason allowlist만 사용한다: `CONTEXT_SUPPORTS_ENTRY`, `CONTEXT_DOES_NOT_SUPPORT_ENTRY`, `ENTRY_CRITERIA_NOT_MET`, `RISK_EXCEEDS_POLICY`, `UNCERTAINTY_EXCEEDS_POLICY`, `EVIDENCE_SUPPORTS_ENTRY`, `EVIDENCE_OPPOSES_ENTRY`, `EVIDENCE_MIXED`, `EVIDENCE_INSUFFICIENT`, `SCOUT_SIGNALS_SUPPORTIVE`, `SCOUT_SIGNALS_ADVERSE`, `SCOUT_SIGNALS_CONFLICTED`, `MOMENTUM_WITHIN_POLICY`, `MOMENTUM_DETERIORATION_EXCEEDS_POLICY`, `DRAWDOWN_WITHIN_POLICY`, `DRAWDOWN_EXCEEDS_POLICY`. Scout-only, 주문·실행 또는 unknown reason은 허용하지 않는다.

server-owned failure reason allowlist는 `DECISION_AGENT_PROVIDER_TIMEOUT`, `DECISION_AGENT_PROVIDER_ERROR`, `DECISION_AGENT_OUTPUT_SCHEMA_INVALID`, `DECISION_AGENT_REASON_NOT_ALLOWED`, `DECISION_AGENT_EVIDENCE_NOT_ALLOWED`, `DECISION_AGENT_INPUT_PROVENANCE_INVALID`, `DECISION_AGENT_CONTEXT_EXPIRED`, `DECISION_AGENT_POLICY_PROVENANCE_INVALID`, `DECISION_AGENT_ROUTE_PROVENANCE_INVALID`, `DECISION_AGENT_CLAIM_OUTCOME_UNKNOWN`이다. model output은 이 failure reason을 생성할 수 없다.

#### 7.13.5 failure, validity와 권한

Provider timeout은 `TIMED_OUT`, Provider/limit/credential/final fail-stop은 `FAILED`, malformed/schema/reason/evidence 오류는 `INVALID_OUTPUT`, Context·Policy·route·prompt·stage input provenance 불일치는 `CONFLICTED`, completion 시 Context expiry는 `TIMED_OUT`으로 기록한다. 모든 경우 action UNKNOWN, confidence 0.0, null score와 완전한 server-owned provenance를 가진 canonical result/hash를 남긴다. stale fencing token의 worker는 어떤 result도 기록할 수 없고 권위 recovery/completion transaction만 `TIMED_OUT/DECISION_AGENT_CLAIM_OUTCOME_UNKNOWN` result를 기록한다.

Result `valid_until`은 Context `valid_until`과 같다. Policy ACTIVE/SUPERSEDED 또는 route/prompt lifecycle timestamp는 runtime TTL이 아니다. Provider 완료 직전 별도 completion transaction이 stage/run을 다시 잠그고 Context existence/same-run/hash/expiry, frozen Policy ID/hash, stage input hash, route/prompt identity/hash와 current fencing token을 재검증한다. 하나라도 실패하면 SUCCEEDED를 저장하지 않는다.

Decision Agent에는 web search, URL/live market/news/DART/Broker/position fetch, filesystem/code execution, Approval/Order/Broker tool을 제공하지 않는다. Decision Agent는 Decision, Approval, Order, ArbiterResult를 생성하거나 ExecutionStage/Activation Gate를 변경할 권한이 없다.

| ID | 요구사항 |
| --- | --- |
| AI-256 | 세 Decision Agent Provider input은 위 exact `decision-agent-input-v1`이며 동일 DecisionContext를 server-owned dereference한 동일 context material과 자기 frozen PolicyProfile만 포함한다. |
| AI-257 | resolved Context material과 canonical JSON/list/time/Decimal/null 규칙은 결정론적이며 Provider가 ID로 추가 조회하지 않는다. |
| AI-258 | `policy-schema-v1.policy_parameters`는 위 여섯 required field의 type/range를 strict 검증하고 default, unknown key, Prompt threshold 복제를 허용하지 않는다. |
| AI-259 | role↔agent type↔frozen PolicyProfile은 일대일이며 cross-role policy와 현재 ACTIVE policy 재선택을 거부한다. |
| AI-260 | raw `decision-agent-model-output-v1`과 server-owned `decision-agent-result-v1`을 분리하고 server-owned provenance를 모델이 생성하지 못하게 한다. |
| AI-261 | status/action/confidence/score와 stage state는 위 matrix를 정확히 따르며 failure 또는 invalid output을 BUY/WAIT/REJECT로 축소하지 않는다. |
| AI-262 | positive/negative evidence ref는 frozen verified EvidenceBundle ID의 서로 겹치지 않는 부분집합이고 Decision Agent reason은 위 allowlist만 사용한다. |
| AI-263 | 모든 권위 terminal Decision Agent stage는 canonical structured Result/hash를 가지며 Provider·validation·provenance·expiry failure도 UNKNOWN fail-closed result로 남긴다. |
| AI-264 | Result validity는 Context를 초과하지 않고 completion transaction이 Context·Policy·route·input hash·fencing을 재검증한 뒤에만 SUCCEEDED를 commit한다. |
| AI-265 | Decision Agent는 frozen input evaluator일 뿐 외부 acquisition과 거래·Arbiter 권한이 없다. |

### 7.14 ENTRY_ARBITER runtime 계약

`ENTRY_ARBITER`는 LLM Agent가 아니라 Provider·network·도구가 없는 서버 소유
deterministic internal stage다. Arbiter는 C/B/A의 검증된 `status`와 `action`,
`consensus-policy-v1`만 사용하며 PolicyProfile, confidence, score, Agent reason 또는
현재 시장을 다시 해석하지 않는다.

#### 7.14.1 canonical input

`entry-arbiter-input-v1`의 exact top-level field는 다음과 같다.

```yaml
entry_arbiter_input:
  schema_version: entry-arbiter-input-v1
  decision_context_id:
  decision_context_hash:
  policy_version: consensus-policy-v1
  input_results:
    - role:
      agent_type:
      stage_run_id:
      output_hash:
      status:
      action:
  valid_until:
```

`input_results`는 정확히 세 항목이고 `CONSERVATIVE`, `BALANCED`, `AGGRESSIVE`
순서다. role/type은 AI-219의 일대일 mapping을 따른다. PolicyProfile payload,
confidence, entry/risk score, Agent reason, Route, Prompt, Model, raw Provider response와
현재 시장 data는 포함하지 않는다. 전체 canonical JSON의 UTF-8 bytes를 lowercase
SHA-256으로 계산한 값이 `ENTRY_ARBITER AgentStageRun.input_hash`다. C/B/A 완료나 DB
조회 순서는 canonical input과 hash에 영향을 주지 않는다.

materialization 전에는 세 stage가 같은 AgentRun과 DecisionContext ID/hash에 속하고
역할별 정확히 하나이며 terminal structured `decision-agent-result-v1`과
`output_hash`를 가지는지 검증한다. canonical output hash, strict schema,
stage state/result status, role/agent type, frozen Policy provenance와 Context/Result
`valid_until`도 다시 검증한다. `action`만으로 consensus하지 않는다.

#### 7.14.2 정상 non-success와 구조 오류

schema-valid Result의 `INSUFFICIENT_DATA | CONFLICTED | TIMED_OUT | FAILED |
INVALID_OUTPUT`은 action `UNKNOWN`인 정상 consensus input이다. 이 경우 Arbiter는
정상 실행되고 `SUCCEEDED + MANDATORY_UNKNOWN/UNKNOWN` ArbiterResult를 남긴다.

stage·output·hash 누락, duplicate role, hash/schema/role/type/state/Policy provenance
불일치, cross-run/context 또는 materialization 전 만료는 consensus input이 아니다.
reconciliation은 `ENTRY_ARBITER`를 만들지 않고 fail-closed하며 현재 설정으로
repair하거나 `MANDATORY_UNKNOWN`으로 축소하지 않는다. materialization 뒤 integrity
불일치는 stage를 `CONFLICTED`, 실행 전 또는 completion 시 만료는 `TIMED_OUT`, pure
evaluator의 예상치 못한 내부 실패는 `FAILED`로 끝내며 세 경우 모두 output JSON/hash를
남기지 않는다. 새 stage state는 만들지 않는다.

#### 7.14.3 consensus pattern과 reason

precedence와 exact mapping은 다음과 같다.

| 순서 | decision_pattern | 조건 | action | reason code |
| --- | --- | --- | --- | --- |
| 1 | `MANDATORY_UNKNOWN` | 하나라도 `status != SUCCEEDED` 또는 `action=UNKNOWN` | `UNKNOWN` | `ARBITER_MANDATORY_UNKNOWN` |
| 2 | `MULTIPLE_REJECT` | 위 조건 미해당, `REJECT >= 2` | `REJECT` | `ARBITER_MULTIPLE_REJECT` |
| 3 | `SINGLE_REJECT` | 위 조건 미해당, `REJECT == 1` | `WAIT` | `ARBITER_SINGLE_REJECT` |
| 4 | `ALL_BUY` | C/B/A 모두 `BUY` | `BUY` | `ARBITER_ALL_BUY` |
| 5 | `BALANCED_PLUS_ONE_BUY` | B=`BUY`, C/A 중 정확히 하나 `BUY`, 다른 하나 `WAIT` | `BUY` | `ARBITER_BALANCED_PLUS_ONE_BUY` |
| 6 | `DEFAULT_WAIT` | 위 조건 모두 미해당 | `WAIT` | `ARBITER_DEFAULT_WAIT` |

`decision_pattern`은 위 여섯 값만 허용한다. `reason_codes`는 위 server-owned
allowlist에서 pattern과 일대일인 정확히 한 항목만 가진다. Decision Agent reason은
복사·병합하지 않는다. precedence가 개별 예시보다 우선한다.

정규 예시는 `BUY/BUY/BUY → ALL_BUY/BUY`, `WAIT/BUY/BUY`와
`BUY/BUY/WAIT → BALANCED_PLUS_ONE_BUY/BUY`, `WAIT/BUY/WAIT →
DEFAULT_WAIT/WAIT`, `REJECT/BUY/BUY`와 `BUY/WAIT/REJECT →
SINGLE_REJECT/WAIT`, `REJECT/REJECT/BUY → MULTIPLE_REJECT/REJECT`,
`BUY/TIMED_OUT-UNKNOWN/BUY`와 `INVALID_OUTPUT-UNKNOWN/WAIT/BUY →
MANDATORY_UNKNOWN/UNKNOWN`이다.

#### 7.14.4 exact ArbiterResult와 validity

`entry-consensus-v1`의 exact top-level field는 `schema_version`,
`decision_context_id`, `decision_context_hash`, `action`, `policy_version`,
`input_result_ids`, `input_results`, `decision_pattern`, `reason_codes`,
`valid_until`이다. `input_results` item shape와 순서는 7.14.1과 같고,
`input_result_ids`는 그 목록의 stage ID를 같은 순서로 중복 보존한다.

`valid_until`은 DecisionContext 및 정상 C/B/A Result의 `valid_until`과 정확히 같다.
runtime-dependent 생성·완료 시각은 payload가 아니라 AgentStageRun lifecycle에만
기록한다. output hash는 exact canonical Result JSON의 SHA-256이다. confidence,
consensus confidence, 평균·가중 score, entry/risk score, PolicyProfile, Prompt, Model,
Provider field는 금지한다.

| ID | 요구사항 |
| --- | --- |
| AI-266 | `entry-arbiter-input-v1`은 7.14.1의 exact field와 C/B/A canonical order를 사용하고 전체 canonical JSON hash를 stage input hash로 사용한다. |
| AI-267 | Arbiter는 같은 run/context의 역할별 정확히 한 terminal structured Result를 strict 검증하며 정상 non-success와 structural corruption을 7.14.2대로 구분한다. |
| AI-268 | `consensus-policy-v1`은 7.14.3의 precedence, 여섯 pattern과 pattern/action/reason 일대일 mapping을 적용한다. |
| AI-269 | `entry-consensus-v1`은 7.14.4의 exact field만 가지며 ordered stage IDs/hashes, Context ID/hash, policy와 validity를 보존한다. |
| AI-270 | Arbiter evaluator는 normalized status/action만 받는 pure function이며 DB·clock·config·network 접근과 confidence/score/PolicyProfile/Agent reason 재해석을 하지 않는다. |
| AI-271 | 유효한 non-success Result의 UNKNOWN consensus는 Arbiter stage `SUCCEEDED`이고, structural conflict·expiry·internal failure에는 authoritative ArbiterResult를 남기지 않는다. |
| AI-272 | ArbiterResult `valid_until`은 Context 및 세 Result validity와 같고 만료된 입력으로 UNKNOWN을 포함한 어떤 ArbiterResult도 생성하지 않는다. |
| AI-273 | ENTRY_ARBITER는 route·prompt·model·invocation·web·tool·live data·Broker가 없고 ArbiterResult 외 거래·실행 resource를 생성하지 않는다. |
| AI-274 | 같은 canonical Arbiter input과 policy는 completion/query order와 무관하게 같은 input hash, Result와 output hash를 생성한다. |
| AI-275 | DIAGNOSTIC v7은 ArbiterResult에서 종료하며 Finalizer는 후속 단계에서 provenance/hash/policy/expiry/gate만 검증하고 consensus를 재해석하지 않는다. |

### 7.15 v7 TRADING Decision Finalizer 계약

Decision Finalizer는 AgentStage나 worker claim 대상이 아닌 server-owned application
service다. LlmInvocation, Route, Prompt, Provider, network와 tool을 사용하지 않고 confidence,
score, risk 또는 consensus를 계산하지 않는다. `validate → Arbiter action 보존 → immutable
Decision insert`만 수행한다. TRADING `ENTRY_ARBITER` completion transaction 안에서 Decision을
insert하지 않으며, Arbiter commit 뒤 별도 finalization reconciliation이 같은 helper를
opportunistic trigger, idle sweep와 crash recovery에 사용한다.
v7 helper는 legacy worker `_finalize_run()`, `create_mock_decision()`,
`create_mock_trading_decision()`, `finalize_position_advisory()` 또는 deterministic v1 ENTRY
score/threshold path를 호출·wrap·재사용하지 않는다.

#### 7.15.1 eligibility와 source validation

Finalization은 다음 조건을 모두 만족할 때만 가능하다.

1. source AgentRun은 `agent-dag-v7 + purpose=TRADING + analysis_context=ENTRY`이고 admission
   당시 frozen Activation Gate ID/hash가 둘 다 non-null이다.
2. run당 하나인 `decision-context-v1`의 canonical hash가 일치하고 DB-authoritative time이
   Context `valid_until`보다 이르다.
3. C/B/A는 같은 run/context의 exact ordered authoritative lineage이고, source
   `ENTRY_ARBITER` stage는 `SUCCEEDED`, `route_id=null`, `invocation_id=null`이다.
4. stage output은 strict `entry-consensus-v1`, stored output hash와 recomputed canonical
   hash가 같고 Context ID/hash, ordered C/B/A IDs/hashes/status/actions,
   `policy_version=consensus-policy-v1`과 validity가 모두 source와 일치한다.
5. `CONFIGURATION_SPEC.md` CFG-104~111의 frozen/current Gate live revalidation이 PASS하고
   conflicting Decision이 없다.

source stage/run/hash 불일치, malformed source, expiry 또는 Gate failure는 action을 WAIT,
REJECT, UNKNOWN으로 바꾸지 않고 Decision을 0건으로 유지한다. DecisionContext ID/hash를
Decision table에 복제하지 않으며 `Decision → ENTRY_ARBITER result → Context → C/B/A →
upstream`으로 resolve한다.

#### 7.15.2 finalization identity와 action

`entry-finalization-identity-v1` exact canonical material은 `schema_version`, `agent_run_id`,
`decision_context_id`, `decision_context_hash`, `arbiter_stage_run_id`,
`arbiter_output_hash`, `consensus_policy_version`의 일곱 field다. key 사전순·공백 없는 compact
JSON을 UTF-8로 직렬화한 SHA-256 lowercase hex 앞 58자에 `v7fin-`을 붙여 정확히 64자인
`evaluation_request_id`를 만든다. action은 Arbiter output hash에 이미 결합되므로 별도
field가 아니고 Gate는 run의 frozen provenance이므로 identity material에 넣지 않는다.

Gate와 source가 유효한 TRADING run은 Arbiter의 `BUY | WAIT | REJECT | UNKNOWN`을 정확히
같은 action의 immutable Decision으로 모두 finalization한다. 이는 주문 결과가 아니라
authoritative evaluation outcome이다. Finalizer는 BUY에서도 ExecutionStage, Approval,
OrderIntent, TradingOrder 또는 Broker를 생성·호출하지 않는다.

#### 7.15.3 sourced Decision representation과 API

canonical persistence/API discriminator schema version은
`sourced-entry-decision-v1`이다. source lineage 세 field가 모두 null이면 기존
`schema_version=1.0` legacy response를 변경 없이 사용한다. 세 source field가 모두
non-null이면 schema version이 반드시 `sourced-entry-decision-v1`이어야 하고, 반대 조합과
부분 lineage는 invalid data다.

Finalizer의 authoritative mapping은 `purpose=TRADING`, `decision_kind=ENTRY`,
`action=ArbiterResult.action`, `evaluation_request_id=entry-finalization-identity-v1`,
`decision_input_id=Context.decision_input_snapshot_id`, `input_snapshot_id`와 symbol/market은
그 frozen input provenance, `valid_until=ArbiterResult.valid_until`,
`reason_codes_json=ArbiterResult.reason_codes` canonical order, source run/stage/hash는 exact
source다. v7에는 단일 configuration 대표값이 없으므로 `configuration_version_id=null`이고
Activation provenance는 AgentRun에서 resolve한다.

`sourced-entry-decision-v1`의 legacy physical field 분류는 다음과 같다. `schema_version`,
action, reason, validation status와 source/input identity는 authoritative value(A), 아래 null은
not applicable(B)이며 neutral sentinel(C)은 사용하지 않는다.

| field | exact sourced-v7 value |
| --- | --- |
| `confidence` | `null`; Arbiter consensus confidence 없음 |
| `risk_level` | `null`; aggregate risk 판단 없음 |
| `model_provider`, `model_id`, `prompt_version` | 모두 `null`; C/B/A provenance는 source lineage로 resolve |
| `scout_output_json`, `core_output_json` | 모두 `null`; fake legacy aggregate 금지 |
| `reason_codes_json` | ArbiterResult의 정확히 한 server-owned reason 목록 |
| `latency_ms` | `null`; legacy single-model latency가 적용되지 않음 |
| `validation_status` | `VALID`; 기존 valid Decision 의미를 그대로 사용 |
| `execution_outcome` | `null`; 기존 enum에 NOT_EXECUTED equivalent가 없고 후속 결과는 DecisionExecution에 기록 |
| `execution_mode` | `null`; 후속 mode는 DecisionExecution에 기록 |
| `configuration_version_id` | `null`; Gate/Policy 대표값 합성 금지 |

API union의 legacy branch는 기존 `DecisionResponse` field와 Scout/Core parsing을 그대로
유지한다. sourced branch 이름은 `SourcedEntryDecisionResponse`, discriminator는
`schema_version=sourced-entry-decision-v1`과 all-non-null source lineage의 동시 일치다.
exact top-level field는 `schema_version`, `request_id`, `decision_id`, `purpose`,
`evaluation_request_id`, `decision_kind`, `symbol`, `market`, `input_snapshot_id`,
`decision_input_id`, `action`, `reason_codes`, `confidence`, `risk_level`,
`configuration_version_id`, `execution_mode`, `execution_outcome`, `validation_status`,
`execution`, `valid_until`, `created_at`, `lineage`다. 이 branch에서 위 nullable field는 JSON
null이고 Finalizer 직후 `execution=null`이다.
Decision row는 불변이므로 후속 Execution Orchestrator도 두 legacy execution field를 갱신하지
않고 별도 DecisionExecution을 만들며, 이후 API는 `execution` object로 그 상태를 노출한다.

`lineage` exact field는 `source_agent_run_id`, `source_stage_run_id`,
`source_stage_output_hash`, `decision_context_id`, `decision_context_hash`,
`consensus_policy_version`, `decision_pattern`, `input_results`다. `input_results`는
`entry-consensus-v1`의 C/B/A 순서와 item exact field(`role`, `agent_type`, `stage_run_id`,
`output_hash`, `status`, `action`)를 그대로 read-time resolve한다. legacy Scout/Core 또는
representative model/confidence를 합성하지 않는다. Gate denial/failure는 Decision row가
없으므로 이 API에 fake Decision으로 노출하지 않는다. 기존 `GET /decisions`와
`GET /decisions/{decision_id}` endpoint는 유지한다. list envelope의
`schema_version=1.0`, `request_id`, `items`는 유지하되 `items`가 legacy 또는 sourced branch의
discriminated union이고 detail도 같은 item union을 반환한다.

| ID | 요구사항 |
| --- | --- |
| AI-276 | Finalizer는 provider-less server service이며 Arbiter completion과 분리된 reconciliation에서 source·Gate를 검증하고 action을 보존해 Decision만 insert한다. |
| AI-277 | Finalizer eligibility는 7.15.1의 run, Context, C/B/A, Arbiter schema/hash/validity/policy, frozen/current Gate와 conflict 검사를 모두 요구한다. |
| AI-278 | `entry-finalization-identity-v1`은 7.15.2의 exact 일곱 field와 canonical hash/prefix 규칙을 사용하며 action과 Gate를 직접 material에 넣지 않는다. |
| AI-279 | valid/open TRADING source의 BUY, WAIT, REJECT, UNKNOWN은 모두 Arbiter action과 동일한 immutable Decision이 되고 Gate failure는 어떤 action Decision으로도 변환되지 않는다. |
| AI-280 | sourced v7 Decision schema version은 `sourced-entry-decision-v1`이고 7.15.3의 physical null/value mapping을 사용하며 sentinel과 fake legacy payload를 금지한다. |
| AI-281 | sourced Decision의 reason은 Arbiter reason만 canonical order로 보존하고 C/B/A reason을 병합하지 않는다. |
| AI-282 | source-lineage presence와 schema version은 양방향 일치해야 하며 source run/stage/hash의 application validation은 DB constraint만 신뢰하지 않는다. |
| AI-283 | Decision API는 기존 legacy branch를 깨지 않고 exact `SourcedEntryDecisionResponse` branch와 read-time `lineage` object를 제공한다. |
| AI-284 | Finalizer 성공은 ExecutionStage·Approval·OrderIntent·TradingOrder·Broker side effect 0건이며 BUY도 주문 권한이 아니다. |
| AI-285 | DIAGNOSTIC run은 Finalizer 대상이 아니고 동일 slot의 TRADING run과 purpose가 identity에 포함된 별도 admission이며 mutation·promotion·복사가 금지된다. |
| AI-286 | v7 Finalizer는 legacy finalizer와 deterministic v1 ENTRY score/threshold path를 호출·wrap·재사용하지 않고 별도 sourced contract만 사용한다. |
