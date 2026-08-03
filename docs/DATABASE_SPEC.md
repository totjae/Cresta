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
| `watchlist_items` | id, symbol, enabled, strategy_scope | 활성 항목 최대 3개 |
| `configuration_versions` | id, scope, target_symbol, state, payload, schema_version | 활성 범위별 1개 |
| `market_snapshots` | id, symbol, market, observed_at, quality, payload_hash, payload | 판단 참조 불변 |
| `indicator_snapshots` | id, market_snapshot_id, calculator_version, payload | 입력·버전 unique |
| `decisions` | id, kind, symbol, input_snapshot_id, output, action, valid_until | 구조화 스키마 검증 결과 포함 |
| `approvals` | id, decision_id, status, expires_at, actor_id, reauth_id | 상태 전이 제약 |
| `order_intents` | id, order_group_id, symbol, side, requested_quantity, action, config_version | 의도 단위 |
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
| DB-026 | `configuration_versions`는 scope, target, category, sequence, state, payload hash와 생성·검증·활성화 시각을 저장하고 동일 대상·범주의 `ACTIVE` 행을 하나로 제한한다. |
| DB-027 | 실행 권한 payload는 정규화된 JSON과 SHA-256 해시로 저장하고 `ACTIVE` 또는 `SUPERSEDED` 행을 API로 수정하지 않는다. |
