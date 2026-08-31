# Cresta 테스트 계획

### Phase 11A.2 disposable runtime data / backup policy correction (2026-08-31)

| ID | 요구사항 | 검증 | 결과 |
| --- | --- | --- | --- |
| T-V2-OPS-11A2-001 | DB-070~073, OPS-040~044, SEC-072 | MOCK/development PostgreSQL·Redis·runtime history 내구성 정책 | PASS — disposable, data loss accepted, backup optional |
| T-V2-OPS-11A2-002 | DB-062, DB-072, OPS-042 | backup restore 없는 fresh recovery model | PASS — Git migration chain → fresh DB head `20260829_0044` → runtime restart |
| T-V2-OPS-11A2-003 | DB-071, OPS-040~041 | 기존 fresh dump disposition | PASS — `OPTIONAL_PRE_DEPLOY_SNAPSHOT`, 삭제 없음, encryption/off-host blocker 없음 |
| T-V2-OPS-11A2-004 | SEC-032~033, KIW-011~012 | disposable data와 secret 정책 분리 | PASS — credential/password/API key Git 금지와 file secret 규칙 불변 |
| T-V2-OPS-11A2-005 | LIVE future readiness | 현재 예외의 LIVE 자동 적용 여부 | PASS — LIVE backup·retention·RPO/RTO 명시적 미결정 유지 |
| T-V2-OPS-11A2-006 | 문서·migration consistency | 관련 명세, migration head, whitespace | PASS — code/runtime/migration 변경 없음, head `20260829_0044`, `git diff --check` PASS |

이번 Phase는 문서 정책 정정이다. backend/frontend full suite는 재실행하지 않았으며 database, Compose, systemd, running container와 dependency를 변경하지 않았다. Preflight B의 npm audit high 1건은 별도 OPEN risk로 유지하고 이 정책 변경의 migration/recreate blocker로 사용하지 않는다.

### Phase 11A.1 frontend test baseline cleanup (2026-08-31)

| ID | 요구사항 | 검증 | 결과 |
| --- | --- | --- | --- |
| T-V2-UI-11A1-001 | UI-060~064, 운영 휴장 UI 계약 | 기존 고정 `2026-08-13` 입력의 현재 KST native validity | PASS — `rangeUnderflow=true`를 재현해 submit/POST 0의 원인 확인 |
| T-V2-UI-11A1-002 | 운영 휴장 생성·해제 | 렌더링된 KST `min` 날짜로 생성, 목록 갱신, 해제 | PASS — focused 1/1, sleep/timeout/skip/xfail 없음 |
| T-V2-UI-11A1-003 | API 운영 휴장 계약 | POST body 날짜·CSRF와 추가 TOTP 부재 | PASS — exact rendered valid date와 CSRF 전송, reauth 0 |
| T-V2-UI-11A1-004 | repository regression | frontend full/typecheck/build, backend full, Ruff, diff | PASS — frontend 19/19, backend 757/757, 나머지 전부 PASS |

분류는 `STALE_TEST`다. [Web UI 명세](docs/WEB_UI_SPEC.md)와 [API 명세](docs/API_SPEC.md)는 오늘부터 730일 이내 날짜만 허용하고 production date input도 KST의 같은 경계를 적용한다. 따라서 production behavior는 변경하지 않고 시간 경과로 과거가 된 fixture만 현재 렌더링 계약에서 얻은 유효 날짜로 교정했다. 테스트는 input validity와 실제 POST payload를 추가 확인하므로 native validation을 우회하거나 assertion을 제거한 통과가 아니다. deployment·trading semantics와 migration 변경은 없다.

### Phase 11A deployment & operational readiness (2026-08-31)

| ID | 요구사항 | 검증 | 결과 |
| --- | --- | --- | --- |
| T-V2-OPS-11A-001 | OPS-088, CFG-132~135 | Compose topology, one-shot migration owner, API/worker dependency gate | PASS — migration만 upgrade 소유, API·4 worker 모두 성공 완료 gate |
| T-V2-OPS-11A-002 | OPS-089 | `/healthz` dependency-free liveness, `/readyz` DB/head readiness | PASS — exact 0044는 200, DB failure/head drift는 503 fail-closed |
| T-V2-OPS-11A-003 | OPS-090~092 | Redis authority, persistence, port exposure, bounded logging | PASS — DB/Redis internal only, gateway loopback only, json-file 10m×5 |
| T-V2-OPS-11A-004 | CFG-132~135 | `.env.example` Settings inventory, secret와 safe default | PASS — direct secret 제외 전 field coverage, MOCK/SHADOW/handoff OFF |
| T-V2-OPS-11A-005 | OPS-088~089 | PostgreSQL one-shot migration 뒤 API startup/readiness | PASS — PostgreSQL 17.11, fresh→`20260829_0044`, `/readyz` 200 |
| T-V2-OPS-11A-006 | OPS-093 | API·scheduler·agent·sourced-handoff start/stop, handoff OFF idle | PASS — actual process lifecycle, shutdown traceback 0 |
| T-V2-OPS-11A-007 | CFG-135 | malformed handoff setting, unavailable broker configuration | PASS — startup/fail-fast rejection, broker external call 0 |
| T-V2-OPS-11A-008 | OPS-094 | Compose base/kiwoom/optional overlay static validation | PASS — YAML parse와 topology contract; Docker config/build/up는 NOT_RUN_LOCAL |
| T-V2-OPS-11A-009 | frontend build path | clean install, typecheck, production build, UI tests | PARTIAL — ci/typecheck/build PASS, tests 18/19; 기존 운영 휴장 async test 1 FAIL |
| T-V2-OPS-11A-010 | backend regression | SQLite full suite, deployment/worker focused, Ruff, diff | PASS — 757/757, focused PASS, Ruff/diff PASS |

PostgreSQL 검증은 `127.0.0.1 / cresta_acceptance`의 test-only PostgreSQL 17.11과 실행별 격리 schema만 사용했고 종료 후 Phase 11A schema를 제거했다. API는 production과 동일한 Uvicorn command로 시작해 liveness/readiness와 application shutdown log를 확인했다. Windows `CTRL_BREAK` 종료 코드는 Linux SIGTERM과 동일하지 않으므로 Ubuntu container signal 검증은 server preflight에 남긴다.

로컬에는 Docker CLI가 없어 실제 `docker compose config`, image build, container start/restart와 log rotation 관찰을 실행하지 않았다. 모든 YAML·service dependency·logging·port·secret contract의 static validation은 PASS이고, 실제 Compose config/build/up, migration job completion, Ubuntu SIGTERM, host bind/secret 권한, restart/boot 및 multi-day Stage A~C soak는 `SERVER_PREFLIGHT_REQUIRED`다. LIVE와 production DB 사용은 0이며 schema migration은 없고 repository head는 `20260829_0044`다.

### Phase 10G.2 production sourced handoff / final MOCK system acceptance (2026-08-30)

| ID | 요구사항 | 검증 | 결과 |
| --- | --- | --- | --- |
| T-V2-PG-10G2-001 | CFG-127~131, EXE-284 | unset/default false, malformed env, disabled runtime | PASS — default OFF, malformed startup failure, PostgreSQL execution 0 |
| T-V2-PG-10G2-002 | EXE-282~283, EXE-288 | actual worker의 WAIT/REJECT/UNKNOWN handoff | PASS — 각 NO_ACTION exact-one, Approval/Intent/Order/Broker 0 |
| T-V2-PG-10G2-003 | EXE-283, EXE-288 | BUY+SHADOW actual worker | PASS — SHADOW_RECORDED, Approval/Order/Broker 0 |
| T-V2-PG-10G2-004 | EXE-282~288 | Finalizer BUY→actual worker→manual Approval→Broker worker→MOCK | PASS — Decision/Execution/Approval/Intent/Order/Broker call 각 1 |
| T-V2-PG-10G2-005 | EXE-282~288 | Finalizer BUY→actual worker automatic→Broker worker→MOCK | PASS — Decision/Execution/Intent/Order/Broker call 각 1, Approval 0 |
| T-V2-PG-10G2-006 | EXE-285, EXE-289 | same Decision 10회 sweep, dual worker, restart | PASS — DecisionExecution exact-one, duplicate downstream 0 |
| T-V2-PG-10G2-007 | EXE-282, EXE-289 | Finalizer write hook separate session visibility, commit 및 rollback | PASS — uncommitted 0, commit 뒤 1, rollback 0 |
| T-V2-PG-10G2-008 | EXE-286 | 실제 worker 첫 DB session outage 후 다음 cadence recovery | PASS — partial execution 0, 복구 뒤 NO_ACTION exact-one |
| T-V2-PG-10G2-009 | EXE-287, CFG-128 | disabled/enabled stop event와 active sweep completion | PASS — 새 iteration 차단, task join, context-managed session cleanup |
| T-V2-PG-10G2-010 | EXE-251~258 | PostgreSQL MOCK_AUTOMATIC fixed-stop 회귀와 PAUSE_ENTRY | PASS — SELL authority/send exact-one, risk reduction 유지 |
| T-V2-PG-10G2-011 | 전체 회귀 | PostgreSQL 전체, SQLite 전체, Ruff, diff, schema cleanup | PASS — PostgreSQL 80/80, SQLite 751/751, Ruff/diff PASS, 잔여 schema 0 |

PostgreSQL 대상은 `127.0.0.1 / cresta_acceptance`의 test-only PostgreSQL 17.11이며 실행별 격리 schema와 migration head `20260829_0044`를 사용했다. runtime tests는 direct execution helper만 호출한 것이 아니라 `SourcedHandoffWorker.run()` 또는 동일 production worker instance의 실제 sweep wiring을 통해 검증했다. manual/automatic E2E의 external boundary는 fake/MOCK adapter이고 LIVE와 production DB 호출은 0이다. 초기 focused run에서 UUID-width correlation column을 넘는 prefixed correlation ID를 발견해 immutable Decision UUID 재사용으로 교정했고, 최종 전체 run에는 FAIL/NOT_RUN이 없다.

### Phase 10G.1 PostgreSQL Production Acceptance 환경 확인 (2026-08-29)

- production target은 `deploy/compose.yaml`의 PostgreSQL 17(`postgres:17-alpine`)이다. 로컬 Python에는 `psycopg`가 있으나 `psql`, `postgres`, `pg_ctl`, `initdb`, `createdb`, PostgreSQL service, Docker, Podman 및 WSL 배포판은 없다. PostgreSQL 관련 환경변수와 project-defined test DSN도 발견되지 않았다.
- production/user DB와 임의 remote DB는 사용하지 않았다. 안전한 실제 PostgreSQL instance가 없으므로 fresh/incremental migration, catalog inspection, Phase 9 finalization/Gate, Phase 10 execution/Approval/Order/fixed-stop/worker/pre-send concurrency 및 PostgreSQL-backed v7 MOCK E2E는 전부 `NOT_RUN`이다. SQLite 결과, ORM metadata, generated SQL과 mock을 PostgreSQL evidence로 승격하지 않는다.
- 재개 최소 조건은 PostgreSQL 17 local instance, test-only login role와 해당 role 소유의 빈 database, 그리고 `postgresql+psycopg://<test-user>:<secret>@127.0.0.1:<port>/<test-db>` 형태의 로컬 전용 URL이다. native PostgreSQL이면 `createuser`/`createdb`로, Docker가 있으면 별도 ephemeral PostgreSQL 17 container로 준비할 수 있으며 Docker 자체는 필수가 아니다. 비밀번호와 완성된 인증 URL은 저장소에 기록하지 않는다.
- 환경이 준비되면 빈 DB→`20260828_0042`, `0040→0041→0042`, 실제 catalog, concurrent transaction/race matrix, PostgreSQL focused/relevant regression을 우선 실행한 뒤 SQLite full suite, Ruff와 `git diff --check`를 별도로 실행한다. 이번 환경 확인 후 전체 Ruff와 `git diff --check`는 PASS했지만 SQLite full suite는 재실행하지 않았다. 현재 Phase 10G.1은 `INCOMPLETE`이고 Phase 10G.2는 시작할 수 없다.

### Phase 10F Broker Pre-Send Authority / Unsent Revocation 검증 (2026-08-29)

- `backend/tests/test_phase_10f_broker_pre_send.py` focused 32건에서 valid manual/automatic DECISION_EXECUTION과 fixed-stop STOP_TRIGGER의 `BROKER_SEND` Guard 및 commit-before-network를 확인했다. current stage/mode downgrade, Decision expiry, PAUSE_ENTRY BUY, invalid Approval, stage DB retryable, SUBMITTING commit failure, financial stale·wrong-price capacity·stricter/looser current risk·UNKNOWN conflict·stage provenance·authority key·strict MOCK, fixed-stop stage/action/position/key/strict MOCK, missing intent·null/unknown/mismatched/broken source 및 legacy/imported fail-closed, unclassified recovery/idempotency를 검증했다.
- 기존 `test_kiwoom_order_sender.py` 13건과 worker/lease 6건에서 typed BROKER_DIAGNOSTIC 1주 경계, FIFO/`SKIP LOCKED`, ACK/REJECTED/UNKNOWN, gate close와 no blind resend를 재검증했다. STOP_TRIGGER는 PAUSE_ENTRY가 활성이어도 valid risk-reduction SELL을 전송하고 authority 상실 시 immutable quantity를 유지한 `INVALIDATED`, trigger `EXIT_PENDING`, RiskEvent ACTIVE로 복구한다.
- Phase 10E~9E, Guard/Approval/financial/order creation/stop/reconciliation 회귀가 통과했고 backend 전체 741건이 100% PASS했다. 전체 Ruff와 `git diff --check`도 PASS다. SQLite 검증만 수행했으며 PostgreSQL locking/concurrency/0041·0042 DDL 검증은 `NOT_RUN`이다.
- internal `reconcile_next_unsent_authority()`는 수동 foundation으로만 추가했고 startup/scheduler/periodic activation, sourced scheduler/Finalizer hook, LIVE와 replacement authority는 열지 않았다.

### Phase 10E MOCK_AUTOMATIC / Fixed-Stop Authority 구현 검증 (2026-08-29)

- `backend/tests/test_phase_10e_mock_automatic.py` 11건과 직접 영향 회귀를 통해 sourced MOCK_AUTOMATIC+AUTOMATIC의 Approval 0·DECISION_EXECUTION authority-key·exact-one CREATED BUY, stage/mode downgrade, strict MOCK, rollback을 검증했다.
- fixed-stop은 v7 Stage exact-one과 명시적 versioned `fixed_stop_loss` action policy만 사용한다. SHADOW Order 0, APPROVAL_ONLY EXIT_PENDING/Order 0, MOCK_AUTOMATIC+AUTOMATIC의 typed STOP_TRIGGER Guard·managed available quantity·exact-one CREATED SELL, PAUSE_ENTRY 비차단, strict MOCK, recovery와 rollback을 검증했다.
- Phase 10E focused/직접 영향 회귀 41건, Phase 10C.2 단독 13건과 backend 전체 pytest가 100% PASS(종료 코드 0)했다. 전체 Ruff와 `git diff --check`도 PASS이며 0041/0042 파일 diff는 없다.
- Broker submission/pre-send, production sourced sweep, LIVE와 production Stage seed는 검증·활성화하지 않았다. SQLite 결과를 PostgreSQL concurrency 증거로 사용하지 않으며 PostgreSQL은 `NOT_RUN`이다.

### Phase 10D Guard Completeness / Manual Approval Authority 구현 검증 (2026-08-28)

- `backend/tests/test_phase_10d_execution_authority.py` 집중 시험 10건이 통과했다. 금융 TTL 경계·frozen/current minimum·exact request context·cash-only 100% band, canonical `ordauth-` identity, 승인 owner/CAS/결합 proof, transaction rollback과 APPROVAL_ONLY/MOCK_AUTOMATIC fail-closed matrix를 검증했다.
- backend 전체 회귀는 100% PASS(종료 코드 0), 전체 Ruff와 `git diff --check`는 PASS다. SQLite에서 0041→0042 migration 회귀를 포함해 검증했으며 PostgreSQL은 로컬 검증 환경이 없어 `NOT_RUN`이다.
- `MOCK_AUTOMATIC + AUTOMATIC`, fixed-stop 변경, Broker pre-send/send, scheduler/sweep/Finalizer hook, LIVE 및 production stage seed는 이 구현 검증의 대상이 아니다.

### Phase 10D.2 Guard Freshness / Order Authority Identity 계약 (2026-08-28)

| ID | 요구사항 | 계획 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-V2-EXE-AUTH-017 | GRD-109, CFG-122 | funds age 29초 / TTL 30초 | fresh | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-018 | GRD-109, CFG-122 | funds age 정확히 30초 / TTL 30초 | inclusive boundary fresh | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-019 | GRD-109, CFG-122 | funds age 30초 초과 | stale, Guard PASS 0 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-020 | GRD-109, CFG-122 | exact capacity age 정확히 10초 / TTL 10초 | inclusive boundary fresh | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-021 | GRD-109, CFG-122 | exact capacity age 10초 초과 | stale, Guard PASS 0 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-022 | GRD-108, CFG-125 | frozen TTL 30 / current TTL 60 | effective 30, authority 확대 없음 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-023 | GRD-108, CFG-125 | frozen TTL 30 / current TTL 10 | effective 10, current 강화 적용 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-024 | GRD-107, CFG-123 | quote TTL만 변경 | funds/capacity TTL 불변, fallback 없음 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-025 | GRD-109 | snapshot `received_at > now` | invalid, negative age clamp 없음 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-026 | CFG-121~124 | 기존 valid Risk Policy에 신규 field 누락 | canonical funds 30 / capacity 10 적용; invalid type/range 거부 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-027 | EXE-264~266 | 같은 DecisionExecution + Approval 반복 | deterministic same `ordauth-` key | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-028 | EXE-266 | 같은 execution, 다른 Approval ID material | 서로 다른 digest; exact-one Approval lifecycle은 별도 검증 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-029 | EXE-267 | automatic `approval_id=null` 반복 | deterministic same key, synthetic Approval 0 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-030 | EXE-269 | 같은 authority에서 price 변경 | authority key 불변 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-031 | EXE-269 | 같은 authority에서 quantity 변경 | authority key 불변 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-032 | EXE-269, EXE-272 | Risk Policy version/change | authority key 불변, live revoke만 가능 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-033 | EXE-269, EXE-272 | Stage version/hash/change | authority key 불변, no-promotion 유지 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-034 | EXE-270, ORD-057 | same key + conflicting immutable terms | fail-closed, 새 intent/key/order 0 | 통과 (Phase 10D focused) |
| T-V2-EXE-AUTH-035 | EXE-266~267 | manual Approval ID vs automatic explicit null | canonical material과 key가 다름 | 통과 (Phase 10D focused) |

이 항목들은 Phase 10D.2에서 확정한 계약이며 Phase 10D focused 및 전체 backend 회귀에서 구현 검증을 마쳤다. missing/stale refresh는 network-outside-transaction 후 exact persisted reselect하고, NULL-vs-zero와 wrong-context를 구분하며 Guard provenance를 저장한다.

### Phase 10D resume semantic-blocker audit (2026-08-28)

| 대상 | 확인 시나리오 | 결과 |
| --- | --- | --- |
| financial prerequisite | 0042 ORM/service/selectors와 Phase 10D.1B acceptance 존재 확인 | PASS — 이전 persistence blocker 해소 |
| OrderIntent authority identity | `DECISION_EXECUTION_SPEC`, `DATABASE_SPEC`, `ORDER_EXECUTION_SPEC`, Phase 10B/10C.1/원 Phase 10D 요청에서 exact `authority_key` material 탐색 | BLOCKED — stable/unique 요구만 있고 canonical material·prefix·serialization/hash 정의 없음 |
| financial freshness policy | Guard/Configuration/Decision Execution/DB 명세와 `RiskPolicyPayload`에서 account funds/capacity TTL 탐색 | BLOCKED — quote 전용 `quote_stale_seconds`만 존재; 금융 threshold 없음 |
| no-invention boundary | execution/Approval ID 또는 ad-hoc hash와 quote TTL 재사용 여부 | PASS — 임의 authority key·금융 TTL을 만들지 않음 |
| prohibited scope preservation | 0041/0042, production Python, Approval API, Guard, fixed-stop, worker/pre-send, scheduler diff | PASS — 이번 resume audit에서 변경 없음 |

두 normative blocker 때문에 Phase 10D focused/전체 회귀는 새 구현 acceptance로 실행하지 않았다. 직전 Phase 10D.1B의 backend 687건 PASS 근거는 유지하지만 이를 Phase 10D COMPLETE 증거로 재해석하지 않는다. 계약 확정 뒤 authority-key collision/retry, funds/capacity missing·stale boundary, PRE_ORDER/APPROVAL_REVALIDATION, owner/CAS/reauth/rollback과 full backend suite를 실행한다.

### Phase 10D.1B Kiwoom Financial Adapter & Authority Projection (2026-08-28)

| 대상 | 확인 시나리오 | 결과 |
| --- | --- | --- |
| numeric normalization | padded positive/negative/zero, plain zero, missing/null/blank, alphabetic/decimal/comma/non-string | PASS — zero와 missing 분리, signed amount 보존, malformed structured rejection |
| `kt00001` Adapter | explicit `qry_tp=3`, `ka00001` identity verification, source/account/environment/UTC receipt provenance, D+1/D+2 mapping | PASS — OFFICIAL_SCHEMA_FIXTURE; actual MOCK call은 NOT_RUN |
| `kt00010` Adapter | exact symbol/BUY side/price, supplied optional field만 전송, 모든 margin band 및 100% amount/quantity | PASS — OFFICIAL_SCHEMA_FIXTURE; account-wide flattening 없음 |
| append-only selectors | funds exact account/environment latest, capacity full nullable request identity latest, cross-symbol/price/account/environment 차단 | PASS |
| reconciliation failure | funds success append 및 timeout 시 no-new-row/prior evidence preservation, 기존 order/position projection 계속 | PASS |
| query-and-persist | broker read 이후 짧은 transaction, 동일 request의 성공 관측도 영구 dedupe 없이 새 row | PASS |
| migration | `20260828_0041 → 20260828_0042`, 두 empty table, nullable BIGINT, indexes/checks, no backfill, populated downgrade refusal | PASS on SQLite |
| focused/regression | Adapter·reconciliation·authority 71건, Phase 10C.2/10C.1/9E 및 broker/worker 묶음 | PASS |
| full backend | 전체 pytest | PASS — 687 tests |
| static/diff | 전체 Ruff, `git diff --check` | PASS |
| PostgreSQL | official project PG migration/index/nullability | NOT_RUN — configured PostgreSQL URL 및 Docker CLI 없음 |

MOCK evidence provenance는 `OFFICIAL_SCHEMA_FIXTURE`만 존재한다. `REDACTED_MOCK_FIXTURE`와 실제 credential-backed MOCK call은 없으며 LIVE와 주문성 API는 호출하지 않았다. 이 단계는 future Guard가 account funds와 exact cash/100%-margin capacity 및 `received_at`을 읽을 foundation만 제공하고 Guard·Approval·Order authority를 열지 않는다.

### Phase 10D.1A Kiwoom Broker Financial Source Contract 검증 (2026-08-28)

| 대상 | 공식 source 검증 | 결과 |
| --- | --- | --- |
| `kt00001` | official API spec의 meta/request/response/example 대조 | PASS — account-level `entr`, `ord_alow_amt`, `pymn_alow_amt`, margin-band 및 D+1/D+2 field와 signed zero-padded KRW representation 확인 |
| `kt00009` | official TR명·request/response 대조 | PASS — 계좌별주문체결현황이며 financial source가 아님; 원 후보 의미 정정 |
| `kt00010` | official required request와 amount/quantity response 대조 | PASS — required symbol/side/price에 결합된 order simulation; margin-band amount/quantity, orderable cash와 withdrawable amount 확인 |
| account-wide/order-specific | request identity와 response 의미 비교 | PASS — `kt00001` account funds와 `kt00010` order capacity를 분리하는 Option B 확정 |
| representation | official type/required/length/description와 example 대조 | PASS — optional String, KRW/quantity units, zero-padding, signed values; missing은 zero와 다르고 negative는 field별 의미로 처리 |
| freshness | response timestamp field 존재 여부 확인 | PASS — broker observation timestamp 없음; adapter receipt 시각을 `received_at` server observation time으로만 사용 |
| MOCK evidence | local credential/configuration 존재 여부를 값 노출 없이 검사 | NOT_RUN — `CRESTA_KIWOOM_*` 설정과 `.env` 부재, Docker CLI 미제공; LIVE/주문 호출 0, fixture 없음 |

Phase 10D.1A는 source-contract verification only로 production Python·ORM·migration·Guard·Approval·Order·reconciliation을 변경하지 않았다. Phase 10D.1B는 official schema fixture와 별도 redacted MOCK fixture provenance를 구분하고, `kt00001` account funds 및 request-bound `kt00010` capacity의 normalization/persistence/ordering 시험을 구현한다.

### Phase 10D.1 Broker Account Authority Projection 착수 blocker (2026-08-28)

| 대상 | 확인 시나리오 | 결과 |
| --- | --- | --- |
| broker financial source | 현행 account snapshot API ID와 DTO/normalizer를 검사 | `ka10075`/`ka10076`/`kt00018`의 주문·체결·포지션 및 server `observed_at`만 존재; cash/buying-power source 없음 |
| 공식 API 후보 | 키움 REST API 공식 가이드에서 account financial TR 존재 여부를 확인 | `kt00001`과 `kt00010`의 존재는 확인했으나 정확한 response field, MOCK 지원과 account-wide buying-power semantics는 repository에서 미검증 |
| no-fabrication boundary | cash=buying power, missing=0 또는 추정 raw field mapping을 허용하는지 검토 | 금지; authoritative source 계약 전 production implementation 중단 |
| persistence readiness | ORM, reconciliation, migration 0041에서 financial projection을 확인 | 대상 table/column/update path 없음; persistence만으로 source gap 해결 불가 |

Phase 10D.1의 A~N focused test, migration test, SQLite/PostgreSQL 검증은 구현 전 source blocker 때문에 실행하지 않았다. 최신 backend 회귀 근거는 Phase 10C.2의 668건 통과 기록이며 이번 착수 검토가 이를 재실행한 것으로 주장하지 않는다. 공식 raw contract와 redacted MOCK fixture가 확보되면 cash/buying-power의 zero-vs-missing, malformed no-update, identity/freshness, monotonic ordering, 0041→신규 revision과 legacy 보존 시험을 추가한다.

### T-CONSOLE-KIWOOM-STATUS — 상단 키움 상태 일치 (2026-08-22)

- `T-CONSOLE-KIW-STATUS-001`: `/system/health`가 키움 연결을 알리고 `/system/broker`가 worker·gate `READY`를 반환하면 Console 상단이 `키움 모의투자 READY`와 `키움 Gate READY`를 표시하는지 확인한다.
- `T-CONSOLE-KIW-STATUS-002`: 상단에 legacy `Paper Gate`를 키움 상태로 표시하지 않고 Broker 조회 실패는 `UNKNOWN`, 미설정은 `NOT_CONFIGURED`로 구분하는지 확인한다.

Evidence: 상태 READY·NOT_CONFIGURED·UNKNOWN과 인증·Broker 회귀를 묶은 집중 component 시험 5개, TypeScript와 production build가 통과했다. Console 전체 19개 중 이번 변경 관련 18개가 통과하고 기존 운영 휴장 비동기 시험 1개만 실패했다.

### T-KIW-REJECTION-DIAGNOSTIC — 키움 주문 거절 원인 보존 (2026-08-18)

- `T-KIW-REJ-001`: HTTP 200의 `return_code != 0` 주문 응답이 `KIWOOM_ORDER_REJECTED`와 정규화한 Broker 코드·메시지를 만들고 HTTP를 재시도하지 않는지 확인한다.
- `T-KIW-REJ-002`: 주문 거절은 `REJECTED`, 취소 거절은 수량 불변 `RECONCILING`을 유지하면서 해당 OrderEvent에 정제된 코드·사유만 영속하는지 확인한다.
- `T-KIW-REJ-003`: Bearer token, credential 명칭의 값과 8~12자리 연속 숫자가 메시지·API 응답·이벤트 payload에 남지 않는지 확인한다.
- `T-KIW-REJ-004`: `/orders/{id}`가 nullable Broker 결과만 반환하고 `payload_json`을 노출하지 않으며 Console 주문 상세가 결과가 있는 거절 이벤트에만 사유를 표시하는지 확인한다.

Evidence: Adapter·주문 송신기·주문 API 집중시험 51개, backend 전체 364개와 Ruff가 통과했다. Console 집중 component 시험 3개, TypeScript와 production build가 통과했다. Frontend 전체 component 16개 중 기존 운영 휴장 비동기 시험 1개만 실패하고 15개가 통과했다. 2026-08-22 Ubuntu 기능 브랜치 배포 후 서버 이미지에서 집중시험 51개가 다시 통과했고 Compose 전체 health, 내부 HTTP healthz, 키움 Worker `READY`와 브라우저 주문 상세 렌더링을 확인했다. 과거 거절 이벤트 2건에는 소급 metadata가 없고 빈 Broker 거절 문구도 표시되지 않았다. DB migration은 없으며 실제 신규 키움 모의투자 업무 거절의 코드·사유 수신·표시는 다음 장중 인수시험으로 남긴다.

### T-AI-CONSOLE-IA — AI 판단 이력 정보구조 개편 (2026-08-18)

