# 데이터베이스 및 영속성 명세

## 1. 목적

Cresta의 사용자·설정·판단·주문·체결·포지션·위험·감사 데이터를 PostgreSQL에 일관되게 저장하고, 중복 주문과 부분체결 경쟁에서도 복구 가능한 트랜잭션 경계를 정의한다.

## 2. 적용 범위

- PostgreSQL 운영 데이터와 시계열 파티션
- Redis 캐시·큐·lease의 역할 경계
- 테이블, 키, 제약조건, 인덱스와 트랜잭션
- migration, 보존, 백업과 민감정보

PostgreSQL이 실행 중인 동안에는 계속 주문·실행 상태의 권위 원본이다. 다만 현재 `MOCK`/development 배포의 **장기 내구성 정책**은 별도이며, Phase 11A.2에서 runtime data 유실을 의도적으로 허용한다. 이 예외를 LIVE 내구성 정책으로 해석하지 않는다.

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
| `reauth_proofs` | id, proof_hash, user_id, target_action, target_id, expires_at, consumed_at | 대상 결합·1회 사용, 원문 proof 저장 금지 |
| `auth_rate_limits` | subject_hash, failure_count, lockout_level, locked_until, updated_at | 평문 login ID·IP 저장 금지 |
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
| `trading_gates` | account_alias, environment, status, reason, version, updated_at | 계좌별 1개, READY 전 주문 생성 금지 |
| `order_intents` | id, execution_id, guard_evaluation_id, order_group_id, symbol, side, requested_quantity, action, config_version | execution당 최대 1개 |
| `orders` | id, intent_id, parent_order_id, client_order_id, idempotency_key, broker_order_id, status, quantities, unfilled_policy, fill_timeout_seconds, reprice_attempts, next_action_at | client/idempotency unique. 미체결 자동처리 시각과 횟수는 재시작 후에도 보존 |
| `order_events` | id, order_id, event_type, source, source_key, payload_hash, occurred_at | source 중복 방지 |
| `fills` | id, order_id, broker_fill_key, quantity, price, fee, tax, filled_at | broker fill 중복 방지 |
| `positions` | id, account_alias, symbol, quantity, available_quantity, average_price, managed_quantity, managed_average_price, origin, state, version | account+symbol unique. Broker 총량과 Cresta 관리량을 분리하며 발화 시점 stop_price는 `stop_triggers`에 고정 |
| `position_events` | id, position_id, cause_type, cause_id, before, after | 불변 원장 |
| `risk_events` | id, scope, rule_code, severity, state, account_alias, symbol, input_snapshot_id, input_json, resolution, resolved_at, correlation_id | 범용 위험 원장. scope로 FIXED_STOP/DAILY_LOSS/SPREAD/CONNECTION 구분, ACTIVE→RESOLVED |
| `stop_triggers` | id, account_alias, position_id, position_version, symbol, market, risk_policy_version_id, stop_price, trigger_price, snapshot_id, state, result_code, guard_evaluation_id, risk_event_id, halt_scope, version | 고정 손절 trigger 상태머신. (position_id, position_version, risk_policy_version_id) unique로 idempotency, EXIT_PENDING 영속 |
| `emergency_stops` | id, account_alias, level, state, reason, activation/release idempotency key, activated_by, released_by, version, timestamps | 계좌당 현재 상태 1개; 변경 이력은 감사 로그에 보존 |
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
| DB-152 | 키움 연동 환경의 `orders`, `fills`, `positions`는 Broker account snapshot의 로컬 projection이다. projection 반영과 해당 run의 mismatch 계산은 하나의 DB transaction에서 수행한다. |
| DB-153 | Broker에서 가져온 주문은 `environment + account_alias + broker_order_id`, 체결은 결정론적 `broker_fill_key`, position은 `account_alias + symbol` 제약으로 반복 snapshot에서도 멱등해야 한다. |
| DB-154 | position은 Broker 기준 `quantity`, `available_quantity`, `average_price`와 Cresta 귀속 `managed_quantity`, `managed_average_price`를 함께 저장한다. `0 <= managed_quantity <= quantity`, `0 <= available_quantity <= quantity`를 강제하고 OPEN origin은 `CRESTA_MANAGED`, `EXTERNAL`, `MIXED` 중 수량 구성과 일치시킨다. 변경은 `position_events`에 reconciliation run ID와 전후 값을 기록한다. |
| DB-155 | projection은 판단·승인·설정·order intent를 삭제하거나 의미 변경하지 않는다. Broker-only 주문에 필요한 intent는 `BROKER_IMPORTED`로 별도 생성하고 원래 Cresta intent와 구분한다. |

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
| DB-062 | 현재 `MOCK`/development에서 되돌릴 수 없는 migration은 명시적 운영 승인과 fresh database 재구축 가능성 검증을 요구하지만 사전 백업·복원 시험을 deployment blocker로 요구하지 않는다. 향후 LIVE의 destructive migration·rollback 요구사항은 LIVE readiness에서 별도로 확정한다. |
| DB-063 | 초기 관리자 생성과 시스템 기본 설정 seed는 반복 실행해도 중복 생성되지 않아야 한다. |
| DB-064 | 파일에서 읽은 DB 비밀번호는 SQLAlchemy URL에서 percent-encoding하고, Alembic ConfigParser에 주입할 때 `%`를 이중 이스케이프한다. migration 오류에는 완성된 인증 URL이나 비밀번호를 출력하지 않는다. |

### 3.10 보존·백업

현재 `MOCK`/development의 PostgreSQL·Redis와 Decision·Order·execution을 포함한 runtime history는 재생성 가능한 disposable operational data다. 유실 시 Git repository의 migration chain으로 fresh database를 만들고 runtime을 재시작한다. snapshot은 작업 편의를 위한 선택 사항이며 데이터 보존 보장이 아니다. 이 정책은 credential·password·API key를 Git에 넣거나 secret 취급을 완화하지 않는다.

| ID | 요구사항 |
| --- | --- |
| DB-070 | 현재 `MOCK`/development에는 주문·체결·포지션·Decision·execution·설정·위험·감사 runtime data의 최소 보존기간을 두지 않으며 데이터 유실을 허용한다. 향후 LIVE 보존기간과 hold 정책은 LIVE readiness에서 별도로 정의한다. |
| DB-071 | 현재 `MOCK`/development의 백업은 선택 사항이다. `/home/totquf4171/cresta/backups`의 snapshot은 `OPTIONAL_PRE_DEPLOY_SNAPSHOT`이며 암호화·off-host 복제·존재 여부를 deployment blocker로 사용하지 않는다. |
| DB-072 | 현재 `MOCK`/development 복구 기준은 fresh database에 전체 Alembic migration을 적용하고 runtime을 재시작하는 것이다. optional snapshot restore rehearsal은 완료 조건이 아니다. |
| DB-073 | 보존 삭제와 법적·감사 hold 절차는 향후 LIVE 보존정책이 활성화될 때 함께 정의한다. 현재 MOCK data 삭제는 별도 승인된 운영 작업으로만 수행하며 일반 배포가 임의로 data directory를 지우지 않는다. |

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
- 현재 MOCK/development에서 fresh database에 migration head를 적용하고 runtime을 재시작할 수 있다.

## 6. 미결정·보류 항목

