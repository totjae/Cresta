# Scout·Core AI 판단 계약 명세

## 1. 목적

Scout와 Core가 사용할 입력 snapshot, 제한된 출력 스키마, 호출 조건, 실패 처리와 감사 기준을 정의해 AI가 주문 권한이나 Guard 정책을 우회하지 못하게 한다.

## 2. 적용 범위

- 감시 종목의 Scout 주기 분석
- 신규진입과 보유 포지션의 Core 판단
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