- `T-AI-CONSOLE-IA-001`: AI 판단 화면의 기본 탭이 `운영 판단`이고 `자동 포지션 분석`, `수동 진단`, `전체 이력`을 키보드와 포인터로 전환할 수 있는지 확인한다.
- `T-AI-CONSOLE-IA-002`: `TRADING_ADVISORY` run은 자동 포지션 탭에만 나타나며 요약 행에서 `자동 포지션 분석`, POSITION context, Core/SHADOW 결과, fusion 상태와 시각을 식별할 수 있는지 확인한다.
- `T-AI-CONSOLE-IA-003`: 수동 Agent·Mock 진단 입력은 수동 진단 탭에서만 보이고 DIAGNOSTIC 결과가 자동 포지션 분석과 섞이지 않는지 확인한다.
- `T-AI-CONSOLE-IA-004`: 초기 목록은 최대 12개 요약 행만 렌더링하고 `더 보기`로 다음 12개를 확장하며, 전체 stage·reason·호출·구조화 응답은 선택한 단일 상세만 렌더링하는지 확인한다.
- `T-AI-CONSOLE-IA-005`: 운영 판단 탭은 TRADING Decision과 승인 영역만 표시하고 DIAGNOSTIC Decision은 수동 진단, 모든 유형의 요약 이력은 전체 이력에서 조회되는지 확인한다.

Evidence: 탭 분리, advisory/diagnostic 격리, 단일 상세, reason 축약과 12개 단위 확장을 포함한 관련 component 시험 4개가 통과했다. TypeScript와 Next.js production build가 통과했다. 전체 component 16개 중 이번 변경과 무관한 기존 운영 휴장 비동기 시험 1개만 실패해 15개가 통과했다. Ubuntu Console에 `1ba7554`를 배포한 뒤 Compose 전체 서비스 healthy, 내부 root/healthz HTTP 200, 외부 HTTPS root 200·TLS 검증 0 및 healthz 정상 응답을 확인했다. 2026-08-22 `186b25a` 배포 후 인증된 실제 데이터에서 Decision과 `TRADING_ADVISORY` 요약 행의 `aria-expanded=true`, 바로 다음 형제 `ARTICLE.decision-detail.inline`과 올바른 상세 label을 확인했다.

### T-POSITION-AGENT-FUSION — 외부 Agent POSITION 판단 안전 결합 (2026-08-17)

- `T-POSITION-FUSION-001`: 열린 포지션 scheduler tick이 결정론적 TRADING basis를 먼저 실행 계층에 전달하고, 5개 ACTIVE route가 준비된 경우 같은 basis를 정확히 하나의 `TRADING_ADVISORY/PENDING` run에 연결하는지 확인한다.
- `T-POSITION-FUSION-002`: 수동 DIAGNOSTIC run은 basis/fusion provenance가 없고 판단·승인·주문을 생성하지 않는지 확인한다.
- `T-POSITION-FUSION-003`: 동일 사용자·시장·종목·market snapshot·canonical position hash와 current position version이 모두 일치하며 basis가 유효한 경우에만 결합하는지 확인한다.
- `T-POSITION-FUSION-004`: 필수 4 Scout와 Core가 모두 `SUCCEEDED`, Core v2 schema 통과, `incomplete_roles=[]`, `shadow_assessment!=UNKNOWN`이어야 하며 실패·timeout·schema 오류·누락은 `FAILED_SAFE`로 종료하는지 확인한다.
- `T-POSITION-FUSION-005`: confidence 0.70 이상 `EXIT_RISK_ELEVATED`는 기존 HOLD보다 강한 `PARTIAL_SELL(0.5)`, `EXIT_RISK_HIGH`는 `FULL_SELL`을 새 불변 Decision으로 만들고, `HOLD_SUPPORTIVE/NEUTRAL`과 낮은 confidence는 새 Decision을 만들지 않는지 확인한다.
- `T-POSITION-FUSION-006`: 결합은 결정론적 행동을 낮추지 않고 confidence로 수량을 확대하지 않으며 기존 `PARTIAL_SELL/FULL_SELL`보다 약하거나 같은 결과를 무시하는지 확인한다.
- `T-POSITION-FUSION-007`: 결합 Decision도 기존 행동별 실행 권한·Guard·승인/자동 주문 경계로만 전달되고, 같은 run 재처리가 Decision·DecisionExecution·승인·주문을 중복 생성하지 않는지 확인한다.
- `T-POSITION-FUSION-008`: basis 만료와 position version 변경은 각각 `EXPIRED`/`FAILED_SAFE`로 종료하고, 독립적으로 이미 처리된 결정론적 판단과 FIXED_STOP을 취소하지 않는지 확인한다.
- `T-POSITION-FUSION-009`: migration `20260817_0038`의 upgrade→downgrade→upgrade가 통과하고 DIAGNOSTIC/advisory 제약과 basis/fusion Decision FK·유일성이 유지되는지 확인한다.
- `T-POSITION-FUSION-010`: Console 조회가 `DIAGNOSTIC · 주문 없음`과 `TRADING_ADVISORY · 모델 SHADOW/서버 결합`을 구분하고 fusion policy/state/reason을 표시하는지 확인한다.

Evidence: 집중시험 14개, backend 전체 364개와 Ruff, SQLite migration `20260817_0038` upgrade→downgrade→upgrade가 통과했다. Frontend TypeScript·production build는 통과했고 component 전체 15개 중 기존 운영 휴장 비동기 시험 1개는 이번 변경과 무관하게 실패해 14개가 통과했다. Ubuntu PostgreSQL migration, 실제 외부 Provider POSITION advisory, Guard 이후 키움 모의 SELL 송신·체결은 배포 후 인수시험으로 남긴다.

## 2026-08-14 신규매수 미체결 취소 1차

- `T-ORD-LIFE-001`: BUY 주문 접수 시 10초 뒤 `next_action_at`이 영속되고 그 전에는 취소하지 않는다.
- `T-ORD-LIFE-002`: timeout 뒤 잔량 전부를 대상으로 취소 요청을 정확히 한 번 보내고 정상 접수 후 `CANCEL_PENDING`을 유지한다.
- `T-ORD-LIFE-003`: 부분체결 BUY는 실제 잔량만 취소하며 수량 불변조건을 유지한다.
- `T-ORD-LIFE-004`: 취소 응답 유실은 `UNKNOWN`과 `ORDER_CANCEL_OUTCOME_UNKNOWN` gate를 만들고 자동 재요청하지 않는다.
- `T-ORD-LIFE-005`: 명시적 취소 업무 거절은 `RECONCILING`으로 전환하고 원주문의 수량을 변경하지 않는다.
- `T-ORD-LIFE-006`: SELL·종료 주문·미도래 주문은 첫 자동취소 대상에서 제외한다.

Evidence: migration `20260814_0037` upgrade→downgrade→upgrade, 관련 집중시험 43개, backend 전체 344개, Ruff, Frontend TypeScript·production build와 주문 원장 집중 component 시험이 통과했다. Frontend 전체 14개 중 기존 운영 휴장 비동기 시험 1개는 실패하고 13개가 통과했다. 2026-08-15 Ubuntu PostgreSQL migration, SHADOW 격리 관련 회귀, 주문 API 계약과 기존 주문의 `NONE/0` 보존을 확인했다. 실제 장중 `kt10003` 취소·부분체결 경쟁은 아직 미검증이다.

### T-POS-MANAGED — Broker 총수량과 Cresta 관리수량 분리 (2026-08-14)

- `T-POS-MANAGED-001`: Broker-only position은 총수량·매도가능수량·평균단가를 반영하고 관리수량 0, origin `EXTERNAL`인지 확인한다.
- `T-POS-MANAGED-002`: Cresta BUY 체결 3주와 외부 보유 5주가 함께 있는 Broker 총수량 8주는 관리 3주·외부 5주·origin `MIXED`이며 같은 snapshot 재처리 결과와 event 수가 멱등인지 확인한다.
- `T-POS-MANAGED-003`: 기존 Cresta 관리 attribution에 체결 이력이 없는 업그레이드 row는 보수적으로 보존·총수량 이내 제한되고, Broker 부재 시 모든 수량 필드가 0으로 닫히는지 확인한다.
- `T-POS-MANAGED-004`: `MIXED` fixed stop은 관리평균단가로 발화하고 외부수량을 제외한 관리수량만 SELL 주문에 넣으며, 순수 `EXTERNAL`은 fail-closed인지 확인한다.
- `T-POS-MANAGED-005`: 인증된 포지션 API와 Console이 총수량·매도가능수량·Broker 평균단가·관리수량·관리평균단가·외부수량·origin을 표시하는지 확인한다.

Evidence: migration `20260814_0036` upgrade→downgrade→upgrade, backend 전체 337개 회귀, Ruff, Frontend TypeScript·포지션 집중 component 시험·production build 통과. Frontend 전체 14개 중 기존 운영 휴장 시험 1개는 비동기 timeout으로 실패했다. 2026-08-15 Ubuntu 적용 후 키움 snapshot 재대조에서 `005930` 1주가 `managed=1/external=0/CRESTA_MANAGED`로 분류됐고 포지션 API 계약과 관련 회귀가 통과했다.

### 2026-08-14 키움 projection Console 조회

- `T-READ-001`: `KIWOOM_MOCK_PRIMARY`의 `EXTERNAL` OPEN 포지션이 인증된 `/positions` 목록·상세와 시스템 `open_positions` 집계에 포함되고, legacy `PAPER` 포지션 조회가 유지되는지 확인한다.
- Evidence: backend 포지션·reconciliation 집중시험 20개, Ruff, 해당 Frontend component 시험과 TypeScript 검사 통과. Ubuntu 서버 배포 후 API·Frontend·Nginx healthy, worker `READY`, 키움 MOCK projection 1건(`005930`, 1주, 평균 269000원, `OPEN/EXTERNAL`)을 확인했다. 전체 Frontend 회귀의 운영 휴장 시험 1개 timeout은 별도 기존 시험 안정화 대상으로 남긴다.

### T-EVIDENCE-RECONCILE — 장중 근거 정리와 시간 결정론 회귀 (2026-08-13)

- `T-EVIDENCE-001`: 장중 시험 결과에서 관측한 상태 전이와 미검증 구간을 구분하고, 키움 거절 원인을 증거 없이 호가단위 문제로 확정하지 않는지 확인한다.
- `T-TIME-001`: Risk Guard fixture의 snapshot·worker heartbeat·decision 유효시간과 실행 함수의 기준 시각이 동일한 `NOW`를 사용해 실행 날짜와 무관하게 같은 결과를 내는지 확인한다.
- `T-TIME-002`: 위험 차단 시험이 목표 rule code만 확인하는 데 그치지 않고 clean 상태와 SHADOW 상태도 불필요한 `DECISION_EXPIRED`·`MARKET_DATA_STALE` 없이 통과하는지 확인한다.

Evidence: 승인·주문 생성·전체 Risk Guard·고정손절 집중시험 37개, backend 전체 325개 시험과 Ruff가 2026-08-13 현재 통과했다. `git diff --check`도 통과했다.

### T-APR-SNAPSHOT — 승인 시점 최신 snapshot 재검사

- `T-APR-SNAPSHOT-001`: 승인 생성 뒤 stream이 더 최신 정상 snapshot으로 전진해도 가격편차가 허용 범위 안이면 승인이 성공하고 최신 매도 1호가·최초 승인 수량으로 주문이 생성되는지 확인한다.
- `T-APR-SNAPSHOT-002`: 승인 직전 최신 snapshot이 stale 또는 비정상 품질이면 `MARKET_DATA_STALE` 또는 품질 reason으로 무효화되고 주문이 0건인지 확인한다.
- `T-APR-SNAPSHOT-003`: 최신 정상 가격이 reference 가격의 허용편차를 넘으면 `PRICE_DEVIATION_EXCEEDED`로 무효화되고 주문이 0건인지 확인한다.
- `T-APR-SNAPSHOT-004`: 승인 직전 Guard evaluation의 `snapshot_id`와 주문 감사 입력이 실제 승인 시점 최신 snapshot을 가리키고 판단의 reference snapshot은 승인 scope에 그대로 남는지 확인한다.

Evidence: `test_approvals_api.py`의 최신 정상 snapshot 성공·stale 차단·가격편차 차단 시험을 포함한 승인·주문 생성·Risk Guard·고정손절 집중시험 40개, backend 전체 328개 시험과 Ruff가 2026-08-14 현재 통과했다. Ubuntu 장중 재검증은 대기 중이다.

### T-RISK-GUARD — 전체 Risk Guard 보강 (2026-08-13)

- `T-RISKCALC-001`: `unrealized_loss`가 OPEN position의 (최신가 - 평균단가) * 수량을 합산하는지 확인한다.
- `T-RISKCALC-002`: `daily_realized_loss`가 당일 SELL Fill의 (체결가 - 평균단가) * 수량을 합산하는지 확인한다.
- `T-RISKCALC-003`: `daily_loss_pct`가 REALIZED_PLUS_UNREALIZED일 때 실현+미실현 손실을 합산하고 REALIZED_ONLY일 때 실현만 계산하는지 확인한다.
- `T-RISKCALC-004`: `open_position_exposure`가 per-symbol/전체 노출을 최신가로 계산하고 가격 부재 시 원가 기준으로 하는지 확인한다.
- `T-RISKCALC-005`: `daily_entry_count`가 당일 BUY 주문 수를 세는지 확인한다.
- `T-RISKCALC-006`: `consecutive_loss_count`가 최근 SELL Fill부터 연속 손실 횟수를 세고 이익에서 멈추는지 확인한다.
- `T-RISKCALC-007`: `spread_pct`가 (ask-bid)/midpoint*100을 계산하는지 확인한다.
- `T-RISKCALC-008`: `broker_connection_ok`가 worker READY+websocket+heartbeat+gate READY일 때만 통과하고 재동기화 중에는 차단하는지 확인한다.
- `T-GRD-FULL-001`: clean 상태(위험 없음)에서 BUY가 전체 Risk Guard를 통과하는지 확인한다.
- `T-GRD-FULL-002`: 전체 노출 한도 초과 시 `TOTAL_EXPOSURE_LIMIT`로 BUY 차단, 주문 0건인지 확인한다.
- `T-GRD-FULL-003`: 보유 종목 수 한도 초과 시 `OPEN_POSITIONS_LIMIT`로 차단하는지 확인한다.
- `T-GRD-FULL-004`: 일일 손실 한도 초과 시 `DAILY_LOSS_LIMIT`로 차단하는지 확인한다.
- `T-GRD-FULL-005`: spread 한도 초과 시 `SPREAD_LIMIT`로 차단하는지 확인한다.
- `T-GRD-FULL-006`: Broker 연결 단절 시 `BROKER_CONNECTION_OK`로 차단하는지 확인한다.
- `T-GRD-FULL-007`: 활성 일일손실 risk_event 존재 시 `NO_ACTIVE_DAILY_LOSS_EVENT`로 차단하는지 확인한다.
- `T-GRD-FULL-008`: SHADOW 단계에서는 전체 Risk Guard 통과해도 주문 0건, `SHADOW_RECORDED`인지 확인한다.
- `T-RISK-CONFIG-006`: `daily_loss_limit_pct`/`max_consecutive_losses` 범위 초과·잘못된 basis 거부, 유효값 활성화·조회되는지 확인한다.

Evidence: 구현 시점 backend 전체 회귀 325개 통과(신규 20), Ruff lint 통과, migration `20260813_0035` 왕복 통과, Frontend TypeScript·14개 component 시험·production build 통과. Ubuntu PostgreSQL에도 `20260813_0035`를 적용했다. 2026-08-13 장중 모의투자에서 clean 통과, `SPREAD_LIMIT`, `TOTAL_EXPOSURE_LIMIT`, `SYMBOL_EXPOSURE_LIMIT`, `DAILY_ENTRIES_LIMIT`, `DAILY_LOSS_LIMIT`, `BROKER_CONNECTION_OK` 차단과 설정·연결 회복을 확인했다. 일일 손실은 `REALIZED_PLUS_UNREALIZED`에서 차단되고 `REALIZED_ONLY`에서 통과했다. 테스트 입력은 정리 후 risk event 0건·OPEN position 0건·worker READY로 복구했다.

### T-APR-ORDER — 승인형 BUY 주문 + FIXED_STOP SELL 주문 연결 (2026-08-13)

- `T-ORD-CREATE-001`: 공통 Order Creation Service가 `OrderIntent`+`TradingOrder(CREATED)`를 원자 생성하고, 같은 `idempotency_key`+동일 payload는 같은 주문을 반환하며, 같은 키+다른 payload는 `IDEMPOTENCY_CONFLICT`로 거부하는지 확인한다.
- `T-APR-001`: `APPROVAL_ONLY` + `MANUAL_APPROVAL` BUY가 Guard 통과 시 `Approval(PENDING)`을 만들고 주문은 0건인지 확인한다.
- `T-APR-002`: PENDING 승인을 승인하면 Guard·가격편차 재검사 후 `CREATED` BUY 주문을 원자 생성하고 `Approval(APPROVED)`·`DecisionExecution(ORDER_CREATED)`로 전환되는지 확인한다.
- `T-APR-003`: 같은 `idempotency_key`로 승인 재시도 시 주문이 1건만 생성되는지 확인한다.
- `T-APR-004`: PENDING 승인 거절 시 주문 0건, `Approval(REJECTED)`·`DecisionExecution(REJECTED)`로 종료되는지 확인한다.
- `T-APR-005`: `SHADOW` 단계에서는 `MANUAL_APPROVAL`/`AUTOMATIC` 모두 Approval·주문 0건, `SHADOW_RECORDED`로 유지되는지 확인한다.
- `T-APR-006`: 만료된 승인 승인 시도는 `EXPIRED`로 종료되고 주문 0건인지 확인한다.
- `T-STOP-SELL-002`: `SHADOW` 단계에서는 trigger가 `SHADOW_RECORDED`로 유지되고 주문 0건인지 확인한다.
- `T-STOP-SELL-003`: `EXTERNAL` position은 자동 매도되지 않고 `EXIT_PENDING`(`POSITION_MANAGED_QUANTITY_POSITIVE`)으로 차단되며, `MIXED` position은 관리수량만 주문하는지 확인한다.
- `T-PROV-001`: Position이 기본 `CRESTA_MANAGED`이고 `EXTERNAL` 태깅이 가능한지 확인한다.

Evidence: 구현 시점 backend 전체 회귀 305개 통과(신규 16), Ruff lint 통과, migration `20260813_0034` upgrade→downgrade→upgrade 왕복 통과, Frontend TypeScript·14개 component 시험·production build 통과. 2026-08-13 장중 모의투자에서 승인 BUY가 `PENDING→APPROVED`, 주문이 `CREATED→VALIDATING→SUBMITTING→REJECTED`로 전이해 사용자 승인부터 Broker 송신까지 연결됨을 확인했다. 당시 주문 이벤트에는 키움의 안전한 업무 거절 코드·사유가 없어 원인은 미확인이었다. 이 관측성 공백은 후속 `T-KIW-REJECTION-DIAGNOSTIC` 구현으로 보완했으며 당시 과거 이벤트를 추정해 소급 작성하지 않는다. 최초 시험에서 확인된 stream 최신 snapshot과 판단 snapshot 간 승인 경쟁 조건은 `T-APR-SNAPSHOT` 구현으로 수정했으며 Ubuntu 장중 재검증은 대기 중이다. FIXED_STOP은 현재 매수호가가 손절가보다 높아 미발화가 정상임을 확인했지만, 실제 가격 도달 후 SELL 주문 송신·체결은 미검증이다. SHADOW 회귀는 `SHADOW_RECORDED`와 Approval·CREATED 주문 0건을 확인했다.

#### Historical / Superseded behavior

- `T-APR-007` — **HISTORICAL**: 당시 `APPROVAL_ONLY + AUTOMATIC` BUY가 승인 없이 `CREATED` 주문을 생성하던 현행 구현 동작을 검증했다. EXE-202 및 `T-V2-EXE-001`의 목표 계약으로 superseded됐으며 신규 acceptance test로 사용하지 않는다.
- `T-STOP-SELL-001` — **HISTORICAL**: 당시 `APPROVAL_ONLY`에서 FIXED_STOP trigger가 승인 없이 SELL `CREATED` 주문을 생성하던 현행 구현 동작을 검증했다. EXE-211~213 및 `T-V2-EXE-007`의 목표 계약으로 superseded됐으며 신규 acceptance test로 사용하지 않는다.

### T-DECISION-SELL — 판단 기반 부분·전량매도 연결 (2026-08-15)

- `T-DECISION-SELL-001`: `PARTIAL_SELL`은 `floor(매도가능 관리수량 × sell_ratio)`를 사용하고 1주 미만을 `QUANTITY_BELOW_ONE`으로 차단하며 전량매도로 승격하지 않는다.
- `T-DECISION-SELL-002`: `FULL_SELL`은 Broker 총수량이 아니라 예약수량을 제외한 매도가능 관리수량만 사용하고 순수 `EXTERNAL` 포지션을 차단한다.
- `T-DECISION-SELL-003`: `DISABLED`는 Guard·승인·주문 0건, `MANUAL_APPROVAL`은 PENDING 승인 1건, `AUTOMATIC`은 승인 없이 SELL CREATED 주문 1건을 만든다.
- `T-DECISION-SELL-004`: 승인 범위에 position ID/version·수량·기준 snapshot/가격을 고정하고 승인 시 version 변경·수량 감소·활성/UNKNOWN 주문·stale 시세·가격편차를 `INVALIDATED`로 종료한다.
- `T-DECISION-SELL-005`: 동일 판단과 동일 승인 재시도는 승인·OrderIntent·TradingOrder를 최대 1건만 만들며 주문은 Broker worker만 송신한다.
- `T-DECISION-SELL-006`: Console 승인 카드와 확인창이 BUY 고정 문구 없이 `PARTIAL_SELL`·`FULL_SELL`, 수량과 Cresta 관리분 SELL임을 표시한다.

Evidence: `backend/tests/test_decision_execution_sell.py` 10개와 기존 승인·SHADOW·전체 Risk Guard·Order Creation·position provenance를 합친 관련 backend 38개 시험, Ruff lint, Frontend TypeScript·production build와 SELL 승인 component 시험이 통과했다. DB migration 변경은 없다. 전체 backend 회귀는 Windows pytest 기본 임시 디렉터리 권한 오류가 있어 workspace `--basetemp`로 구간 검증했다. Frontend 전체 15개 중 기존 운영 휴장 비동기 시험 1개는 이번 변경과 무관하게 실패하고 14개가 통과했다. 실제 장중 키움 SELL 접수·부분체결·취소는 별도 인수시험으로 유지한다.

### T-POSITION-DECISION — 보유 포지션 정기 판단 (2026-08-17)

- `T-POSITION-DECISION-001`: 열린 포지션이 있는 scheduler 대상은 같은 슬롯에서 ENTRY 대신 POSITION 판단 하나를 생성하고 반복 tick이 이를 중복 생성하지 않는지 확인한다.
- `T-POSITION-DECISION-002`: POSITION 입력 JSON에 포지션 ID·version, 수량·평균단가·미실현손익률·고정손절 거리와 현재 지표 snapshot이 고정되고 민감정보가 포함되지 않는지 확인한다.
- `T-POSITION-DECISION-003`: `position-policy-v1`의 경계값에서 70점 미만은 HOLD, 70~89점은 PARTIAL_SELL 50%, 고정손절 도달 또는 90점 이상은 FULL_SELL인지 확인한다.
- `T-POSITION-DECISION-004`: stale·degraded·지표 누락·잘못된 원가 입력은 주문 유도 행동이 아니라 HOLD/DATA_INSUFFICIENT로 축소되고 독립 고정손절 trigger에는 영향을 주지 않는지 확인한다.
- `T-POSITION-DECISION-005`: 단일 활성 사용자의 열린 포지션은 감시 목록에서 해제돼도 scheduler가 분석하며, 복수 활성 사용자 환경에서는 계좌 포지션을 임의 사용자에게 귀속하지 않는지 확인한다.

Evidence: 신규 POSITION 정책·scheduler 시험과 기존 scheduler·Mock AI·SHADOW·판단 SELL·전체 Risk Guard를 합친 집중시험 34개, backend 전체 358개와 Ruff lint가 통과했다. DB migration은 없다. 실제 Ubuntu scheduler 연속운전, POSITION 판단의 승인 카드 생성과 키움 모의 SELL 송신은 다음 구현과 함께 배포해 검증한다.

### T-STOP-001 — 고정 손절 trigger SHADOW (2026-08-12)

- `T-STOP-001`: `compute_stop_price`가 평균단가와 `fixed_stop_loss_pct`로 손절가를 계산하고 허용 범위(0.1%~20%) 경계를 통과하는지 확인한다.
- `T-STOP-002`: `should_trigger`가 매수호가 ≤ 손절가(경계 포함)일 때만 발화하고 None/0/양수 미달은 미발화인지 확인한다.
- `T-STOP-003`: gate READY·정상 시세·TRADING 세션에서 trigger가 `SHADOW_RECORDED`로 발화하고 `OrderIntent`·`TradingOrder`·`Decision`·`Approval`이 0건인지 확인한다.
- `T-STOP-004`: 게이트 미준비·재동기화·활성/UNKNOWN 주문·stale 시세·비거래 세션은 `EXIT_PENDING` + `risk_events` ACTIVE로 영속되는지 확인한다.
- `T-STOP-005`: `EXIT_PENDING` trigger가 gate READY 후 재평가에서 `SHADOW_RECORDED`로, risk_event가 `RESOLVED/BROKER_RECOVERED`로 전환되는지 확인한다.
- `T-STOP-006`: 같은 (position, version, policy) 두 번 평가해 `StopTrigger` 1개, 중복 생성 없이 재평가하는지 확인한다.
- `T-STOP-007`: position version 변경(체결) 시 기존 활성 trigger `SUPERSEDED`, 새 version으로 신규 trigger 생성을 확인한다.
- `T-STOP-008`: `PAUSE_ENTRY` 활성 상태에서도 FIXED_STOP trigger가 차단되지 않고(신규매수 전용) 통과하는지 확인한다.
- `T-STOP-009`: 활성 RISK_POLICY가 없을 때 `SAFE_DEFAULT`(-2.0%)를 사용하고 `risk_policy_version_id=null`을 기록하는지 확인한다.
- `T-STOP-010`: 매도가능수량 부족·포지션 종료 시 `SELL_QUANTITY_EXCEEDED`/`POSITION_CLOSED`로 처리되는지 확인한다.
- `T-RISKEVENT-001`: `risk_events`가 scope별로 독립 동작하고 생성·`RESOLVED` 전이·비밀값 미포함을 확인한다.

Evidence: 집중시험 16개, backend 전체 회귀 289개 통과, Ruff lint 통과, SQLite migration `20260812_0033` upgrade→downgrade→upgrade 왕복 통과. Ubuntu PostgreSQL `20260812_0033` 적용 완료, 웹 Console·신규 API(`/system/stop-triggers`, `/system/risk-events`) 정상 응답 확인. 실제 장중 발화·EXIT_PENDING 회복·FIXED_STOP 주문 연결은 대기 중이다.

### T-GUARD-PAUSE-ENTRY-001 — 영속 신규매수 중지

- 로그인 세션·CSRF·고유 `Idempotency-Key`로 `PAUSE_ENTRY`를 활성화하고 동일 키 재시도가 같은 상태를 반환하는지 확인한다.
- 활성 상태가 시스템 준비 응답과 BUY Guard의 `EMERGENCY_STOP_ACTIVE` 차단 결과에 반영되는지 확인한다.
- 해제 후 상태가 `RELEASED`로 바뀌고 활성화·해제 감사 로그가 남는지 확인한다.
- Console에서 확인과 사유 입력 후 활성화하고 새 시스템 상태를 다시 읽는지 확인한다.

## 1. 목적

제품·운영·주문 명세의 요구사항을 검증 가능한 시험으로 연결한다. 구현 전에는 계획 상태로 유지하고, 실제 실행 후 결과와 근거를 추가한다.

### LLM 출력 토큰 기본값 시험 (2026-08-11)

- `T-LLM-TOKEN-001`: `max_output_tokens`를 생략한 신규 Model Profile API 요청이 `8192`를 저장·반환하는지 확인한다.
- `T-LLM-TOKEN-002`: Provider 조회 결과가 출력 한도를 제공하지 않으면 `8192`, 제공하면 해당 값(최대 `32768`)을 사용하는지 확인한다.
- `T-LLM-TOKEN-003`: Console의 신규 역할 후보 5개가 `max output=8192`로 시작하고 사용자가 명시적으로 변경할 수 있는지 확인한다.
- `T-LLM-TOKEN-004`: migration이 DB server default를 `8192`로 변경하되 기존 Profile·Route 값을 수정하지 않는지 upgrade/downgrade로 확인한다.

Evidence: Backend 전체 시험과 Ruff, Frontend component 시험과 TypeScript 검사가 통과했다. SQLite migration 왕복에서 head default `8192`, downgrade default `1024`, 재-upgrade `8192`를 확인했고 Ubuntu PostgreSQL에서도 `20260811_0025` head와 default `8192`를 확인했다. 신규 역할 후보 화면의 기본값은 다음 배포 회귀에서 다시 확인한다.

### LLM 파라미터 안전 기본값 시험 (2026-08-11)

- `T-LLM-PARAM-001`: 신규 Model Profile의 sampling·seed 기본값이 null이고 수동 등록의 context 한도가 임의 생성되지 않는지 확인한다.
- `T-LLM-PARAM-002`: Gemini 3.x가 temperature/top_p를 생략하고 reasoning을 thinkingLevel로 변환하는지 확인한다.
- `T-LLM-PARAM-003`: `openai/gpt-5-*` routed ID도 reasoning 모델로 판정해 sampling 파라미터를 전송하지 않는지 확인한다.
- `T-LLM-PARAM-004`: 신규 Route 120초, Flex UI 300초, 연결 제한 10초를 확인한다.
- `T-LLM-PARAM-005`: 지원 Provider에만 service tier를 허용하고 양수 비용 제한은 미지원으로 거부하는지 확인한다.
- `T-LLM-PARAM-006`: 일일 호출 한도 도달 시 외부 호출 없이 RATE_LIMITED Invocation을 기록하는지 확인한다.
- `T-LLM-PARAM-007`: migration `20260811_0025`가 기존 값은 보존하면서 temperature nullable과 Route timeout server default를 변경하는지 왕복 확인한다.