- 실제 거래량 기준 파티션 크기와 자동 보관 스케줄
- 향후 LIVE의 보존기간, 암호화 backup, off-host 매체, RPO/RTO와 restore drill

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
| DB-111 | `decision_input_snapshots`는 사용자와 목적, 시장·종목, 기준시각, 시장·지표 snapshot ID, 품질·세션, canonical 입력 JSON과 SHA-256 해시를 불변 저장한다. POSITION 입력 JSON에는 서버가 계산한 포지션 snapshot과 Risk Policy provenance를 포함하되 실제 계좌번호와 사용자 식별자는 모델 입력 JSON 밖의 소유권 metadata로만 저장한다. |
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
| DB-124 | Agent Runtime의 `agent_runs`는 owner, purpose, market·symbol, market snapshot·input hash, DAG·route map, 상태·Core action, valid_until과 unique idempotency key를 저장한다. 기존 DAG version 이력은 보존하고 신규 admission은 현행 `agent-dag-v4`를 사용한다. |
| DB-125 | `agent_stage_runs`는 run+role unique, dependency·route·invocation 참조, 불변 input/output JSON·hash, 상태·오류·시각을 저장하며 완료 stage를 API로 수정하지 않는다. |
| DB-126 | `evidence_items`는 출처·시각·facts·content hash를 저장하고 `evidence_bundles`는 owner·run·상태·evidence ID·canonical hash를 저장한다. 외부 수집이 없으면 `PARTIAL` 빈 bundle을 만들고, OpenDART 같은 검증 source가 있으면 해당 evidence ID를 불변 bundle에 포함한다. |
| DB-127 | `agent_stage_runs.invocation_id`는 stage의 첫 invocation을 참조한다. `llm_invocations.stage_run_id`는 같은 stage의 기본·예비 호출을 각각 보존하며 API는 생성 순서로 반환한다. |
| DB-132 | `agent_stage_runs`는 `lease_owner_id`, `lease_expires_at`, `fencing_token`, `attempt_count`, `max_attempts`, `timeout_at`, `heartbeat_at`을 저장한다. fencing과 attempt는 음수가 아니며 max attempt는 1 이상이어야 한다. |
| DB-133 | worker claim 조회는 `state, available_at, lease_expires_at, created_at, sequence` 인덱스를 사용하고 PostgreSQL에서는 잠금된 행을 건너뛰어 복수 worker가 같은 stage를 claim하지 않게 한다. |
| DB-134 | 완료 stage의 output JSON·hash와 invocation 참조는 수정하지 않는다. 만료 복구도 완료 상태를 `PENDING` 또는 `RUNNING`으로 되돌리지 않는다. |
| DB-135 | 간편 Provider 등록은 모델 목록 검증 성공 뒤 Provider와 발견 Model Profile을 같은 DB transaction으로 저장한다. secret 파일 쓰기 뒤 DB commit 실패 시 새 secret 파일을 제거한다. |
| DB-136 | 발견 모델은 `DRAFT`로 저장하고 사용자가 활성화한 모델만 `VALIDATED`가 된다. 등록·동기화가 기존 ACTIVE route를 자동 변경하지 않는다. |
| DB-137 | 모델 재동기화는 기존 `provider_model_id`를 중복 생성하지 않고 새 모델만 추가하며 기존 검증 모델과 route 이력을 자동 삭제하지 않는다. |
| DB-138 | Provider citation에서 정규화한 source candidate는 기존 `evidence_items`에 run별 중복 URL 없이 `source_tier=UNRATED`, `extraction_method=RULE`로 저장한다. 후보 저장은 불변 `evidence_bundles`를 수정하지 않는다. |
| DB-139 | `agent_stage_runs.role`은 내부 `EVIDENCE_CANDIDATE_AUDITOR`를 허용한다. 감사 결과는 stage `output_json`에 저장하고 별도 LLM invocation이나 EvidenceBundle 수정은 생성하지 않는다. |
| DB-140 | reason code 정책은 코드의 versioned allowlist로 관리하고 각 외부 호출 입력에 정책 버전과 허용 목록을 포함한다. 미등록 code 출력은 `llm_invocations.error_code=LLM_REASON_CODE_NOT_ALLOWED`로 보존하고 완료 stage 출력에는 채택하지 않는다. |
| DB-141 | 검증된 OpenDART 공시는 `source_type=DART_DISCLOSURE`, `source_tier=PRIMARY`, `source_name=OPENDART`, 공식 viewer URL과 안전한 facts만 저장한다. API key와 원문 응답은 저장하지 않는다. |
| DB-142 | `llm_invocations`는 Adapter가 추출한 구조화 model output만 최대 64 KiB canonical JSON, SHA-256과 capture 시각으로 저장한다. Provider envelope·assistant 원문 text·prompt·tool 결과·citation 원문은 저장하지 않는다. |
| DB-143 | 구조화 model output은 server schema 검증 전에 캡처해 미등록 reason code나 evidence reference 같은 실패 분석에도 사용한다. 금지 key 또는 크기 초과 output은 저장하지 않고 invocation을 전용 오류로 fail-closed 처리한다. |
| DB-128 | `llm_model_profiles`는 Provider에 속한 재사용 가능한 모델 카탈로그이며 model 기본 생성 파라미터와 capability 검증 결과를 저장한다. 역할 이름을 model alias나 provider model ID로 강제하지 않는다. |
| DB-129 | `llm_role_routes`는 역할 배정 version으로 사용하고 generation override와 계산 가능한 상속 출처를 저장한다. 같은 owner·scope·role의 `ACTIVE`는 부분 unique 하나만 허용한다. |
| DB-130 | 역할 배정 활성화 transaction은 새 route를 `ACTIVE`, 기존 활성 route를 `SUPERSEDED`로 원자 전환한다. 여러 `VALIDATED` 행은 후보 이력일 뿐 runtime 기본값이 아니다. |
| DB-131 | 기존 중복 `VALIDATED` route migration은 행을 삭제하거나 임의 활성화하지 않는다. 이력으로 보존하고 사용자가 명시적으로 현재 배정을 선택하도록 한다. |
# Provider template migration (2026-08-07)

- Migration `20260807_0017` adds `provider_template_id` and `deleted_at` to `llm_provider_profiles`.
- Provider names are unique per owner only while `deleted_at IS NULL`, so a safely deleted connection name can be reused.
- Provider deletion is a tombstone operation; model, route, invocation, decision, and audit history is not cascaded away.

## Prompt profile migration (2026-08-08)

- Migration `20260808_0018` creates `llm_prompt_profiles` and adds nullable `prompt_profile_id` to `llm_role_routes` for legacy compatibility.
- Prompt rows are immutable after creation except for the `DRAFT → VALIDATED → DISABLED` lifecycle. `(owner_id, role, version_number)` and `(owner_id, role, version_label)` are unique.
- Routes reference a concrete prompt row; superseding a role route never rewrites or deletes its prompt.

## 단순 LLM 실패 정책 migration (2026-08-08)

- Migration `20260808_0019` converts legacy `fallback_policy=NONE` to `FAIL_STOP` and permits only `FAIL_STOP` or `FAILOVER`.
- `fallback_model_profile_ids_json` remains the existing storage field but application validation permits zero models for `FAIL_STOP` and exactly one different validated model for `FAILOVER`.
- A fallback attempt creates a second `llm_invocations` row for the same stage; invocation rows and safe error codes remain queryable after the run finishes.

## LLM route service policy migration (2026-08-10)

- Migration `20260810_0020` adds non-null `llm_role_routes.service_tier` with `DEFAULT`, `PRIORITY`, and `FLEX` values.
- Migration `20260811_0021` adds `llm_role_routes.web_search_enabled` and records `runtime_context_at` plus `web_search_enabled` on every `llm_invocations` attempt.
- `20260811_0021` widens `timeout_ms` to 1–600 seconds and, at that migration point, changed the new-route default to 30 seconds. Historical route values were not rewritten.

## Agent evidence and LLM diagnostic migrations (2026-08-11)

- Migration `20260811_0022` adds `EVIDENCE_CANDIDATE_AUDITOR` to the allowed `agent_stage_runs.role` values. The auditor is deterministic and does not create an LLM invocation.
- Migration `20260811_0023` adds bounded `model_output_json`, `model_output_hash`, and `model_output_captured_at` fields to `llm_invocations`. They store Adapter-extracted structured output for diagnostics, never the credential, request header, raw prompt, or unbounded raw provider response.
- Migration `20260811_0024` changes only the default for newly created `llm_model_profiles.max_output_tokens` to `8192`. Existing model profiles and route snapshots are preserved.
- Migration `20260811_0025` makes model `temperature` nullable so omission means Adapter/provider default, and changes only the new-route `timeout_ms` server default to `120000`. Existing route timeout values are preserved.
- Migration `20260811_0026` adds nullable v4 analysis context, frozen position snapshot and hash, Core shadow assessment, and the `NOT_APPLICABLE` stage state. Existing v1~v3 rows are not rewritten.
- Migration `20260811_0027` adds immutable Market Context snapshots and nullable server-input provenance references to Agent runs. Existing v1~v4 rows are not rewritten.
- `20260811_0027` 이후 migration은 같은 단일 chain에 append하고 이 문서와 `IMPLEMENTATION_STATUS.md`를 함께 갱신한다.

## Agent SHADOW 판단 계약 v2 영속성

| ID | 요구사항 |
| --- | --- |
| DB-144 | `agent_runs`는 불변 `analysis_context`, position snapshot 또는 `NO_OPEN_POSITION` 표지와 그 canonical hash, Core의 nullable `shadow_assessment`를 저장한다. 기존 run은 null과 기존 DAG version으로 의미를 보존한다. |
| DB-145 | `agent_stage_runs.state`는 `NOT_APPLICABLE`을 허용한다. 애플리케이션은 v2 출력 계약을 사용하는 `agent-dag-v4/v5 + ENTRY + POSITION_RISK_SCOUT` 조합에서만 이 상태를 생성하고 다른 역할·context의 사용을 거부한다. |
| DB-146 | `agent-assessment-v2`에서 stage 상태가 `SUCCEEDED`가 아니면 entry·exit 점수는 모두 null이어야 한다. DB JSON 저장 전 서버 검증으로 교차 필드 불변식을 강제한다. |
| DB-147 | migration `20260811_0026_agent_shadow_contract_v2`는 신규 nullable column과 stage 상태 제약만 추가하고 기존 v1~v3 run, stage와 invocation을 재작성하지 않는다. downgrade는 `NOT_APPLICABLE` stage를 `INSUFFICIENT_DATA`로 안전 축소한 뒤 신규 구조를 제거한다. |

## 서버 소유 판단 입력 v1 영속성

| ID | 요구사항 |
| --- | --- |
| DB-148 | `market_context_snapshots`는 source identity, market·symbol, source tier·quality, observed/received/valid 시각, canonical payload JSON과 SHA-256 hash를 불변으로 저장한다. |
| DB-149 | `agent_runs`는 nullable `server_input_policy_version`, `market_context_snapshot_id`, `market_context_snapshot_hash`를 저장한다. 기존 v1~v4 run은 null로 의미를 보존한다. |
| DB-150 | position 파생값과 Risk Policy provenance는 기존 frozen `position_snapshot_json`과 그 hash에 포함한다. 별도 mutable 계산 행이나 stage 실행 시 재계산을 허용하지 않는다. |
| DB-151 | migration `20260811_0027_server_owned_agent_inputs`는 기존 run을 재작성하지 않고 신규 table·nullable column·index와 제약만 추가한다. downgrade는 신규 참조 column을 제거한 뒤 context table을 삭제한다. |

## POSITION Agent 결합 provenance

| ID | 요구사항 |
| --- | --- |
| DB-152 | `agent_runs.purpose`는 기존 `DIAGNOSTIC`과 scheduler 전용 `TRADING_ADVISORY`를 구분한다. advisory row만 unique `basis_decision_id`, `fusion_policy_version`, `fusion_state`를 가지며 진단 row에는 이 필드가 null이어야 한다. |
| DB-153 | advisory의 `fusion_state`는 `PENDING | NO_ESCALATION | ESCALATED | EXPIRED | FAILED_SAFE`만 허용한다. 상향 판단이 생성된 경우 unique `fusion_decision_id`와 안정적인 `fusion_reason_code`를 저장한다. |
| DB-154 | migration `20260817_0038_position_agent_fusion` upgrade는 기존 Agent run을 재작성하지 않고 nullable provenance와 제약을 추가한다. 기준·결합 판단은 별도 불변 `decisions` row로 유지한다. downgrade는 신규 advisory run을 `DIAGNOSTIC` 이력으로 축소한 뒤 결합 provenance column을 제거하며 이미 생성된 불변 판단·실행 이력은 삭제하지 않는다. |

