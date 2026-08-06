# 데이터베이스 및 영속성 명세

## 1. 목적

Cresta의 사용자·설정·판단·주문·체결·포지션·위험·감사 데이터를 PostgreSQL에 일관되게 저장하고, 중복 주문과 부분체결 경쟁에서도 복구 가능한 트랜잭션 경계를 정의한다.

## 2. 적용 범위

- PostgreSQL 운영 데이터와 시계열 파티션
- Redis 캐시·큐·lease의 역할 경계
- 테이블, 키, 제약조건, 인덱스와 트랜잭션
- migration, 보존, 백업과 민감정보

## 3. 상세 명세

### 3.1 공통 원칙

| ID | 요구사항 |
| --- | --- |
| DB-001 | PostgreSQL을 사용자 설정, 주문, 체결, 포지션, 감사와 거래 상태의 영속 진실 공급원으로 사용한다. |
| DB-002 | Redis 데이터만으로 주문 권한·체결·포지션·비상정지를 확정하지 않는다. |
| DB-003 | 모든 식별자는 UUIDv7 또는 시간 정렬 가능한 동등 식별자를 사용하고 외부 키움 식별자와 분리한다. |
| DB-004 | 모든 시각은 PostgreSQL `timestamptz` UTC로 저장하고 UI에서 Asia/Seoul로 변환한다. 거래일은 별도 KST `date`로 저장한다. |
| DB-005 | 가격·금액·비율·수량은 명시적 정밀도의 `numeric` 또는 정수를 사용하고 부동소수점 타입을 사용하지 않는다. |
| DB-006 | 핵심 레코드는 `created_at`, `updated_at`, `version`과 필요 시 `correlation_id`를 가진다. |

### 3.2 핵심 테이블