Evidence: Backend 전체 시험과 Ruff, Frontend component 시험과 TypeScript 검사가 통과했다. SQLite `20260811_0025` 왕복에서 head는 temperature nullable·timeout `120000`·max output `8192`, downgrade는 temperature non-null default `0`·timeout `30000`, 재-upgrade는 head 값을 확인했다. Ubuntu PostgreSQL에서도 `20260811_0025` head를 적용했고 OpenAI·LLM Gateway의 실제 SHADOW 요청과 schema 재검증을 확인했다.

### 현행 검증 기록 해석 원칙 (2026-08-11)

- 아래 표의 `계획`, `부분 통과`와 과거 evidence 문구는 해당 테스트가 마지막으로 갱신된 시점의 기록이다. 이후 날짜가 붙은 회귀시험·Ubuntu 실서버 evidence가 같은 항목을 검증했다면 최신 기록이 우선한다.
- 현재 구현 기준선은 migration `20260811_0027`, Agent Runtime `agent-dag-v6`, 외부 LLM `DIAGNOSTIC/SHADOW`, OpenDART PRIMARY evidence다.
- OpenDART 실제 호출은 삼성전자 최근 3일 공시 6건 수집까지 확인했다. 외부 LLM은 OpenAI와 LLM Gateway를 통한 역할별 구조화 응답 성공·실패 이력을 확인했다.
- 이 문서의 과거 미구현 문구만으로 기능 완료를 판정하지 않는다. 최종 상태는 [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)와 해당 날짜의 실행 evidence를 함께 확인한다.

## 2. 적용 범위

- 설정 검증
- 거래 세션과 감시 스케줄러
- 주문 가격 산정과 Guard
- 부분체결, 취소, 정정과 재주문
- 키움 모의투자 주문·체결 Adapter
- 재시작과 재동기화
- ID·비밀번호·TOTP 인증, 세션과 재인증
- 시장데이터, Scout·Core·다중 에이전트·LLM Provider 계약, DB/API와 운영 복구

## 3. 테스트 케이스

### 3.1 제품 설정

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-PRD-001 | PRD-010~015 | 행동별 자동·승인·비활성 설정 저장 | 각 행동이 독립적으로 저장·적용됨 | 계획 |
| T-PRD-002 | PRD-020~025 | 익일 보유 금지와 장 마감 청산 비활성 조합 | 모순 설정 거부 | 계획 |
| T-PRD-003 | PRD-003~004 | 모의투자 환경에서 실거래 서버 선택 | 시작 또는 주문 차단 | 계획 |
| T-PRD-004 | PRD-005 | Core·Guard 코드에서 키움 TR·원본 필드 의존 검사 | Broker interface 외 직접 의존 없음 | 계획 |
| T-PRD-005 | PRD-030~033 | Web UI 설정 조회·변경·이력·무결성 해제 시도 | 정책 설정 가능, 변경 불가 규칙은 상태만 표시 | 계획 |
| T-PRD-006 | PRD-040~044 | agent·provider·model·fallback 변경과 장애 발생 | DAG·구조화 계약 유지, SHADOW 기본, Guard 우회·신규매수 없음 | 계획 |

### 3.2 거래 세션

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-SES-001 | SES-001 | 07:30 계좌 대조 실패 | 신규매수 차단, 오류 표시 | 계획 |
| T-SES-002 | SES-004~007 | 11:00 전후 시간 진행 | 집중·일반 분석 주기 전환 | 계획 |
| T-SES-003 | SES-020~024 | 신규매수 종료 후 미체결 매수 | 잔량 취소 | 계획 |
| T-SES-004 | SES-030~034 | 동시호가 신규매수 금지 | 주문 미생성 | 계획 |
| T-SES-005 | SES-040~044 | 익일 보유 금지 포지션 장 마감 | 단계적 청산 및 잔량 경보 | 계획 |
| T-SES-006 | SES-010~012 | 당일 캘린더 누락·임시 단축장 수정 | 신규주문 차단, 근거·재인증 새 버전만 허용 | 계획 |
| T-SES-007 | SES-008 | 신규매수 종료 기본값 조회 | 10:00으로 표시·적용 | 계획 |

### 3.3 주문 가격과 미체결

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-ORD-001 | ORD-001~005 | AI가 임의 가격 포함 | 가격 무시 또는 스키마 거부, 규칙 가격 사용 | 계획 |
| T-ORD-002 | ORD-010~013 | 승인 후 가격편차 초과 | 주문 차단 및 재승인 요청 | 계획 |
| T-ORD-003 | ORD-020 | 신규매수 미체결 | 제한 재호가 후 취소, 시장가 전환 없음 | 계획 |
| T-ORD-004 | ORD-021~024 | 10주 중 4주 부분체결 | 4주 포지션 반영, 잔량 정책 실행 | Paper 통과 |
| T-ORD-005 | ORD-030~033 | 취소 확인 전 재주문 시도 | 대체 주문 차단 | 계획 |
| T-ORD-006 | ORD-032 | 주문 응답 시간초과 | UNKNOWN 및 재동기화, 중복 전송 없음 | 부분 통과 |
| T-ORD-007 | ORD-006~007 | 계산 경계가격이 호가단위 사이에 위치 | 승인 범위를 넓히지 않는 방향으로 보정 | 계획 |

### 3.4 상태 머신과 키움 매핑

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-STM-001 | STM-001~003 | REST 주문 성공 후 미체결 | ACKNOWLEDGED/OPEN, FILLED 아님 | Paper 통과 |
| T-STM-002 | STM-010~013 | 취소 처리 중 추가 체결 | 체결 우선 반영, 수량 일치 | Paper 통과 |
| T-STM-003 | STM-020~023 | 응답 유실 후 키움에 주문 존재 | 조회로 기존 주문 연결, 재전송 없음 | 부분 통과 |
| T-STM-004 | STM-030~035 | 동일 체결 이벤트 2회 수신 | 한 번만 반영 | Paper 통과 |
| T-STM-005 | STM-030~035 | 부분체결 후 잔량 취소 | 체결+취소+잔량=주문수량 | Paper 통과 |
| T-STM-006 | STM-020~023 | WebSocket 단절 후 체결 발생 | REST 대조로 주문·포지션 복구 | 계획 |
| T-STM-007 | STM-012 | 정정주문 수행 | 원주문 보존, 부모·자식 관계 생성 | Paper 통과 |
| T-PAP-001 | PAP-001~003, REC-001 | 시작 게이트·실거래 설정·멱등성 재호출 | READY 전·실거래 차단, 동일 payload 기존 주문 반환 | 통과 |
| T-PAP-002 | PAP-004~005, STM-010~013, STM-030~035 | 부분체결·중복체결·취소 대기 중 추가체결 | 중복 없이 포지션·수량 원자 반영, 잔량만 취소 | 통과 |
| T-PAP-003 | PAP-005, STM-012 | 부분체결 주문 정정 | 원주문 REPLACED 보존, 잔량 자식 주문 생성 | 통과 |
| T-PAP-004 | PAP-006, STM-020~023 | 주문 응답 유실 후 같은 종목 신규 주문 | UNKNOWN 유지, 신규 주문 차단, 기존 key 재조회 허용 | 통과 |
| T-PAP-005 | PAP-007~008 | NXT 주문·미인증 주문 조회·Paper 생성 API 탐색 | `UNSUPPORTED_IN_MOCK`, 조회 401, 생성 API 없음 | 통과 |

### 3.5 계좌·주문 재동기화

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-REC-001 | REC-001~005 | 서버 시작 직후 주문 시도 | 전체 대조 완료 전 주문 차단 | 부분 통과 |
| T-REC-002 | REC-010~018 | WebSocket 단절 후 부분체결 | 재연결 대조로 체결·잔량 복원 | 계획 |
| T-REC-003 | REC-020~024 | 내부·키움 보유 수량 불일치 | 키움 수량을 운영 기준으로 반영하고 감사 기록 | 계획 |
| T-REC-004 | REC-030~034 | 스냅샷 조회 중 체결 이벤트 도착 | 버퍼 재생 후 한 번만 반영 | 계획 |
| T-REC-005 | REC-040~043 | 주문 응답 유실 후 키움 주문 발견 | 기존 주문 연결, 중복 제출 없음 | 계획 |
| T-REC-006 | REC-040~043 | 대조 후에도 주문 결과 불명확 | READY 전환 금지 및 종목 격리 | 계획 |
| T-REC-007 | REC-050~054 | 내부에 없는 체결 발견 | 누락 체결 복원 및 불일치 해결 기록 | 계획 |
| T-REC-008 | REC-060~063 | 키움 앱에서 외부 주문 생성 | 외부 주문 생성·종목 차단, 전략 자동 편입 없음 | 계획 |
| T-REC-009 | REC-030~034 | 같은 체결이 스냅샷과 WebSocket에 존재 | 중복 제거 후 수량 불변조건 유지 | 계획 |
| T-REC-010 | REC-064~065 | 외부 포지션 편입·평균단가 불일치 | 필수 정책 승인, BROKER_BASIS와 차이 표시 | 계획 |
| T-REC-011 | REC-070~071,076~077 | 읽기 전용 bootstrap 대조 성공·불일치·조회 실패 | READY 금지, 각각 RECONCILING·HALTED·DEGRADED와 run 보존 | 통과 (2026-08-03, 자동) |
| T-REC-012 | REC-072~075 | 외부/내부 주문·포지션·체결 합계 조합 | 안정된 mismatch 코드, 자동 주문·Fill·포지션 수정 없음 | 통과 (2026-08-03, 자동) |

### 3.6 키움 Broker Adapter

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-KIW-001 | KIW-001~005 | 등록된 출구 IP에서 모의 인증·계좌조회 | 지정 모의 계좌 조회 성공 | 계획 |
| T-KIW-002 | KIW-010~013 | 컨테이너 이미지 교체 | 데이터·로그·백업 유지 | 계획 |
| T-KIW-003 | KIW-020~025 | 로그·오류·진단자료 검사 | App Key·Secret·토큰·전체 계좌번호 미노출 | 계획 |
| T-KIW-004 | KIW-030~034 | 기대 출구 IP와 실제 IP 불일치 | Broker 시작 또는 신규매수 차단 | 계획 |
| T-KIW-005 | KIW-040~044 | 동일 계좌 Broker worker 2개 동시 시작 | 하나만 Active, 다른 worker 주문 불가 | 계획 |
| T-KIW-006 | KIW-040~044 | Active worker lease 상실 | 주문 중단·재동기화 후에만 승계 | 계획 |
| T-KIW-007 | KIW-050~054 | MOCK 설정에서 LIVE secret 주입 | 서비스 시작 거부 | 계획 |
| T-KIW-008 | KIW-060~063 | 모의 환경에서 NXT/SOR 주문 요청 | 주문 생성 전 차단 | 계획 |
| T-KIW-009 | KIW-070~073 | 호출 제한을 넘는 조회·주문 요청 | 중앙 큐 제한 준수, 손절·취소 우선 | 계획 |
| T-KIW-010 | KIW-080~084 | WebSocket 단절·재연결 | 신규매수 차단, 재구독·재동기화 후 복귀 | 계획 |
| T-KIW-011 | KIW-090~092 | 모의 URL에서 토큰 발급 응답 수신·재호출 | KST 만료시각 해석, 메모리 재사용, 60분 전 단일 갱신 | 통과 (2026-08-01, 자동) |
| T-KIW-012 | KIW-093, KIW-095 | 일반 REST 인증 실패와 오류·비 JSON 응답 | 토큰 폐기 후 1회만 재시도, 실패 응답 격리 | 통과 (2026-08-01, 자동) |
| T-KIW-013 | KIW-094, MKT-070~074 | `ka10001` fixture 정규화 | 부호 제거·필수값 검증·결정적 hash·명시적 거래상태 | 통과 (2026-08-01, 자동) |
| T-KIW-014 | KIW-096, API-086 | Broker 비활성·secret 누락·secret 준비 상태 조회 | 각각 `NOT_CONFIGURED`, `NOT_CONFIGURED`, `CONFIGURED`; 외부 인증 전 `CONNECTED` 금지 | 통과 (2026-08-01, 자동) |
| T-KIW-015 | KIW-090 | MOCK 환경에 운영 Kiwoom URL 주입 시도 | 설정 검증에서 기동 거부 | 통과 (2026-08-01, 자동) |
| T-KIW-016 | KIW-097 | `ka00001` 정상·필드 누락·잘못된 형식 fixture | 숫자 10자리만 내부 계좌 식별값으로 수용 | 통과 (2026-08-03, 자동) |
| T-KIW-017 | KIW-098~100 | secret 계좌 일치·불일치·8자리 prefix 입력 | 정확한 10자리만 통과, 불일치 fail-closed, 출력은 마스킹 | 통과 (2026-08-03, 자동) |
| T-KIW-018 | KIW-101 | `kiwoom-check` 성공·인증 실패·계좌 실패 | 안정된 상태·오류 코드와 종료코드, 비밀값 미출력 | 통과 (2026-08-03, 자동) |
| T-KIW-019 | KIW-102~103 | 일회성 점검 종료 후 시스템 상태 조회 | API는 `READY`를 주장하지 않고 구성 상태만 유지 | 통과 (2026-08-03, 자동) |
| T-KIW-020 | KIW-097~103 | 운영 서버의 MOCK token으로 `ka00001` 조회 후 10자리 secret 일치 점검 | 마스킹된 `ACCOUNT_VERIFIED`, 전체 계좌·token 미출력 | 통과 (2026-08-03, 실서버 수동) |
| T-KIW-021 | KIW-104~106 | 세 snapshot API의 단일·다중 페이지와 중간 실패 | 공식 body/header, 전체 페이지 성공 전 결과 미사용 | 통과 (2026-08-03, 자동) |
| T-KIW-022 | KIW-105 | 빈·반복 next-key와 20페이지 초과 | `KIWOOM_INVALID_PAGINATION`, 무한 호출 없음 | 통과 (2026-08-03, 자동) |
| T-KIW-023 | KIW-107~109 | 정상·경계·비지원 주문/체결/잔고 fixture | 엄격 정규화, 비지원/수량 위반 fail-closed, Fill 미생성 | 통과 (2026-08-03, 자동) |
| T-KIW-024 | KIW-110 | reconciliation CLI 성공·불일치·외부 실패 | 비밀 없는 요약과 안정된 종료코드 | 통과 (2026-08-03, 자동) |

### 3.7 Guard 리스크 및 비상정지

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-GRD-001 | GRD-001~006 | Web UI에서 리스크 정책 변경 | 검증·영향 미리보기·확정 후 버전 적용 | 계획 |
| T-GRD-002 | GRD-010~014 | 주문금액이 종목 한도보다 큰 설정 | 활성화 거부 | 계획 |
| T-GRD-003 | GRD-020~025 | 일일 손실 한도 도달 | 설정된 범위의 신규매수 중지 | 계획 |
| T-GRD-004 | GRD-030~034 | 새 손절가가 현재가보다 높은 장중 변경 | 즉시 손절 영향 경고 및 적용 시점 선택 | 계획 |
| T-GRD-005 | GRD-040~044 | WebSocket 단절 기준 도달 | 신규매수 차단 및 재동기화 요구 | 계획 |
| T-GRD-006 | GRD-050~055 | 비상정지 실행·재시작·해제 | 상태 유지, 재인증·재동기화 후 해제 | 계획 |
| T-GRD-007 | GRD-060~064 | 특정 종목 외부 포지션 발생 | SYMBOL_HALT, 다른 안전 종목은 정책대로 처리 | 계획 |
| T-GRD-008 | GRD-070~071 | 무결성 규칙 해제 API 요청 | 서버 거부 및 감사 로그 | 계획 |
| T-GRD-009 | GRD-015, GRD-045 | 금액·손절·데이터 임계값 허용범위 밖 설정 | 자동 보정 없이 활성화 거부 | 계획 |

### 3.8 사용자 설정

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-CFG-001 | CFG-001~004 | 모든 설정 영역 조회 | 최종값·출처·수정 가능 여부 표시 | 계획 |
| T-CFG-002 | CFG-010~013 | 종목별 재정의 추가·삭제 | 우선순위 적용 및 기본값 복귀 | 계획 |
| T-CFG-003 | CFG-020~024 | 초안 저장 후 활성화·롤백 | 불변 버전 생성, 초안은 거래 미적용 | 계획 |
| T-CFG-004 | CFG-030~034 | 장중 손실 제한 완화 | 재인증 및 적용 시점 검증 | 계획 |
| T-CFG-005 | CFG-040~043 | 미리보기 후 포지션 변경 | 오래된 미리보기 확정 거부 | 계획 |
| T-CFG-006 | CFG-050~052 | 두 브라우저 동시 설정 변경 | 오래된 버전 저장 거부 | 계획 |
| T-CFG-007 | CFG-060~061 | 위험 완화 사유 누락·31일 뒤 예약 | 활성화 거부 및 오류 표시 | 계획 |

### 3.9 Web UI

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-UI-001 | UI-001~004 | 콘셉트 적용 화면 검토 | 국내주식·키움 모의투자 정보와 비색상 상태 표시 | 계획 |
| T-UI-002 | UI-010~014 | MOCK·RECONCILING 상태 | 전역 상태와 주문 제한·비상정지 접근 표시 | 계획 |
| T-UI-003 | UI-020~023 | 오래된 시세가 있는 대시보드 | STALE·경과시간 표시, 승인 불가 | 계획 |
| T-UI-004 | UI-030~035 | 네 번째 감시 종목 등록 | 등록 차단 및 남은 슬롯 표시 | 통과 (2026-08-04, API·component) |
| T-UI-005 | UI-040~043 | 부분체결·UNKNOWN 포지션 | 잔량·재동기화 상태 표시 | 계획 |
| T-UI-006 | UI-050~054 | 승인 만료·가격 이탈 | 승인 비활성화와 원인 표시 | 계획 |
| T-UI-007 | UI-060~064 | API 자격증명 영역 검사 | 비밀 입력·원문 없이 상태만 표시 | 계획 |
| T-UI-008 | UI-070~074 | EMERGENCY_LIQUIDATE 실행 | 영향 미리보기·강한 확인·지속 상태 표시 | 계획 |
| T-UI-009 | UI-080~084 | 외부 주문 발견 | 격리·해결 선택과 거래 차단 범위 표시 | 계획 |
| T-UI-010 | UI-090~093 | WebSocket 단절·복구 | 마지막 정상시각·재조회·상태 전환 표시 | 계획 |
| T-UI-011 | UI-085~087, API-094~098 | 시스템 상태에서 MOCK 시장가 1주 시험 | Broker READY 후만 활성, TOTP 재인증·CSRF, CREATED를 체결로 표시하지 않음 | 통과 (2026-08-04, 자동 fixture) |
| T-UI-017 | UI-100~105 | 데스크톱·태블릿·모바일·키보드 검사 | 반응형·포커스·대비·감소된 움직임 통과 | 부분 통과 |

### 3.10 인증 및 보안

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-SEC-001 | SEC-001~006, UI-AUTH-001~002 | ID·비밀번호만 입력하고 보호 화면·API 접근 | TOTP 완료 전 접근 거부 | 계획 |
| T-SEC-002 | SEC-010~015, UI-AUTH-003 | 정상·만료·앞뒤 시간 구간·재사용 TOTP 검증 | 허용 구간의 미사용 코드만 성공 | 계획 |
| T-SEC-003 | SEC-020~025 | 공개 회원가입·TOTP 조회·분실 복구 시도 | 공개 우회 없음, 로컬 절차 후 기존 세션 폐기 | 계획 |
| T-SEC-004 | SEC-030~034 | DB·로그·DOM·브라우저 저장소의 비밀값 검사 | 평문 비밀번호·TOTP secret·복구 코드·세션 토큰 미노출 | 계획 |
| T-SEC-005 | SEC-040~044, UI-AUTH-004 | 계정·IP에서 연속 5회 인증 실패 | 15분 잠금 및 계정 열거 불가능한 공통 오류 | 계획 |
| T-SEC-006 | SEC-050~055, UI-AUTH-005~007 | 비활동 만료·8시간 만료·로그아웃·WebSocket 연결 | 세션과 연결 종료, 요청 자동 재실행 없음 | 계획 |
| T-SEC-007 | SEC-053 | CSRF 토큰·Origin 누락 또는 불일치 상태 변경 요청 | 변경 전 서버 거부 및 감사 기록 | 계획 |
| T-SEC-008 | SEC-060~064 | TOTP 재인증 없이 주문 승인·한도 완화·비상정지 해제 | 대상 행동이 원자적으로 거부됨 | 계획 |
| T-SEC-009 | SEC-061~062 | 다른 요청에서 발급한 재인증 증명 재사용 | 대상 불일치 또는 재사용으로 거부 | 계획 |
| T-SEC-010 | SEC-070~072 | 인증 성공·실패·잠금·재설정 감사 로그 검사 | 필요한 메타데이터만 있고 인증값은 없음 | 계획 |
| T-SEC-011 | SEC-010~012 | 서버 시각 허용 오차 초과 | 로그인·재인증 fail-closed 및 운영 경보 | 계획 |
| T-UI-AUTH-001 | UI-AUTH-001~005, SEC-003, SEC-034 | 미인증 초기 접속·비밀번호 성공·TOTP 성공·새로고침 | TOTP 전 보호 화면 미노출, 성공 후 Console, 인증값 브라우저 저장 없음 | 부분 통과 |
| T-UI-AUTH-002 | UI-AUTH-004~007, SEC-051~053 | 인증 실패·세션 만료·로그아웃 | 일반 오류, 보호 상태 폐기, 상태 변경 자동 재실행 없음 | 부분 통과 |
| T-UI-PAP-001 | UI-PAP-001~006, API-080~085 | 실제 Paper 상태·빈 주문·주문 상세·포지션 조회와 401 발생 | 실제 저장값만 표시, 빈 상태 구분, 세션 폐기, 운영 생성 컨트롤 없음 | 로컬 통과 |

### 3.11 시장데이터 및 Watch

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-MKT-001 | MKT-001~005 | KRX·NXT 동일 종목 이벤트 정규화 | 시장별 snapshot 분리, 단위·시각·품질 유지 | 부분 통과 |
| T-MKT-002 | MKT-010~014 | 중복·역순·순번 갭·누적량 역행 주입 | 중복 억제, 현재값 비역행, gap 복구 요청 | 부분 통과 |
| T-MKT-003 | MKT-020~024 | quote·호가 지연 후 정상 이벤트 복구 | 신규매수 차단, 안정 구간·대조 후 재개 | 계획 |
| T-MKT-004 | MKT-030~034 | 고정 tick fixture로 1분봉·VWAP 계산 | 기준값·버전·시장과 정확히 일치 | 계획 |
| T-MKT-005 | MKT-040~043 | VI·거래정지·호가부재·기업행동 이벤트 | 주문 차단 또는 분석 기준 초기화 | 계획 |
| T-MKT-006 | MKT-050~053 | Redis 유실과 Watch 승계 | DB snapshot 복원 후 단일 writer 처리 | 계획 |
| T-MKT-007 | MKT-060~066, DB-080~083 | 동일·충돌·역순·순번 갭·거래량 역행 fixture 주입 | 중복 억제, 충돌 격리, 이전 정상 snapshot 유지 | 로컬 통과 |
| T-MKT-008 | API-090~094 | 미인증·KRX/NXT·없음·정상·지연 quote 조회와 mutation 탐색 | 401/404/검증 오류, 명시적 품질·경과시간, mutation 없음 | 로컬 통과 |

### 3.12 Scout·Core AI 계약

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-AI-001 | AI-001~005 | Scout·Core가 가격·주문 명령 출력 시도 | 스키마 거부, 주문 미생성 | 계획 |
| T-AI-002 | AI-010~014 | 결측·지연·비밀 포함 입력 생성 시도 | 호출 차단 또는 비밀 제거와 품질 표시 | 계획 |
| T-AI-003 | AI-020~023 | Scout enum·점수·reason 오류 | UNKNOWN 분석 상태, Core 자동 실행 없음 | 계획 |
| T-AI-008 | AI-024 | 미등록 reason code와 표시문 생성 | 출력 거부, 등록된 서버 번역만 사용 | 계획 |
| T-AI-004 | AI-030~034 | 상태 불일치 행동·비율·만료 출력 | 실행 거부 및 검증 오류 기록 | 계획 |
| T-AI-005 | AI-040~044 | timeout·응답유실·1회 재시도 | 중복 판단·주문 없이 신규매수 차단 | 계획 |
| T-AI-006 | AI-050~053 | 외부 텍스트에 주문 지시 삽입 | 비신뢰 데이터 처리, 도구·주문 접근 없음 | 계획 |
| T-AI-007 | AI-060~063 | 동일 fixture로 모델 버전 비교 | 미래 데이터 없이 동일 평가 기준 사용 | 계획 |

### 3.12.1 다중 에이전트 오케스트레이션

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-MAO-001 | MAO-001~005, AI-091~094 | 동일 입력으로 DAG를 중복 tick하고 DIAGNOSTIC run을 실행 경로에 전달 | run·Core 1개, 진단 승격·승인·주문 0건 | 계획 |
| T-MAO-002 | MAO-010~014 | 가격을 포함한 웹 문서·중복 기사·출처 없는 요약 수집 | 가격은 Watch만 사용, 중복 묶음, 출처·시각·hash 없는 증거 거부 | 계획 |
| T-MAO-003 | MAO-020~023 | 상충 공시·뉴스와 외부 정보 없음 | `CONFLICTED/PARTIAL`, 임의 사실 선택·긍정 신호 없음 | 계획 |
| T-MAO-004 | MAO-030~034 | Scout가 미등록 evidence·reason, 잘못된 점수와 결측 추정 출력 | `INVALID_OUTPUT/INSUFFICIENT_DATA`, Core 신규매수 차단 | 계획 |
| T-MAO-005 | MAO-040~045, AI-095~099 | 필수 Scout timeout·실패·Core fallback 시도 | `WAIT/RISK_BLOCK`, Core 재전송·무승인 fallback·주문 없음 | 계획 |
| T-MAO-006 | MAO-050~054 | stage worker 중복 claim·crash·lease 만료·응답유실 | 단일 실행, 완료 stage 재호출 없음, 불명확 결과 격리 | 계획 |
| T-MAO-007 | MAO-060~063 | N100 동시 호출·queue 지연·비용 한도 초과 | admission·우선순위·유효시간 준수, Guard 지속 | 계획 |
| T-MAO-008 | MAO-070~074 | 웹 원문에 주문·비밀·내부 URL 접근 지시 삽입 | 명령 무시, SSRF·도구·Broker 접근 차단, 안전 escape | 계획 |
| T-MAO-009 | MAO-080~083 | 신규 model·prompt·DAG를 SHADOW로 실행·활성화 시도 | 회귀시험·TOTP 전 활성화 불가, SHADOW 승인·주문 0건 | 계획 |
| T-MAO-010 | MAO-090~098, DB-124~127, API-135, UI-118~119 | 5개 Mock route로 DIAGNOSTIC DAG 실행·중복 요청·route 변조 | run 1개, stage 8개·invocation 5개 provenance, Candidate Audit 후 Core WAIT, decision·approval·order 0건 | 통과 (2026-08-06 최초, 2026-08-11 Candidate Audit 회귀) |
| T-MAO-011 | MAO-100~107, DB-132~134, API-143~144, UI-138 | 비동기 admission, stage claim·lease 만료·재claim과 이전 fencing 완료 시도, scheduler ACTIVE route admission | stage 단일 소유·fencing 증가·늦은 완료 거부, UI 비동기 상태 갱신, 최종 PARTIAL/WAIT, decision·approval·order 0건 | 통과 (2026-08-06, 자동 DB·API·component fixture) |

### 3.12.2 Cresta v2 ENTRY Decision Architecture