## Cresta v2 ENTRY v7 영속성 계약

이 절은 Phase 2A 역설계와 Phase 2B mapping 결정을 반영한 `agent-dag-v7`의 영속성 기준이다. 기존 `agent-dag-v1`~`agent-dag-v6`, `CORE`, `TRADING_ADVISORY`, `position-agent-fusion-v1` 및 과거 deterministic ENTRY 판단은 당시 의미로 유지하고 이 절의 객체로 소급 해석하거나 backfill하지 않는다.

### v7 Evaluation Run과 목적

| ID | 요구사항 |
| --- | --- |
| DB-157 | 신규 `DecisionRun`을 만들지 않는다. v7 ENTRY evaluation root는 기존 `agent_runs`를 재사용하며 `dag_version=agent-dag-v7`, `analysis_context=ENTRY`로 식별한다. `agent_runs.id`가 evaluation lineage root다. |
| DB-158 | `agent_runs.purpose`는 기존 `DIAGNOSTIC`, 기존 POSITION 전용 `TRADING_ADVISORY`와 v7 scheduler 전용 `TRADING`을 허용한다. 최초 v7 구현은 `DIAGNOSTIC`만 생성하고, activation 이후 production evaluation은 admission부터 `TRADING`이어야 하며 DIAGNOSTIC run이나 결과를 TRADING으로 승격·복사하지 않는다. |
| DB-159 | v7 run은 nullable `policy_profile_version_map_json`, `policy_profile_version_map_hash`, `activation_gate_version_id`, `activation_gate_version_hash` provenance를 사용한다. v7은 policy map을 반드시 가지며 v7 `TRADING`은 유효한 activation gate ID/hash도 반드시 가진다. 기존 v1~v6와 POSITION advisory row는 nullable 상태로 보존하고 backfill하지 않는다. |

`policy_profile_version_map_json`은 Conservative, Balanced, Aggressive의 정확히 세 항목을 agent type 순서로 정규화한 canonical JSON이다. 각 항목은 `configuration_version_id`, category, sequence, agent type과 payload hash를 포함한다. PolicyProfile은 공통 판단 입력이 아니므로 DecisionContext manifest에 포함하지 않는다.

### `decision_contexts`

| ID | 요구사항 |
| --- | --- |
| DB-160 | `decision_contexts`는 v7 AgentRun당 최대 하나인 서버 소유 불변 `decision-context-v1` reference manifest다. 최소 `id`, unique `run_id` FK, `schema_version`, `decision_input_snapshot_id` FK, `evidence_bundle_id` FK, nullable `market_context_snapshot_id` FK, 고정 v7 Scout stage ID 4개, Candidate Audit stage ID, configuration provenance JSON/hash, version manifest JSON/hash, canonical `manifest_json`, `context_hash`, `frozen_at`, `valid_until`, `created_at`을 저장한다. |
| DB-161 | Context는 MarketSnapshot, EvidenceItem, EvidenceBundle 또는 stage output 원문을 복제하지 않는다. `manifest_json`은 기존 immutable row ID와 저장된 hash, 적용 상태·schema/version provenance만 포함하며 각 Scout와 Candidate Audit의 stage ID, role, terminal state와 output hash를 보존한다. |
| DB-162 | v7 market/scout base input은 기존 `decision_input_snapshots`를 `scout-input-v2` schema로 재사용한다. 과거 `scout-input-v1`을 재해석하지 않으며 Context와 향후 finalized Decision은 같은 snapshot ID를 참조한다. |
| DB-163 | `AgentStageRun.state`가 scheduling의 권위 상태이고 structured result의 `status`는 같은 값이어야 한다. Technical, News·Disclosure, Market·Sector Scout는 schema-valid output/hash가 있는 `SUCCEEDED | INSUFFICIENT_DATA | CONFLICTED`, ENTRY의 Position Risk Scout는 이에 더해 명시적 `NOT_APPLICABLE`을 freeze 가능한 terminal로 허용한다. Candidate Audit은 `SUCCEEDED`여야 한다. stage 부재, state/status 불일치, output/hash 부재, `TIMED_OUT | FAILED | INVALID_OUTPUT`은 Context freeze를 거부한다. `INSUFFICIENT_DATA | CONFLICTED | NOT_APPLICABLE`은 누락과 구분해 manifest에 보존하며 AI 계약의 BUY fail-closed 규칙을 적용한다. |
| DB-164 | Context freeze는 모든 필수 Scout와 Candidate Audit terminal 확인, 참조 row 잠금·검증, canonical manifest/hash 계산과 Context insert를 한 transaction에서 수행한다. Context commit 이후에만 세 Decision Agent stage가 runnable하며 commit 전에는 claim할 수 없다. 같은 `run_id + manifest/hash` 재시도는 기존 Context를 반환하고 같은 run의 다른 manifest/hash는 conflict로 fail-closed 한다. Context update·replacement는 금지한다. |
| DB-165 | Context `valid_until`은 DecisionInputSnapshot, EvidenceBundle에 포함된 evidence freshness, MarketContextSnapshot, Scout result와 AgentRun의 유효시각 중 가장 이른 값으로 서버가 계산하고 그 값을 manifest/hash에 포함한다. 호출자가 별도 더 긴 값을 지정할 수 없으며 필수 참조의 유효시각을 계산할 수 없으면 freeze를 거부한다. |
| DB-166 | Context가 참조하는 Scout stage, Candidate Audit stage와 EvidenceBundle은 모두 `run_id`와 같은 AgentRun 소속이어야 한다. 첫 구현은 각 기본 FK와 freeze transaction의 잠금 기반 server validation으로 이를 강제한다. PostgreSQL과 SQLite 간 차이가 큰 불필요한 composite FK 또는 generic context-reference relation table은 도입하지 않으며 same-run 불일치는 insert 전에 rollback한다. |

Context canonical JSON은 UTF-8, Unicode 비 ASCII 문자를 escape하지 않는 JSON, key 사전순 정렬, 배열의 계약상 정규 순서, 불필요한 공백 없는 `,`·`:` separator를 사용한다. hash는 canonical UTF-8 bytes의 lowercase SHA-256 hex다. `schema_version`은 canonicalization 규칙을 포함하며 알 수 없는 상위 version을 자동 해석하지 않는다.

### v7 role과 결과 영속성

| ID | 요구사항 |
| --- | --- |
| DB-167 | `agent_stage_runs.role` allowlist는 기존 역할을 모두 유지하고 `CONSERVATIVE_DECISION`, `BALANCED_DECISION`, `AGGRESSIVE_DECISION`, `ENTRY_ARBITER`를 추가한다. DB allowlist는 역사적 역할의 합집합이고 애플리케이션의 versioned DAG validation이 v1~v6 run에 신규 role 삽입을 거부한다. |
| DB-168 | `llm_role_routes`와 `llm_prompt_profiles`에는 세 Decision Agent role만 추가한다. `ENTRY_ARBITER`는 internal stage이므로 `route_id`, `invocation_id`가 모두 null이어야 하며 LlmRoleRoute, PromptProfile, ModelProfile과 LlmInvocation을 만들 수 없다. |
| DB-169 | DecisionAgentResult 전용 table을 만들지 않는다. 세 Decision Agent의 `AgentStageRun.output_json/output_hash`에 `decision-agent-result-v1`을 저장하고 stage role과 `agent_type`을 일대일 검증한다. stage input hash는 DecisionContext ID/hash, 해당 PolicyProfile ID/hash, route/input contract version에 결합하며 완료 output과 hash를 수정하지 않는다. |
| DB-170 | ArbiterResult 전용 table을 만들지 않는다. `ENTRY_ARBITER` stage의 `output_json/output_hash`가 `entry-consensus-v1` ArbiterResult이며 Context ID/hash, C/B/A result stage ID/hash, agent type 순서로 정규화된 result 목록, consensus policy version, decision pattern, action, reason code와 schema version을 보존한다. `(run_id, role)` unique로 한 run의 Arbiter를 최대 하나로 제한하고 동일 result IDs/hashes와 consensus policy version은 동일 canonical output/hash를 생성해야 한다. |

### PolicyProfile

| ID | 요구사항 |
| --- | --- |
| DB-171 | PolicyProfile은 별도 table이나 PromptProfile/RoleRoute로 표현하지 않고 system-owned `configuration_versions`를 재사용한다. category는 `V7_ENTRY_POLICY_CONSERVATIVE`, `V7_ENTRY_POLICY_BALANCED`, `V7_ENTRY_POLICY_AGGRESSIVE`로 고정하고 `scope=SYSTEM`, `target_id=MOCK`을 첫 deployment target으로 사용한다. 각 payload는 `policy-schema-v1`, 일치하는 agent type, policy parameters와 validation metadata를 포함하며 payload/hash는 기존 ConfigurationVersion 불변 계약을 따른다. |
| DB-172 | v7 admission은 세 category에서 정확히 한 개씩의 ACTIVE version을 같은 transaction snapshot으로 선택해 DB-159의 canonical map에 고정한다. 누락·중복·agent type/category 불일치·payload hash 불일치는 admission을 거부하며 실행 중 ACTIVE 교체가 기존 run map을 변경하지 않는다. |

### Finalized ENTRY Decision lineage와 멱등성