| 테이블 | 필수 핵심 필드 | 주요 제약 |
| --- | --- | --- |
| `users` | id, login_id, password_hash, password_params, status | `login_id` 대소문자 정규화 unique |
| `totp_credentials` | user_id, encrypted_secret, key_version, last_used_step | user당 활성 credential 1개 |
| `recovery_codes` | id, user_id, code_hash, used_at | 평문 저장 금지 |
| `sessions` | id_hash, user_id, created_at, last_seen_at, expires_at, revoked_at | 원문 token 저장 금지 |
| `auth_challenges` | id_hash, user_id, type, expires_at, consumed_at, attempts | 1회 사용 |
| `instruments` | symbol, name, market_support, tradable_status | symbol unique |
| `watchlist_items` | id, user_id, symbol, market, created_at, updated_at | user+market+symbol unique, 사용자별 최대 3개 |
| `configuration_versions` | id, scope, target_symbol, state, payload, schema_version | 활성 범위별 1개 |
| `market_snapshots` | id, symbol, market, observed_at, quality, payload_hash, payload | 판단 참조 불변 |
| `minute_bars` | id, market, symbol, bucket_start, OHLCV, turnover, snapshot 범위, version | market+symbol+bucket unique |
| `indicator_snapshots` | id, market_snapshot_id, calculator_version, VWAP, SMA5, session_high, drawdown, spread, relative volume, volatility | market snapshot 1:1 |
| `decision_input_snapshots` | id, user_id, purpose, market/indicator snapshot ID, observed_at, input_json, input_hash | canonical 입력 불변·사용자 metadata는 JSON 밖에 저장 |
| `decisions` | id, purpose, kind, symbol, input_snapshot_id, decision_input_id, output, action, valid_until | 구조화 스키마 검증 결과 포함·불변 |
| `decision_executions` | id, decision/rule source, action, mode, policy versions, state, version | source+action+policy unique |
| `guard_evaluations` | id, phase, subject, result, rule_results, input versions, valid_until | 불변 평가 |
| `approvals` | id, execution_id, decision_id, status, scope_snapshot, expires_at, actor_id, reauth_id, version | execution당 최대 1개 |
| `order_intents` | id, execution_id, guard_evaluation_id, order_group_id, symbol, side, requested_quantity, action, config_version | execution당 최대 1개 |
| `orders` | id, intent_id, parent_order_id, client_order_id, idempotency_key, broker_order_id, status, quantities | client/idempotency unique |
| `order_events` | id, order_id, event_type, source, source_key, payload_hash, occurred_at | source 중복 방지 |
| `fills` | id, order_id, broker_fill_key, quantity, price, fee, tax, filled_at | broker fill 중복 방지 |
| `positions` | id, account_alias, symbol, quantity, average_price, stop_price, state, version | 활성 account+symbol unique |
| `position_events` | id, position_id, cause_type, cause_id, before, after | 불변 원장 |
| `risk_events` | id, scope, rule_code, severity, state, input_snapshot, resolution | 원인·해제 추적 |
| `emergency_stops` | id, level, state, activated_by, released_by, timestamps | 활성 계좌당 1개 |
| `reconciliation_runs` | id, scope, trigger, state, started_at, completed_at | 단계·checkpoint 포함 |
| `reconciliation_mismatches` | id, run_id, code, symbol, severity, state, broker_value, internal_value | 해결 이력 |
| `broker_leases` | account_alias, owner_id, fencing_token, expires_at, version | account_alias PK |
| `broker_worker_states` | account_alias, state, fencing_token, websocket_connected, subscriptions_ready, heartbeat/reconciliation 시각, error_code | account_alias PK, 비밀 저장 금지 |
| `analysis_scheduler_leases` | scheduler_name, owner_id, fencing_token, expires_at, version | scheduler_name PK |
| `analysis_scheduler_states` | scheduler_name, state, fencing_token, heartbeat/tick/완료/다음 시각, 집계, error_code | scheduler_name PK, 비밀 저장 금지 |
| `llm_provider_profiles` | id, name, adapter_type, endpoint, secret_ref, data_policy, state, version | name unique, credential 원문 금지 |
| `llm_model_profiles` | id, provider_profile_id, alias, provider_model_id, capabilities, limits, state, version | provider+alias+version unique |
| `llm_role_routes` | id, role, primary/fallback models, policy, prompt/schema versions, state | role·scope별 ACTIVE 1개 |
| `agent_runs` | id, purpose, symbol, input/DAG/route versions, state, idempotency_key, timestamps | idempotency unique |
| `agent_stage_runs` | id, run_id, role, input hash, state, lease, invocation_id, output ref | run+role unique |
| `evidence_items` | id, symbol, source, source/event/received 시각, facts, content hash, raw ref | source URL/hash 중복 추적 |
| `evidence_bundles` | id, symbol, as_of, policy version, state, evidence refs, bundle hash | 불변 canonical bundle |
| `llm_invocations` | id, stage_run_id, requested/actual provider·model, state, usage, latency, cost, hashes | credential·Authorization 원문 금지 |
| `audit_logs` | id, actor_type, actor_id, action, target, result, metadata, correlation_id, created_at | append-only |

### 3.3 주문·체결 제약과 트랜잭션

| ID | 요구사항 |
| --- | --- |
| DB-010 | `orders.idempotency_key`와 `client_order_id`는 전역 unique로 강제한다. |
| DB-011 | 키움 주문번호는 환경·계좌별 부분 unique로 관리하고 null 상태 중복을 허용한다. |
| DB-012 | 주문수량, 체결수량, 취소수량과 잔량은 모두 0 이상이며 주문 상태 머신의 수량 불변조건을 CHECK 또는 트랜잭션 검증으로 강제한다. |
| DB-013 | 체결 삽입, 주문 누적수량 변경, 포지션 변경과 position event 생성은 하나의 DB 트랜잭션에서 처리한다. |
| DB-014 | 체결 중복 키가 없으면 환경·계좌·주문번호·체결시각·가격·수량의 안정된 복합키와 원본 해시를 사용하고 충돌은 재동기화 대상으로 격리한다. |
| DB-015 | 주문 상태 변경은 기대 `version`을 조건으로 갱신하고 0행 갱신 시 최신 상태를 다시 읽어 재평가한다. |
| DB-016 | `UNKNOWN`·`RECONCILING` 주문이 있는 종목의 새 주문 생성은 DB 거래 게이트에서도 거부한다. |

