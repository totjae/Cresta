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

### 6.3 Scout 입력 snapshot과 지표 기반 Mock 계약

| ID | 요구사항 |
| --- | --- |
| AI-086 | 모든 신규 진단·거래 판단은 모델 호출 전에 `scout-input-v1` 불변 입력 snapshot을 만들고 판단이 해당 입력 ID와 시장·지표 snapshot을 참조한다. |
| AI-087 | 입력 JSON은 기준시각, 품질·세션, 정규화 quote, `watch-indicators-v2` 지표와 명시적인 null 영역을 포함하며 사용자 ID·계좌번호·인증·Broker 자격증명을 포함하지 않는다. |
| AI-088 | 입력 JSON은 정렬된 key와 고정 Decimal 문자열로 canonicalize해 SHA-256 해시를 저장한다. 저장된 JSON의 재계산 해시가 다르면 판단 실행에 사용하지 않는다. |
| AI-089 | 현재 market snapshot에 연결된 v2 지표가 없거나 계산 버전이 다르면 Scout는 `UNKNOWN/DATA_INSUFFICIENT`, Core는 `RISK_BLOCK`을 반환한다. 결측 지표를 0으로 대체하지 않는다. |
| AI-090 | `deterministic-mock-v2`는 VWAP 위치, SMA5 방향, 상대 거래량, 실현 변동성, 고점 낙폭과 spread만으로 허용 reason code와 점수를 생성한다. 같은 입력 hash는 같은 출력을 만들어야 한다. |