| ID | 요구사항 |
| --- | --- |
| DB-173 | 기존 `decisions`에 v7 전용 nullable `source_agent_run_id` FK, `source_stage_run_id` FK와 `source_stage_output_hash`를 둔다. 세 값은 all-or-none이다. 값이 있는 Decision은 `purpose=TRADING`, `decision_kind=ENTRY`, source run `agent-dag-v7 + purpose=TRADING + analysis_context=ENTRY`, source stage `role=ENTRY_ARBITER`, `source_stage.run_id=source_agent_run_id`, 저장 hash와 immutable stage output hash 일치를 모두 만족해야 한다. |
| DB-174 | v7 sourced ENTRY Decision은 동일 source run과 source Arbiter stage별 최대 하나다. 논리 uniqueness는 필수이며 migration은 PostgreSQL partial unique와 SQLite가 동일 의미를 검증할 수 있는 nullable unique index/constraint 전략을 각각 명시한다. legacy Decision의 source 필드는 모두 null이고 backfill하지 않는다. |
| DB-175 | Finalizer는 기존 `decisions.evaluation_request_id` unique를 재사용한다. identity material은 `schema_version=entry-finalization-identity-v1`, AgentRun ID, DecisionContext ID/hash, ENTRY_ARBITER stage ID/output hash와 consensus policy version을 key 사전순·공백 없는 canonical JSON으로 직렬화하고 UTF-8 SHA-256을 계산한다. 저장값은 `v7fin-` 뒤에 hash 앞 58 hex를 붙인 64자 문자열이다. |
| DB-176 | Finalizer transaction의 commit 결과가 불명확하면 같은 `evaluation_request_id`를 먼저 조회한다. 기존 row의 source run/stage/hash, Context ID/hash와 consensus policy version이 전부 일치하면 기존 Decision을 반환하고 하나라도 다르면 idempotency conflict로 fail-closed 한다. 새 FinalizationIdentity table은 만들지 않는다. |

### v7 TRADING Activation Gate

| ID | 요구사항 |
| --- | --- |
| DB-177 | Activation Gate는 별도 table이나 ExecutionStage 결합으로 표현하지 않고 system-owned `configuration_versions` category `V7_ENTRY_ACTIVATION`을 사용한다. 첫 target은 `scope=SYSTEM`, `target_id=MOCK`이다. ConfigurationVersion의 `ACTIVE` lifecycle과 payload의 `gate_state=OPEN | CLOSED`는 별개이며 ACTIVE row 부재, CLOSED, schema/hash 오류, 만료 또는 target version 불일치는 CLOSED와 동일하게 처리한다. |
| DB-178 | `activation-gate-v1` payload는 target DAG, DecisionContext·DecisionAgentResult schema, 세 PolicyProfile ID/hash map, consensus policy, prompt/model/route version map, safety evidence descriptor 목록, canonical version snapshot/hash, validation policy version과 유효시간을 포함한다. 알 수 없는 필드·schema 또는 부분 version snapshot은 OPEN으로 해석하지 않는다. |
| DB-179 | 각 safety evidence descriptor는 `test_id`, requirement IDs, `result=PASSED`, code revision 또는 동등 build identity, TEST_PLAN/spec version, `executed_at`, `valid_until` 또는 명시적 freshness contract, `evidence_ref`, `evidence_hash`를 모두 가진다. 단순 boolean 통과값은 허용하지 않으며 activation acceptance set 전체가 존재·PASSED·target version 일치·freshness·hash 검증을 통과해야 gate validation이 성공한다. |
| DB-180 | v7 TRADING run admission은 당시 ACTIVE+OPEN gate ID/hash를 AgentRun에 freeze한다. Finalizer는 현재 gate를 다시 조회해 run freeze ID/hash, ACTIVE+OPEN 상태, DAG/policy/schema/route map과 Context/Arbiter 유효성을 재검증한다. gate가 superseded·CLOSED·변경되면 기존 run을 새 gate로 승격하지 않고 finalization을 거부한다. ExecutionStage는 이 검증과 독립적으로 Decision 이후 Approval/Order admission을 제어한다. |

### 보존·삭제와 compatibility

| ID | 요구사항 |
| --- | --- |
| DB-181 | v7 AgentRun, DecisionContext, source Scout/Arbiter stage, EvidenceBundle과 finalized Decision은 판단 보존기간 동안 애플리케이션 API로 UPDATE·DELETE하지 않는다. 신규 Context 및 Decision source FK는 `ON DELETE RESTRICT` 또는 동등 `NO ACTION`을 사용하고 surviving Decision의 lineage를 `SET NULL`이나 cascade로 조용히 제거하지 않는다. |
| DB-182 | Phase 3 migration은 신규 table, additive role allowlist, nullable provenance와 source constraint만 추가한다. 기존 v1~v6 AgentRun·CORE·POSITION advisory·deterministic ENTRY Decision을 재작성하거나 Context/source lineage를 backfill하지 않는다. v7 Context 또는 sourced Decision이 존재하는 DB의 downgrade는 lineage를 삭제·null 처리하지 않고 명시적으로 거부하며, v7 row가 없는 경우에만 신규 구조를 제거한다. |

### v7 upstream runtime 영속 계약

이 절은 Phase 4 구현 전에 확정하는 기존 table 재사용 계약이다. Phase 3A~3C의 schema·freeze·PolicyProfile 계약을 변경하지 않으며 Phase 4B 자체는 migration이나 runtime을 만들지 않는다.

| ID | 요구사항 |
| --- | --- |
| DB-183 | `scout-input-v2`는 기존 `decision_input_snapshots`의 불변 `input_json/input_hash`에 저장한다. canonical input의 정확한 top-level field는 `schema_version`, `user_id`, `purpose`, `analysis_context`, `snapshot_id`, `market`, `symbol`, `observed_at`, `valid_until`, `data_quality`, `session_state`, `quote`, `indicators`, `position`, `open_orders`, `account_risk_summary`, `market_context`, `strategy`, `configuration_version`, `prior_decision_summary`, `server_input_policy_version`, `market_snapshot_provenance`, `indicator_provenance`, `market_context_provenance`다. v1 공통 field의 이름·null 의미·Decimal/time canonicalization을 유지하며 hash는 DB-160 아래의 canonical JSON 규칙을 사용한다. |
| DB-184 | `market_snapshot_provenance`는 snapshot ID·payload hash·source·event/received time, `indicator_provenance`는 nullable snapshot ID·calculator/input version·payload hash·validity, `market_context_provenance`는 nullable snapshot ID/hash/schema/quality/observed/received/valid time을 보존한다. input의 `user_id`는 내부 user UUID이고 계좌번호·로그인 식별자·비밀을 포함하지 않는다. `prior_decision_summary`는 v1 naming compatibility를 위한 명시적 null이다. EvidenceBundle·Scout output·DecisionContext·PolicyProfile·Decision Agent·Arbiter·주문·실행 data는 저장하지 않는다. |
| DB-185 | `scout-input-v2.valid_until`과 `input_hash`는 AI-252~254의 source provenance 및 minimum-validity 계약으로 서버가 계산한다. 동일 source와 policy version은 동일 canonical input/hash를 만들고 PolicyProfile 변경은 이를 변경하지 않는다. 필수 source hash/version/validity 누락·불일치·만료는 v7 admission을 거부한다. |
| DB-186 | Phase 4 v7 admission transaction은 DecisionInputSnapshot, 네 Scout route version snapshot, DB-172 policy map, AgentRun과 upstream 7개 AgentStageRun을 원자적으로 고정한다. C/B/A Decision Agent, `ENTRY_ARBITER`, `CORE` stage는 materialize하지 않으며 실패 시 partial row를 commit하지 않는다. 이는 DB-167의 최종 role allowlist나 MAO-200의 11-stage 논리 DAG를 축소하지 않는다. |
| DB-187 | v7 upstream 완료 checkpoint는 새 column/state 없이 `AgentRun.state=RUNNING`, 필수 upstream terminal AgentStageRun과 unique DecisionContext 존재로 표현한다. DecisionContext Freeze는 Candidate Audit commit 뒤 별도 transaction이며 동일 manifest retry·conflict는 DB-164를 그대로 적용한다. v1~v6 terminal CORE finalization 의미는 변경하지 않는다. |
| DB-188 | `evidence-verifier-v2`와 `evidence-candidate-audit-v2`는 별도 table 없이 해당 AgentStageRun의 immutable `output_json/output_hash`에 저장한다. schema별 필드·freshness·canonical ordering은 MAO-228~231을 따르고, v1~v6 historical output을 backfill·재해석하지 않는다. |
| DB-189 | v7 Scout AgentStageRun의 `input_hash`는 MAO-233~235의 `scout-role-input-v1` canonical material에 결합한다. DecisionInputSnapshot·EvidenceBundle·route와 역할별 nullable provenance는 저장된 ID/hash로 해석 가능해야 하며 다른 Scout output 또는 PolicyProfile을 포함하지 않는다. |
| DB-190 | v7 upstream route snapshot은 기존 네 Scout LlmRoleRoute row의 immutable identity/version/hash를 재사용한다. `CORE` route를 요구하거나 동일 Scout route row를 v7용으로 복제하지 않으며 v1~v6 route provenance를 변경하지 않는다. |

### 11.7 v7 Decision Agent runtime persistence