### 3.3.1 첫 재동기화 저장 계약

| ID | 요구사항 |
| --- | --- |
| DB-017 | `reconciliation_runs`는 account_alias, trigger, scope, state, 시작/완료/snapshot 시각, mismatch 집계, 요청 API ID JSON, correlation_id와 비밀 없는 요약 JSON을 저장한다. |
| DB-018 | run state는 `RUNNING`, `SUCCEEDED`, `MISMATCH`, `FAILED`만 허용하며 종료 상태에는 completed_at이 필요하다. |
| DB-019 | `reconciliation_mismatches`는 run FK, code, symbol, severity, state, broker/internal 비교 JSON과 생성/해결 시각을 저장한다. account/token과 원본 응답은 금지한다. |
| DB-026 | mismatch severity는 `WARNING`, `CRITICAL`, state는 `OPEN`, `RESOLVED`로 제한하고 run 삭제 시 mismatch도 함께 삭제한다. |
| DB-027 | `broker_leases` 획득·갱신·해제는 account_alias 행 잠금과 owner/fencing token 비교로 원자 처리한다. 만료 전 다른 owner는 획득할 수 없다. |
| DB-028 | `broker_worker_states`는 worker 상태와 연결·구독·heartbeat·최근 재동기화만 저장하며 계좌번호·접근 token·App Key·원본 WebSocket payload를 저장하지 않는다. |
| DB-029 | `READY` 전환과 worker 상태 갱신은 현재 lease owner와 fencing token 검증을 같은 transaction에서 통과해야 한다. |

권장 트랜잭션 격리 수준은 일반 명령 `READ COMMITTED`와 명시적 행 잠금이며, 계좌 소유권·설정 활성화처럼 경합이 적고 중요도가 높은 작업은 `SERIALIZABLE` 또는 동등한 낙관적 재시도를 사용한다.

### 3.4 설정·승인·인증 제약

| ID | 요구사항 |
| --- | --- |
| DB-020 | 동일 scope와 target에는 `ACTIVE` 설정 버전이 하나만 존재하도록 부분 unique index를 둔다. |
| DB-021 | 활성 설정 payload는 수정하지 않고 변경·롤백 모두 새 버전을 생성한다. |
| DB-022 | 승인 상태 전이는 조건부 갱신으로 한 번만 처리하고 만료 승인이나 이미 처리된 승인을 재사용하지 않는다. |
| DB-023 | 고위험 승인에는 대상 요청과 결합된 사용 완료 전 재인증 식별자를 저장한다. |
| DB-024 | 비상정지 활성화·해제와 감사 이벤트를 같은 트랜잭션에 저장한다. |
| DB-025 | 세션·challenge·복구 코드 원문은 저장하지 않고 검증 가능한 해시만 저장한다. |

### 3.5 감사·불변 데이터

| ID | 요구사항 |
| --- | --- |
| DB-030 | `audit_logs`, `order_events`, `position_events`, 활성화된 설정과 판단 입력 snapshot은 애플리케이션 API로 UPDATE·DELETE하지 않는다. |
| DB-031 | 정정은 기존 이벤트 수정이 아니라 보정 이벤트 추가로 수행한다. |
| DB-032 | 감사 로그 접근은 애플리케이션 쓰기 역할과 운영 조회 역할로 분리한다. |
| DB-033 | JSON payload에는 `schema_version`을 저장하고 애플리케이션이 알 수 없는 상위 버전을 자동 해석하지 않는다. |
| DB-034 | 비밀값·전체 계좌번호·인증 token은 JSON payload와 감사 metadata에서도 금지한다. |

### 3.6 인덱스와 파티션

첫 버전은 별도 TimescaleDB 의존 없이 PostgreSQL native range partition을 사용한다. TimescaleDB 도입은 실제 데이터량과 대표 쿼리 측정 후 별도 migration 명세로 검토한다.

필수 인덱스:

```text
orders(account_alias, broker_order_id)
orders(symbol, status)
orders(order_group_id, created_at)
fills(order_id, filled_at)
positions(account_alias, symbol)
decisions(symbol, created_at desc)
decision_executions(decision_id, state)
decision_executions(user_id, state, created_at)
guard_evaluations(subject_type, subject_id, evaluated_at desc)
approvals(user_id, status, expires_at)
audit_logs(created_at, action)
market_snapshots(symbol, market, observed_at desc)
```

| ID | 요구사항 |
| --- | --- |
| DB-040 | 고용량 시장 snapshot·감사·이벤트 테이블은 월 단위 시간 파티션을 기본으로 한다. |
| DB-041 | 활성 주문·포지션·설정 조회는 전체 시계열 파티션 스캔 없이 수행돼야 한다. |
| DB-042 | migration 전후 대표 쿼리 실행계획과 인덱스 용량을 시험한다. |

### 3.7 Redis 역할

| ID | 요구사항 |
| --- | --- |
| DB-050 | Redis는 최신 시세 캐시, 작업 큐, 짧은 중복 억제와 상태 방송에 사용한다. |
| DB-051 | Redis lease는 보조 신호이며 Broker 주문 권한은 PostgreSQL fencing token 검증을 함께 통과해야 한다. |
| DB-052 | Redis 전체 유실 후 PostgreSQL과 Broker 재동기화로 복구할 수 있어야 한다. |
| DB-053 | 큐 작업에는 멱등성 키를 포함하고 ack 전 worker 종료 시 안전하게 재처리한다. |

### 3.8 첫 Watch 영속 테이블

| ID | 요구사항 |
| --- | --- |
| DB-080 | `market_snapshots`는 정규화 가격·호가·거래량, 원본 식별자·해시, 이벤트·수신시각과 품질을 불변 행으로 저장한다. |
| DB-081 | `market_stream_states`는 `market + symbol`을 기본키로 하고 현재 정상 snapshot, 최근 순번·거래량, 품질과 version을 저장한다. |
| DB-082 | snapshot 원본 식별자는 `source + market + symbol + sequence_or_hash` unique로 중복을 DB에서도 차단한다. |
| DB-083 | snapshot 삽입과 stream 상태 변경은 하나의 transaction에서 처리하고 stream 상태는 낙관적 version 검사를 사용한다. |

### 3.9 migration과 초기화

| ID | 요구사항 |
| --- | --- |
| DB-060 | 모든 스키마 변경은 순서가 있는 migration으로 관리하고 운영 DB에서 자동 destructive migration을 실행하지 않는다. |
| DB-061 | 애플리케이션 시작 시 DB schema version 호환성을 검사하고 불일치 시 거래 worker를 시작하지 않는다. |
| DB-062 | 되돌릴 수 없는 migration은 사전 백업·복원 시험·명시적 운영 승인 절차를 요구한다. |
| DB-063 | 초기 관리자 생성과 시스템 기본 설정 seed는 반복 실행해도 중복 생성되지 않아야 한다. |
| DB-064 | 파일에서 읽은 DB 비밀번호는 SQLAlchemy URL에서 percent-encoding하고, Alembic ConfigParser에 주입할 때 `%`를 이중 이스케이프한다. migration 오류에는 완성된 인증 URL이나 비밀번호를 출력하지 않는다. |

### 3.10 보존·백업

| ID | 요구사항 |
| --- | --- |
| DB-070 | 주문·체결·포지션·판단·설정·위험·감사 기록은 기본 5년 보존한다. 원본 tick은 시장데이터 명세의 단기 보존 정책을 따른다. |
| DB-071 | 백업은 암호화하고 `/home/totquf4171/cresta/backups`에 두되 운영 DB와 동일 장애만으로 함께 소실되지 않도록 별도 복제본을 둔다. |
| DB-072 | 백업 성공만 확인하지 않고 정기적으로 격리 환경에서 복원과 핵심 불변조건을 검증한다. |
| DB-073 | 보존 삭제는 파티션 단위 승인 작업으로 수행하고 법적·감사 hold가 걸린 데이터는 제외한다. |

## 4. 오류·예외 또는 경계 조건