이 절은 Phase 1에서 시작해 후속 설계 단계에서 구체화한 Cresta v2 목표 계약의 시험 계획과 단계별 실행 근거다. 상태 열에 통과 근거가 명시된 항목 외에는 아직 실행하지 않았으며 기존 Core·Agent Runtime v1~v6 시험과 통과 근거는 변경하지 않는다. 특히 `T-V2-EXE-001`~`003`은 Phase 0에서 확인된 현행 구현 결함을 고정하는 회귀시험이라 수정 전 코드에서는 실패할 수 있다.

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-V2-AI-001 | AI-205~208 | 같은 ENTRY run으로 세 Decision Agent 호출 | 세 Agent가 동일 DecisionContext ID/hash를 사용 | 계획 (Phase 1) |
| T-V2-AI-002 | AI-206 | DecisionContext 고정 후 최신 시세·DB 변경 | 이미 시작한 세 Agent 입력은 변하지 않음 | 계획 (Phase 1) |
| T-V2-AI-003 | AI-207 | 한 Agent 결과 또는 prompt를 다른 Agent 입력에 주입 | 계약 또는 runtime에서 거부 | 계획 (Phase 1) |
| T-V2-AI-004 | AI-200~204 | Scout·Decision Agent·Arbiter가 Order/Approval/Broker 필드나 호출 생성 시도 | schema/runtime 거부, 거래 resource 0건 | 계획 (Phase 1) |
| T-V2-AI-005 | AI-209~212 | 허용되지 않은 action·reason·evidence 반환 | `INVALID_OUTPUT`, BUY 없음 | 계획 (Phase 1) |
| T-V2-AI-006 | AI-213~215 | 동일 context를 세 PolicyProfile로 실행 | 입력은 동일하고 policy/provenance만 역할별 상이 | 계획 (Phase 1) |
| T-V2-AI-007 | AI-216~218 | Decision Agent timeout·provider·schema 실패 | `deterministic-mock-v2` BUY fallback 0건, Guard 안전 규칙 지속 | 계획 (Phase 1) |
| T-V2-AI-008 | AI-217 | 필수 Agent 1개 실패 상태에서 Arbiter 실행 | BUY 생성 0건 | 계획 (Phase 1) |
| T-V2-AI-009 | AI-226 | ArbiterResult를 Execution Orchestrator에 직접 전달 | 입력 거부, TRADING Decision·Approval·Order 0건 | 계획 (Phase 1) |
| T-V2-AI-010 | AI-227~231 | 검증된 ArbiterResult를 정상 finalization | `purpose=TRADING`, `decision_kind=ENTRY` Decision 정확히 1개와 완전한 lineage | 계획 (Phase 1) |
| T-V2-AI-011 | AI-229 | 동일 run·context·ArbiterResult·policy로 finalization 반복 | 최종 ENTRY Decision 최대 1개 | 계획 (Phase 1) |
| T-V2-AI-012 | AI-230 | context hash·provenance·만료 불일치 상태에서 finalization | TRADING Decision 0건, fail-closed 결과 기록 | 계획 (Phase 1) |
| T-V2-AI-013 | AI-231 | Finalizer 처리 전후 action과 confidence 비교 | ArbiterResult action을 재해석하지 않고 새 confidence를 계산하지 않음 | 계획 (Phase 1) |
| T-V2-AI-014 | AI-219 | runtime role과 DecisionAgentResult.agent_type 조합 전체 검증 | 세 역할은 일대일 매핑, ENTRY_ARBITER의 agent_type 거부 | 계획 (Phase 1) |
| T-V2-AI-015 | AI-236~237 | v7 Scheduler admission과 production ENTRY 실행 | pipeline 시작·결과 인계만 수행, 자체 판단 규칙과 deterministic-mock-v2 BUY 사용 0건 | 계획 (Phase 1) |
| T-V2-AI-016 | AI-229~230 | Finalizer의 TRADING Decision 영속 transaction에서 DB 오류 또는 commit 결과 불명확 발생 | Execution/Broker로 새 결과를 전달하지 않고 lineage/idempotency key로 기존 Decision을 먼저 조회; 중복 Decision 0건, 확인 불가 시 fail-closed 유지 | 계획 (Phase 1) |
| T-V2-ARB-001 | AI-220~225, AI-268 | 모든 C/B/A status·action 조합과 9개 normative example | `consensus-policy-v1` precedence, 여섯 pattern/action/reason mapping과 truth table에 완전히 일치 | 통과 (2026-08-26 Phase 8C pure evaluator matrix) |
| T-V2-ARB-002 | AI-203, AI-270, AI-273, MAO-250, MAO-253 | ENTRY_ARBITER 실행과 handler surface 검사 | route/invocation/Prompt/Model/LLM/Provider/network/web/tool/live/Broker 0건 | 통과 (2026-08-26 Phase 8C provider-less production dispatch) |
| T-V2-ARB-003 | AI-222, AI-274, DB-197, DB-201 | 동일 authoritative input을 DB query·C/B/A completion order만 바꿔 반복 | canonical input hash, Result와 output hash가 모두 동일 | 통과 (2026-08-26 Phase 8C canonical determinism) |
| T-V2-ARB-004 | AI-221, AI-269, DB-200~201 | 지정한 Context와 C/B/A stage IDs/hashes/status/actions/policy/validity로 Arbiter 실행 | exact ordered `input_result_ids/input_results`와 full Finalizer-ready lineage 보존 | 통과 (2026-08-26 Phase 8C result lineage) |
| T-V2-ARB-005 | AI-266~267, DB-197~198 | missing stage/output/hash, malformed Result, hash mismatch, duplicate/mismatched role, cross-run/context | reconciliation fail-closed, ENTRY_ARBITER stage 0건, structural corruption을 UNKNOWN으로 축소하지 않음 | 통과 (2026-08-26 Phase 8C structural matrix) |
| T-V2-ARB-006 | AI-267, AI-272, DB-198 | Context 또는 Result가 materialization 전에 만료되거나 validity가 불일치 | ENTRY_ARBITER stage 0건 | 통과 (2026-08-26 Phase 8C expiry/validity rejection) |
| T-V2-ARB-007 | AI-271~272, MAO-252, DB-202~203 | materialization 뒤 C/B/A/Context/input tamper 또는 expiry | mismatch는 CONFLICTED, expiry는 TIMED_OUT, 두 경우 output JSON/hash null | 통과 (2026-08-26 Phase 8C claim/completion revalidation) |
| T-V2-ARB-008 | AI-271, MAO-249, DB-202 | C/B/A 중 structured INSUFFICIENT_DATA/CONFLICTED/TIMED_OUT/FAILED/INVALID_OUTPUT | dependency eligibility 유지, ENTRY_ARBITER SUCCEEDED와 MANDATORY_UNKNOWN/UNKNOWN Result | 통과 (2026-08-26 Phase 8C five-status matrix) |
| T-V2-ARB-009 | MAO-248~249 | 일반 dependency failure policy와 Arbiter-specific terminal eligibility 비교 | generic rule이 Arbiter를 차단하지 않고 C/B/A 세 role AND만 적용 | 통과 (2026-08-26 Phase 8C specialized claim gate) |
| T-V2-ARB-010 | MAO-246~247, MAO-254, DB-199 | reconciliation 반복·process crash·exact same input 및 mismatched existing input hash | stage 정확히 1개, same input reuse, mismatch conflict와 기존 row 불변 | 통과 (2026-08-26 Phase 8C idempotent materializer) |
| T-V2-ARB-011 | MAO-250~252, DB-203 | claim/lease recovery와 stale fencing completion | 권위 completion 최대 1개, stale overwrite 0건, integrity/expiry terminal matrix 준수 | 통과 (2026-08-26 Phase 8C fencing/recovery) |
| T-V2-ARB-012 | AI-270~271, MAO-253 | pure evaluator internal failure 주입 | stage FAILED, output JSON/hash null, 임의 UNKNOWN Result 없음 | 통과 (2026-08-26 Phase 8C injected failure) |
| T-V2-ARB-013 | AI-273, AI-275, MAO-255 | BUY 포함 모든 ArbiterResult와 DIAGNOSTIC completion | ArbiterResult 외 Decision·Approval·Order·Broker·Finalizer·Activation side effect 0건 | 통과 (2026-08-26 Phase 8C authority boundary) |
| T-V2-ARB-014 | AI-269, DB-200~204 | exact Result field set, forbidden confidence/score/Policy/Prompt/Model/Provider field와 runtime timestamp 입력 | exact schema만 허용, forbidden/unknown field 거부, canonical lineage/hash 재현 | 통과 (2026-08-26 Phase 8C strict Pydantic contract) |
| T-V2-ARB-015 | AI-266~275, MAO-246~255, DB-197~204 | production C/B/A three-result checkpoint부터 ENTRY_ARBITER E2E | exact one stage/result, canonical consensus와 Finalizer-ready lineage, trading resource 0건 | 통과 (2026-08-26 Phase 8C production worker E2E) |
| T-V2-ARB-016 | MAO-200, MAO-236, DB-191 | Phase 7 C/B/A, Phase 4~6 upstream, v1~v6 stored fixture 회귀 | 기존 stage/result/input/output hash와 runtime 의미 불변 | 통과 (2026-08-26 Phase 8C focused 125 + legacy 68 + full 532 regression) |
| T-V2-ARB-017 | AI-266~275, MAO-246~255, DB-197~204 | Market/Input부터 upstream 7, Context, C/B/A Provider worker와 Arbiter worker까지 all-BUY 및 mandatory-UNKNOWN production-style E2E | fixture의 Result 직접 삽입 없이 BUY/UNKNOWN canonical ArbiterResult 생성, provider-less 및 authority boundary 유지 | 통과 (2026-08-26 Phase 8D FULL-E2E-BUY/UNKNOWN) |
| T-V2-ARB-018 | AI-267~268, AI-271, MAO-249, DB-202 | C/B/A 각 role에 다섯 structured non-success를 개별 주입 | 15개 case 모두 Arbiter stage SUCCEEDED, MANDATORY_UNKNOWN/UNKNOWN과 exact server reason 1개 | 통과 (2026-08-26 Phase 8D 15-case role/status matrix) |
| T-V2-ARB-019 | AI-267, AI-271~272, MAO-246, MAO-251~252, DB-198, DB-202~203 | missing C/B/A, output/hash/schema/identity/Context/Policy/validity corruption과 materialization 전후 expiry | pre-stage corruption/expiry는 stage 0건, post-stage corruption은 CONFLICTED, expiry는 TIMED_OUT, output 없음 | 통과 (2026-08-26 Phase 8D structural/expiry acceptance) |
| T-V2-ARB-020 | AI-268, AI-270, AI-274 | normative truth table 및 status/action 가능한 조합을 반복·순서 변경 평가 | pattern/action/reason 1:1, pure evaluator 결과가 clock/config/DB와 무관 | 통과 (2026-08-26 Phase 8D 27 success-action combinations) |
| T-V2-ARB-021 | AI-266, AI-269~270, AI-274, DB-197, DB-200~201 | Context/result identity·hash·status/action·validity 변화와 confidence/score/Agent reason/current control-plane 변화 비교 | 계약 field 변화만 input/output hash에 반영되고 제외 field 및 조회·완료 순서는 consensus 의미를 바꾸지 않음 | 통과 (2026-08-26 Phase 8D canonical hash/exclusion acceptance) |
| T-V2-ARB-022 | MAO-246~247, MAO-254, DB-199 | reconciliation 전 crash와 PENDING materialization 뒤 claim 전 crash를 idle/normal worker로 복구 | 기존 또는 새 exact-one stage를 materialize/claim/execute하며 duplicate 없음 | 통과 (2026-08-26 Phase 8D two-boundary crash recovery) |
| T-V2-ARB-023 | MAO-250~252, DB-203 | lease reclaim 뒤 구 owner가 stale completion 시도 | write 0건, 새 fencing owner/state/output 불변 | 통과 (2026-08-26 Phase 8C/8D fencing regression) |
| T-V2-ARB-024 | AI-270~271, MAO-253, DB-202 | evaluator/canonical Result unexpected failure 주입 | FAILED, output JSON/hash 없음, fabricated UNKNOWN 없음, 오류 로그 관찰 가능 | 통과 (2026-08-26 Phase 8C/8D injected internal failure) |
| T-V2-ARB-025 | AI-270, AI-273, MAO-250, MAO-253, DB-204 | 실제 E2E Arbiter 전후 invocation/control-plane/external side-effect 계수 및 strict Result surface 검사 | route/invocation/Prompt/Model/Provider/network/tool/live/Broker 0건, confidence/score/Agent reason aggregation 없음 | 통과 (2026-08-26 Phase 8D provider-less/exclusion acceptance) |
| T-V2-ARB-026 | AI-267, AI-270, AI-274, MAO-251~252 | Result 저장 뒤 ACTIVE Policy·Route·Prompt와 unrelated Agent 상태 변경 및 reconciliation | frozen Result provenance만 검증하고 terminal Arbiter output JSON/hash 불변 | 통과 (2026-08-26 Phase 8D Policy supersession/immutability) |
| T-V2-ARB-027 | AI-269, AI-275, DB-200~204 | 한 Context, C/B/A 각 1개, Arbiter 1개의 persisted lineage만으로 Finalizer 입력 재구성 | ordered stage IDs/hashes/status/action, Context/policy/validity와 Arbiter hash를 current control-plane 조회 없이 검증 가능 | 통과 (2026-08-26 Phase 8D exact-one Finalizer-ready lineage) |
| T-V2-ARB-028 | AI-273, AI-275, MAO-255 | all-BUY와 mandatory-UNKNOWN Arbiter 성공 뒤 downstream resource/Finalizer trigger 조사 | Decision·Approval·OrderIntent·TradingOrder·Broker·Activation·Execution 0건 | 통과 (2026-08-26 Phase 8D authority/Finalizer boundary) |
| T-V2-ARB-029 | MAO-200, MAO-236, DB-191 | historical 4-route v7 및 Phase 3B~8C·LLM/provider/control-plane·v1~v6 회귀 | 과거 run retroactive Decision/Arbiter stage 0건, 기존 runtime 의미와 전체 backend 회귀 유지 | 통과 (2026-08-26 Phase 8D focused + legacy 98 + full 571 regression) |
| T-V2-MAO-001 | MAO-200~209 | v7 ENTRY DAG 정상 실행 | 기존 Scout 재사용, Decision Agent 3개와 Arbiter 1회 실행 | 계획 (Phase 1) |
| T-V2-MAO-002 | MAO-203~205 | Decision Agent 병렬 실행 | 상호 dependency와 결과 공유 없음 | 계획 (Phase 1) |
| T-V2-MAO-003 | MAO-200 | 과거 v1~v6 run 조회·replay | 기존 의미와 결과 변경 없음 | 계획 (Phase 1) |
| T-V2-MAO-004 | AI-232~235, AI-285, MAO-210~212, MAO-260 | v7 DIAGNOSTIC 정상 완료 | ArbiterResult 존재, run SUCCEEDED/completed_at, TRADING Decision·Approval·Order 0건 | 통과 (2026-08-27 Phase 9D 네 action DIAGNOSTIC closure) |
| T-V2-MAO-005 | AI-234, AI-285, MAO-256 | 과거 DIAGNOSTIC ArbiterResult의 TRADING 승격·복사 또는 purpose mutation 시도 | 거부, 거래 resource 0건 | 통과 (2026-08-27 Phase 9C.2 Gate provenance 없는 purpose mutation fail-closed 및 별도 admission identity) |
| T-V2-MAO-006 | AI-235, AI-279, MAO-260, EXE-215 | activation gate CLOSED와 ExecutionStage MOCK_AUTOMATIC 상태에서 정상 ArbiterResult finalization | run CANCELLED/ACTIVATION_GATE_CLOSED, Decision·Approval·Order 0건 | 통과 (2026-08-27 Phase 9D live CLOSED denial/lifecycle/audit) |
| T-V2-ACT-002 | AI-239~241, CFG-104~106 | 필수 safety evidence 누락·FAILED equivalent·malformed·stale·hash mismatch | Gate invalid, TRADING admission 0건 | 통과 (2026-08-27 Phase 9C.1 strict validator + Phase 9C.2 admission INVALID/0-run boundary) |
| T-V2-ACT-003 | AI-239~241, CFG-104~106 | DAG·policy·schema·prompt/model/route version snapshot 불일치 | Gate invalid, TRADING admission 0건 | 통과 (2026-08-27 Phase 9C.2 actual Policy/Scout·Decision Route/Model/Prompt exact mismatch matrix) |
| T-V2-ACT-004 | AI-241, CFG-107~108 | validation/admission/finalization 중 Gate version 변경 | frozen version 승격 없이 admission 또는 finalization 거부 | 통과 (2026-08-27 Phase 9C.2 admission freeze + Phase 9D live supersession denial) |
| T-V2-ACT-005 | AI-242, AI-284, CFG-111, EXE-214~219 | OPEN Gate와 ExecutionStage SHADOW에서 정상 ArbiterResult finalization | TRADING Decision 정확히 1건, ExecutionStage resource·Approval·Order·Broker 0건 | 통과 (2026-08-27 Phase 9D four-action Finalizer E2E/side-effect zero) |
| T-V2-ACT-006 | CFG-104~106, DB-178~179 | exact activation payload를 key/list 순서만 바꿔 canonicalize | same version snapshot hash와 payload hash; unknown/default/partial null 거부 | 통과 (2026-08-27 Phase 9C.1 canonical key/fixed list/UTC/null/hash validation) |
| T-V2-ACT-007 | CFG-107, MAO-257 | ACTIVE Gate 부재, CLOSED, 둘 이상, malformed를 각각 admission | 모든 case AgentRun·stage 0건 | 부분 통과 (2026-08-27 Phase 9C.2 no-Gate/CLOSED/INVALID와 ambiguity 분류의 0-run 의미; PostgreSQL exact-one 미검증) |
| T-V2-ACT-008 | CFG-107, MAO-257, DB-180 | valid ACTIVE+OPEN Gate admission | TRADING run 1개와 exact Gate ID/hash freeze | 통과 (2026-08-27 Phase 9C.2 internal admission, upstream 7, exact full-payload hash freeze/idempotent reuse) |
| T-V2-ACT-009 | CFG-108, DB-180, DB-211 | admission 뒤 동일 Gate CLOSED 또는 새 Gate ACTIVE | Decision 0건, CLOSED/SUPERSEDED exact audit와 CANCELLED lifecycle | 통과 (2026-08-27 Phase 9D production-style CLOSED/SUPERSEDED finalization denial) |
| T-V2-ACT-010 | CFG-108, DB-208 | transaction start 뒤 write boundary 전 Gate closure/supersession | staged insert rollback, Decision 0건 | 부분 통과 (2026-08-27 Phase 9D injected write-boundary supersession rollback; PostgreSQL TOCTOU/locking 미검증) |
| T-V2-ACT-011 | CFG-105, CFG-111, EXE-218 | Gate target/state와 ExecutionStage/action mode 비교 | 상호 변환·자동 변경 없음 | 통과 (2026-08-27 Phase 9C.2 Gate admission-only, LLM/Arbiter/Execution semantic isolation) |
| T-V2-ACT-012 | DB-210, DB-213 | run 생성 전 Gate CLOSED/invalid 및 transient DB read/lock failure | exact admission AuditLog action/result/metadata, partial run/stage 0건, DB failure retry | 부분 통과 (2026-08-27 Phase 9C.2 exact CLOSED/INVALID/DB_RETRYABLE audit shape와 0 partial row; 실제 DB 장애 복구는 미검증) |
| T-V2-DB-CTX-001 | DB-160~164, AI-244 | 같은 run과 같은 canonical manifest/hash로 Context freeze 반복 | DecisionContext 정확히 1개, 동일 ID/hash 반환 | 통과 (2026-08-25 Phase 3B SQLite) |
| T-V2-DB-CTX-002 | DB-164 | 같은 run에 다른 manifest/hash로 Context freeze 재시도 | conflict로 fail-closed, 기존 Context 변경 없음 | 통과 (2026-08-25 Phase 3B immutable conflict) |
| T-V2-DB-CTX-003 | DB-166, MAO-215 | 다른 run의 Scout stage 또는 EvidenceBundle을 Context에 참조 | freeze transaction rollback, Context 0건 | 통과 (2026-08-25 Phase 3B same-run selection) |
| T-V2-DB-CTX-004 | DB-164, MAO-214 | Context commit 전 Decision Agent stage claim | claim 불가; Decision Agent invocation 0건 | 통과 (2026-08-25 Phase 3B claim prerequisite) |
| T-V2-DB-CTX-005 | DB-163, AI-244 | ENTRY Position Risk stage가 명시적 `NOT_APPLICABLE` result인 경우와 stage 자체가 없는 경우 | 전자는 Context freeze 가능, 후자는 실패; 두 상태가 구분되어 보존 | 통과 (2026-08-25 Phase 3B explicit result) |
| T-V2-DB-CTX-006 | DB-165 | required reference별 valid_until이 서로 다른 Context freeze | 서버가 가장 이른 값을 Context valid_until과 manifest/hash에 저장; 유효시각 계산 불가는 freeze 거부 | 통과 (2026-08-25 Phase 3B earliest validity) |
| T-V2-DB-POL-001 | DB-159, DB-171~172, AI-245 | 세 PolicyProfile ACTIVE version으로 v7 admission | C/B/A의 정확한 ID/category/sequence/agent type/hash map을 canonical freeze | 통과 (2026-08-25 Phase 3C SQLite canonical admission) |
| T-V2-DB-POL-002 | DB-172, AI-245 | run 실행 중 ACTIVE PolicyProfile 교체 | 기존 run map/hash와 stage 입력 provenance 불변 | 통과 (2026-08-25 Phase 3C immutable freeze/historical resolution) |
| T-V2-DB-ROLE-001 | DB-167, MAO-213~217 | 신규 네 role을 v7과 v1~v6 run에 각각 저장 시도 | v7 DAG에서만 허용하고 v1~v6 삽입은 runtime validation 거부 | 부분 통과 (2026-08-25 Phase 3A DB allowlist; DAG validation은 Phase 3B) |
| T-V2-DB-ROLE-002 | DB-168, AI-247, DB-199, DB-204 | ENTRY_ARBITER에 route·prompt·model 또는 invocation 생성 시도 | 거부; Arbiter stage의 route_id/invocation_id NULL과 invocation 0건 | 통과 (2026-08-26 Phase 8C application claim/completion validation and zero invocation) |
| T-V2-DB-FIN-001 | DB-173~176, DB-205~208, AI-277~280 | 정상 v7 TRADING Finalizer persistence | sourced-entry-decision-v1 exact mapping, source run/stage/hash와 Context·C/B/A·Arbiter 역추적 | 통과 (2026-08-27 Phase 9D SQLite production-style Finalizer/API lineage) |
| T-V2-DB-FIN-002 | DB-173, DB-207 | source stage run_id, source run purpose/DAG/context가 불일치 | SOURCE_CONFLICTED, rollback, Decision 0건 | 통과 (2026-08-27 Phase 9D authoritative source application validation) |
| T-V2-DB-FIN-003 | DB-173, DB-207 | source stage role/state/route/invocation이 exact Arbiter contract와 불일치 | SOURCE_CONFLICTED, Decision 0건 | 통과 (2026-08-27 Phase 9D strict source-stage validator/wrong-role acceptance) |
| T-V2-DB-FIN-004 | DB-173, DB-207 | source stored/recomputed hash 또는 Context/C/B/A lineage 불일치 | SOURCE_CONFLICTED, Decision 0건 | 통과 (2026-08-27 Phase 9D hash/lineage revalidation) |
| T-V2-DB-FIN-005 | DB-174~175, DB-209, AI-278 | 동일 finalization identity/material 반복 및 concurrent insert | 동일 Decision 반환, source/evaluation identity별 최대 1개 | 부분 통과 (2026-08-27 Phase 9D SQLite exact retry/unique recovery foundation; PostgreSQL race 미검증) |
| T-V2-DB-FIN-006 | DB-176, DB-209 | commit 결과 불명확 재시도와 같은 identity의 payload mismatch | 조회 후 exact match만 반환; mismatch는 identity conflict, 기존 row 불변 | 통과 (2026-08-27 Phase 9D lookup-first retry/exact comparator) |
| T-V2-DB-FIN-007 | AI-279~281, DB-205 | BUY/WAIT/REJECT/UNKNOWN ArbiterResult 각각 finalization | action/reason/validity exact 보존 Decision 각 1건 | 통과 (2026-08-27 Phase 9D four-action production-style E2E) |
| T-V2-DB-FIN-008 | AI-280, DB-205~206 | sourced Decision physical fields 검사 | confidence/risk/model/prompt/Scout/Core/latency/execution/config null, validation VALID; sentinel 0건 | 통과 (2026-08-27 Phase 9D actual Finalizer insert exact physical mapping) |
| T-V2-DB-FIN-009 | AI-277, DB-208 | Context/Arbiter가 transaction start 후 insert 전 만료 | rollback, SOURCE_EXPIRED audit, Decision 0건 | 통과 (2026-08-27 Phase 9D initial/write-boundary expiry rollback) |
| T-V2-DB-FIN-010 | AI-284, EXE-217~219 | BUY 포함 Finalizer success side-effect count | Decision 1, DecisionExecution·Approval·OrderIntent·TradingOrder·Broker 0 | 통과 (2026-08-27 Phase 9D resource-count assertions) |
| T-V2-DB-FIN-011 | DB-210~211, MAO-260~262 | success와 일곱 failure class 발생 | exact AuditLog action/result/metadata와 run state/error/completed_at mapping | 통과 (2026-08-27 Phase 9D exact ten-field audit/lifecycle matrix) |
| T-V2-DB-FIN-012 | MAO-259, DB-209 | Arbiter commit 뒤 process crash, idle sweep와 opportunistic trigger 반복 | 같은 helper가 Decision exact-one과 terminal run을 복구 | 통과 (2026-08-27 Phase 9D direct/idle reconciliation recovery) |
| T-V2-DB-FIN-013 | AI-286 | v7 Finalizer call surface와 legacy finalizer spy 검사 | `_finalize_run`·mock decision·position advisory·v1 score/threshold 호출 0건 | 통과 (2026-08-27 Phase 9D isolated provider-less service and legacy regression) |
| T-V2-FIN-API-001 | AI-280, AI-282~283 | source-null legacy Decision list/detail 조회 | 기존 DecisionResponse와 Scout/Core parsing·field semantics 불변 | 통과 (2026-08-27 Phase 9C.1 legacy Decision/API full regression) |
| T-V2-FIN-API-002 | AI-280, AI-283, DB-205 | sourced BUY/WAIT/REJECT/UNKNOWN list/detail 조회 | SourcedEntryDecisionResponse 직렬화와 action/null field 정확성 | 통과 (2026-08-27 Phase 9D four-action list/detail actual rows) |
| T-V2-FIN-API-003 | AI-282~283 | schema version과 source lineage가 불일치하거나 부분 lineage인 row 조회 | data integrity failure, legacy parser/fake payload 적용 없음 | 통과 (2026-08-27 Phase 9C.1 discriminator + Phase 9E persisted Context/C/B/A lineage fail-closed) |
| T-V2-FIN-API-004 | AI-283, DB-207 | sourced detail lineage resolve | Context ID/hash, consensus policy/pattern, ordered C/B/A IDs/hashes/status/action 재구성 | 통과 (2026-08-27 Phase 9D actual Finalizer row lineage resolution) |
| T-V2-FIN-LIFE-001 | MAO-260 | DIAGNOSTIC BUY/WAIT/REJECT/UNKNOWN Arbiter completion | 모든 action run SUCCEEDED, completed_at 1회, Decision 0 | 통과 (2026-08-27 Phase 9D four-action closure) |
| T-V2-FIN-LIFE-002 | MAO-260 | DIAGNOSTIC Arbiter CONFLICTED/TIMED_OUT/FAILED | run FAILED와 exact ENTRY_ARBITER_* error code, Decision 0 | 통과 (2026-08-27 Phase 9D terminal-stage mapping) |
| T-V2-FIN-LIFE-003 | MAO-260~261 | TRADING 네 action Finalizer success/reuse | Decision exact-one과 run SUCCEEDED/completed_at 원자 commit | 통과 (2026-08-27 Phase 9D four-action/idempotency transaction) |
| T-V2-FIN-LIFE-004 | MAO-260, DB-211 | CLOSED/superseded/invalid Gate | expected denial은 CANCELLED, invalid config는 FAILED, exact error/audit, Decision 0 | 통과 (2026-08-27 Phase 9D live Gate lifecycle matrix) |
| T-V2-FIN-LIFE-005 | MAO-260, DB-211 | source expiry/conflict/identity conflict | run FAILED와 exact error/audit, Decision 0 | 통과 (2026-08-27 Phase 9D terminal source/identity matrix) |
| T-V2-FIN-LIFE-006 | MAO-262, DB-210~211 | transient DB/lock failure 뒤 recovery | RUNNING/completed_at null/retryable error 유지 후 exact-one success; persistent retry audit | 부분 통과 (2026-08-27 Phase 9D SQLite injected rollback/retry audit; PostgreSQL lock failure/recovery 미검증) |
| T-V2-P9E-001 | PRD-045~047, AI-276~286 | ACTIVE+OPEN Gate에서 full TRADING BUY/WAIT/REJECT/UNKNOWN production-style E2E | exact sourced Decision 1, run SUCCEEDED, action/reason/null payload 보존 | 통과 (2026-08-27 Phase 9E acceptance) |
| T-V2-P9E-002 | CFG-104~111, DB-213 | no Gate/CLOSED/INVALID 및 actual snapshot 영역별 mismatch admission | run/stage/input 0, exact admission audit와 Gate classification | 통과 (2026-08-27 Phase 9C.2/9E 종합 재검증; PostgreSQL ambiguity locking 제외) |
| T-V2-P9E-003 | CFG-108, AI-241, DB-180 | Gate A admission 뒤 mid-pipeline Gate B/CLOSED | semantic input·Arbiter 완료 가능, frozen A 불변, Finalizer에서만 차단 | 통과 (2026-08-27 Phase 9C.2/9D/9E regression) |
| T-V2-P9E-004 | AI-278, DB-175 | finalization identity 반복 및 각 authoritative lineage field 변경 | same source same 64-char ID, 여섯 lineage field sensitivity, action/Gate direct field 제외 | 통과 (2026-08-27 Phase 9E deterministic identity acceptance) |
| T-V2-P9E-005 | DB-173, DB-207 | Arbiter state/role/route/invocation/output/hash/policy/Context/C/B/A corruption 12종 | 모두 SOURCE_CONFLICTED, Decision 0, run FAILED | 통과 (2026-08-27 Phase 9E 12-case source matrix) |
| T-V2-P9E-006 | DB-209, MAO-259~262 | reconciliation 중복 발견, success retry와 ambiguous caller retry | Decision/terminal/success audit exact-one, completed_at 불변 | 통과 (2026-08-27 Phase 9E SQLite sequential duplicate semantics) |
| T-V2-P9E-007 | DB-210~213 | admission DB retry와 Finalizer Gate DB retry taxonomy | admission은 ACTIVATION_GATE_DB_RETRYABLE_FAILURE, Finalizer는 FINALIZATION_DB_RETRYABLE_FAILURE | 통과 (2026-08-27 Phase 9C.2/9E exact boundary acceptance) |
| T-V2-P9E-008 | AI-283, DB-207 | Finalizer-created sourced API에서 persisted C/B/A row hash 변조 | current control-plane 조회 없이 full persisted lineage validation 실패, fake legacy fallback 0 | 통과 (2026-08-27 Phase 9E correctness fix/acceptance) |
| T-V2-P9E-009 | EXE-216, GRD-097 | sourced WAIT/REJECT/UNKNOWN 실제 execution router 전달 | NO_ACTION, GuardEvaluation·Approval·OrderIntent·TradingOrder 0 | 통과 (2026-08-27 Phase 9E three-action routing acceptance) |
| T-V2-P9E-010 | AI-284, EXE-217~220 | Finalizer-created BUY 직후 authority count | DecisionExecution·Approval·OrderIntent·TradingOrder·Broker 0, execution fields null | 통과 (2026-08-27 Phase 9D/9E regression) |
| T-V2-P9E-011 | DB-205~206, DB-212 | migration 0040 head/UNKNOWN/schema length/null representation/downgrade guard/legacy preservation | SQLite upgrade·constraints·guard·legacy row 통과 | 통과 (2026-08-27 Phase 9E migration regression) |
| T-V2-P9E-012 | PRD-045, MAO-260 | historical DIAGNOSTIC와 v1~v6 regression | retroactive Gate/promotion/sourced Decision 0, 기존 의미 불변 | 통과 (2026-08-27 Phase 9E full backend regression) |
| T-V2-P9E-013 | AI-286, MAO-259 | Finalizer 전후 stage/invocation/provider/side-effect count | Finalizer AgentStage/LlmInvocation/Provider/web/tool 증가 0 | 통과 (2026-08-27 Phase 9D/9E boundary regression) |
| T-V2-P9E-014 | DB-174~175, DB-180, DB-208~209 | PostgreSQL migration/locking/concurrent admission·Finalizer·source unique | 공식 local environment 발견 시 targeted production validation | 미실행 (2026-08-27 Docker/DSN/service/secret 없음; OPEN_BACKLOG) |
| T-V2-P9E-015 | PRD-045~047, AI-276~286, CFG-104~111 | Phase 9 closure 종합 판정 | application/runtime contract CLOSED, PostgreSQL production validation 별도 OPEN_BACKLOG | 통과 (2026-08-27 Phase 9E closure) |
| T-V2-DB-GATE-001 | DB-177~180, AI-238~249 | ACTIVE+OPEN이며 schema/hash/version/evidence가 유효한 activation manifest | scheduler-owned v7 TRADING admission 가능, gate ID/hash를 run에 freeze | 통과 (2026-08-27 Phase 9C.2 server-owned admission과 exact Gate ID/full payload hash freeze) |
| T-V2-DB-GATE-002 | DB-178~179, AI-239~240 | required safety evidence 누락·FAILED·expired·target mismatch·hash 오류 | gate validation과 TRADING admission 거부 | 통과 (2026-08-27 Phase 9C.1 validator + Phase 9C.2 selector/admission fail-closed) |
| T-V2-DB-GATE-003 | DB-180, AI-241 | run admission 후 gate superseded 또는 ACTIVE CLOSED version으로 교체 | 기존 run을 새 gate로 승격하지 않고 finalization 거부 | 통과 (2026-08-27 Phase 9D frozen provenance 유지/live denial) |
| T-V2-DB-GATE-004 | DB-180, AI-242, EXE-214~215 | Activation OPEN + ExecutionStage SHADOW에서 valid v7 TRADING result finalization | Finalized Decision 가능, Approval·OrderIntent·Order 0건 | 통과 (2026-08-27 Phase 9D OPEN Gate Decision/authority boundary) |
| T-V2-DB-MIG-001 | DB-157~182, DB-060~063 | PostgreSQL Phase 3 migration upgrade→downgrade→upgrade | 신규 constraint·table·index 왕복 성공, 기존 판단·주문 이력 보존 | 부분 통과 (2026-08-25 SQLite 왕복; PostgreSQL 미검증) |
| T-V2-DB-MIG-002 | DB-166, DB-174, DB-182 | SQLite test schema에 같은 logical FK/unique/role 계약 적용 | PostgreSQL과 동일 인수 의미로 schema·constraint 시험 통과 | 통과 (2026-08-25 Phase 3A SQLite) |
| T-V2-DB-MIG-003 | DB-157~182 | migration 전후 agent-dag-v1~v6 fixture 조회·replay | 기존 purpose·role·Core output 의미와 row 수 변경 없음 | 통과 (2026-08-25 Phase 3A legacy fixture) |
| T-V2-DB-MIG-004 | DB-157~182 | 기존 POSITION TRADING_ADVISORY와 fusion fixture migration 왕복 | basis/fusion lineage와 행동 의미 보존 | 통과 (2026-08-25 Phase 3A SQLite fixture) |
| T-V2-DB-MIG-005 | DB-173~174, DB-182 | legacy deterministic ENTRY Decision migration | source run/stage/hash는 NULL, backfill·재해석 없음 | 통과 (2026-08-25 Phase 3A SQLite fixture) |
| T-V2-DB-MIG-006 | DB-181~182 | v7 Context 또는 sourced Decision이 있는 DB에서 downgrade 시도 | lineage 삭제·SET NULL 없이 downgrade 명시적 거부 | 통과 (2026-08-25 Phase 3A v7 TRADING guard) |
| T-V2-DB-MIG-007 | DB-205~206 | Phase 9C upgrade 후 legacy Decision fixture | 기존 non-null payload/API와 row/hash 불변 | 통과 (2026-08-27 Phase 9C.1 SQLite legacy row/migration/API regression) |
| T-V2-DB-MIG-008 | DB-205~206 | 25-char sourced schema, UNKNOWN action과 exact nullable representation insert | PostgreSQL/SQLite constraint 통과; truncation 없음, invalid mixed representation 거부 | 부분 통과 (2026-08-27 Phase 9C.1 SQLite ORM/CHECK; PostgreSQL 미검증) |
| T-V2-DB-MIG-009 | DB-206, DB-212 | sourced/UNKNOWN/nullable row 존재 상태 downgrade | 명시적 거부, coercion·sentinel·lineage 삭제 없음 | 통과 (2026-08-27 Phase 9C.1 SQLite explicit 0040 refusal) |
| T-V2-DB-MIG-010 | DB-206 | sourced row 없는 upgrade→downgrade→upgrade | 기존 legacy rows와 constraints 보존 | 부분 통과 (2026-08-27 Phase 9C.1 SQLite roundtrip; PostgreSQL 미검증) |
| T-V2-UPSTREAM-001 | MAO-221, DB-186 | v7 diagnostic upstream admission | Intel·Verifier·네 Scout·Candidate Audit만 생성; CORE·C/B/A·Arbiter stage와 실행 0건 | 통과 (2026-08-25 Phase 4C SQLite atomic admission) |
| T-V2-UPSTREAM-002 | AI-251, MAO-225 | v7 네 Scout contract dispatch | legacy v1 path가 아닌 AgentAssessmentV2 schema/validation 사용 | 통과 (2026-08-25 Phase 4C production E2E) |
| T-V2-UPSTREAM-003 | AI-255, MAO-232 | ENTRY이며 열린 position 없음 | Position Risk stage 존재, explicit `NOT_APPLICABLE/UNKNOWN`, score null, `OPEN_POSITION_NOT_FOUND`, Provider 0건 | 통과 (2026-08-25 Phase 4C production E2E) |
| T-V2-UPSTREAM-004 | MAO-228, DB-188 | v7 Verifier 정상 완료 | v2 output에 stage ID/role/status, bundle ID/hash, observed/valid time과 policy provenance 존재·hash 검증 | 통과 (2026-08-25 Phase 4C production E2E) |
| T-V2-UPSTREAM-005 | MAO-231, DB-188 | v7 Candidate Audit 정상 완료 | v2 output에 stage ID/role/status, candidate/group/reason과 provenance 존재·hash 검증 | 통과 (2026-08-25 Phase 4C production E2E) |
| T-V2-UPSTREAM-006 | MAO-224, DB-187 | Candidate Audit output commit 후 reconciliation | 별도 transaction으로 DecisionContext 정확히 1개 freeze | 통과 (2026-08-25 Phase 4C production E2E) |
| T-V2-UPSTREAM-007 | MAO-224, DB-164 | 같은 manifest reconciliation 반복 | 동일 Context 반환, 중복 row 0건 | 통과 (2026-08-25 Phase 4C reconciliation retry) |
| T-V2-UPSTREAM-008 | MAO-224, DB-164 | 같은 run의 다른 manifest/hash로 freeze reconciliation | 기존 Context 불변, conflict fail-closed | 통과 (2026-08-25 Phase 3B conflict + Phase 4C reconciliation regression) |
| T-V2-UPSTREAM-009 | MAO-222~223, DB-187 | Context freeze 뒤 v7 upstream checkpoint | AgentRun `RUNNING`, CORE lookup·terminal finalize 0건 | 통과 (2026-08-25 Phase 4C production E2E) |
| T-V2-UPSTREAM-010 | MAO-223, DB-187 | v1~v6 terminal Core run finalization | 기존 SUCCEEDED/PARTIAL/FAILED 계산과 결과 불변 | 통과 (2026-08-25 Phase 4C legacy finalization regression) |
| T-V2-INPUT-V2-001 | AI-252~254, DB-183~185 | 동일 source/provenance와 policy version으로 input 반복 생성 | 동일 canonical `scout-input-v2` JSON/hash | 통과 (2026-08-25 Phase 4C deterministic input) |
| T-V2-INPUT-V2-002 | AI-253, DB-185 | source 고정 후 ACTIVE PolicyProfile 교체 | scout-input-v2 JSON/hash 불변 | 통과 (2026-08-25 Phase 4C Context/Policy separation) |
| T-V2-INPUT-V2-003 | AI-252, DB-184~185 | MarketContext snapshot/hash/validity provenance 변경 | scout-input-v2와 hash 변경 | 통과 (2026-08-25 Phase 4C context provenance) |
| T-V2-INPUT-V2-004 | AI-254, DB-185 | 필수 market/indicator/config input 만료 또는 validity 불명 | partial run 없이 admission 거부 | 통과 (2026-08-25 Phase 4C expired input rollback) |
| T-V2-SCOUT-HASH-001 | MAO-233~235, DB-189 | 역할에 전달되는 input/dependency/route provenance 변경 | 해당 Scout stage input hash 변경, replay mismatch 거부 | 통과 (2026-08-25 Phase 4C role-input hash validation) |
| T-V2-SCOUT-HASH-002 | MAO-234, DB-189 | 해당 역할에 무관한 다른 Scout output 또는 PolicyProfile 변경 | 해당 Scout input hash 불변 | 통과 (2026-08-25 Phase 4C role isolation) |
| T-V2-EVIDENCE-V7-001 | MAO-228~230 | source별 verified evidence와 고정 freshness policy snapshot | item validity와 Verifier valid_until이 결정론적이며 run/item 중 최솟값 | 통과 (2026-08-25 Phase 4C KRX production E2E) |
| T-V2-EVIDENCE-V7-002 | MAO-229~230 | stale evidence, timestamp/source rule/config provenance 누락 또는 usable item 0개 | 임의 유효기간 연장 없이 freeze-eligible success 거부, Context 0건 | 통과 (2026-08-25 Phase 4C stale evidence fail-closed) |
| T-V2-TECH-001 | AI-251, MAO-225 | v7 production Technical Scout 실행 | server-owned provenance가 결합된 `AgentAssessmentV2`, 정확한 stage/role/schema 저장 | 통과 (2026-08-25 Phase 5 external Provider E2E) |
| T-V2-TECH-002 | MAO-233~235, DB-189 | Indicator provenance 변경 | Technical Scout `scout-role-input-v1` hash 변경 | 통과 (2026-08-25 Phase 5 canonical material/hash) |
| T-V2-TECH-003 | AI-253, MAO-234 | PolicyProfile 변경, Technical source material 고정 | Technical Scout role input/hash 불변 | 통과 (2026-08-25 Phase 5 role isolation) |
| T-V2-TECH-004 | AI-251, MAO-225 | Provider가 미허용·다른 run·URL evidence ref 반환 | output validation 실패, Technical stage fail-closed | 통과 (2026-08-25 Phase 5 evidence subset validation) |
| T-V2-TECH-005 | AI-251 | Provider가 Technical allowlist 밖 reason code 반환 | output validation 실패, normalized assessment 생성 금지 | 통과 (2026-08-25 Phase 5 reason allowlist) |
| T-V2-TECH-006 | AI-251~254 | 필수 Indicator 누락·stale·충돌 | admission 또는 stage가 fail-closed하고 fabricated score 없음 | 통과 (2026-08-25 Phase 5 frozen Indicator conflict) |
| T-V2-TECH-007 | AI-251, MAO-225 | Provider timeout/error/schema-invalid | Technical failure가 trading Decision·Approval·Order를 만들지 않음 | 통과 (2026-08-25 Phase 5 timeout/provider/schema failure) |
| T-V2-TECH-008 | AI-251, MAO-224 | production Technical result를 포함한 upstream 완료 | Candidate Audit 뒤 DecisionContext freeze 성공 | 통과 (2026-08-25 Phase 5 production E2E) |
| T-V2-TECH-009 | AI-251, MAO-225 | v7 Technical route·request 검사 | web search/tool/external acquisition 비활성, server-provided input만 전달 | 통과 (2026-08-25 Phase 5 route/request inspection) |
| T-V2-TECH-010 | AI-091, MAO-091 | agent-dag-v6 Technical regression | 기존 AgentAssessmentV2, Core input과 finalization 의미 불변 | 통과 (2026-08-25 Phase 5 existing Agent Runtime regression) |
| T-V2-NEWS-001 | AI-251, MAO-225 | v7 News production path 실행 | canonical role input을 사용한 `AgentAssessmentV2` 저장 | 통과 (2026-08-25 Phase 6 external Provider E2E) |
| T-V2-NEWS-002 | MAO-075 | News route의 web search 비활성 | Provider request의 web search/tool 사용 0건 | 통과 (2026-08-25 Phase 6 request inspection) |
| T-V2-NEWS-003 | MAO-079, DB-138 | 허용된 News search가 새 source candidate 반환 | candidate는 `UNRATED`로 저장되고 현재 EvidenceBundle은 불변 | 통과 (2026-08-25 Phase 6 candidate E2E) |
| T-V2-NEWS-004 | MAO-079 | Provider가 UNRATED candidate를 `evidence_refs`로 반환 | output 거부, candidate는 verified evidence로 승격되지 않음 | 통과 (2026-08-25 Phase 6 evidence subset) |
| T-V2-NEWS-005 | AI-251, MAO-225 | News Provider timeout/error/schema/reason/evidence 실패 | fabricated score와 trading resource 없이 fail-closed | 통과 (2026-08-25 Phase 6 parametrized failures) |
| T-V2-MARKET-001 | AI-114, AI-251 | valid frozen MarketContext로 v7 Market production path 실행 | provenance가 결합된 `AgentAssessmentV2` 저장 | 통과 (2026-08-25 Phase 6 external Provider E2E) |
| T-V2-MARKET-002 | MAO-233~235, DB-189 | frozen MarketContext provenance 변경 또는 실행 시 불일치 | role hash 변경 또는 Provider 전 실행 거부 | 통과 (2026-08-25 Phase 6 provenance revalidation) |
| T-V2-MARKET-003 | AI-114, MAO-137 | MarketContext 만료·conflicted quality | server-owned null-score safe result로 fail-closed | 통과 (2026-08-25 Phase 6 hash/quality/expiry cases) |
| T-V2-MARKET-004 | MAO-075, MAO-079 | 허용된 Market search가 새 source candidate 반환 | candidate는 `UNRATED`, 현재 bundle은 불변 | 통과 (2026-08-25 Phase 6 candidate E2E) |
| T-V2-MARKET-005 | AI-253, MAO-234 | PolicyProfile 변경, Market source material 고정 | Market role input/hash 불변 | 통과 (2026-08-25 Phase 6 hash isolation) |
| T-V2-POSRISK-001 | AI-255, MAO-232 | v7 ENTRY에 열린 position 없음 | explicit `NOT_APPLICABLE/UNKNOWN`, score null, Provider 0건 | 통과 (2026-08-25 Phase 6 role acceptance) |
| T-V2-POSRISK-002 | AI-244, MAO-215 | Position Risk stage가 없는 상태에서 Context freeze | `NOT_APPLICABLE`로 간주하지 않고 freeze 실패 | 통과 (2026-08-25 Phase 6 missing-stage rejection) |
| T-V2-POSRISK-003 | MAO-233~235, DB-189 | frozen position provenance 변경 | Position role hash 변경 또는 실행 fail-closed | 통과 (2026-08-25 Phase 6 hash isolation) |
| T-V2-POSRISK-004 | MAO-075, AI-255 | Position Risk route/request 검사 | web search·external acquisition·추가 broker lookup 0건 | 통과 (2026-08-25 Phase 6 route and zero-invocation) |
| T-V2-POSRISK-005 | AI-104~105 | Provider 적용 가능한 기존 Position Risk의 provider/schema 실패 | null-score fail-closed, 기존 v6 의미 유지 | 통과 (2026-08-25 Phase 6 v6 regression) |
| T-V2-SCOUT-ROLE-001 | MAO-233~235, DB-189 | News evidence, MarketContext, Position provenance를 각각 변경 | 관련 역할 hash만 변경되고 무관 역할 hash는 불변 | 통과 (2026-08-25 Phase 6 canonical material isolation) |
| T-V2-SCOUT-ROLE-002 | AI-253, MAO-234 | PolicyProfile 변경 | 네 Scout role input/hash 모두 불변 | 통과 (2026-08-25 Phase 6 policy isolation) |
| T-V2-SCOUT-ROLE-003 | MAO-234 | 다른 Scout output 변경 | 해당 Scout input/hash 불변 | 통과 (2026-08-25 Phase 6 output isolation) |
| T-V2-SCOUT-ROLE-004 | AI-244, MAO-224 | production 네 Scout output 뒤 Candidate Audit·reconciliation | fixture 직접 삽입 없이 DecisionContext freeze 성공 | 통과 (2026-08-25 Phase 6 production E2E) |
| T-V2-SCOUT-ROLE-005 | AI-244, DB-163 | 한 Scout production failure | 다른 Scout output 불변, trading resource 0건, Context fail-closed | 통과 (2026-08-25 Phase 6 failure isolation) |
| T-V2-DA-IN-001 | AI-256~257 | 동일 Context·참조 row로 C/B/A Provider input resolve와 canonicalize 반복 | 세 role의 resolved Context material이 같고 각 반복의 `decision-agent-input-v1` hash가 동일 | 통과 (2026-08-25 Phase 7C SQLite builder/hash) |
| T-V2-DA-IN-002 | AI-256, AI-259 | 같은 Context에서 자기 PolicyProfile 또는 다른 role PolicyProfile 적용 | 자기 frozen profile만 결합되고 cross-role profile은 Provider 전 거부 | 통과 (2026-08-25 Phase 7C own-policy registry) |
| T-V2-DA-IN-003 | AI-257, AI-262 | evidence/Scout 저장 순서, timezone·Decimal 표현만 변경 | 정의된 정렬·정규화 후 canonical input과 allowlist가 동일 | 통과 (2026-08-25 Phase 7C strict canonical contracts) |
| T-V2-DA-POL-001 | AI-258~259 | 여섯 policy field의 missing/unknown/type/range 오류와 ACTIVE 교체 후 historical resolve | 잘못된 profile은 거부하고 frozen ID/hash가 맞는 SUPERSEDED profile은 재현 | 통과 (2026-08-25 Phase 7C semantic/frozen resolver) |
| T-V2-DA-ROUTE-001 | MAO-236, DB-191 | 신규 Phase 7 admission과 기존 Phase 4~6 run replay | 신규 run은 정확히 일곱 route를 freeze하고 historical 네-route run은 변경 없이 유효 | 통과 (2026-08-25 Phase 7C seven/four-route compatibility) |
| T-V2-DA-MAT-001 | MAO-237~240, DB-194 | Context freeze 뒤 decision-stage reconciliation | C/B/A 세 stage만 원자 생성되고 Arbiter/Core stage는 0건 | 통과 (2026-08-25 Phase 7C SQLite atomic materialization) |
| T-V2-DA-MAT-002 | MAO-238, DB-194 | 동일 reconciliation 반복, exact partial retry, 기존 hash mismatch | 중복 0건, partial은 완성, mismatch는 전체 rollback·기존 row 불변 | 통과 (2026-08-25 Phase 7C SQLite retry/conflict) |
| T-V2-DA-HASH-001 | DB-192 | Context/자기 Policy/Route/Prompt provenance를 각각 변경하고 무관 role policy/result 변경 | 관련 stage input hash만 변경되고 무관 role material에는 불변 | 통과 (2026-08-25 Phase 7C canonical stage material) |
| T-V2-DA-DISPATCH-001 | MAO-239~242 | ready C/B/A stage 동시 claim·dispatch | 세 role이 Scout/Core가 아닌 Decision handler로 독립·병렬 실행 | 통과 (2026-08-25 Phase 7D explicit worker dispatch/parallel-ready dependency) |
| T-V2-DA-RESULT-001 | AI-260~264, DB-193 | valid Provider success output | exact server provenance를 가진 canonical result/hash와 일치하는 terminal state 저장 | 통과 (2026-08-25 Phase 7D C/B/A production dispatch) |
| T-V2-DA-RESULT-002 | AI-261 | status/action/confidence/score matrix의 모든 허용·금지 조합 | 허용 조합만 저장되고 금지 조합은 structured `INVALID_OUTPUT/UNKNOWN` | 통과 (2026-08-25 Phase 7C matrix + Phase 7D failure normalization) |
| T-V2-DA-EVID-001 | AI-262 | verified bundle 안/밖 ID, positive-negative 중복, URL·Scout ID·UNRATED ref | 정확한 frozen VERIFIED ID 부분집합만 허용하고 나머지는 structured INVALID_OUTPUT | 통과 (2026-08-25 Phase 7D frozen allowlist/overlap rejection) |
| T-V2-DA-REASON-001 | AI-262 | 공통 allowlist와 Scout/order/unknown reason 반환 | allowlist만 허용하고 미허용 reason은 structured INVALID_OUTPUT | 통과 (2026-08-25 Phase 7D strict model/server reason separation) |
| T-V2-DA-FAIL-001 | AI-263, MAO-243 | timeout, Provider/limit/credential/final fail-stop | 각각 TIMED_OUT 또는 FAILED의 UNKNOWN result/hash가 남고 BUY fallback 0건 | 통과 (2026-08-25 Phase 7D timeout/provider fail-stop) |
| T-V2-DA-FAIL-002 | AI-260~263 | malformed JSON, schema, reason, evidence 오류 | INVALID_OUTPUT structured result/hash와 exact request/actual/fallback provenance 저장 | 통과 (2026-08-25 Phase 7D strict raw-output normalization) |
| T-V2-DA-FAIL-003 | AI-263~264 | Context·Policy·Route·Prompt·stage input provenance tamper | CONFLICTED structured result/hash, Provider 또는 SUCCEEDED commit 0건 | 통과 (2026-08-25 Phase 7D pre-call and completion revalidation) |
| T-V2-DA-EXP-001 | AI-264, DB-195 | Provider 응답 전 Context 만료 또는 completion-time hash/fencing 변경 | SUCCEEDED 저장 0건; expiry는 TIMED_OUT, mismatch/stale worker는 계약대로 fail-closed | 통과 (2026-08-25 Phase 7D expiry/fencing race) |
| T-V2-DA-TX-001 | MAO-242, DB-196 | 느린 Provider 호출 중 concurrent stage/run 조회·lease recovery | network call 동안 row lock 없음, stale fencing write 0건, 권위 terminal result 최대 1개 | SQLite 통과 (2026-08-25 Phase 7D transaction-state assertion/stale write; PostgreSQL concurrency 대기) |
| T-V2-DA-TOOLS-001 | AI-265, MAO-243 | 세 role request/tool surface 및 결과 resource 검사 | web/live/Broker/filesystem/Approval/Order/Arbiter tool과 거래 resource 모두 0건 | 통과 (2026-08-25 Phase 7D tool `NONE`, allowed tools 빈 목록, 거래/Arbiter resource 0건) |
| T-V2-DA-E2E-001 | AI-256~265, MAO-236~245 | production upstream Context부터 세 Decision Agent DIAGNOSTIC 완료 | C/B/A 결과 3개와 lineage/hash 보존, Arbiter·Finalizer·Decision·Approval·Order 0건 | 통과 (2026-08-25 Phase 7D production worker success/failure E2E) |
| T-V2-DA-ARB-READY-001 | MAO-244, AI-246~247 | 후속 Arbiter가 세 result stage ID/hash를 입력으로 조회 | C/B/A 결과를 재작성 없이 exact lineage로 사용 가능, 현재 Phase에서는 Arbiter 실행 0건 | 통과 (2026-08-25 Phase 7E exact three-stage identity/context/provenance/hash) |
| T-V2-DA-ACC-001 | AI-256~260, MAO-239~244, DB-192 | production C/B/A E2E와 A→C→B 역순 실행 | 동일 canonical Context, 자기 Policy/Route/Prompt만 사용하고 dependency·input hash·semantic result가 실행 순서와 무관 | 통과 (2026-08-25 Phase 7E shared-context/isolation/order acceptance) |
| T-V2-DA-ACC-002 | AI-260~263, DB-193 | 네 success 조합, role별 다섯 non-success 상태와 세 mixed-result 조합 | 정확한 action/status matrix와 canonical result/hash 3개를 보존하고 consensus side effect 0건 | 통과 (2026-08-25 Phase 7E production success/failure/mixed matrices) |
| T-V2-DA-ACC-003 | AI-262, MAO-243 | VERIFIED와 UNRATED/stale/different-run/URL/title/stage/hash/candidate/nonexistent evidence, model/server/Scout reason | frozen VERIFIED namespace와 model allowlist만 허용하며 위반은 INVALID_OUTPUT | 통과 (2026-08-25 Phase 7E evidence/reason matrix) |
| T-V2-DA-ACC-004 | AI-259, AI-264, DB-195~196 | 실행 중 Context expiry, Policy corruption/supersession, Route/Prompt supersession, fencing·lease recovery | 정상 frozen supersession은 허용하고 corruption/expiry/stale completion은 structured fail-closed | 통과 (2026-08-25 Phase 7E completion/recovery race acceptance) |
| T-V2-DA-ACC-005 | AI-260, AI-263, MAO-245 | primary Provider failure 뒤 configured fallback 성공 | requested primary profile, actual fallback provider/model, fallback flag/path와 hash 차이를 보존 | 통과 (2026-08-25 Phase 7E production fallback provenance) |
| T-V2-DA-ACC-006 | AI-265, MAO-240~244 | C/B/A all BUY와 terminal 후 reconciliation/config·다른 Agent 변화 | 외부 도구·거래·Arbiter resource 0건, exact stage 3개와 terminal output 불변 | 통과 (2026-08-25 Phase 7E authority/immutability/layer closure) |
| T-V2-DA-LEGACY-001 | MAO-236, DB-191 | v1~v6와 Phase 4~6 stored fixture replay | 기존 role/route/finalization 의미와 row/hash 불변 | 통과 (2026-08-25 Phase 7C v1~v6 및 historical four-route regression) |
| T-V2-EXE-001 | EXE-200~203 | `APPROVAL_ONLY` + BUY policy `AUTOMATIC` | 직접 Order 생성 0건, fail-closed | 계획·현행 결함 회귀 |
| T-V2-EXE-002 | EXE-204~205 | PENDING Approval 생성 후 `SHADOW` 전환 뒤 approve | Approval `INVALIDATED`, Order 0건 | 계획·현행 결함 회귀 |
| T-V2-EXE-003 | EXE-206~208 | CREATED Order 생성 후 `SHADOW` downgrade 뒤 worker dispatch | Broker `place_order` 호출 0회 | 계획·현행 결함 회귀 |
| T-V2-EXE-004 | EXE-201 | `SHADOW`에서 BUY consensus 생성 | 판단 기록만 존재, Approval·OrderIntent·Order 0건 | 계획 (Phase 1) |
| T-V2-EXE-005 | EXE-203 | `MOCK_AUTOMATIC` + BUY consensus + Guard PASS | 자동 MOCK Order 정확히 1건 | 통과 (2026-08-29 Phase 10E) |
| T-V2-EXE-006 | EXE-209~210 | privileged diagnostic order 실행과 운영 readiness 조회 | 진단 경로·권한·표시가 production execution 및 자동매매 준비 상태와 분리 | 계획 (Phase 1) |
| T-V2-EXE-007 | PRD-014, CFG-084~085, EXE-211~213 | `APPROVAL_ONLY`에서 FIXED_STOP trigger와 `AUTOMATIC`·`MANUAL_APPROVAL` mode 평가 | 자동 Order 0건; user-bound Approval authority가 없으면 synthetic Approval 없이 EXIT_PENDING 위험·경보 유지 | 통과 (2026-08-29 Phase 10E P0 closure) |
| T-V2-EXE-008 | EXE-213 | `MOCK_AUTOMATIC`에서 FIXED_STOP trigger, 정상 market·position·gate, Guard PASS와 managed sell quantity 양수 | Approval 없이 MOCK SELL Order 정확히 1건; 동일 trigger 재처리 중복 0건, Broker Worker 경로로만 송신 | 통과 (2026-08-29 Phase 10E CREATED boundary) |
| T-V2-EXE-009 | EXE-200~203, EXE-211 | ENTRY BUY, POSITION PARTIAL_SELL/FULL_SELL, FIXED_STOP 원인을 ExecutionStage별 table-driven 평가 | SHADOW는 Approval·Order 0건; APPROVAL_ONLY는 허용된 승인만 생성하고 직접 자동 Order 0건; MOCK_AUTOMATIC은 action policy와 Guard가 허용할 때만 자동 MOCK Order | 부분 통과 (2026-08-29 Phase 10E sourced BUY/fixed-stop; 일반 POSITION actions는 기존 범위) |
| T-V2-EXE-010 | EXE-216, GRD-097 | legacy/sourced WAIT·REJECT·UNKNOWN Decision routing | 모두 NO_ACTION, BUY-like Guard·Approval·Order·Broker 0건 | 통과 (2026-08-27 Phase 9E actual sourced routing, Guard/Approval/Order 0) |
| T-V2-EXE-011 | EXE-217~220 | sourced BUY Finalizer 완료 및 후속 execution persistence | Decision의 execution_mode/outcome은 불변 null, Finalizer 시 DecisionExecution·Approval·Order·Broker 0건, 후속 상태는 DecisionExecution에만 기록 | 부분 통과 (2026-08-27 Phase 9D Finalizer half complete; 후속 Execution integration은 범위 외) |
| T-V2-EXE-012 | EXE-218~219 | Gate OPEN/CLOSED와 세 ExecutionStage/mode 조합 | Gate와 execution 권한 독립, 둘 중 필요한 조건 하나라도 실패하면 권한 확대 없음 | 통과 (2026-08-27 Phase 9C.2 Gate semantic isolation + Phase 9D/9E null execution authority regression) |
| T-V2-EXE-AUTH-001 | PRD-048, EXE-222~225, DB-214 | sourced WAIT/REJECT/UNKNOWN 반복·동시 handoff와 policy/stage 변경 | Decision당 `v7exe-` DecisionExecution 정확히 1개, NO_ACTION/원 action code, Guard·Approval·Order 0 | 계획 (Phase 10C.2) |
| T-V2-EXE-AUTH-002 | EXE-221, EXE-226, GRD-098 | source run/Context/C/B/A/Arbiter/hash/Decision representation corruption과 임의 TRADING BUY | `SOURCE_AUTHORITY_INVALID`, external authority 0, Decision/AgentRun 불변 | 계획 (Phase 10C.2) |
| T-V2-EXE-AUTH-003 | CFG-112~117, DB-215~216 | stage payload unknown/missing/hash/expiry/evidence/ACTIVE ambiguity와 세 valid stage | strict invalid fail-closed, exact-one current selection, frozen ID/hash 보존 | 계획 (Phase 10C.1) |
| T-V2-EXE-AUTH-004 | PRD-049, EXE-229~232, CFG-115~118 | frozen/current stage·mode의 상승/하락 조합과 Approval/CREATED 경쟁 | 자동 promotion 0, minimum authority, Approval/Order unsent invalidation, Broker 0 | 계획 (Phase 10D/10F) |
| T-V2-EXE-AUTH-005 | EXE-233~240 | 세 stage×세 action mode table 및 APPROVAL_ONLY+AUTOMATIC | exact matrix; forbidden 조합은 FAILED_SAFE/AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY, side effect 0 | 통과 (2026-08-29 Phase 10D/10E) |
| T-V2-EXE-AUTH-006 | EXE-247~250, API-127~128, SEC-066~068, DB-225~227 | Approval 생성/approve/reject owner, expected_version, proof 재사용·expiry와 transaction failure | cross-user/충돌/proof 오류 fail-closed, valid path exactly one Order, partial commit 0 | 계획 (Phase 10D) |
| T-V2-EXE-AUTH-007 | EXE-241~246, GRD-098~106 | BUY source/validity/session/snapshot/buying-power/한도/active·UNKNOWN order/Broker input 단독·복합 실패 | phase별 immutable Guard, 하나라도 block이면 authority 0, Decision BUY 불변 | 계획 (Phase 10D) |
| T-V2-EXE-AUTH-008 | EXE-253~256, DB-219~225, ORD-052 | source type별 OrderIntent provenance와 automatic/manual linkage corruption·duplicate | exact typed chain, approval null/required invariant, authority key당 initial intent/order 최대 1 | 계획 (Phase 10C.1/10E) |
| T-V2-EXE-AUTH-009 | EXE-251~252, GRD-104~105, DB-217~218 | FIXED_STOP을 SHADOW/APPROVAL_ONLY/MOCK_AUTOMATIC에서 평가하고 Guard FK 검사 | SHADOW Order 0, APPROVAL_ONLY EXIT_PENDING, MOCK만 auto; PostgreSQL subject FK valid | 통과(SQLite, 2026-08-29 Phase 10E); PostgreSQL FK/concurrency NOT_RUN |
| T-V2-EXE-AUTH-010 | EXE-257~258, EXE-274~281, DB-228, ORD-053, STM-037~042 | source별 broker pre-send provenance/stage/mode/Guard/Approval corruption·downgrade | send 0, CREATED→INVALIDATED+authority event; valid authority만 SUBMITTING | 통과 (SQLite, 2026-08-29 Phase 10F); PostgreSQL NOT_RUN |
| T-V2-EXE-AUTH-011 | EXE-225, EXE-241, STM-039 | Decision 만료가 handoff 전, Guard/Approval/Order 중, pre-send 전, SUBMITTING 후 발생 | submit 전 safe disposition/send 0; SUBMITTING 후 기존 reconciliation lifecycle 유지 | 계획 (Phase 10C.2/10D/10F) |
| T-V2-EXE-AUTH-012 | PRD-049, EXE-244, GRD-104 | PAUSE_ENTRY가 initial/approval/order/pre-send 각 boundary에서 활성화 | BUY authority/send 0와 exact code; risk-reduction SELL/FIXED_STOP은 entry stop으로 차단하지 않음 | 계획 (Phase 10D/10F) |
| T-V2-EXE-AUTH-013 | PRD-050, EXE-238, EXE-255, EXE-278, CFG-120 | MOCK_AUTOMATIC, Broker diagnostic, LIVE env/account/adapter 변조 | validated MOCK만 send; diagnostic은 privileged 1주 분리; LIVE automatic 불가능 | 통과 (SQLite, 2026-08-29 Phase 10F); LIVE 미개방 |
| T-V2-EXE-AUTH-014 | EXE-259~260, DB-224, DB-229, API-129, ORD-054 | Finalizer commit 뒤 crash, duplicate sweep, public execute endpoint 부재, unclassified CREATED, SUBMITTING/UNKNOWN recovery | server-owned missing execution 복구, no duplicate·client override, unclassified unsent invalidation, no blind resend | 계획 (Phase 10C.2/10F) |
| T-V2-EXE-AUTH-015 | DB-214~230 | PostgreSQL concurrent lifecycle/Approval/Order 생성, stage lock, Guard subject FK와 ambiguous send | exact-one/CAS/FK/fencing/transaction invariants 모두 실제 PostgreSQL 통과 | 계획 (Phase 10G; required stage evidence) |
| T-V2-EXE-AUTH-016 | EXE-224, EXE-254~255, DB-214, DB-224 | legacy DecisionExecution/Order, Broker diagnostic/import와 migration 전 row replay | legacy 의미 불변, source 추측·backfill 없음, unsafe CREATED만 runtime invalidation | 계획 (Phase 10G) |