| ID | 요구사항 |
| --- | --- |
| DB-191 | Phase 7 이후 신규 v7 AgentRun의 route version map은 네 Scout와 C/B/A Decision role의 정확히 일곱 route identity/version/hash를 가진다. `ENTRY_ARBITER`와 `CORE`는 포함하지 않는다. Phase 4~6에 이미 생성된 네-route run은 수정·backfill하지 않으며 route map shape로 historical run을 invalid 처리하지 않는다. |
| DB-192 | 각 C/B/A AgentStageRun `input_hash`는 exact `decision-agent-stage-input-v1` canonical material의 SHA-256이다. exact field set은 `schema_version`, `decision_context_id`, `decision_context_hash`, `role`, `agent_type`, `policy_profile_id`, `policy_profile_hash`, `route_id`, `route_version`, `route_version_hash`, `prompt_profile_id`, `prompt_version`, `prompt_hash`, `requested_model_profile_id`, `input_contract_version=decision-agent-input-v1`이다. 다른 Decision Agent 결과·정책, Scout route map 또는 호출자가 만든 hash는 포함하지 않는다. |
| DB-193 | `decision-agent-result-v1`은 별도 table 없이 C/B/A AgentStageRun의 immutable `output_json/output_hash`에 저장한다. output hash는 AI-260~264의 exact server-owned result를 canonicalize하며 terminal stage state는 result status와 AI-261 matrix대로 일치해야 한다. 권위 terminal C/B/A stage에 null result/hash를 허용하지 않는다. |
| DB-194 | decision-stage reconciliation은 DB-164 Context reconciliation과 분리된 transaction이다. `(run_id, role)` unique constraint와 저장된 input/route/prompt/policy hash를 이용해 정확한 세 stage를 원자적·멱등적으로 만들며 exact partial retry만 복구하고 mismatch에서는 기존 row를 변경하지 않는다. 새 table·state·column은 요구하지 않는다. |
| DB-195 | Decision Agent result의 `valid_until`은 DecisionContext `valid_until`과 정확히 같고 Policy/route/prompt lifecycle timestamp가 이를 연장하지 않는다. completion transaction은 run/stage row와 fencing을 잠근 뒤 Context same-run/hash/expiry 및 frozen Policy/route/prompt/input hash를 다시 검증하고 output/state를 한 transaction에서 commit한다. |
| DB-196 | claim transaction은 lease/fencing만 짧게 commit하고 Provider network call과 immutable input resolution 동안 DB row lock을 보유하지 않는다. completion 또는 권위 recovery transaction만 structured result/hash와 terminal state를 함께 기록하며 stale fencing token update는 0행이어야 한다. |

### 11.8 ENTRY_ARBITER runtime persistence

| ID | 요구사항 |
| --- | --- |
| DB-197 | `entry-arbiter-input-v1`의 exact field는 `schema_version`, `decision_context_id`, `decision_context_hash`, `policy_version`, `input_results`, `valid_until`이다. `input_results`는 C/B/A 순서의 정확히 세 항목이고 item exact field는 `role`, `agent_type`, `stage_run_id`, `output_hash`, `status`, `action`이다. 전체 canonical JSON의 UTF-8 SHA-256이 ENTRY_ARBITER stage `input_hash`다. |
| DB-198 | arbiter-stage reconciliation은 같은 run/context의 역할별 한 terminal C/B/A stage를 잠그고 structured Result, canonical output hash, role/type, state/status, frozen Policy provenance와 Context/Result validity를 검증한 뒤 ENTRY_ARBITER를 별도 transaction에서 materialize한다. 구조 오류·cross-run/context·만료에서는 stage를 만들지 않는다. |
| DB-199 | ENTRY_ARBITER는 `(run_id, role)` unique를 재사용하며 route_id와 invocation_id가 null이고 dependency JSON은 C/B/A 세 role의 canonical AND 목록이다. 같은 input hash 재시도는 기존 row를 반환하고 다른 hash 또는 identity의 기존 row는 수정하지 않고 conflict로 종료한다. 새 table·column·state·migration은 요구하지 않는다. |
| DB-200 | `entry-consensus-v1` exact field는 `schema_version`, `decision_context_id`, `decision_context_hash`, `action`, `policy_version`, `input_result_ids`, `input_results`, `decision_pattern`, `reason_codes`, `valid_until`이다. `input_results`는 DB-197과 같은 ordered item 목록이고 `input_result_ids`는 그 stage IDs와 같은 순서로 정확히 일치한다. |
| DB-201 | ArbiterResult `valid_until`은 DecisionContext와 세 DecisionAgentResult validity에 정확히 일치한다. runtime timestamp는 Result payload에 넣지 않고 AgentStageRun lifecycle metadata로만 저장하며 exact canonical Result JSON의 SHA-256을 `output_hash`로 저장한다. |
| DB-202 | 구조적으로 유효한 non-success C/B/A Result를 평가한 UNKNOWN consensus는 ENTRY_ARBITER `SUCCEEDED`와 non-null canonical output/hash다. materialization 뒤 provenance/input mismatch는 `CONFLICTED`, expiry는 `TIMED_OUT`, evaluator internal failure는 `FAILED`이고 세 상태 모두 output_json/output_hash가 null이다. |
| DB-203 | claim/completion transaction은 C/B/A stage identity와 output hash, strict Result, Context identity/hash/expiry, stored/rebuilt Arbiter input hash, route/invocation null과 fencing을 다시 검증한다. stale fencing completion은 0행이고 terminal output을 update·replace하지 않는다. |
| DB-204 | ENTRY_ARBITER는 LlmRoleRoute, PromptProfile, ModelProfile, LlmInvocation 또는 별도 result/lineage table을 만들지 않는다. 향후 Finalizer는 기존 AgentRun, DecisionContext, ordered C/B/A stage IDs/hashes, exact Arbiter stage/output hash와 consensus policy/validity로 lineage를 검증한다. |

### 11.9 Activation Gate와 sourced v7 Decision finalization

Activation payload의 exact schema·ordering·hash source of truth는
`CONFIGURATION_SPEC.md` CFG-104~111이고 Finalizer 행동·API mapping은
`AI_DECISION_SPEC.md` AI-276~286이다. `V7_ENTRY_ACTIVATION`은 기존
ConfigurationVersion과 AgentRun의 `activation_gate_version_id/hash`를 재사용하며 새 Gate
table을 만들지 않는다.

`sourced-entry-decision-v1` row의 exact persistence는 다음과 같다.

| Decision field | exact value/nullability |
| --- | --- |
| `purpose`, `decision_kind` | `TRADING`, `ENTRY` |
| `evaluation_request_id` | canonical `v7fin-` 64-char identity |
| `decision_input_id` | DecisionContext의 DecisionInputSnapshot ID |
| `input_snapshot_id`, `symbol`, `market` | frozen DecisionInputSnapshot provenance |
| `schema_version` | `sourced-entry-decision-v1` |
| `action` | ArbiterResult의 `BUY | WAIT | REJECT | UNKNOWN` exact value |
| `reason_codes_json`, `valid_until` | ArbiterResult의 canonical reason list와 validity |
| `source_agent_run_id`, `source_stage_run_id`, `source_stage_output_hash` | v7 TRADING run, exact ENTRY_ARBITER stage, canonical output hash; 모두 non-null |
| `validation_status` | `VALID` |
| `model_provider`, `model_id`, `prompt_version` | `null` |
| `scout_output_json`, `core_output_json` | `null` |
| `confidence`, `risk_level`, `latency_ms` | `null` |
| `configuration_version_id`, `execution_mode`, `execution_outcome` | `null` |

위 sourced Decision row는 불변이므로 후속 실행도 마지막 두 execution field를 update하지
않고 기존 `decision_executions`에 mode/outcome을 append한다.

Phase 9C additive migration은 `decisions.schema_version`의 저장 길이를 16에서 32로
확대해 `sourced-entry-decision-v1`을 손실 없이 저장하고, `ck_decisions_action`에 `UNKNOWN`을 추가하며
`model_provider`, `model_id`, `prompt_version`, `scout_output_json`, `core_output_json`,
`confidence`, `risk_level`, `latency_ms`, `execution_outcome`만 nullable로 전환한다.
`ck_decisions_execution_outcome`은 null 또는 기존 네 값만 허용하도록 바꾼다. source
all-or-none FK와 두 partial unique index는 0039를 그대로 재사용한다. 새 Context column,
FinalizationIdentity table, legacy column 삭제·rename과 legacy row backfill은 없다.

간단한 representation CHECK는 source가 모두 null인 legacy row가 위 아홉 legacy field를
모두 non-null로 유지하고, source가 모두 non-null인 row는
`schema_version=sourced-entry-decision-v1`이며 위 아홉 field가 모두 null,
`validation_status=VALID`, `purpose=TRADING`, `decision_kind=ENTRY`,
`action IN (BUY, WAIT, REJECT, UNKNOWN)`임을 보장한다. 나머지
cross-table DB-173 의미는 FK만으로 표현할 수 없으므로 Finalizer application validator가
run DAG/purpose/context, stage role/run/state/null route/invocation과 canonical output hash를
insert 전과 write boundary에 검사한다.

Finalizer transaction은 다음 순서다.

```text
BEGIN
1. source AgentRun lock
2. purpose/DAG/analysis-context validation
3. DecisionContext integrity and DB-time expiry validation
4. C/B/A + ENTRY_ARBITER lineage/schema/output hash validation
5. frozen/current Activation Gate lock and validation
6. entry-finalization-identity-v1 build
7. evaluation_request_id와 source unique 기존 Decision lookup
8. existing row exact immutable payload/lineage comparison
9. immutable Decision insert
10. flush
11. source, Gate identity/state/evidence와 expiry write-boundary recheck
12. Decision, FINALIZATION_SUCCEEDED audit, AgentRun SUCCEEDED/completed_at COMMIT
```

step 11에서 DB-authoritative time이 Context/Arbiter/Gate validity 이상이거나 current ACTIVE
Gate ID/hash/state/evidence가 frozen provenance와 달라지면 insert를 rollback한다. network,
Provider와 Execution side effect는 transaction 안팎 모두 없다. terminal denial/failure는
Decision 없이 AuditLog와 run terminal transition을 한 transaction으로 commit한다.

동일 identity retry는 source lineage뿐 아니라 위 표의 모든 immutable Decision field가
exact match일 때만 기존 row를 반환한다. mismatch는 기존 row를 update하지 않고
`FINALIZATION_IDENTITY_CONFLICT`다. PostgreSQL concurrent insert의 loser는 IntegrityError를
rollback하고 authoritative row를 재조회해 같은 exact 비교를 수행한다. commit outcome이
불명확한 retry는 insert보다 `evaluation_request_id` 조회가 먼저다.
이미 `SUCCEEDED`인 run의 exact Decision 재조회는 기존 row만 반환하고 completed_at 또는
success audit을 추가하지 않는다. run lock과 terminal-state check로 authoritative
`FINALIZATION_SUCCEEDED` audit은 run당 최대 하나다.