- DB 연결 또는 commit 결과가 불명확하면 주문을 재전송하지 않고 DB와 키움 양쪽을 재동기화한다.
- 수량 불변조건이나 중복 체결 제약 위반은 해당 종목 거래를 중지하고 원본 이벤트를 격리한다.
- migration 실패 시 이전 애플리케이션을 자동 실행하지 않고 schema 호환성을 확인한다.
- 디스크 부족·read-only 전환 시 신규 주문을 금지한다. 기존 Broker 주문은 조회 후 수동 복구 대상으로 유지한다.

## 5. 검증·인수 조건

- 동시에 같은 멱등성 키를 제출해도 주문이 하나만 생성된다.
- 부분체결·취소 경쟁 중에도 주문수량 불변조건과 포지션 수량이 유지된다.
- Redis를 비운 뒤 DB와 키움 조회만으로 거래 상태를 복구할 수 있다.
- 인증·Broker 비밀 원문이 DB dump에 존재하지 않는다.
- migration과 암호화 백업 복원을 깨끗한 환경에서 재현할 수 있다.

## 6. 미결정·보류 항목

- 실제 거래량 기준 파티션 크기와 자동 보관 스케줄
- 암호화 백업의 서버 외부 보관 매체

### 6.1 실행 권한 설정 저장 계약

| ID | 요구사항 |
| --- | --- |
| DB-090 | `configuration_versions`는 scope, target, category, sequence, state, payload hash와 생성·검증·활성화 시각을 저장하고 동일 대상·범주의 `ACTIVE` 행을 하나로 제한한다. |
| DB-091 | 실행 권한 payload는 정규화된 JSON과 SHA-256 해시로 저장하고 `ACTIVE` 또는 `SUPERSEDED` 행을 API로 수정하지 않는다. |

### 6.2 AI 판단 저장 계약

| ID | 요구사항 |
| --- | --- |
| DB-092 | `decisions`는 고유 evaluation request, `DIAGNOSTIC | TRADING` purpose, 입력 snapshot, 모델·프롬프트·스키마 버전과 Scout/Core 출력을 저장한다. 실행의 가변 상태를 판단 행에 덮어쓰지 않는다. |
| DB-093 | 판단 유효시간·confidence·reason code와 JSON 출력은 검증된 값만 저장하며 API에서 기존 판단을 수정·삭제하지 않는다. |

### 6.3 감시 종목 저장 계약

| ID | 요구사항 |
| --- | --- |
| DB-094 | `watchlist_items`는 사용자, 숫자 6자리 종목, 시장과 생성·수정시각을 저장하고 `user_id + market + symbol`을 unique로 제한한다. |
| DB-095 | 사용자별 최대 3개 검사는 등록 transaction의 사용자 행 잠금과 count로 직렬화한다. 삭제는 시세 snapshot을 삭제하지 않는다. |
| DB-096 | worker는 전체 사용자의 활성 KRX 종목 합집합만 읽으며 사용자·인증 정보를 WebSocket payload에 포함하지 않는다. |
| DB-097 | `minute_bars`는 market·symbol·KST 1분 bucket을 unique로 하고 OHLC, 거래량, turnover, 입력 snapshot 범위와 version을 저장한다. |
| DB-098 | `indicator_snapshots`는 입력 market snapshot과 1:1로 연결하며 calculator version, VWAP, SMA5, session high, drawdown, spread와 분봉 수를 저장한다. |

### 6.4 판단 실행·Guard·승인 저장 계약