### Phase 10G.1 PostgreSQL production acceptance 검증 계획 (2026-08-29)

로컬 `127.0.0.1`의 test-only PostgreSQL 17 `cresta_acceptance` database 안에 실행별
격리 schema를 만들고, 각 schema의 `search_path`를 고정해 운영 DB와 public schema를
사용하지 않는다. 실제 비밀번호와 전체 DSN은 출력·문서화하지 않는다. fresh migration과
`0040→0041→0042` incremental migration, PostgreSQL catalog의 FK/CHECK/index/predicate/
nullability/type/ON DELETE를 먼저 확인한 뒤 application-level concurrency와 E2E를 실행한다.

| ID | 관련 요구사항 | PostgreSQL 검증 | 기대 결과 |
| --- | --- | --- | --- |
| T-V2-PG-10G1-001 | DB-205~230, DB-238 | fresh→0042, 0040→0041→0042와 catalog inspection | head 0042, 실제 FK/CHECK/partial unique/index/type/nullability/RESTRICT 일치 |
| T-V2-PG-10G1-002 | DB-205~216, EXE-221~230 | Gate/Finalizer/sourced execution/ExecutionStage 동시 처리 | 각 canonical identity·ACTIVE stage 정확히 1개, loser deterministic recovery, ambiguity fail closed |
| T-V2-PG-10G1-003 | DB-217~225, EXE-247~256 | typed Guard FK, Approval CAS/reauth, authority-key/fixed-stop concurrent create와 rollback | 잘못된 FK 거부, 정확히 한 winner와 initial authority, outer rollback 시 부분 row 0 |
| T-V2-PG-10G1-004 | DB-228~230, EXE-274~281, KIW-150~154 | SKIP LOCKED, lease fencing, CREATED→INVALIDATED/SUBMITTING, stage·PAUSE_ENTRY·expiry race | submit authority 최대 1, semantic revoke와 submit 모순 없음, DB 실패는 CREATED 유지 |
| T-V2-PG-10G1-005 | KIW-125~134, STM-037~042 | BROKER_SEND Guard atomicity, ambiguous send와 reconciliation/worker retry | Guard+transition 원자성, UNKNOWN 영속, blind resend·CREATED 복귀 0 |
| T-V2-PG-10G1-006 | EXE-216, EXE-233~258 | PostgreSQL-backed WAIT/REJECT/UNKNOWN/SHADOW/manual/automatic/fixed-stop MOCK E2E | 지정 terminal 결과와 exact Order 수량, MOCK adapter만 호출, LIVE 0 |