Finalization audit은 기존 append-only `audit_logs`를 사용한다. `actor_type=SYSTEM`,
`actor_id=AgentRun.owner_id`, `target=AgentRun.id`, `correlation_id=AgentRun.id`다. `action`과
`result` exact mapping은 다음과 같다.

| action | result | terminal/retry |
| --- | --- | --- |
| `FINALIZATION_SUCCEEDED` | `SUCCEEDED` | terminal success |
| `ACTIVATION_GATE_CLOSED` | `BLOCKED` | terminal safety denial |
| `ACTIVATION_GATE_SUPERSEDED` | `BLOCKED` | terminal safety denial |
| `ACTIVATION_GATE_INVALID` | `INVALID` | terminal failure |
| `SOURCE_EXPIRED` | `EXPIRED` | terminal failure |
| `SOURCE_CONFLICTED` | `CONFLICTED` | terminal failure |
| `FINALIZATION_IDENTITY_CONFLICT` | `CONFLICTED` | terminal failure |
| `FINALIZATION_DB_RETRYABLE_FAILURE` | `RETRYABLE_FAILURE` | non-terminal retry |
| `ACTIVATION_GATE_DB_RETRYABLE_FAILURE` | `RETRYABLE_FAILURE` | pre-run admission retry |

`metadata_json`은 strict `finalization-audit-v1`로 `schema_version`, `agent_run_id`,
`decision_id`, `evaluation_request_id`, `decision_context_id`, `source_stage_run_id`,
`source_stage_output_hash`, `activation_gate_version_id`, `activation_gate_version_hash`,
`retryable`의 정확히 열 field를 모두 가진다. unavailable value는 JSON null이고
`retryable`은 위 두 `*_DB_RETRYABLE_FAILURE` action에서만 true다. success audit은 Decision/run과 같은 transaction,
terminal failure audit은 terminal run transition과 같은 transaction이다. DB operation이
rollback된 retryable failure는 별도 짧은 transaction으로 run error와 audit을 저장한다.
metadata JSON은 UTF-8, `ensure_ascii=false`, key 사전순, compact separator로 canonicalize하고
unknown/missing field를 허용하지 않는다.
그 transaction조차 DB 장애로 불가능하면 운영 log만 임시 사용하고, DB가 복구된 첫
reconciliation이 새 finalization 시도 전에 해당 failure audit을 영속한다.

TRADING admission이 run 생성 전에 Gate 부재 또는 CLOSED로 거부되면 같은 AuditLog
`action=ACTIVATION_GATE_CLOSED`, `result=BLOCKED`를 사용하고, malformed·evidence/hash
invalid·ACTIVE ambiguity는 `action=ACTIVATION_GATE_INVALID`, `result=INVALID`를 사용한다.
이때 `target=V7_ENTRY_ACTIVATION:MOCK`, `correlation_id`는 scheduler evaluation request UUID,
`actor_type=SYSTEM`, `actor_id`는 대상 owner ID다. metadata는 같은
`finalization-audit-v1` shape에서 `agent_run_id`, Decision/source identity와 frozen Gate가
없으면 null이고 발견된 Gate ID/hash만 가능한 범위에서 기록한다. admission DB read/lock
failure는 run을 만들지 않고 scheduler의 기존 retry/backoff를 사용하며, DB가 사용 가능한
별도 transaction에서 `action=ACTIVATION_GATE_DB_RETRYABLE_FAILURE`,
`result=RETRYABLE_FAILURE`, `retryable=true`로 영속한다.

| ID | 요구사항 |
| --- | --- |
| DB-205 | sourced v7 Decision은 위 exact physical mapping과 `sourced-entry-decision-v1` discriminator를 사용하며 semantic value가 없는 legacy field를 sentinel로 채우지 않는다. |
| DB-206 | Phase 9C migration은 schema_version 길이 32 확대, UNKNOWN action, exact 아홉 nullable field, nullable execution outcome CHECK와 representation CHECK만 additive 적용하고 legacy row를 변경하지 않는다. |
| DB-207 | Finalizer application validator는 DB-173의 cross-table run/stage/hash 의미와 Context/C/B/A/Arbiter/Gate validity를 insert 전과 write boundary에 검증한다. |
| DB-208 | finalization transaction은 위 12-step ordering을 따르고 Decision·success audit·run terminal transition을 원자적으로 commit한다. |
| DB-209 | 동일 identity 또는 source unique retry는 exact immutable payload까지 일치할 때만 기존 Decision을 반환하며 concurrent/ambiguous commit도 재조회 후 같은 비교를 사용한다. |
| DB-210 | Finalizer outcome은 위 exact AuditLog action/result와 strict metadata로 영속하고 logs-only denial을 허용하지 않는다. |
| DB-211 | Gate denial·source expiry/conflict·identity conflict는 Decision 0건과 terminal audit/run 상태이고 transient DB failure는 rollback 후 non-terminal retry 상태를 보존한다. |
| DB-212 | sourced v7 row가 하나라도 있거나 UNKNOWN/nullable representation을 기존 schema가 표현할 수 없으면 downgrade를 명시적으로 거부하며 coercion, sentinel backfill 또는 lineage 삭제를 하지 않는다. |
| DB-213 | run 생성 전 Gate admission denial과 transient DB failure도 위 exact AuditLog representation으로 영속하며 admission failure에서 partial AgentRun/stage를 남기지 않는다. |

Activation acceptance set의 구체적인 test ID는 [테스트 계획](../TEST_PLAN.md)의 `Cresta v2 ENTRY Decision Architecture` 절을 따른다. AI 행동 의미는 [AI 판단 계약](AI_DECISION_SPEC.md), v7 stage 순서와 authority는 [다중 에이전트 오케스트레이션 명세](MULTI_AGENT_ORCHESTRATION_SPEC.md), Activation Gate와 ExecutionStage의 분리는 [판단 실행·승인 명세](DECISION_EXECUTION_SPEC.md)를 따른다.

### 11.10 sourced ENTRY execution authority persistence

Phase 10은 별도 ExecutionStage lifecycle table을 만들지 않는다. current stage는
`ConfigurationVersion(SYSTEM, MOCK, V7_ENTRY_EXECUTION_STAGE)`, frozen lifecycle은 기존
`decision_executions`를 사용한다. additive migration은 legacy row를 재분류하지 않고 다음
shape를 추가한다.

`decision_executions`에는 nullable `contract_version`,
`execution_stage_version_id`(ConfigurationVersion RESTRICT FK),
`execution_stage_payload_hash`를 추가한다. sourced row는
`contract_version=sourced-entry-execution-v1`, existing `execution_key`는
`entry-execution-identity-v1`의 `v7exe-<64 lowercase hex>`다. 기존 non-null `mode`와 `stage`는
sourced contract에 한해 nullable로 바꾸고 conditional CHECK를 둔다. `NO_ACTION` 및
config selection 전 `FAILED_SAFE`(`SOURCE_AUTHORITY_INVALID | DECISION_EXPIRED |
EXECUTION_STAGE_UNAVAILABLE`)는 mode/stage/stage ID/hash가 모두 null이다. BUY가 authority
selection을 통과한 lifecycle은 mode/stage/stage ID/hash가 모두 non-null이다. legacy row는
mode/stage non-null을 계속 요구한다.
`contract_version=sourced-entry-execution-v1`인 `decision_id` partial unique index로 Decision당
exact-one을 DB에서도 강제한다. legacy row의 새 field는 null로 보존하고 기존 key 의미를
바꾸지 않는다.

`guard_evaluations.execution_id`는 nullable DecisionExecution FK로 유지하고 nullable
`stop_trigger_id`(StopTrigger RESTRICT FK)를 추가한다. 신규 row conditional CHECK는 다음과
같다.

```text
subject_type = DECISION_EXECUTION
  => execution_id IS NOT NULL AND stop_trigger_id IS NULL
     AND subject_id = execution_id

subject_type = STOP_TRIGGER
  => execution_id IS NULL AND stop_trigger_id IS NOT NULL
     AND subject_id = stop_trigger_id
```

기존 valid Decision Guard는 그대로 둔다. historical SQLite STOP_TRIGGER row는 subject_id가
실제 StopTrigger를 가리킬 때만 deterministic하게 `stop_trigger_id=subject_id`,
`execution_id=null`로 교정하고, 대상이 없거나 ambiguous하면 migration을 명시적으로
거부한다. PostgreSQL FK 위반을 SQLite 성공으로 정상 취급하지 않는다.

`order_intents`에는 nullable `source_type`, `source_id`, `decision_execution_id`,
`stop_trigger_id`, `guard_evaluation_id`, `approval_id`, `execution_policy_version_id`,
`risk_policy_version_id`, `execution_stage_version_id`, `execution_stage_payload_hash`와
`authority_key`를 추가한다. 신규 authority row의 exact source enum은
`DECISION_EXECUTION | STOP_TRIGGER | BROKER_DIAGNOSTIC | LEGACY_EXECUTION | BROKER_IMPORTED`다.
명시적 FK는 각각 기존 source/Guard/Approval/ConfigurationVersion을 RESTRICT로 참조한다.
`authority_key`는 initial authoritative intent identity이고 신규 non-import source에서 non-null
unique다. TradingOrder는 기존 non-null intent_id를 통해 provenance를 따라가며 lineage를
중복 저장하지 않는다.

DECISION_EXECUTION source conditional contract는 decision_execution_id와
guard_evaluation_id가 non-null이고 source_id=decision_execution_id다. automatic이면
approval_id=null, manual이면 approval_id가 non-null이고 Order 생성 transaction에서
APPROVED여야 한다. STOP_TRIGGER source는 stop_trigger_id와 Guard가 non-null이고 stage/risk
provenance를 가진다. BROKER_DIAGNOSTIC은 별도 diagnostic request identity를 source_id와
authority_key에 보존한다. BROKER_IMPORTED는 이미 Broker에서 관측한 주문이고 신규 send
대상이 아니다.