| ID | 요구사항 |
| --- | --- |
| DB-100 | `decision_executions`는 decision 또는 rule trigger source, 사용자·계좌·종목, 정규 행동, 실행 mode·단계, 실행권한·위험·전략 설정 version, state, correlation ID와 낙관적 version을 저장한다. |
| DB-101 | `decision_id + execution_action + execution_policy_version_id`의 null-safe unique execution key로 같은 판단의 중복 실행을 차단한다. rule trigger는 동등한 안정 source key를 사용한다. |
| DB-102 | `guard_evaluations`는 execution/order subject, phase, 결과·rule results·halt scope, snapshot·position·설정 version과 평가·만료시각을 불변 저장한다. |
| DB-103 | `approvals.execution_id`와 `order_intents.execution_id`는 각각 unique이며 한 execution에 승인과 주문 의도를 하나씩만 허용한다. |
| DB-104 | 승인 생성 transaction은 execution 전이, Guard 평가, approval, risk/audit event를 함께 저장한다. 자동 주문 transaction은 execution 전이, Guard 평가, order intent, `CREATED` order, risk/audit event를 함께 저장한다. |
| DB-105 | 승인 처리 transaction은 reauth proof 1회 소비, expected approval version 조건부 갱신, 새 Guard 평가, order intent·order와 감사를 함께 commit하거나 모두 rollback한다. |
| DB-106 | 승인 scope snapshot은 수량·기준가격/범위·snapshot·position·설정 version과 만료시각을 저장하고 승인 후 수정하지 않는다. |
| DB-107 | execution·approval·order 생성 transaction의 commit 결과가 불명확하면 execution key와 idempotency key를 조회한 뒤에만 재처리한다. |
| DB-108 | `analysis_scheduler_leases`는 scheduler 이름을 PK로 하고 owner ID, fencing token, 만료시각과 version을 저장한다. 획득·갱신은 행 잠금과 owner/token 비교로 원자 처리한다. |
| DB-109 | `analysis_scheduler_states`는 상태, 현재 fencing token, heartbeat, 최근 tick·완료·다음 예정 시각, 최근 처리·생성·건너뜀·실패 수와 비밀이 아닌 오류 code만 저장한다. |
| DB-110 | scheduler lease·state에는 모델 API key, 계좌번호, 원본 시세 payload와 사용자 인증정보를 저장하지 않는다. |
| DB-111 | `decision_input_snapshots`는 사용자와 목적, 시장·종목, 기준시각, 시장·지표 snapshot ID, 품질·세션, canonical 입력 JSON과 SHA-256 해시를 불변 저장한다. 사용자 식별자는 모델 입력 JSON 밖의 소유권 metadata로만 저장한다. |
| DB-112 | `decisions.decision_input_id`는 신규 판단에서 입력 snapshot을 참조한다. 기존 판단 호환을 위해 migration 열은 nullable로 추가하되 새 생성 경로는 null을 허용하지 않는다. |
| DB-113 | `indicator_snapshots`는 v2의 VWAP 대비율, SMA5 기울기, 상대 거래량과 실현 변동성을 nullable 고정소수점으로 저장한다. 기존 v1 행은 재해석하지 않는다. |
| DB-114 | 동일 사용자의 같은 목적·시장 snapshot·입력 hash는 하나만 저장하고 hash 충돌 또는 내용 불일치는 판단 생성 전에 transaction을 rollback한다. |

### 6.5 다중 에이전트·LLM Provider 저장 계약