### Phase 10G.1 PostgreSQL production acceptance 실행 결과 (2026-08-29)

| ID | 결과 | 근거 |
| --- | --- | --- |
| T-V2-PG-10G1-001 | FAIL | PostgreSQL 17.11에서 fresh→0042와 0040→0041→0042 및 FK/CHECK/partial unique/index/BIGINT/nullability/ON DELETE는 PASS. 단, exact `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` 35자를 `order_events.event_type varchar(32)`가 수용하지 못해 catalog capacity FAIL |
| T-V2-PG-10G1-002 | 부분 PASS | Finalizer same Arbiter concurrent exact-one, sourced WAIT concurrent exact-one, ACTIVE stage partial unique, 실제 SKIP LOCKED와 lease fencing PASS. Gate/Stage service activation race는 blocker 뒤 NOT_RUN |
| T-V2-PG-10G1-003 | 부분 PASS | manual Approval의 owner/version/proof, outer rollback, automatic/fixed-stop exact authority와 fixed-stop rollback PASS. 별도 Approval/reauth/authority-key/fixed-stop concurrent winner matrix는 NOT_RUN |
| T-V2-PG-10G1-004 | 부분 PASS | 실제 SKIP LOCKED와 stale lease fencing PASS. revocation event insert truncation으로 semantic `CREATED→INVALIDATED` atomic transition FAIL; 나머지 CREATED/stage/PAUSE_ENTRY/expiry 동시 race NOT_RUN |
| T-V2-PG-10G1-005 | FAIL | revocation OrderEvent가 PostgreSQL에서 rollback돼 BROKER_SEND Guard/INVALIDATED atomicity를 닫지 못함. ambiguous-send와 reconciliation concurrency는 NOT_RUN |
| T-V2-PG-10G1-006 | 부분 PASS | WAIT/REJECT/UNKNOWN/SHADOW, manual Approval→CREATED, automatic BUY→CREATED, fixed-stop exact-one과 MOCK adapter ACK PASS. manual/automatic pre-send 종합과 unclassified revocation은 schema blocker로 FAIL/INCOMPLETE |

실행 근거: `backend/tests/test_phase_10g1_postgresql.py`의 blocker 비종속 17건 PASS,
event capacity 및 automatic/manual/unclassified pre-send 종속 4건 FAIL. 기존 SQLite
backend suite는 PostgreSQL marker 21건을 명시적으로 제외해 741/741 PASS했다. 전체 Ruff와
`git diff --check`는 PASS다. 새 migration, production scheduler/handoff, LIVE network/order,
production DB는 모두 없으며 Phase 10G.1은 `INCOMPLETE`다.

### Phase 10G.1A PostgreSQL schema capacity correction 실행 결과 (2026-08-29)

| ID | 관련 요구사항 | 시험 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-V2-PG-10G1A-001 | DB-223, DB-245, DB-249~250 | fresh→0043, 0042→0043, actual catalog와 upgrade 재적용 | head 0043, `order_events.event_type varchar(64)`, 기존 shorter event 보존 | PASS (PostgreSQL 17.11) |
| T-V2-PG-10G1A-002 | EXE-232/275, ORD-053, STM-038, DB-249 | exact 35-char revocation event insert와 CREATED authority revoke | event/Order INVALIDATED/source lifecycle/audit 원자 commit, Intent 불변, Broker 0 | PASS (PAUSE_ENTRY automatic/manual) |
| T-V2-PG-10G1A-003 | DB-250 | 32자 이하 row가 있는 0043 downgrade와 33~64자 row가 있는 downgrade | safe row는 0042로 축소·재upgrade, 긴 row는 명시적 거부·data/schema 보존 | PASS (PostgreSQL 17.11 + SQLite) |

이번 correction은 event 이름이나 authority semantics를 변경하지 않고 schema/ORM capacity만
교정했다. 실제 PostgreSQL Phase-focused 10건과 SQLite backend 743건, 전체 Ruff 및
`git diff --check`가 PASS했다. 별도 기존 `audit_logs.result varchar(24)`는 24자를 초과하는
exact authority-revocation result를 거부하므로 전체 Phase 10G.1 rerun 전 후속 schema
capacity correction이 필요하다. 이 발견은 0043의 event capacity acceptance와 분리한다.
Phase 10G.1 전체 concurrency acceptance 재실행과 scheduler/handoff는 후속 범위다.

### Phase 10G.1B PostgreSQL audit result capacity correction 실행 결과 (2026-08-29)

| ID | 관련 요구사항 | 시험 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-V2-PG-10G1B-001 | DB-251~252 | 93개 AuditLog.result inventory와 length 검증, fresh→0044, 0043→0044, actual catalog | 모든 literal ≤64, head 0044, `audit_logs.result varchar(64)` | PASS (PostgreSQL 17.11) |
| T-V2-PG-10G1B-002 | EXE-257/275~276, ORD-053, STM-038, DB-251 | automatic/manual authority revoke와 exact audit result | event/audit/Order/source lifecycle 원자 commit, Intent 불변, Broker 0 | PASS (PostgreSQL 17.11) |
| T-V2-PG-10G1B-003 | EXE-276, DB-246/252 | revocation transaction failure injection, safe/refusing downgrade와 re-upgrade | partial row/state 0, 긴 row는 downgrade 거부와 schema/data 보존 | PASS (PostgreSQL 17.11 + SQLite) |

이번 correction은 AuditLog result literal이나 authority semantics를 바꾸지 않고
`audit_logs.result` schema/ORM capacity만 확대한다. Phase 10G.1 전체 concurrency matrix는
후속 rerun에서 수행한다. 실제 PostgreSQL Phase-focused 10건, SQLite backend 746건,
전체 Ruff와 `git diff --check`가 PASS했다.

### Phase 10G.1 PostgreSQL production acceptance full rerun 계획 (2026-08-30)

현재 head `20260829_0044`와 local test-only PostgreSQL 17.11을 사용한다. 실행별 격리
schema/search_path를 유지하고 production DB·LIVE·scheduler/handoff activation은 사용하지
않는다. 기존 T-V2-PG-10G1-001~006을 다음 세부 matrix로 완결하며 하나라도 NOT_RUN이면
Phase 10G.1을 완료로 처리하지 않는다.

| ID | 관련 요구사항 | PostgreSQL full-rerun 검증 | 상태 |
| --- | --- | --- | --- |
| T-V2-PG-10G1R-001 | DB-205~252 | fresh/incremental 0040→0044, actual FK/CHECK/index/predicate/type/nullability/ON DELETE와 0043/0044 capacity | PASS — PostgreSQL 17.11, head 0044와 actual catalog 일치 |
| T-V2-PG-10G1R-002 | DB-205~216, EXE-221~230 | Finalizer, Activation Gate, sourced execution, ExecutionStage exact-one/concurrent activation/TOCTOU/ambiguity | PASS — concurrent winner exact-one, rollback/TOCTOU와 ambiguous fail-closed |
| T-V2-PG-10G1R-003 | DB-217~225, EXE-247~256 | typed Guard invalid FK matrix, financial selector/freshness, Approval create/CAS/approve-vs-reject, reauth one-time, authority-key concurrent create와 rollback | PASS — 10G.1C 이후 canonical CAS loser, raw `StaleDataError` 0과 전체 matrix 확인 |
| T-V2-PG-10G1R-004 | EXE-251~281, ORD-052~060 | fixed-stop concurrent processing, position/reservation authority, SKIP LOCKED, lease fencing과 stale ownership | PASS — exact-one, 수량 한계, typed Guard, distinct claim와 fencing 확인 |
| T-V2-PG-10G1R-005 | DB-228~252, KIW-125~154, STM-037~042 | CREATED→INVALIDATED/SUBMITTING, stage/PAUSE_ENTRY/expiry race, BROKER_SEND atomicity와 DB retryable rollback | PASS — serialization 결과와 broker 최대 1회, rollback/CREATED retry 보존 |
| T-V2-PG-10G1R-006 | KIW-125~154, REC-001~ | ambiguous send, UNKNOWN reconciliation-vs-retry, source dispatch invalid matrix와 no-blind-resend | PASS — UNKNOWN 유지, resend 0, invalid source 전부 fail-closed |
| T-V2-PG-10G1R-007 | EXE-216/233~258 | PostgreSQL-backed WAIT/REJECT/UNKNOWN/SHADOW/manual/automatic/fixed-stop E2E through MOCK adapter | PASS — A~G 모두 MOCK adapter 경계까지 완료, LIVE 0 |
| T-V2-PG-10G1R-008 | 전체 회귀 | PostgreSQL groups 분리 실행, SQLite full suite, Ruff, `git diff --check`, cleanup | PASS — PostgreSQL 69/69, SQLite 748/748, Ruff/diff/cleanup PASS |

최종 실행 결과: 10G.1C correction 이후 `backend/tests/test_phase_10g1_postgresql.py` 69건
전체에서 69 PASS, FAIL 0, NOT_RUN 0이다. 최초 full rerun의 67 PASS/2 FAIL은 바로 아래
10G.1C 기록으로 원인·교정 근거를 보존한다. PostgreSQL과 분리한 SQLite suite는 전용
writable `--basetemp`를 사용해 748/748 PASS했고 전체 Ruff와 `git diff --check`, acceptance
schema cleanup도 PASS했다. Phase 10G.1은 `COMPLETE`이며 Phase 10G.2 readiness는 `YES`다.

### Phase 10G.1C Approval optimistic CAS error normalization 계획 (2026-08-30)

기존 canonical stale 계약 `ApprovalError("APPROVAL_VERSION_CONFLICT", 409)`를 그대로
재사용한다. sourced Approval mutation에서 Approval 객체만 명시적으로 먼저 flush하고 그
좁은 boundary의 `StaleDataError`만 전체 transaction rollback 후 canonical conflict로
변환한다. 이후 commit에서 발생하는 OperationalError·IntegrityError·다른 versioned entity의
`StaleDataError`는 변환하지 않는다. migration/ORM/state/authority/proof semantics는 변경하지 않는다.

| ID | 관련 요구사항 | 검증 | 상태 |
| --- | --- | --- | --- |
| T-V2-PG-10G1C-001 | EXE-248, API-117/127, DB-225~227 | sequential expected_version stale와 service/API error mapping | PASS — `ApprovalError / APPROVAL_VERSION_CONFLICT / 409`, API retryable=false 유지 |
| T-V2-PG-10G1C-002 | EXE-248~250, DB-225~227 | PostgreSQL approve↔approve exactly-one winner, canonical loser와 side effect inventory | PASS — winner 1, conflict loser 1, version 2, Intent/Order/proof/Guard/audit 1 |
| T-V2-PG-10G1C-003 | EXE-248~250, DB-225~227 | PostgreSQL approve↔reject exactly-one mutation과 loser rollback | PASS — final state별 authority 0/1, loser partial side effect 0 |
| T-V2-PG-10G1C-004 | DB-226~227 | Approval flush CAS 오류만 normalize하고 unrelated commit/runtime DB 오류는 원형 전파 | PASS — rollback-before-mapping; commit OperationalError는 원형 전파 |
| T-V2-PG-10G1C-005 | 전체 회귀 | Approval/reauth focused, SQLite full, Ruff와 `git diff --check` | PASS — focused 22, PostgreSQL 관련 7, SQLite 748, Ruff/diff PASS |

Phase 10G.1C는 `COMPLETE`다. migration과 ORM schema는 변경하지 않았으며 Phase 10G.1
전체 69-case final rerun은 후속 단계에서 수행한다. scheduler/handoff/LIVE/production DB는
사용하지 않았고 기존 dirty worktree를 보존했다.

### Phase 10C.1 persistence / stage control-plane 실행 결과 (2026-08-28)

| ID | 요구사항 | 시험 | 결과 |
| --- | --- | --- | --- |
| T-V2-DB-EXE-001 | DB-214~216, EXE-222~225 | sourced discriminator, canonical key, same Decision 중복과 NO_ACTION/DECISION_EXPIRED null representation | PASS — partial unique로 sourced Decision당 최대 1개, valid null representation 허용, partial stage provenance 거부 |
| T-V2-DB-EXE-002 | DB-214~215 | policy/stage/risk 변경과 무관한 `entry-execution-identity-v1` 및 key capacity | PASS — exact two-field canonical input, deterministic `v7exe-` 70자, Decision 변경에만 key 변경 |
| T-V2-CFG-STAGE-001 | CFG-112~114 | SHADOW/APPROVAL_ONLY/MOCK_AUTOMATIC exact payload/evidence와 unknown/LIVE/stale/hash 오류 | PASS — exact schema/required acceptance set만 허용하고 malformed·stale·evidence hash mismatch fail closed |
| T-V2-CFG-STAGE-002 | CFG-112~117 | DRAFT→VALIDATED→ACTIVE, current selector PASS/ABSENT/AMBIGUOUS/DB failure와 production seed 부재 | PASS — exact current provenance만 선택하고 default stage를 합성하지 않음 |
| T-V2-CFG-STAGE-003 | CFG-115~118 | stage/action frozen-current minimum의 promotion/downgrade 조합 | PASS — SHADOW<APPROVAL_ONLY<MOCK_AUTOMATIC, DISABLED<MANUAL_APPROVAL<AUTOMATIC minimum 유지 |
| T-V2-CFG-STAGE-004 | CFG-112~117, API-122 | 인증·CSRF stage draft/validate/activate/current/history API와 activation 후 execution count | PASS — exact ConfigurationVersion lifecycle/response, production seed·DecisionExecution handoff 0건 |
| T-V2-DB-EXE-003 | DB-217~218, GRD-104~105 | FK-on SQLite의 DecisionExecution/StopTrigger typed Guard subject와 wrong execution FK | PASS — StopTrigger FK만 허용, mixed/neither/wrong execution FK 거부 |
| T-V2-DB-EXE-004 | DB-219~224, ORD-052~054 | legacy null provenance, exact source enum/typed refs, authority-key unique와 INVALIDATED representation | PASS — legacy unchanged, mixed/unknown source와 duplicate authority key 거부, unsent terminal state 저장 가능 |
| T-V2-DB-EXE-005 | DB-225 | Approval reauth proof/order 참조와 existing execution unique/version foundation DDL | PASS — 실제 RESTRICT FK와 기존 optimistic version/unique execution 유지 |
| T-V2-DB-EXE-006 | DB-214~230 | 0040→0041 migration, legacy row 보존, historical fixed-stop correction, empty round-trip와 destructive downgrade guard | PASS(SQLite) — head `20260828_0041`; 신규 semantics 존재 시 downgrade 거부 |

Local evidence (2026-08-28): `backend/tests/test_phase_10c1_foundation.py` focused 14건, backend 전체 655건과 Ruff가 통과했다. Alembic head는 `20260828_0041`; empty upgrade→downgrade→upgrade, 0040 legacy OrderIntent 보존, deterministic StopTrigger Guard FK 교정과 destructive downgrade guard를 확인했다. Stage API lifecycle 뒤 DecisionExecution은 0건이다. SQLite FK-on regression은 통과했으나 실제 PostgreSQL DDL, partial unique/FK, exact-one concurrent insert와 lock behavior는 미실행 `OPEN_BACKLOG`이다. `alembic check`는 0041 신규 drift 없이 기존 agent-run/emergency-stop/indicator/market-context metadata drift 때문에 non-zero이며 별도 schema-alignment backlog다. Phase 10B의 `T-V2-EXE-AUTH-001~016`은 해당 후속 runtime phase가 끝날 때까지 계획 상태를 유지한다.

### Phase 10C.2 sourced execution orchestrator 실행 결과 (2026-08-28)

| ID | 요구사항 | 시험 | 결과 |
| --- | --- | --- | --- |
| T-V2-EXE-AUTH-001 | PRD-048, EXE-222~224, DB-214, DB-229 | 실제 Phase 9 WAIT/REJECT/UNKNOWN, config 0개, direct retry와 reconciliation retry | PASS — policy/stage lookup 없이 nullable NO_ACTION, canonical key, execution/audit 정확히 1개와 Guard/Approval/Order 0 |
| T-V2-EXE-AUTH-002 | EXE-221, EXE-226, GRD-098 | finalized BUY source hash tamper와 full historical validator reuse | PASS — `FAILED_SAFE / SOURCE_AUTHORITY_INVALID`, Guard·downstream authority 0, Decision mutation 0 |
| T-V2-EXE-AUTH-003 | CFG-112~117, EXE-228~230 | SHADOW current stage PASS provenance와 absent/DB retry 분류 | PASS — exact ID/hash freeze; absent는 terminal unavailable, DB retry는 partial lifecycle 0 |
| T-V2-EXE-AUTH-004 | PRD-049, EXE-229~231, CFG-115~119 | sourced SHADOW에서 frozen/current stage·action minimum helper 적용 | PASS(10C.2 SHADOW 범위) — permissive current 값으로 승격하지 않으며 restrictive DISABLED는 Guard 전 권한 회수 |
| T-V2-EXE-AUTH-005 | EXE-234, EXE-236 | SHADOW×DISABLED/MANUAL_APPROVAL/AUTOMATIC과 Guard PASS/BLOCK | PASS(10C.2 SHADOW 범위) — DISABLED, SHADOW_RECORDED, GUARD_BLOCKED exact state; Approval/Order/Broker 0 |
| T-V2-EXE-AUTH-007 | EXE-241~246, GRD-098~106 | 기존 BUY PRE_ORDER rules 호출, typed subject, boundary expiry | PASS(기존 Guard completeness 범위) — actual execution FK/stop FK null, expiry race는 stale Guard 없이 FAILED_SAFE; Phase 10D completeness 대기 |
| T-V2-EXE-AUTH-011 | EXE-225, EXE-241 | handoff 전 expiry와 SHADOW commit-boundary expiry | PASS — stage lookup/Guard 0 또는 uncommitted Guard 0, `FAILED_SAFE / DECISION_EXPIRED` |
| T-V2-EXE-AUTH-014 | EXE-224, EXE-259, DB-226, DB-229, API-129 | manual deterministic reconciliation, duplicate scan, nullable Decision API projection | PASS(10C.2 범위) — missing execution 복구와 second scan 0, public endpoint/automatic activation 없음 |

Local evidence (2026-08-28): `backend/tests/test_phase_10c2_sourced_execution.py` focused 13건, Phase 10C.1/9E/9D/9C/8D 및 execution/Guard/fixed-stop/Approval/Broker/configuration/legacy runtime을 포함한 회귀군 185건, backend 전체 668건, Ruff와 `git diff --check`가 통과했다. 실제 Phase 9-style finalized lineage를 모든 action에 사용했고 Decision API의 nullable mode/stage projection을 확인했다. Approval, OrderIntent, TradingOrder 생성은 모두 0이며 Broker path는 호출하지 않는다. 실제 PostgreSQL partial unique/concurrent loser recovery/typed FK/row-lock·TOCTOU는 미실행 `OPEN_BACKLOG`; Phase 10D에서 full BUY Guard, Approval authority와 frozen/current Risk Policy intersection을 계속 검증한다.

### Phase 10D 착수 blocker 기록 (2026-08-28)

| 대상 | 확인 결과 | 상태 |
| --- | --- | --- |
| GRD-099, GRD-102, EXE-242 current buying power | ORM/0041에 account cash·buying-power·freshness projection이 없고 `BrokerAccountSnapshot`/reconciliation도 open orders·fills·positions만 제공 | BLOCKED — migration/Broker 변경 금지 조건과 충돌하므로 Phase 10D INCOMPLETE |
| API-127~128, SEC-066~068 Approval boundary | current action request에 expected_version/reauth proof가 없고 service owner/CAS/proof consumption이 미구현 | 조사 완료, persistence blocker 해소 후 구현 필요 |
| DB-226~227 transaction ownership | `create_approval()` 내부 commit 및 `create_order()` unique-race 내부 rollback이 outer authority transaction contract와 불일치 | 조사 완료, persistence blocker 해소 후 구현 필요 |

Phase 10D focused test와 runtime 변경은 시작하지 않았다. Risk Policy entry amount를 buying power로 가장하거나 broker network call로 우회하지 않았고, 기존 Phase 10C.2 668-test evidence가 최신 실행 근거로 유지된다.