`orders.status`에는 unsent terminal `INVALIDATED`를 additive로 추가한다. CREATED에서만
authority revocation으로 전이할 수 있고 quantity invariant는 requested=remaining인 채
유지한다. event exact type은 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND`다. migration 이전
unclassified row의 provenance column은 null로 남긴다. unclassified CREATED row는 migration
중 source를 추측하지 않고 이후 pre-send/reconciliation에서
`INVALIDATED / ORDER_SOURCE_UNCLASSIFIED`로 닫는다. 이미 SUBMITTING 이상인 row는 기존
ambiguous-send/reconciliation lifecycle을 유지한다.

Approval의 기존 unique execution_id와 version을 재사용하고 `reauth_proof_id`를 실제
`reauth_proofs.id` RESTRICT FK, `order_id`를 `orders.id` RESTRICT FK로 정합화한다. application
CAS는 PENDING+owner+expected_version 조건부 갱신이며 같은 transaction에서 proof를 소비한다.

| ID | 요구사항 |
| --- | --- |
| DB-214 | sourced DecisionExecution은 `contract_version=sourced-entry-execution-v1`, canonical `v7exe-` key와 sourced decision_id partial unique로 Decision당 exact-one을 강제한다. policy/stage 변경은 새 row를 허용하지 않는다. |
| DB-215 | authority selection을 통과한 sourced BUY DecisionExecution은 current stage ConfigurationVersion ID/hash와 mode/stage를 non-null freeze한다. NO_ACTION과 pre-selection FAILED_SAFE는 mode/stage/provenance가 모두 null이며 conditional representation CHECK로 default stage 합성을 금지한다. |
| DB-216 | stage control-plane은 기존 ConfigurationVersion exact-one ACTIVE lifecycle을 재사용하며 별도 ExecutionStage table을 만들지 않는다. |
| DB-217 | GuardEvaluation은 nullable execution_id와 stop_trigger_id의 위 conditional subject CHECK/FK를 사용한다. StopTrigger ID를 DecisionExecution FK에 저장하는 현행 misuse는 P0 migration blocker다. |
| DB-218 | historical valid STOP_TRIGGER subject는 실제 target exact match일 때만 deterministic 교정하고 orphan/ambiguous row가 있으면 migration을 거부한다. legacy Decision Guard는 재작성하지 않는다. |
| DB-219 | OrderIntent는 위 exact source/provenance columns와 source enum을 저장하고 authority_key unique로 같은 initial authority의 intent/order 중복을 차단한다. |
| DB-220 | DECISION_EXECUTION source는 execution/Guard와 optional Approval link가 서로 같은 authority chain이어야 한다. automatic은 approval null, manual은 APPROVED Approval required를 application validator와 FK/unique로 강제한다. |
| DB-221 | STOP_TRIGGER, BROKER_DIAGNOSTIC, LEGACY_EXECUTION, BROKER_IMPORTED는 source별 validator를 사용하며 unknown/null 신규 source는 authority를 갖지 않는다. BROKER_IMPORTED는 send candidate가 아니다. |
| DB-222 | TradingOrder는 OrderIntent FK를 통해 source chain을 결정적으로 추적하고 Decision/Arbiter lineage를 중복 저장하지 않는다. replacement Order는 같은 intent/order_group lifecycle이며 새 DecisionExecution이 아니다. |
| DB-223 | orders CHECK에 unsent terminal `INVALIDATED`를 추가하고 CREATED authority revocation은 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` OrderEvent와 audit을 같은 transaction에 저장한다. |
| DB-224 | migration 이전 null-source CREATED Order는 backfill하지 않고 runtime reconciliation에서 INVALIDATED/ORDER_SOURCE_UNCLASSIFIED로 닫는다. SUBMITTING 이후 row는 existing external-side-effect recovery를 유지한다. |
| DB-225 | Approval은 execution당 최대 하나를 유지하고 owner+PENDING+expected_version CAS, target-bound one-time reauth proof 소비와 Order 생성/상태 전이를 한 transaction에서 처리한다. reauth_proof_id와 order_id는 실제 FK로 정합화한다. |
| DB-226 | execution handoff transaction은 source validation, config freeze, lifecycle create/reuse, initial Guard, terminal/Approval/automatic intent, audit을 함께 commit하며 helper의 중간 commit을 허용하지 않는다. |
| DB-227 | Approval transaction은 row lock/CAS, owner/proof, current authority, Guard, OrderIntent/CREATED Order와 execution/approval/audit 전이를 함께 commit하거나 rollback한다. |
| DB-228 | broker pre-send transaction은 Order→Intent→source와 current stage/policy/emergency/expiry를 검증하고 authority가 있으면 SUBMITTING/fencing을 commit한다. network call 동안 DB lock을 유지하지 않는다. |
| DB-229 | finalized sourced Decision 중 authoritative execution이 없는 row를 찾는 reconciliation은 exact-one unique와 lookup-first loser recovery를 사용하며 WAIT·REJECT·UNKNOWN도 NO_ACTION으로 복구한다. |
| DB-230 | Phase 10 PostgreSQL acceptance는 sourced exact-one, stage locking, Approval CAS, intent/order unique, Guard subject FK, fixed-stop 교정, concurrent creation, pre-send fencing과 ambiguous-send reconciliation을 실제 PostgreSQL에서 검증한다. |
| DB-231 | `account_funds_snapshots`와 `order_capacity_snapshots`는 서로 분리된 append-only 금융 증거다. 기존 주문·체결·포지션에서 값을 추정하거나 backfill하지 않고 raw Broker payload도 저장하지 않는다. |
| DB-232 | account funds identity는 broker/account_alias/environment/source_api_id/query_type/received_at이고 `entr`, `ord_alow_amt`, `pymn_alow_amt` 및 D+1/D+2 예수·인출 가능 값을 nullable `BIGINT`로 저장한다. missing과 authoritative zero를 구분하고 signed amount를 보존한다. |
| DB-233 | capacity identity는 broker/account_alias/environment/source_api_id/symbol/side/trade_type/requested_price와 nullable io_amount/requested_quantity/expected_buy_price 전체다. 응답의 `ord_alowa`, 예수·인출 가능 금액, 20/30/40/50/60/감면60/100% margin별 amount와 quantity를 구별해 nullable `BIGINT`로 저장한다. |
| DB-234 | capacity quantity는 null 또는 0 이상이고 요청 price는 양수다. usable cash-only 필드가 누락돼도 NULL evidence를 보존할 수 있으나 이를 usable authority로 승격하지 않는다. |
| DB-235 | latest funds selector는 broker/account/environment exact match 후 `received_at DESC, id DESC`, capacity selector는 모든 request identity의 NULL-safe exact match 후 같은 정렬을 사용한다. 다른 계좌·환경·symbol·side·price fallback은 금지한다. |
| DB-236 | snapshot에는 time-dependent `is_fresh`를 저장하지 않는다. future Guard는 persisted `received_at`과 server current time으로 age를 계산한다. |
| DB-237 | 금융 조회 network call 동안 DB row lock이나 장기 transaction을 유지하지 않는다. successful normalize 뒤 짧은 append transaction으로 저장하며 실패 시 새 row는 0건이다. |
| DB-238 | `20260828_0042`는 0041 이후 두 table과 selector index를 additive 생성하며 backfill은 없다. table에 금융 evidence가 있으면 destructive downgrade를 명시적으로 거부한다. |
| DB-239 | financial freshness는 snapshot에 `is_fresh`를 저장하지 않고 GRD-107~116의 evaluation-time 계산을 사용한다. 기존 Guard `rule_results`/evidence와 audit JSON은 frozen/current policy version·TTL, effective TTL, snapshot provenance, evaluation time과 age를 재구성 가능하게 보존한다. |
| DB-240 | future `received_at`은 usable evidence가 아니고 required nullable financial field는 fresh row에서도 BLOCK이다. authoritative zero는 NULL과 구분해 0으로 보존하며 account funds와 exact capacity를 상호 대체하지 않는다. |
| DB-241 | funds/capacity refresh는 network call과 normalization/persist를 authority transaction 밖에서 끝낸 뒤, 짧은 Guard/Approval transaction이 exact persisted row를 다시 선택한다. refresh 실패는 partial Guard PASS나 Order row를 만들지 않는다. |
| DB-242 | OrderIntent authority identity의 canonical material·serialization·digest는 EXE-263~273을 단일 기준으로 한다. existing `authority_key` 128-character column과 unique foundation은 72-character `ordauth-<sha256>`를 수용하므로 Phase 10D.2 schema migration은 없다. |
| DB-243 | authority_key는 source grant uniqueness, request_hash/idempotency/client_order_id는 exact order/submission lifecycle을 담당한다. 같은 key의 immutable terms conflict는 새 row나 새 key 없이 fail-closed하고 transaction을 rollback한다. |
| DB-244 | pre-contract row에 authority_key를 추측 backfill하지 않는다. Phase 10B source taxonomy, execution당 exact-one Approval과 initial intent/order authority, Phase 10D.1B append-only financial tables·exact selectors는 변경하지 않는다. |
| DB-245 | Phase 10F는 기존 0041/0042 representation으로 구현하며 schema migration이 없다. BROKER_SEND Guard는 기존 typed subject FK/CHECK를 사용하고 revocation reason은 OrderEvent/Audit JSON 및 lifecycle result_code에 저장한다. |
| DB-246 | pre-send semantic BLOCK의 Guard, Order INVALIDATED, authority event/audit와 source lifecycle 회수는 한 transaction이다. DB failure는 모두 rollback해 partial Guard 또는 terminal state를 남기지 않는다. |
| DB-247 | revocation event source identity는 Order별 deterministic key로 exactly-once를 보장한다. helper는 CREATED만 잠그고 INVALIDATED/SUBMITTING 이상을 변경하지 않는다. |
| DB-248 | Phase 10F SQLite 검증은 PostgreSQL `SKIP LOCKED`, row-lock ordering, CREATED→INVALIDATED/SUBMITTING race, lease fencing과 ambiguous commit의 production concurrency evidence를 대체하지 않는다. |
| DB-249 | additive migration `20260829_0043`은 `order_events.event_type`만 `varchar(32)`에서 `varchar(64)`로 확대해 exact `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` 35자를 손실 없이 저장한다. event 이름·의미·기존 row를 변경하거나 backfill하지 않는다. ORM capacity도 64로 일치시킨다. |
| DB-250 | `20260829_0043` downgrade는 모든 `order_events.event_type` 길이가 32 이하일 때만 `varchar(32)`로 축소한다. 32자를 초과하는 row가 하나라도 있으면 silent truncation 없이 명시적으로 거부하고 기존 schema/data를 보존한다. |