| ID | 요구사항 |
| --- | --- |
| DB-115 | `llm_provider_profiles`에는 secret 참조와 상태만 저장하고 key/token/private key 원문을 저장하지 않는다. endpoint·adapter·data policy 변경은 새 version 또는 감사 가능한 조건부 갱신으로 처리한다. |
| DB-116 | `llm_model_profiles` capability는 contract fixture 결과와 확인시각을 포함하고, 발견된 모델을 자동으로 활성화하지 않는다. |
| DB-117 | `llm_role_routes`는 `DRAFT/VALIDATED/ACTIVE/SUPERSEDED`를 사용하고 같은 role·scope의 ACTIVE 행을 부분 unique로 하나만 허용한다. |
| DB-118 | `agent_runs.idempotency_key`는 purpose·symbol·input snapshot·DAG version·분석 slot을 포함하며 동일 판단 run 중복을 차단한다. |
| DB-119 | `agent_stage_runs`는 조건부 상태 전이와 lease/fencing으로 한 worker만 실행하고 완료 출력은 수정하지 않는다. |
| DB-120 | `evidence_items`와 `evidence_bundles`는 원문 참조·사실·출처·시각·hash를 분리하고 bundle 생성 후 수정하지 않는다. |
| DB-121 | `llm_invocations`는 요청·실제 provider/model, route, request ID, 상태, 사용량, 지연, 비용, retry/fallback과 redacted hash를 저장하고 prompt 원문은 별도 보존 정책 없이는 저장하지 않는다. |
| DB-122 | agent stage 완료와 invocation 완료, Core 판단 enqueue는 유실·중복을 막는 transaction 또는 transactional outbox로 연결한다. |
| DB-123 | invocation·evidence 보존 삭제는 판단·감사 legal hold와 참조 무결성을 확인하고 hash·최소 provenance를 유지한다. |
| DB-124 | Agent Runtime v1의 `agent_runs`는 owner, purpose, market·symbol, market snapshot·input hash, DAG·route map, 상태·Core action, valid_until과 unique idempotency key를 저장한다. |
| DB-125 | `agent_stage_runs`는 run+role unique, dependency·route·invocation 참조, 불변 input/output JSON·hash, 상태·오류·시각을 저장하며 완료 stage를 API로 수정하지 않는다. |
| DB-126 | `evidence_items`는 출처·시각·facts·content hash를 저장하고 `evidence_bundles`는 owner·run·상태·evidence ID·canonical hash를 저장한다. v1은 외부 수집 없이 `PARTIAL` 빈 bundle 하나만 생성한다. |
| DB-127 | `agent_stage_runs.invocation_id`는 `llm_invocations`를 unique FK로 참조해 stage당 invocation을 최대 하나로 제한하고 invocation의 `stage_run_id`와 같은 식별자를 기록한다. |
| DB-132 | `agent_stage_runs`는 `lease_owner_id`, `lease_expires_at`, `fencing_token`, `attempt_count`, `max_attempts`, `timeout_at`, `heartbeat_at`을 저장한다. fencing과 attempt는 음수가 아니며 max attempt는 1 이상이어야 한다. |
| DB-133 | worker claim 조회는 `state, available_at, lease_expires_at, created_at, sequence` 인덱스를 사용하고 PostgreSQL에서는 잠금된 행을 건너뛰어 복수 worker가 같은 stage를 claim하지 않게 한다. |
| DB-134 | 완료 stage의 output JSON·hash와 invocation 참조는 수정하지 않는다. 만료 복구도 완료 상태를 `PENDING` 또는 `RUNNING`으로 되돌리지 않는다. |
| DB-135 | 간편 Provider 등록은 모델 목록 검증 성공 뒤 Provider와 발견 Model Profile을 같은 DB transaction으로 저장한다. secret 파일 쓰기 뒤 DB commit 실패 시 새 secret 파일을 제거한다. |
| DB-136 | 발견 모델은 `DRAFT`로 저장하고 사용자가 활성화한 모델만 `VALIDATED`가 된다. 등록·동기화가 기존 ACTIVE route를 자동 변경하지 않는다. |
| DB-137 | 모델 재동기화는 기존 `provider_model_id`를 중복 생성하지 않고 새 모델만 추가하며 기존 검증 모델과 route 이력을 자동 삭제하지 않는다. |
| DB-128 | `llm_model_profiles`는 Provider에 속한 재사용 가능한 모델 카탈로그이며 model 기본 생성 파라미터와 capability 검증 결과를 저장한다. 역할 이름을 model alias나 provider model ID로 강제하지 않는다. |
| DB-129 | `llm_role_routes`는 역할 배정 version으로 사용하고 generation override와 계산 가능한 상속 출처를 저장한다. 같은 owner·scope·role의 `ACTIVE`는 부분 unique 하나만 허용한다. |
| DB-130 | 역할 배정 활성화 transaction은 새 route를 `ACTIVE`, 기존 활성 route를 `SUPERSEDED`로 원자 전환한다. 여러 `VALIDATED` 행은 후보 이력일 뿐 runtime 기본값이 아니다. |
| DB-131 | 기존 중복 `VALIDATED` route migration은 행을 삭제하거나 임의 활성화하지 않는다. 이력으로 보존하고 사용자가 명시적으로 현재 배정을 선택하도록 한다. |