### 3.12.3 LLM Provider 및 Gateway

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-LLM-001 | LLM-001~005, LLM-080~083 | 공식·Gateway·Ollama·사용자 endpoint profile 검증 | 허용 Adapter 선택, 비허용 scheme·credential URL 거부, credential 비노출 | 통과 (2026-08-05 fixture, 2026-08-11 OpenAI·Gateway 실서버) |
| T-LLM-002 | LLM-010~014, LLM-080~084, CFG-090~095 | 발견 모델·미검증 capability·route 이중 활성화 | 자동 활성화 없음, Mock fixture보다 넓은 capability와 SHADOW 외 route 거부 | 부분 통과 (2026-08-05, 자동 fixture) |
| T-LLM-003 | LLM-020~024, LLM-083 | Mock Adapter canonical fixture와 외부 Adapter 선택 | deterministic 내부 result와 SHADOW 외부 호출을 분리하고 거래·승인·주문에는 연결하지 않음 | 통과 (2026-08-05 Mock, 2026-08-11 외부 SHADOW) |
| T-LLM-004 | LLM-030~033 | timeout·cancellation·허용되지 않은 header/body override | 호출 격리, global 설정 불변, Authorization/host override 거부 | 계획 |
| T-LLM-005 | LLM-040~044 | 429·5xx·timeout·응답유실·Gateway 내부 fallback | 유효시간 내 제한 재시도, Core fail-closed, 실제 route 불명확 시 활성화 금지 | 계획 |
| T-LLM-006 | LLM-050~054 | usage 누락·가격 미확정·호출/비용 한도·Ollama 과부하 | `UNKNOWN` 비용, 한도 차단, Core 사용 전 benchmark gate | 계획 |
| T-LLM-007 | LLM-060~065, LLM-080~085, SEC-080~085 | profile API·감사·DOM의 credential과 비허용 endpoint 검사 | Foundation credential ref·raw secret field 거부, 감사 원문 미기록, 비허용 loopback 차단, 외부 전송 0건 | 부분 통과 (2026-08-05, Foundation 범위) |
| T-LLM-008 | LLM-070~074, LLM-080~085, API-130~137, UI-110~117 | Web UI Mock profile·model·SHADOW route 초안·검증 흐름 | credential 입력 없음, validation 분리, activation·agent run endpoint 없음 | 부분 통과 (2026-08-05, API·component) |
| T-LLM-009 | DB-115~123, LLM-080 | Foundation migration·profile/model/route 참조와 주문 경계 | `0013` head·FK·SHADOW 제약, invocation·approval·order 0건 | 부분 통과 (2026-08-05, Foundation 범위) |
| T-LLM-010 | MAO-080~083, LLM-013·042·053 | 동일 fixture로 cloud 모델·Gateway·Ollama SHADOW 비교 | schema 통과율·환각·p95 지연·비용 보고, 운영 판단 영향 0건 | 계획 |
| T-LLM-011 | LLM-015~019·075~077, CFG-096~100, DB-128~131, API-138~142, UI-120~126 | 여러 Provider·Model 등록 후 동일 모델을 복수 역할에 배정하고 역할 모델·파라미터 변경, 중복 VALIDATED 이력 조회·일괄 활성화 | 역할별 현재 배정 정확히 1개, model 재사용, 파라미터 상속·capability 거부, TOTP 1회 원자 전환, 기존 배정 이력 보존, 누적형 기본 UI 제거 | 통과 (2026-08-06, API·component·migration fixture) |

### 3.13 데이터베이스 및 영속성

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-DB-001 | DB-001~006 | 시각·가격·식별자 schema 검사 | UTC·고정소수점·내외부 ID 분리 | 계획 |
| T-DB-002 | DB-010~016 | 동시 중복주문·부분체결·취소 경쟁 | unique·수량 불변·원자적 포지션 유지 | 부분 통과 |
| T-DB-003 | DB-020~025 | 설정 이중 활성화·승인 재사용·세션 원문 검사 | 제약 위반 거부, 원문 미저장 | 계획 |
| T-DB-004 | DB-030~034 | 감사·이벤트 수정·삭제 시도 | append-only 역할에서 거부 | 계획 |
| T-DB-005 | DB-040~042 | 활성 주문 대표 쿼리 실행계획 | 전체 시계열 scan 없이 인덱스 사용 | 계획 |
| T-DB-006 | DB-050~053 | Redis 전체 삭제 후 worker 재시작 | DB·Broker로 복구, 작업 중복 없음 | 계획 |
| T-DB-007 | DB-060~063 | schema 불일치·migration 실패·seed 재실행 | worker 시작 차단, 중복 seed 없음 | 계획 |
| T-DB-008 | DB-064, SEC-033 | `/` 등 예약문자가 포함된 DB 비밀번호로 Alembic 실행 | URL은 정상 해석되고 오류·로그에 비밀번호 또는 완성된 인증 URL 미노출 | 단위 통과·PostgreSQL 재검증 대기 |
| T-DB-009 | DB-070~073 | 현재 MOCK runtime data 유실 후 fresh database 재구축 | 전체 migration head 적용과 runtime 재시작 가능; 기존 row 보존 요구 없음 | 계획 (Phase 11A.2 정책 정정) |

### 3.14 HTTP·WebSocket API

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-API-001 | API-001~006 | 알 수 없는 필드·부동소수점 금액·위조 Guard 전송 | 검증 거부 또는 서버 재계산 | 계획 |
| T-API-002 | API-010~014 | 같은 키 동시 요청·payload 변경·응답유실 | 하나의 결과, 충돌 감지, 안전 조회 | 계획 |
| T-API-003 | API-020~022 | 포지션 version 변경 중 부분매도 요청 | 최신 수량 재검사와 stale 요청 거부 | 계획 |
| T-API-004 | API-030~032 | 승인 만료·재사용·다른 재인증 증명 | 승인·주문 생성 거부 | 계획 |
| T-API-005 | API-040~042 | 오래된 preview로 위험 설정 활성화 | version 충돌, 기존 설정 유지 | 계획 |
| T-API-006 | API-050~052 | 로그인 단계 열거·CSRF 없는 변경 요청 | 일반 오류와 변경 거부 | 계획 |
| T-API-007 | API-060~062 | Guard 차단·내부 예외 응답 검사 | 표준 코드, 비밀·stack 미노출 | 계획 |
| T-API-008 | API-070~074 | stream 중복·gap·replay 범위 초과 | 중복 제거와 REST snapshot 복구 | 계획 |

### 3.15 배포·운영·장애복구

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-OPS-001 | OPS-001~005 | root container·비밀 포함 이미지·내부 포트 노출·원격 7788 접속 | gateway는 `127.0.0.1:7788`만 수신하고 외부는 도메인 HTTPS만 허용 | 계획 |
| T-OPS-002 | OPS-010~013 | 의존 서비스 지연·종료 중 UNKNOWN 주문 | READY 차단, 상태 영속·재동기화 | 계획 |
| T-OPS-003 | OPS-020~023 | schema 비호환 이미지 배포 | 거래 중지 상태 유지와 안전 롤백 | 계획 |
| T-OPS-004 | OPS-030~034 | 디스크 85%·UNKNOWN·시각 오차 발생 | 경보·구조화 로그·상세 비공개 health | 계획 |
| T-OPS-005 | OPS-040~044 | 현재 MOCK fresh database recovery와 optional snapshot 분류 | migration head·MOCK/secret/Broker 재동기화 확인 전 주문 금지; backup 부재는 blocker 아님 | 계획 (Phase 11A.2 정책 정정) |
| T-OPS-006 | OPS-050~053 | DB·Redis·키움·시세 장애 주입 | 장애별 게이트와 복구 순서 준수 | 계획 |
| T-OPS-007 | OPS-060~063 | Web 또는 Broker secret 유출 가정 | 세션·token 폐기, 증거 보존·사고 기록 | 계획 |
| T-OPS-008 | OPS-006~007 | N100 자원 제한과 디스크 임계값 검사 | 예약 메모리 유지, 20% 경고·10% 차단 | 계획 |
| T-OPS-009 | OPS-002 | host secret이 `0600` 사용자 소유인 상태와 준비 스크립트 실행 후 API 읽기 검사 | 준비 전 API 접근 실패, 실행 후 `10001:10001`·`0400`이며 migration에서 읽기 성공 | 실서버 부분 통과 |
| T-OPS-010 | OPS-008 | 제한된 host 권한에서 API 이미지 빌드 후 UID와 source import 검사 | 컨테이너는 `10001:10001`이며 `/app/app/broker/kiwoom.py`를 읽고 import 가능 | 통과 (2026-08-03, 실서버 수동) |
| T-KIW-025 | KIW-111~112, DB-027~029 | 같은 계좌에서 worker 두 개가 동시에 lease 획득 | 하나만 획득하고 만료·fencing 전에는 승계 불가 | 단위 통과 |
| T-KIW-026 | KIW-113~115 | LOGIN·REG·재동기화 단계별 성공/실패 | 모두 성공한 현재 lease owner만 READY, 실패는 fail-closed | 통과 (단위·2026-08-04 실서버) |
| T-KIW-027 | KIW-114, KIW-116 | PING과 `00`·`04` 이벤트 수신 | PING echo, 계좌 이벤트를 REST 대조 trigger로 분류 | 단위 통과·실서버 대기 |
| T-KIW-028 | KIW-117~119 | token 교체·단절·lease 상실·종료 | 재로그인·backoff·READY 해제·소유 lease만 해제 | 재시작·fencing 실서버 통과, 장애주입 대기 |
| T-KIW-029 | KIW-120, API-087~089 | CLI·HTTP Broker 상태 조회 | 연결 상태 제공, owner/token/계좌/원문 오류 미노출 | 단위 통과 |
| T-KIW-030 | KIW-121~123 | 매수·매도·정정·취소 공식 fixture와 잘못된 시장·종목·수량·가격 | 정확한 TR/body, KRX·7자리 주문번호 검증, 부적합 요청 송신 전 차단 | 통과 (2026-08-04, 자동) |
| T-KIW-031 | KIW-124~125, STM-004·023, ORD-035 | 주문 성공·업무거절·401·timeout·5xx·비 JSON 응답 | 성공만 ACK 후보, 업무거절만 REJECTED 후보, 불명확 오류는 UNKNOWN 후보, HTTP 재송신 없음 | 통과 (2026-08-04, 자동 fixture) |
| T-KIW-032 | KIW-126 | 같은 TR의 연속 주문과 서로 다른 TR 호출 | TR별 최소 1초 간격과 주입 가능한 clock 기반 결정론적 검증 | 통과 (2026-08-04, 자동 clock) |
| T-KIW-033 | KIW-127~128, STM-005, ORD-034·036 | 같은 내부 주문 재호출·SUBMITTING 중 crash 가정 | 최초 호출 전 상태 commit, 후속 호출 송신 0회, 공개 주문 명령 없음 | 통과 (2026-08-04, 자동) |
| T-KIW-034 | KIW-129~131, STM-026, ORD-037 | READY worker polling에 여러 계좌·상태 주문 배치 | 대상 계좌 CREATED 중 가장 오래된 한 건만 선택·송신, 다른 주문 미변경 | 통과 (2026-08-04, 자동) |
| T-KIW-035 | KIW-132~133, STM-025 | CREATED·SUBMITTING·UNKNOWN 주문별 worker 시작 대조 | CREATED는 Broker 불일치 아님, SUBMITTING·UNKNOWN은 fail-closed, 자동 재송신 0회 | 통과 (2026-08-04, 자동) |
| T-KIW-036 | KIW-134, ORD-038 | polling 송신 결과 UNKNOWN | 다음 주문 미처리, 즉시 ORDER_OUTCOME_UNKNOWN 전체 재동기화, 식별 불가 시 HALTED | 통과 (2026-08-04, 자동) |
| T-KIW-037 | KIW-135 | ACKNOWLEDGED·REJECTED 주문이 polling 대상에 함께 존재 | 기존 결과 주문 재송신 0회, CREATED만 대상 | 통과 (2026-08-04, 자동) |
| T-KIW-038 | KIW-136, REC-080~082 | `00`·`04` 이벤트 수신 후 debounce 중 CREATED 주문 존재 | 즉시 RECONCILING, 대조 전 송신 0회, BROKER_EVENT 대조 성공 후에만 polling 재개 | 통과 (2026-08-04, 자동) |
| T-KIW-039 | KIW-137, SEC-065, API-094~098 | Web MOCK 진단 주문 요청·proof 재사용·worker 비준비 | READY에서만 BUY 1주 CREATED·감사, proof 재사용 403, 비준비 409 | 통과 (2026-08-04, 자동) |
| T-OPS-011 | OPS-003 | API 컨테이너 IP 변경 후 gateway를 재시작하지 않고 health·login 요청 | Docker DNS 재해석 후 새 API로 연결되고 502가 지속되지 않음 | 설정 계약 통과·실서버 대기 |
| T-OPS-012 | OPS-014 | 배포 Compose의 장기 실행 서비스 재시작·health 설정 검사 | API·Frontend 포함 전 서비스 `unless-stopped`, PostgreSQL·Redis·API·Frontend·gateway healthcheck 존재 | 통과 (2026-08-05, 자동 계약) |
| T-OPS-013 | OPS-015~016 | `cresta-boot.service` 정적 계약과 Ubuntu 부팅 시험 | Docker·network-online 이후 두 Compose 파일을 `up -d --wait --wait-timeout 180`으로 조정하고 실패 재시도; 부팅 후 core 5종 healthy·worker Up·내부 health 200 | 통과 (2026-08-05, 자동 계약·Ubuntu 재부팅 9초 복구) |
| T-OPS-015 | OPS-070~075 | Provider secret 미설정·외부 API 장애·비용 한도·Ollama 과부하 | core·Broker·Guard 유지, AI route만 차단, secret·Ollama 포트 미노출 | 계획 |

## 4. 시험 환경

시험은 다음 층으로 구분한다.

1. 단위 테스트: 상태 전이, 가격 계산, 설정 충돌 검사
2. 계약 테스트: 저장된 키움 요청·응답 샘플과 Adapter 매핑
3. 통합 테스트: 키움 모의투자 KRX 주문·체결·취소·정정
4. 장애 주입: 응답 지연, WebSocket 단절, 중복·역순 이벤트와 재시작
5. 수동 인수 시험: Console 승인, 경고와 감사 로그 확인

실제 자격증명은 테스트 데이터나 결과 문서에 기록하지 않는다.

## 5. 검증·인수 조건

- 모든 확정 요구사항 ID에 최소 하나의 테스트가 연결된다.
- 주문·체결 시험은 수량 불변조건을 자동 검사한다.
- 실패한 시험을 통과로 표시하지 않고 재현 정보와 영향 범위를 기록한다.
- 키움 모의투자에서 검증할 수 없는 NXT/SOR는 `미검증`으로 유지한다.
- 구현 완료 상태는 대응 시험 통과 후에만 `검증 완료`로 변경한다.

## 6. 미결정·보류 항목

- Python 테스트는 `pytest` 계열과 주입 가능한 Clock interface를 사용한다. 실제 의존 패키지 버전은 프로젝트 lock file로 고정한다.
- 키움 모의투자에서 부분체결을 안정적으로 재현하지 못하면 결정론적 paper broker simulator를 필수 회귀시험으로 사용하고 실제 키움 결과는 별도 통합시험으로 표시한다.
- 시험 결과는 개발환경 `artifacts/test-results`, 서버 `/home/totquf4171/cresta/artifacts/test-results`에 비밀 제거 후 90일 보관한다.

## 7. 실행 결과

2026-08-01 Backend 인증·Paper 조회, 첫 Watch 영속 기반과 키움 MOCK REST 기반 구현 결과:

| 대상 | 실행 | 결과 | 범위·제약 |
| --- | --- | --- | --- |
| Python 단위·API 시험 | `python -m pytest` | 164개 통과 | 기존 범위, 역할 배정, Provider 모델 발견·원자 등록, 역할별 Prompt Profile, 비동기 Agent claim·lease·fencing·응답 불명 격리·scheduler admission과 주문 0건 포함 |
| Console component 시험 | `npm test` | 11개 통과 | Provider 내부 모델 관리, 역할 배정/이력 분리, ACTIVE route 비동기 DIAGNOSTIC 등록·상태 갱신 포함 |
| Console 타입 검사 | `npm run typecheck` | 통과 | TypeScript strict mode |
| Console production build | `npm run build` | 통과 | Next.js standalone 정적 route 생성 |
| Console HTTP smoke | standalone server에 HTTP 요청 | 통과 | `/` 응답 200과 Cresta metadata 확인 |
| Console production dependency audit | `npm audit --omit=dev --audit-level=high` | 취약점 0건 | Next 하위 PostCSS·Sharp를 검증된 패치 버전으로 고정 |
| 정적 검사 | `python -m ruff check app tests migrations` | 통과 | FastAPI dependency의 B008은 프레임워크 관용구로 제외 |
| 문법 검사 | `python -m compileall -q app tests migrations` | 통과 | Python 3.14 로컬, 배포 기준은 3.12 |
| migration 적용 | `alembic upgrade head`·`current` | 통과 | 빈 SQLite에서 모델 기본값·역할 override를 포함한 `20260806_0015` upgrade→downgrade→upgrade 검증; 실서버 PostgreSQL 적용은 배포 시 확인 필요 |
| gateway 정적 검사 | Compose YAML·환경·Nginx 설정 assertion | 통과 | Backend·Frontend route 분리, `127.0.0.1:7788` 단독 게시 |
| Docker Compose·HTTPS | Ubuntu 서버에서 전체 서비스 기동, migration, host Nginx·TLS 접속과 로그인 | 통과 | PostgreSQL·Redis healthy, secret 읽기, API·Frontend·gateway, HTTPS와 ID·비밀번호·TOTP 로그인 확인 |
| Paper Console 브라우저 점검 | 데스크톱·390px 모바일에서 상태·주문 상세·포지션 화면 확인 | 통과 | 실제 API 계약과 동일한 로컬 조회 fixture 사용, 브라우저 console error 없음, 운영 생성 컨트롤 없음 |
| Watch 상태 UI 변경 점검 | component·TypeScript·production build | 통과 | 인앱 브라우저의 로컬 URL 정책 차단으로 이번 변경의 추가 시각 점검은 미실행 |

검증된 세부 동작은 인증·Paper·Watch 외에 키움 MOCK 인증·snapshot, WebSocket worker 안전 게이트, 계좌 이벤트 수신 즉시 polling 차단과 debounce된 REST 대조, 주문 TR·limiter·FIFO polling·ACK/REJECTED/UNKNOWN과 즉시 재동기화를 포함한다. 실제 서버의 MOCK 인증·시세·계좌 일치는 2026-08-03, 빈 계좌 대조와 worker READY·재시작 fencing은 2026-08-04 통과했다. 2026-08-05 Ubuntu 재부팅에서 systemd Compose 조정 후 약 9초 안에 core 서비스 health와 worker READY가 복구됐다. API 단독 재생성 중 gateway 무중단 재해석, 장중 분봉·지표, Guard 가격정책·실제 전략 모의주문·PostgreSQL 다중 worker 경쟁은 미검증이다.

### 5.1 실행 권한 설정 추가 시험

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-CFG-008 | CFG-070~074, API-043~045, DB-090~091 | 안전 기본값·초안·검증·TOTP 활성화 | 활성 버전 불변성, 활성화 전 미적용, 감사 기록 | 통과 (2026-08-04, 자동) |
| T-UI-012 | UI-036~038 | 8개 행동 모드 편집·검증·TOTP 활성화 | 안전 기본값 출처와 활성화 후 서버 재조회 | 통과 (2026-08-04, component) |
| T-AI-009 | AI-070~074, API-099~101, DB-092~093 | 동일 snapshot 진단·지연 시세·실행 권한 3개 모드 | 결정론적 출력, 중복 억제, 주문 0건, 안전 분기 기록 | 통과 (2026-08-04, 자동) |
| T-UI-013 | UI-039, UI-044~045 | Mock 진단 요청과 판단 목록 표시 | 모델·snapshot·행동·실행 차단 결과를 오인 없이 표시 | 통과 (2026-08-04, component) |
| T-WATCH-009 | MKT-080~081, API-102~104, DB-094~096 | 감시 종목 등록·중복·3개 제한·해제 | 사용자별 유일성·최대 3개·CSRF를 지키고 기존 snapshot은 보존 | 통과 (2026-08-04, 자동) |
| T-WATCH-010 | MKT-082~089·112, KIW-138~143 | 시작·목록 변경 구독과 공식 `0B`·`0D` fixture | 그룹 분리, KRX·NXT item 전체 동기화, suffix 정규화, 시장별 cache·snapshot 격리 | 통과 (2026-08-12, 자동 fixture); 실제 장중 `_NX` payload 대기 |
| T-UI-014 | UI-046~048 | 빈 목록·등록·시세 대기·최신 snapshot·삭제 | 슬롯과 데이터 상태를 오인 없이 표시하고 mutation은 CSRF 사용 | component 등록 통과; 삭제 수동 대기 |
| T-WATCH-011 | MKT-090~095, DB-097~098 | 같은 분·다음 분 체결, 호가만 변경, 거래일 변경, gap·late 입력 | 결정론적 OHLCV·turnover와 VWAP·SMA5·drawdown·spread, 비정상 입력 제외 | 통과 (2026-08-04, 자동 fixture) |
| T-UI-015 | MKT-096, API-105, UI-049 | 지표 없음과 최신 지표가 있는 감시 카드 조회 | 계산 전 null과 지표 값을 구분해 표시 | 통과 (2026-08-04, API·component) |

### 5.2 Guard·판단 실행·승인 추가 시험

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-EXE-001 | EXE-001~014, AI-075~079 | 진단/거래 판단과 Core·Guard 행동 전체 조합 | 진단·비행동 주문/승인 0건, BUY 안전 차단 | 부분 통과 (2026-08-05, 자동; 매도·미지원 전체 조합 대기) |
| T-EXE-002 | EXE-020~025, DB-100~107 | 동일 판단을 반복 라우팅 | execution·Guard 최대 1개, 기존 결과 재조회 | 부분 통과 (2026-08-05, 자동; 동시성·commit 응답 유실 대기) |
| T-EXE-003 | EXE-030~035, CFG-080~084 | `DISABLED`, `MANUAL_APPROVAL`, `AUTOMATIC`과 3개 실행 단계 조합 | 기록만/승인/주문 분기, 상위 단계 gate 우선, 미준비 BUY 차단 | 부분 통과 (2026-08-05, SHADOW·미설정 주문금액 차단; 상위 단계 대기) |
| T-GRD-010 | GRD-080~088, EXE-050~056 | BUY Guard 각 규칙 단독·복합 실패와 경계 금액 | 결정론적 복수 reason, 하나라도 blocking이면 주문 0건 | 부분 통과 (2026-08-13 자동·장중 모의투자; 경계값 전체 조합은 후속) |
| T-GRD-011 | GRD-016~017, EXE-013, ORD-042 | 진입금액 없음·최소 미만·한도 초과·1주 미만·정상금액 | 임의 수량 생성 금지, Decimal 기반 정상 수량만 통과 | 계획 |
| T-GRD-012 | GRD-083~085, EXE-011~012·052~053 | 부분/전량매도·고정손절에서 예약수량·position version·데이터 단절 | 초과매도 0건, stale 승인 무효화, trigger와 EXIT_PENDING 유지 | 계획 |
| T-APR-001 | EXE-040~047, STM-006~009, API-112~121 | 승인·거절·만료·가격/상태 변경·동시 탭·TOTP 재사용 | 한 번만 terminal 전이, 유효 승인만 CREATED 주문과 원자 commit | 부분 통과 (2026-08-14 자동시험과 최신 snapshot 경쟁 개선 완료; Ubuntu 장중 재검증 대기) |
| T-ORD-010 | EXE-060~064, ORD-039~043 | Guard 통과 후 주문 생성과 Broker polling 경쟁 | intent·CREATED·감사 원자 생성, worker만 송신, 활성/UNKNOWN 중복 차단 | 부분 통과 (2026-08-13 자동·장중 Broker 송신 및 명시적 REJECTED; 실제 ACK/체결 대기) |
| T-EXE-004 | EXE-070~073, API-122~124 | SHADOW→APPROVAL_ONLY→MOCK_AUTOMATIC 확대와 축소 | 시험·TOTP 없는 확대 거부, 축소 즉시 적용, 실거래 권한 변화 없음 | 계획 |
| T-AI-010 | AI-080~085, SES-050~053, DB-108~110 | 평일 집중·일반·장외 슬롯, 재시작·중복 tick, snapshot 없음·정상 | 현재 슬롯만 멱등 평가, 정상 TRADING 판단은 SHADOW로 전달, snapshot 없음은 건너뜀, 승인·주문 0건 | 부분 통과 (2026-08-05, 자동; 실제 장중 연속 운전·종목별 예외 주입 대기) |
| T-OPS-014 | OPS-017~018, API-125, UI-106 | scheduler Compose 계약, lease·heartbeat·IDLE·STALE 상태와 대시보드 조회 | scheduler 장애가 API·Broker를 중단하지 않고 상태에 비밀·owner/token을 노출하지 않음 | 통과 (2026-08-05, 자동 계약·API·component fixture) |
| T-AI-011 | MKT-097~099, AI-086~090, DB-111~114, API-126, UI-107 | 충분·부족 분봉, 지표 없음·버전 불일치, 동일 입력 재평가와 판단 조회 | v2 지표 결정론, 결측 null, 입력 hash 재현, 미준비 RISK_BLOCK, 모델 입력 비밀 0건, UI 입력 provenance 표시 | 부분 통과 (2026-08-05, 자동; 실서버 PostgreSQL·장중 연속 입력 대기) |
| T-UI-016 | UI-055~059, UI-075~076, UI-088~089 | 승인 카드·Guard reason·실행 단계 데스크톱/모바일 흐름 | 주문 상태 오인 없음, 만료/무효화 원인 표시, TOTP·접근성 준수 | 계획 |
### 외부 LLM Native Adapter Foundation 시험

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-LLM-020 | LLM-086, LLM-090~091 | OpenAI·Anthropic·Gemini 공식 request/response fixture | 구조화 출력, 실제 model·request ID·usage·latency 정규화 | 통과 (2026-08-06, MockTransport) |
| T-LLM-021 | LLM-087~089 | 외부 Provider 생성, preview, TOTP credential 저장·proof 재사용 | DB·응답·감사에 원문 0건, 서버 생성 ref, Linux 0400, proof 재사용 거부 | 통과 (2026-08-06, API·파일) |
| T-LLM-022 | LLM-090 | timeout·429·401·5xx·잘못된 JSON | 정규 상태, 호출 1회, retry 0, secret 비노출 | 통과 (2026-08-06, MockTransport) |
| T-LLM-023 | LLM-092~093 | 외부 Provider·Model metadata 검증 후 route 검증 | 과금 호출 0건, external route는 runtime 구현 전 차단, 주문 0건 | 통과 (2026-08-06, API) |
| T-LLM-024 | LLM-094~099, API-130~142, UI-127~129 | 공식 Provider 키로 등록 preview·TOTP·모델 발견, 실패·과대 응답·redirect, 모델 사용 전환·재동기화 | 성공 시에만 Provider·secret·모델 저장, 원문 키 0건, 활성 모델만 역할 선택, 기존 route 자동 변경 0건 | 통과 (2026-08-06, MockTransport·API·component) |
# Provider catalog revision tests (2026-08-07)

- Verify exactly 40 catalog entries, OpenAI/Anthropic/Google first, and all remaining entries alphabetically.
- Verify 35 registrable and 5 visible non-registrable templates.
- Verify template endpoint/configuration validation, native and OpenAI-compatible discovery parsing, static model merge, and secret non-disclosure.
- Verify external validated models can create validated SHADOW role candidates while unsupported parameters fail with a precise code.
- Verify deletion requires TOTP, blocks ACTIVE routes, removes the secret, hides the Provider, disables its models, and preserves history.
- Verify the Console has no Models tab and Provider cards retain model controls.

Local evidence: the complete backend suite, Ruff, frontend TypeScript, 11 component tests, Next production build, and SQLite `0017` upgrade→downgrade→upgrade passed. PostgreSQL migration and real external-provider SHADOW calls remain server verification items.

# Prompt profile tests (2026-08-08)

- Verify server-side monotonic role versions, owner isolation, immutable content, and DRAFT→VALIDATED lifecycle.
- Verify unsafe credential, tool, and order instructions are rejected with stable codes.
- Verify route role/state matching and legacy nullable `prompt_profile_id` migration behavior.
- Verify Agent LLM requests prepend the selected system prompt while keeping structured runtime input in a separate user message.
- Verify the role assignment UI creates/selects Prompt Profiles and never displays raw prompt content in run history.

Local evidence: the complete 164-test backend suite, Ruff, frontend TypeScript, 11 component tests, Next production build, and SQLite `0018` upgrade→downgrade-to-`0017`→upgrade passed. PostgreSQL migration and a real external-provider request remain server verification items.

# 로그인 전용 TOTP 개발 정책 시험 (2026-08-08)

- `T-SEC-DEV-001`: ID·비밀번호·TOTP 로그인 전에는 Console과 설정 API에 접근할 수 없고 로그인 완료 후 세션이 발급되는지 검증한다.
- `T-SEC-DEV-002`: 실행 권한 활성화, Provider 등록·credential 설정·삭제, 역할 배정 활성화와 MOCK 주문 시험이 로그인 세션·CSRF를 요구하되 `reauth_proof` 없이 수행되는지 검증한다.
- `T-SEC-DEV-003`: Console의 로그인 이후 확인창에 TOTP 입력란이 없고 `/auth/reauth/totp` 호출이 발생하지 않는지 검증한다.
- `T-SEC-DEV-004`: 재인증 제거 후에도 변경 사유·validation·원자 전환·활성 route 삭제 차단·READY gate·멱등성과 비밀 원문 비노출이 유지되는지 검증한다.