### 11.11 AuditLog result capacity correction

`audit_logs.result`에는 API 입력이나 Broker 원문이 아니라 아래 server-owned exact literal만
영속한다. 2026-08-29 inventory는 93개 unique literal이고 최장 길이는 35자다. 괄호는 문자
길이다. 같은 literal이 여러 경로에 나타나면 한 번만 계수한다.

| persistence path / normative owner | exact result literals |
| --- | --- |
| 인증·control-plane·Finalizer (`SECURITY_SPEC`, DB-210/213) | `BLOCKED`(7), `CONFLICTED`(10), `EXPIRED`(7), `FAILED`(6), `INVALID`(7), `ORDER_CREATED`(13), `PASSED`(6), `PENDING`(7), `REJECTED`(8), `RETRYABLE_FAILURE`(17), `SUCCEEDED`(9), `SUCCESS`(7) |
| DecisionExecution terminal audit (EXE-221~250) | `APPROVAL_PENDING`(16), `DISABLED`(8), `FAILED_SAFE`(11), `GUARD_BLOCKED`(13), `NO_ACTION`(9), `SHADOW_RECORDED`(15) |
| Approval invalidation (EXE-232/249/250/275) | `ACTION_MODE_DOWNGRADED`(22), `APPROVAL_EXPIRED`(16), `EMERGENCY_STOP_ACTIVE`(21), `EXECUTION_STAGE_DOWNGRADED`(26), `EXECUTION_STAGE_UNAVAILABLE`(27), `RISK_POLICY_UNAVAILABLE`(23), `SOURCE_AUTHORITY_INVALID`(24) |
| BUY Guard의 persisted blocking result (Guard risk contract) | `BROKER_CONNECTION_OK`(20), `BROKER_NOT_READY`(16), `CONSECUTIVE_LOSS_LIMIT`(22), `DAILY_ENTRIES_LIMIT`(19), `DAILY_LOSS_LIMIT`(16), `DECISION_EXPIRED`(16), `EMERGENCY_STOP_ACTIVE`(21), `ENVIRONMENT_NOT_MOCK`(20), `MARKET_DATA_STALE`(17), `NO_ACTIVE_DAILY_LOSS_EVENT`(26), `OPEN_POSITIONS_LIMIT`(20), `ORDER_SIZE_NOT_CONFIGURED`(25), `SNAPSHOT_MISSING`(16), `SPREAD_LIMIT`(12), `SYMBOL_EXPOSURE_LIMIT`(21), `SYMBOL_NOT_WATCHED`(18), `TOTAL_EXPOSURE_LIMIT`(20) |
| SELL Guard의 persisted blocking result (Guard/order contract) | `BROKER_READY`(12), `MARKET_DATA_FRESH`(17), `MARKET_SESSION_TRADABLE`(23), `MARKETABLE_SELL_PRICE_AVAILABLE`(31), `NO_ACTIVE_OR_UNKNOWN_ORDER`(26), `NOT_RECONCILING`(15), `POSITION_FOUND`(14), `POSITION_ID_MATCH`(17), `POSITION_MANAGED_QUANTITY_POSITIVE`(34), `POSITION_VERSION_MATCH`(22), `QUANTITY_BELOW_ONE`(18), `SELL_QUANTITY_AVAILABLE`(23), `SELL_RATIO_VALID`(16) |
| shared/financial/Broker Guard blocking result (GRD-107~116, EXE-257/279) | `ACCOUNT_FUNDS_FRESH`(19), `ACTION_NOT_IMPLEMENTED`(22), `CURRENT_ORDER_AMOUNT_ALLOWED`(28), `FINANCIAL_CONTEXT_INVALID`(25), `FROZEN_ORDER_AMOUNT_ALLOWED`(27), `GENERIC_ORDERABLE_AMOUNT_SUFFICIENT`(35), `INSTRUMENT_TRADABLE`(19), `MARGIN_100_AMOUNT_SUFFICIENT`(28), `MARGIN_100_QUANTITY_SUFFICIENT`(30), `ORDER_CAPACITY_FRESH`(20), `ORDERABLE_CASH_SUFFICIENT`(25), `PRICE_DEVIATION_EXCEEDED`(24), `STRICT_MOCK_AUTHORITY`(21) |
| Broker pre-send direct revocation (EXE-274~279, ORD-053/054, STM-038) | `APPROVAL_AUTHORITY_REVOKED`(26), `AUTOMATIC_AUTHORITY_REVOKED`(27), `BROKER_DIAGNOSTIC_AUTHORITY_INVALID`(35), `CURRENT_POLICY_UNAVAILABLE`(26), `EXECUTION_STAGE_PROVENANCE_INVALID`(34), `ORDER_AUTHORITY_KEY_INVALID`(27), `ORDER_SOURCE_NOT_SENDABLE`(25), `ORDER_SOURCE_UNCLASSIFIED`(25), `SOURCE_OWNER_UNAVAILABLE`(24) |
| Broker current BUY Guard derived result (EXE-274/279) | `CURRENT_BROKER_CONNECTION_OK`(28), `CURRENT_BROKER_NOT_READY`(24), `CURRENT_CONSECUTIVE_LOSS_LIMIT`(30), `CURRENT_DAILY_ENTRIES_LIMIT`(27), `CURRENT_DAILY_LOSS_LIMIT`(24), `CURRENT_DECISION_EXPIRED`(24), `CURRENT_EMERGENCY_STOP_ACTIVE`(29), `CURRENT_ENVIRONMENT_NOT_MOCK`(28), `CURRENT_MARKET_DATA_STALE`(25), `CURRENT_NO_ACTIVE_DAILY_LOSS_EVENT`(34), `CURRENT_OPEN_POSITIONS_LIMIT`(28), `CURRENT_ORDER_SIZE_NOT_CONFIGURED`(33), `CURRENT_SNAPSHOT_MISSING`(24), `CURRENT_SPREAD_LIMIT`(20), `CURRENT_SYMBOL_EXPOSURE_LIMIT`(29), `CURRENT_SYMBOL_NOT_WATCHED`(26), `CURRENT_TOTAL_EXPOSURE_LIMIT`(28) |

| ID | 요구사항 |
| --- | --- |
| DB-251 | additive migration `20260829_0044`는 `audit_logs.result`만 `varchar(24)`에서 `varchar(64)`로 확대한다. 위 exact result literal, 기존 row와 authority 의미를 변경하거나 backfill하지 않고 ORM도 64로 일치시킨다. |
| DB-252 | `20260829_0044` downgrade는 모든 `audit_logs.result` 길이가 24 이하일 때만 `varchar(24)`로 축소한다. 24자를 초과하는 row가 하나라도 있으면 명시적으로 거부하고 기존 schema/data를 보존한다. |

## 거래시장 선택 평가

`venue_selection_evaluations`는 [거래시장 자동 선택 명세](VENUE_SELECTION_SPEC.md)의 SHADOW 평가를 보존한다. 사용자·종목·방향·수량·주문유형·긴급도·세션·거래일 상태·캘린더 정책과 판정 근거·NXT 적격성·SOR 지원 여부·양 시장 snapshot 참조·선택 결과·reason code·canonical input hash를 저장한다. 이 테이블은 주문 권한을 가지지 않으며 `order_creation_allowed=false`로 고정한다.

`instrument_venue_states`는 종목·venue별 현재 적격 상태와 근거를 보존한다. 첫 구현은 정상 NXT quote 관측으로 `VERIFIED/QUOTE_OBSERVED`만 기록하며, quote 부재를 `INELIGIBLE`로 기록하지 않는다. 평가 시각의 적격 상태는 `venue_selection_evaluations`에도 복제해 과거 판단을 재현한다.

`market_calendar_overrides`는 날짜별 임시 휴장 운영 이력을 append-only로 보존한다. `market_date`, `ACTIVE/REVOKED` 상태, 사유, 공개 출처 참조, 생성·해제 사용자와 시각을 저장하고 날짜별 활성 행은 하나로 제한한다. 해제는 삭제가 아니라 상태 전이며 강제 개장 값은 저장하지 않는다. `venue_selection_evaluations.calendar_override_id`는 평가 당시 적용된 행을 참조해 과거 판단을 재현한다.

- Migration `20260812_0028`은 SHADOW 거래시장 선택 평가 원장을 추가한다.
- Migration `20260812_0029`는 `instrument_venue_states`를 추가한다. downgrade는 상태 원장만 제거하며 기존 market snapshot과 SHADOW 평가를 보존한다.
- Migration `20260812_0030`은 거래시장 평가에 거래일 상태·캘린더 근거·정책 버전을 추가한다.
- Migration `20260812_0031`은 운영 휴장 이력과 평가별 적용 override 참조를 추가한다. downgrade는 평가 참조와 override 원장을 제거하되 기존 venue 평가의 기본 캘린더 근거는 보존한다.
- 현재 schema head는 `20260817_0038`이다.

### 2026-08-18 키움 주문 거절 진단 metadata

- `DB-156`: 키움 명시적 주문·취소 거절은 기존 append-only `order_events.payload_json`에 `broker_result_code`와 `broker_result_message`만 저장한다. 원문 응답 전체와 인증·계좌 정보는 저장하지 않으며 schema migration 없이 기존 이벤트와 호환한다.