Local evidence: backend 전체 164개 시험과 Ruff, frontend TypeScript 및 11개 component 시험을 통과했다. 로그인 TOTP는 유지되며 로그인 이후 설정·Provider·역할 배정·MOCK 주문 UI의 재인증 호출은 0건이다.

# Provider 삭제 후 이력 회귀 시험 (2026-08-08)

- `T-LLM-DELETE-001`: 비활성 route가 있는 Provider를 삭제하면 관련 route가 `SUPERSEDED`로 전환되고 Provider·활성 모델·역할 후보 목록에서 제외되는지 검증한다.
- `T-LLM-DELETE-002`: 삭제 후 `/ai/routes`, `/ai/role-assignments`와 Console 재조회가 성공하고 보존 route 이력의 모델 별칭·파라미터 provenance를 표시할 수 있는지 검증한다.

# 단순 LLM 실패 정책 시험 (2026-08-08)

- `T-LLM-FAIL-001`: 기본값 `FAIL_STOP`에서 미지원 파라미터·인증·timeout·provider·schema 오류가 발생하면 자동 보정·재호출·주문 없이 실패 이력이 남는지 검증한다.
- `T-LLM-FAIL-002`: `FAILOVER` 역할은 지정한 예비 모델을 최대 1회만 호출하고 성공 모델 또는 최종 `FAIL_STOP` 결과와 시도 순서를 기록하는지 검증한다.
- `T-LLM-FAIL-003`: Core 또는 필수 Scout의 최종 실패 중 신규매수와 AI 주문은 차단되지만 Guard의 손절·비상정지·장마감 청산은 계속 동작하는지 검증한다.

Local evidence: route 계약의 기본 `FAIL_STOP`, 서로 다른 검증 모델 하나만 허용하는 `FAILOVER`, 기본 호출 실패 후 예비 모델 1회 성공과 두 invocation 이력, FAIL_STOP 최종 실패 및 주문 0건을 자동 검증했다. backend 166개 시험·Ruff, frontend TypeScript·11개 component 시험·production build와 SQLite `0019` upgrade→downgrade→upgrade가 통과했다. 실제 외부 Provider 실패와 Guard 독립 동작은 서버·Guard 구현 후 검증한다.

# 외부 LLM SHADOW 출력 채택 시험 (2026-08-10)

- `T-AGENT-EXT-001`: 유효한 외부 Scout JSON을 역할별 계약으로 재검증하고 server-owned provenance를 덧붙여 stage output으로 저장하는지 검증한다.
- `T-AGENT-EXT-002`: 필드 누락·추가, 범위 오류, 허용되지 않은 evidence reference를 `INVALID_OUTPUT`으로 종료하고 FAIL_STOP 또는 단일 fallback만 적용하는지 검증한다.
- `T-AGENT-EXT-003`: Core는 유효한 `WAIT` 응답만 stage에 채택하며 외부 응답을 사용한 전체 DIAGNOSTIC run에서 `Decision`, `Approval`, `TradingOrder`가 0건인지 검증한다.
- `T-AGENT-EXT-004`: Adapter request에 역할별 JSON Schema와 정규화된 market·indicator·position·evidence 입력이 전달되고 credential·주문 도구·원문은 포함되지 않는지 검증한다.

Evidence: 외부 Adapter fixture로 4 Scout·Core 유효 응답의 stage 채택, server-owned provenance, strict-schema 필수 필드, request ID·usage 영속화, 계약 오류의 `INVALID_OUTPUT/FAIL_STOP` 및 주문 0건을 검증했다. Ubuntu 서버에서는 OpenAI·LLM Gateway의 성공·Provider 오류·schema 실패와 역할별 실제 모델 provenance를 확인했다. 외부 출력은 계속 `DIAGNOSTIC/SHADOW`이며 주문 0건이다.

- `T-AGENT-EXT-005`: Adapter가 정규화한 terminal 상태와 `LLM_*` 오류는 `AGENT_LLM_FAIL_STOP` stage 처리 후에도 그대로 보존되고, 완료되지 않은 invocation만 `AGENT_INVOCATION_OUTCOME_UNKNOWN`으로 격리되는지 검증한다.

# 역할별 timeout·service tier 시험 (2026-08-10)

- `T-LLM-ROUTE-006`: route API가 1–600초 timeout과 `DEFAULT/PRIORITY/FLEX`를 검증·영속화·조회하고 migration이 기존 route를 `DEFAULT`로 보존하는지 검증한다.
- `T-LLM-ADAPTER-007`: `DEFAULT`는 service tier 필드를 생략하고 명시적 `PRIORITY/FLEX`는 native/compatible Adapter 요청에 소문자로 전달되는지, 완성 응답이라도 전체 제한시간을 넘으면 결과를 폐기하는지 fixture로 검증한다.
- `T-LLM-UI-008`: 역할별 배정에서 timeout과 tier를 후보에 저장하고 route 요약에서 확인할 수 있으며 `FLEX` 선택 시 300초 권장값이 적용되는지 검증한다.

# LLM Provider web search·runtime clock 시험 (2026-08-11)

- `T-LLM-WEB-009`: 웹 검색 비활성 route는 `tool_policy=NONE`, 활성 route는 Provider별 native 검색 필드로 변환되는지 검증한다.
- `T-LLM-WEB-010`: Core와 capability 미지원 모델의 웹 검색 route 검증이 fail-closed인지 확인한다.
- `T-LLM-TIME-011`: 모든 invocation system context에 UTC와 Asia/Seoul 실행 시각이 있고 DB 이력에 같은 시각과 검색 여부가 저장되는지 검증한다.
- `T-LLM-WEB-012`: Provider 검색 실패가 자동 보정 없이 `FAIL_STOP/FAILOVER`와 오류 이력으로만 처리되는지 확인한다.

# OpenAI 호환 Adapter 정규화 시험 (2026-08-11)

- `T-LLM-ADAPTER-013`: `gpt-5/o1/o3/o4` 모델 요청은 `max_completion_tokens`와 명시된 `reasoning_effort`를 사용하고 `max_tokens`, `temperature`, `top_p`를 전송하지 않는지 확인한다.
- `T-LLM-ADAPTER-014`: 일반 OpenAI 호환 및 Gateway 경유 Gemini 모델은 `max_tokens`, 허용된 sampling 파라미터, strict JSON Schema response format과 server-owned schema instruction을 받는지 확인한다.
- `T-LLM-ADAPTER-015`: LLM Gateway 모델 동기화 시 reasoning 계열 모델 capability가 추가되고 기존 capability를 하향 변경하지 않는지 확인한다.
- `T-LLM-ADAPTER-016`: Provider가 정규화된 요청이나 strict schema를 거부하면 요청을 변경해 재호출하지 않고 기존 오류 상태와 0회 retry를 유지하는지 확인한다.
- `T-LLM-ADAPTER-017`: OpenAI Responses Adapter의 GPT-5/o계열 요청은 reasoning 기본값에서도 `temperature/top_p`를 생략하고, 명시한 reasoning effort만 `reasoning.effort`로 전달하는지 확인한다.

Evidence: OpenAI 호환·Responses Adapter와 parameter policy 집중 시험 및 backend 전체 회귀·Ruff가 통과했다. Ubuntu 서버에서 OpenAI GPT-5 계열과 LLM Gateway 경유 Gemini의 실제 SHADOW 호출, 실제 모델 ID와 schema 통과·실패 이력을 확인했다.

# Provider 출처 후보와 evidence reference 경계 시험 (2026-08-11)

- `T-EVIDENCE-001`: OpenAI Responses, Anthropic, Gemini와 OpenAI-compatible 응답의 알려진 citation 위치가 동일한 canonical 후보로 정규화되는지 확인한다.
- `T-EVIDENCE-002`: HTTPS 공개 URL만 `UNRATED EvidenceItem`으로 저장하고 같은 run의 중복 URL, private/loopback URL과 원문 응답을 저장하지 않는지 확인한다.
- `T-EVIDENCE-003`: 빈 검증 Bundle에서는 모델에 `allowed_evidence_refs=[]`와 빈 배열 반환 규칙을 전달하고 URL·임의 ID 참조를 `LLM_EVIDENCE_REF_NOT_ALLOWED`로 거부하는지 확인한다.
- `T-EVIDENCE-004`: schema, evidence reference와 Core incomplete-role 불일치를 서로 다른 안전한 invocation 오류 코드로 기록하면서 승인·주문은 생성하지 않는지 확인한다.
- `T-EVIDENCE-005`: 신규 run이 8개 stage를 만들고 Candidate Auditor가 네 Scout 종료 전에는 claim되지 않으며 Core는 Auditor 종료 후에만 실행되는지 확인한다.
- `T-EVIDENCE-006`: Provider 후보가 없는 run은 `NO_PROVIDER_SOURCE_CANDIDATES`, 후보가 있는 run은 중복 제거된 ID·Provider별 개수와 `UNRATED_SOURCE_CANDIDATES_PRESENT`를 감사 출력에 기록하는지 확인한다.
- `T-EVIDENCE-007`: Candidate Auditor가 invocation을 만들거나 EvidenceBundle의 hash·evidence IDs를 수정하지 않고 Core 입력에는 후보 개수와 reason code만 전달하는지 확인한다.
- `T-EVIDENCE-008`: EvidenceBundle이 `PARTIAL`이면 모든 LLM stage가 성공해도 run 최종 상태가 `PARTIAL`이며 Decision·Approval·TradingOrder가 0건인지 확인한다.

# 역할별 reason code 계약 시험 (2026-08-11)

- `T-REASON-001`: Scout와 Core 요청에 `reason-code-policy-v1`과 역할별 허용 목록이 포함되고 JSON Schema `reason_codes.items.enum`이 같은 목록인지 확인한다.
- `T-REASON-002`: 역할에 등록되지 않은 reason code를 반환하면 Provider가 schema 성공을 표시했더라도 invocation을 `INVALID_OUTPUT/FAILED`, `LLM_REASON_CODE_NOT_ALLOWED`로 종료하는지 확인한다.
- `T-REASON-003`: 허용된 역할별 code는 server-owned provenance와 함께 stage 출력에 채택되고 기존 evidence reference 및 Core incomplete-role 검사가 그대로 적용되는지 확인한다.
- `T-REASON-004`: Mock fixture, 외부 FAIL_STOP/FAILOVER와 SHADOW 주문 0건 경계를 회귀 검증한다.

Evidence: 역할별 allowlist의 중복·schema enum·입력 정책 버전, 허용 code 채택, 미등록 code의 전용 오류 및 주문 0건을 로컬 회귀시험으로 검증했다. Ubuntu SHADOW 호출에서 역할별 schema 통과와 `LLM_INVALID_OUTPUT` 격리를 모두 확인했다.

# OpenDART PRIMARY evidence 수집 시험 (2026-08-11)

- `T-DART-001`: 공식 endpoint와 40자리 file secret만 허용하며 secret은 DB·stage 출력·오류에 포함하지 않는다.
- `T-DART-002`: `corpCode.xml`의 안전한 ZIP/XML에서 6자리 종목코드를 8자리 고유번호로 해석하고, KST 최근 3일 회사별 공시검색 pagination에서 정확히 같은 종목코드만 채택하며 접수번호 중복을 제거한다.
- `T-DART-003`: `000` 성공과 `013` 빈 성공을 구분하고 인증·IP·한도·HTTP·timeout·page cap 오류는 안정적인 `DART_*` code로 fail-closed 처리한다.
- `T-DART-004`: 검증한 공시를 `DART_DISCLOSURE/PRIMARY` EvidenceItem과 Scout allowlist에 포함하되 Bundle은 `PARTIAL`, 주문은 0건으로 유지한다.

Evidence: MockTransport 고유번호 해석·pagination·필터·빈 결과·Provider 오류, 잘못된 secret의 admission 차단과 Agent DAG 통합을 검증했다. Ubuntu 고정 출구 IP 환경에서 실제 OpenDART 키로 삼성전자 최근 3일 공시 6건과 `OPENDART_PRIMARY` stage 결과를 확인했다.

# KRX PRIMARY 전 거래일 시장 증거 시험 (2026-08-11)

- `T-KRX-001`: 공식 HTTPS host, 승인된 KOSPI·KOSDAQ 일별 endpoint와 40자리 file secret만 허용하고 인증키·원문 응답을 영속화하지 않는다.
- `T-KRX-002`: KST 실행일 이전 7일 안에서 정상 빈 날짜를 건너뛰고 두 시장 응답의 정확한 6자리 종목코드 한 행만 채택한다.
- `T-KRX-003`: 일자·시장 cache가 반복 run의 호출량을 줄이고 HTTP·timeout·인증·quota·형식 오류를 안정적인 `KRX_*` code로 fail-closed 처리한다.
- `T-KRX-004`: 검증한 행을 `KRX_DAILY_MARKET/PRIMARY`로 DART와 함께 불변 bundle에 포함하되 계약 뉴스 coverage 전까지 Bundle `PARTIAL`과 주문 0건을 유지한다.
- `T-BOOT-004`: 선택 secret 유무에 따라 boot reconcile이 DART·KRX overlay를 정확히 포함하고 재부팅 뒤 설정 상태와 Agent 수집을 복원한다.

# NAVER API HUB SECONDARY 뉴스 증거 시험 (2026-08-11)

- `T-NEWS-001`: 공식 Hub endpoint와 Client ID·Secret file만 허용하고 인증 header·요약·원문 응답을 영속화하지 않는다.
- `T-NEWS-002`: 공식 회사명 우선 검색, 종목 identity 일치, HTTPS URL 정규화와 중복 제거를 거친 결과만 `NEWS/SECONDARY`로 채택한다.
- `T-NEWS-003`: 72시간 이내 evidence와 stale ID를 분리하고 빈 결과·비연관 결과·stale-only 결과를 안정적인 reason code로 구분한다.
- `T-NEWS-004`: 인증·권한·quota·HTTP·timeout·형식 오류는 `NAVER_NEWS_*`로 fail-closed 처리하며 단기 cache가 반복 호출을 줄인다.
- `T-NEWS-005`: DART·KRX·NAVER News 복합 Bundle에서도 Provider citation은 `UNRATED`, Core·Scout 허용 근거는 bundle ID 부분집합, 주문은 0건으로 유지한다.

# 구조화 LLM 응답 이력 시험 (2026-08-11)

- `T-LLM-OUTPUT-001`: 성공 output과 server contract 실패 output을 validation 전에 canonical JSON·hash·capture 시각으로 저장한다.
- `T-LLM-OUTPUT-002`: 64 KiB 초과 또는 민감 key 포함 output은 저장하지 않고 전용 오류로 fail-closed 처리한다.
- `T-LLM-OUTPUT-003`: run 소유자만 개별 invocation output을 조회하고 run 목록에는 model output을 포함하지 않는다.
- `T-LLM-OUTPUT-004`: Console은 사용자 요청 시에만 구조화 응답을 불러와 JSON·hash·검증 상태 또는 응답 없음 안내를 표시한다.

Evidence: 성공 및 미등록 reason code 응답의 validation 전 저장·hash·전용 조회, 목록 비노출, 민감 key·64 KiB 초과 fail-closed를 검증했다. `20260811_0023` migration 왕복과 backend·frontend 회귀시험을 통과했고 Ubuntu Console에서 실제 외부 Provider의 구조화 응답과 검증 상태 조회를 확인했다.

# Guard 위험 설정 1차 시험 (2026-08-11)

- `T-RISK-CONFIG-001`: 활성 버전이 없으면 SAFE_DEFAULT와 `entry_order_amount=null`을 반환하며 이를 DB ACTIVE로 오인하지 않는지 확인한다.
- `T-RISK-CONFIG-002`: 금액·손절·시세·spread·가격편차 범위와 `entry≤single≤symbol≤total` 관계를 서버가 거부하고 자동 보정하지 않는지 확인한다.
- `T-RISK-CONFIG-003`: DRAFT→VALIDATED→ACTIVE와 기존 ACTIVE→SUPERSEDED가 category별 원자 전환되고 stale base version은 충돌로 거부되는지 확인한다.
- `T-RISK-CONFIG-004`: 모든 write가 로그인·CSRF를 요구하고 활성화 감사에 비밀 없이 version·hash만 남는지 확인한다.
- `T-RISK-CONFIG-005`: Console이 위험 설정 source·active version·미설정 진입금액 차단을 표시하고 검증·활성화 흐름을 수행하는지 확인한다.

Local evidence: SAFE_DEFAULT의 null 진입금액, 서버 수치·금액 순서 검증, category별 version 생명주기·stale 충돌·감사, Console 검증·활성화와 TOTP 재인증 미사용을 자동 검증했다. backend 전체 209개 테스트·Ruff, frontend TypeScript·12개 component 테스트·production build가 통과했다.

# Agent SHADOW 판단 계약 v2 시험

- `T-AGENT-SHADOW-001`: admission 순간 열린 포지션 유무로 ENTRY/POSITION context와 position snapshot hash가 고정되고 stage 실행 중 포지션 변화가 기존 run을 바꾸지 않는지 확인한다.
- `T-AGENT-SHADOW-002`: ENTRY에서 열린 포지션이 없으면 POSITION_RISK_SCOUT가 `NOT_APPLICABLE`, UNKNOWN, null 점수로 종료되고 Core 불완전 역할에 포함되지 않는지 확인한다.
- `T-AGENT-SHADOW-003`: POSITION에서는 POSITION_RISK_SCOUT가 필수이며 snapshot 누락·오염·만료가 `INSUFFICIENT_DATA` 또는 `CONFLICTED`와 Core UNKNOWN으로 축소되는지 확인한다.
- `T-AGENT-SHADOW-004`: `status != SUCCEEDED`의 모든 Scout 출력에서 두 점수가 null이고, 유효 점수 범위·score-policy-v1 경계와 role별 reason code가 서버에서 검증되는지 확인한다.
- `T-AGENT-SHADOW-005`: Core의 action은 모든 v4 경로에서 WAIT이며 context별 shadow_assessment enum과 불완전 입력의 UNKNOWN 규칙을 지키는지 확인한다.
- `T-AGENT-SHADOW-006`: 같은 snapshot이라도 context 또는 position hash가 다르면 새 run이 생성되고, 같은 v4 입력은 재사용되며 기존 v1~v3 run은 기존 계약으로 조회되는지 확인한다.
- `T-AGENT-SHADOW-007`: 성공·실패·NOT_APPLICABLE 모든 DIAGNOSTIC 경로에서 Decision·Approval·OrderIntent·TradingOrder가 0건인지 확인한다.
- `T-AGENT-SHADOW-008`: Console이 WAIT와 shadow assessment를 분리하고 NOT_APPLICABLE과 null 점수를 각각 `해당 없음`과 `-`로 표시하는지 확인한다.
- `T-AGENT-SHADOW-009`: 기존 v1 schema 선언 route를 v4에서 사용할 때 run snapshot에 선언 schema와 실제 v2 검증 schema가 함께 기록되고 route row는 변경되지 않는지 확인한다.
- `T-AGENT-SHADOW-010`: 필수 Scout가 불완전하면 Core Provider 호출 없이 WAIT/UNKNOWN·confidence 0·정확한 incomplete roles를 서버가 기록하고, 모든 필수 Scout가 완전한 경로에서는 Core Provider 호출을 유지하는지 확인한다.

Local evidence: `T-AGENT-SHADOW-001`~`010` 집중시험, backend 전체 회귀와 Ruff, migration `20260811_0026` upgrade/downgrade/upgrade, frontend TypeScript·12개 component 시험·production build가 통과했다. 외부 Provider fixture에서 불완전 Scout의 Core 호출 0건·PARTIAL/WAIT/UNKNOWN과 완전 입력의 Core 호출 유지를 검증했다. Ubuntu에서 v5 Console 표시는 확인했고 결정론적 Core 축소 재배포 확인은 대기 중이다.

# 서버 소유 Agent 입력 v1 시험 계획

- `T-AGENT-INPUT-001`: 고정 position·market·Risk Policy fixture에서 평가금액, 원가, 미실현손익·수익률, session-high drawdown, stop 가격·거리, tracked duration과 freshness가 canonical Decimal로 재현되는지 확인한다.
- `T-AGENT-INPUT-002`: 활성 Risk Policy와 SAFE_DEFAULT provenance가 version ID·payload hash와 함께 snapshot에 고정되고 admission 후 정책 변경이 기존 run을 바꾸지 않는지 확인한다.
- `T-AGENT-INPUT-003`: stale·누락·hash 불일치 position snapshot은 null 점수의 INSUFFICIENT_DATA/CONFLICTED로 축소되고 Core assessment가 UNKNOWN인지 확인한다.
- `T-MARKET-CONTEXT-001`: trusted service가 정상 index·sector·breadth fixture를 canonicalize하고 breadth 비율·hash·source 시각을 재현하며 중복과 identity 충돌을 구분하는지 확인한다.
- `T-MARKET-CONTEXT-002`: admission이 최신 NORMAL·유효 snapshot만 고정하고 stale·INCOMPLETE·미래 관측 snapshot을 선택하지 않는지 확인한다.
- `T-MARKET-CONTEXT-003`: context 부재 시 MARKET_SECTOR_SCOUT가 Provider 입력을 추정하지 않고 INSUFFICIENT_DATA/null 점수로 종료하는지 확인한다.
- `T-AGENT-INPUT-004`: v5 API·Console이 server input version과 context 고정/없음을 표시하고 기존 v1~v4 run은 nullable field로 조회되는지 확인한다.
- `T-AGENT-INPUT-005`: v5 성공·결측·충돌 모든 경로에서 Decision·Approval·OrderIntent·TradingOrder가 0건인지 확인한다.

Local evidence: 서버 입력·Market Context 집중시험, backend 전체 회귀와 Ruff, migration `20260811_0027` upgrade/downgrade/upgrade, frontend TypeScript·12개 component 시험이 통과했다. Ubuntu PostgreSQL migration과 실제 Console 표시, 운영 Market Context source 연결은 다음 배포에서 확인한다.
## 거래시장 자동 선택

### 거래 캘린더 운영 휴장

| ID | 시험 | 기대 결과 |
| --- | --- | --- |
| T-VEN-CALENDAR-OVERRIDE-001 | 유효한 미래 평일 휴장 등록 | `ACTIVE` 이력이 생성되고 같은 날짜 중복은 409다. |
| T-VEN-CALENDAR-OVERRIDE-002 | 과거·730일 초과 날짜 또는 짧은 사유/출처 등록 | 날짜·도메인 검증은 422, 요청 schema 길이 검증은 공통 400으로 거부되며 override가 생성되지 않는다. |
| T-VEN-CALENDAR-OVERRIDE-003 | 활성 휴장일 SHADOW 평가 | `CLOSED/OPERATIONAL_CLOSURE`, `WAIT`, override ID와 canonical hash가 저장된다. |
| T-VEN-CALENDAR-OVERRIDE-004 | 활성 override 해제 후 재평가 | 기존 평가는 불변이고 새 평가는 기본 캘린더를 사용하며 해제 이력은 조회된다. |
| T-VEN-CALENDAR-OVERRIDE-005 | 인증·CSRF·주문 경계 | 비인증/CSRF 누락 쓰기는 거부되고 Decision·Approval·OrderIntent·TradingOrder는 0건이다. |

| ID | 시험 |
| --- | --- |
| T-VENUE-001 | KST 경계시각 08:00, 08:50, 09:00, 09:00:30, 15:20, 15:30, 15:40, 20:00이 명세 세션으로 정확히 분류된다. |
| T-VENUE-002 | NXT 프리·애프터 단독 세션은 적격 종목과 최신 NXT 호가가 있을 때만 NXT를 선택한다. |
| T-VENUE-003 | 양 시장 일반 주문은 가격 우선, 긴급 주문은 표시 잔량 우선, 완전 동률은 KRX 우선으로 결정된다. |
| T-VENUE-004 | SOR는 REAL·지원됨·양 시장 최신 호가일 때만 추천되며 MOCK에서는 직접 비교한다. |
| T-VENUE-005 | stale·미래·비정상·거래불가·필수 가격/잔량 누락 호가는 후보에서 제외되고 양쪽 모두 없으면 WAIT다. |
| T-VENUE-006 | 진단 API는 서버 snapshot만 사용해 평가를 영속화하고 Approval·OrderIntent·TradingOrder를 생성하지 않는다. |
| T-VENUE-007 | migration `20260812_0028`의 upgrade→downgrade→upgrade가 통과한다. |
| T-VENUE-008 | NXT snapshot이 없는 상태를 미지원으로 단정하지 않고 `UNKNOWN`으로 기록하며, NXT 단독 세션에서는 `WAIT/NXT_ELIGIBILITY_UNVERIFIED`로 종료한다. |
| T-VENUE-009 | 정상 NXT quote는 `instrument_venue_states`에 `VERIFIED/QUOTE_OBSERVED`로 저장되고 이후 SHADOW 평가가 해당 상태를 사용한다. |
| T-VENUE-010 | migration `20260812_0029`의 upgrade→downgrade→upgrade가 통과하며 market snapshot과 기존 0028 평가 원장은 보존된다. |
| T-VENUE-011 | Console 감시 화면은 등록 종목으로 SHADOW 진단을 실행하고 선택 venue·세션·NXT 적격성·양 시장 호가·reason code를 표시하며 주문 생성 컨트롤을 제공하지 않는다. |
| T-VENUE-012 | 진단 실패와 401에서 기존 평가 이력을 보존하고 각각 안전한 오류·세션 만료로 처리한다. |
| T-VENUE-013 | 평일·주말·대한민국 공휴일과 대체공휴일·근로자의 날·연말 휴장일을 공통 캘린더가 판정하며 휴장일 장중 시각도 `CLOSED/WAIT`다. |
| T-VENUE-014 | 거래일 상태·캘린더 근거·정책 버전이 DB·canonical input hash·API·Console에 일치하게 보존되고 판정 실패는 `CALENDAR_UNAVAILABLE/WAIT`다. |
| T-VENUE-015 | migration `20260812_0030`의 upgrade→downgrade→upgrade가 통과하며 기존 평가 행은 보수적인 `UNKNOWN/CALENDAR_UNAVAILABLE` 근거로 조회된다. |

Local evidence (2026-08-12): 평일·주말·공휴일·근로자의 날·연말 휴장과 캘린더 장애 fail-closed 집중 시험, Backend 전체 시험·Ruff, migration `20260812_0030` upgrade→downgrade→upgrade, Frontend TypeScript·12개 component 시험·Next.js production build가 통과했다. Ubuntu PostgreSQL 적용과 실제 카드의 캘린더 근거 표시는 대기 중이다.

Local evidence (2026-08-12, 운영 override): 날짜·메타데이터 경계, 중복 활성 방지, 생성·해제 이력, SHADOW 평가의 override ID·hash 고정과 주문 0건 집중시험이 통과했다. Backend 전체 회귀·Ruff, migration `20260812_0031` upgrade→downgrade→upgrade, Frontend 13개 component 시험·TypeScript·Next.js production build가 통과했다. Ubuntu PostgreSQL 적용과 Console 수동 확인은 대기 중이다.

## 키움 Broker 기준 계좌 projection (2026-08-14)

| ID | 요구사항 | 시험 | 결과 |
| --- | --- | --- | --- |
| T-REC-PROJ-001 | REC-083, DB-153, DB-155 | 내부에 없는 Broker open order를 두 번 처리 | `BROKER_IMPORTED` intent와 주문·event가 한 번만 생성됨 |
| T-REC-PROJ-002 | REC-084, DB-153 | 확정 전량체결 snapshot 반복 처리 | Fill 한 건과 `FILLED` 수량 불변식 유지 |
| T-REC-PROJ-003 | REC-085 | 부분체결 후 open order 부재 | 확인 체결만 저장하고 `RECONCILING/HALTED` 유지 |
| T-REC-PROJ-004 | REC-085 | 주문수량 초과 Broker 체결 | Fill·주문수량을 변경하지 않고 critical mismatch 유지 |
| T-REC-PROJ-005 | REC-086, REC-087, REC-091~092, DB-154 | 신규·변경·Broker 부재 position 처리 | Broker 총량 반영, Cresta 체결 재생에 따른 `EXTERNAL/MIXED/CRESTA_MANAGED` 분류, 부재 row `CLOSED`와 event 기록 |
| T-REC-PROJ-006 | REC-089 | mismatch가 다음 snapshot에서 해소됨 | 이전 OPEN mismatch가 `RESOLVED`와 해결시각으로 전환됨 |
| T-REC-PROJ-007 | REC-090, DB-152 | 유효하지 않은 Broker 수량으로 projection 실패 | transaction rollback, run `FAILED`, gate `DEGRADED` |

Local evidence (2026-08-14): `backend/tests/test_reconciliation.py` 14개와 backend 전체 334개 시험, Ruff, `git diff --check`가 통과했다. Ubuntu 모의투자 서버에서 배포 전 2.0GB PostgreSQL dump를 확보한 뒤 `9244edc`를 적용했다. 키움 snapshot의 체결 1건·position 1건을 반영해 주문 `0087482`가 `FILLED`, position `005930`이 `OPEN/EXTERNAL`이 됐고 관련 과거 mismatch는 모두 `RESOLVED`, OPEN mismatch는 0건이었다. 같은 snapshot의 다음 periodic run은 모든 projection count 0과 mismatch 0으로 멱등 통과했으며 worker와 gate는 `READY/WORKER_HEALTHY`였다. 최초 세 재연결 run은 `FAILED` 후 자동 회복했으나 현재 기록만으로 상세 원인을 식별할 수 없어 실패 원인 영속·로그 보강을 후속 항목으로 남긴다.
