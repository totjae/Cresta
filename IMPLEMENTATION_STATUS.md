# Cresta 구현 상태

### 2026-09-02 Cresta v2 Phase 11B.0B2 Activation Acceptance Candidate Closure 완료

- 기존 `REQUIRED_ACTIVATION_TEST_IDS` 118개를 단일 authority로 사용해
  `backend/tests/activation_acceptance_bindings.json`의 exact 118-ID/155-node binding을
  완성했다. missing/unexpected/duplicate/unresolved는 0이고 required-set hash는
  `d740a14dbcc471e588fc2a03776a216e7bc4c2e6053497d604f3e9804cca913e`와 일치한다.
- structured pytest hook으로 PASS/FAIL/ERROR/SKIP/XFAIL/XPASS/NOT_RUN을 구분하는
  operator runner를 구현했다. all-node PASS만 발행 가능하고 no-publish `run-all`은
  118/118 PASS, 나머지 outcome 0, artifact publication 0으로 완료됐다.
- configured `CRESTA_ARTIFACT_ROOT`만 사용하는 read-only production resolver와 explicit
  `CRESTA_DEPLOYED_REVISION` authority를 API/agent에 연결했다. exact code/test-plan/spec/
  migration/environment/set-hash 및 artifact body/descriptor를 재검증하고 logical invalid는
  422, store infrastructure unavailable은 retryable 503으로 fail-closed한다.
- ACT-012, FIN-LIFE-006, DB-MIG-001/008/010의 실제 PostgreSQL gap과 production resolver
  재생성-session persistence를 추가했다. current manifest의 PostgreSQL node는 13/13 PASS했고
  default backend는 848 PASS와 expected PostgreSQL-only 87 skip으로 완료됐다. focused
  foundation/runner/resolver/Gate API/deployment는 120/120 PASS했다.
- API와 agent만 persistent artifact root를 read-only mount하며 agent 512m, handoff OFF,
  LIVE disabled와 거래 authority/semantic은 변경하지 않았다. Alembic unique head는
  `20260829_0044`, Ruff와 `git diff --check`는 PASS다. final exact-revision evidence artifact는
  아직 발행하지 않았고 server/Gate/Stage/handoff/LIVE mutation과 push도 수행하지 않았다.

### 2026-09-01 Cresta v2 Phase 11B.0B1 Activation Evidence Foundation 완료

- `backend/app/activation_evidence.py`에 strict `activation-evidence-artifact-v1`, canonical
  UTF-8 JSON parser/serializer, SHA-256 identity/reference, 65,536-byte bounded
  content-addressed filesystem store와 PASSED-only publisher foundation을 구현했다.
- store는 configured root에서 digest만으로 path를 구성하고 regular file/symlink를 검사한다.
  complete temporary file의 create-only hard-link publication으로 overwrite를 금지하며 identical
  race는 idempotent success, 기존 corrupt/different bytes와 hash mismatch는 fail-closed한다.
- `activation-acceptance-bindings-v1` strict model/parser, exact node ID와 synthetic-set
  completeness 검증, 기존 `REQUIRED_ACTIVATION_TEST_IDS`에서 계산하는 required-set hash helper를
  구현했다. B1 완료 시점에는 authoritative 118-ID manifest와 실제 evidence artifact를 만들지 않았다.
- foundation 60/60, 기존 Activation Gate 24/24와 backend 전체 821 PASS가 통과했다. 기존
  test DB 미설정 PostgreSQL 81건만 skip이며 신규 skip은 없다. Ruff, `git diff --check`와
  Alembic unique head `20260829_0044`도 PASS했다.
- B1 완료 시점에는 production Settings/API/resolver와 `_unavailable_evidence_loader`를 변경하지
  않아 Gate OPEN이 불가능했다. migration, server, Stage/Gate/handoff/LIVE, commit/push는 변경하거나
  수행하지 않았으며 exact 118 binding과 candidate gap closure는 Phase 11B.0B2에 남긴다.

### 2026-09-01 Cresta v2 Phase 11B.0S Activation Evidence Authority 명세 완료

- `activation-evidence-artifact-v1`의 one-ID/one-artifact body, 64 KiB canonical JSON,
  SHA-256 content addressing과 `sha256:<64hex>` reference를 명세했다.
- deployment-owned filesystem store, create-only publisher, version-controlled test binding,
  exact code/test-plan/spec/migration/environment/required-set binding과 `EXACT_REVISION`
  freshness를 확정했다. Gate API와 runtime worker는 artifact write authority가 없다.
- production resolver는 configured root의 read-only bounded resolver이며 missing, invalid,
  unreadable, corruption과 store unavailable을 fail-closed한다. B0S 완료 시점에는 구체 구현과
  deployment wiring이 `NOT_IMPLEMENTED`였고 `_unavailable_evidence_loader`가 안전 default였다.
- 현재 배포의 PolicyProfile 3/3, Decision route 3/3과 Decision prompt 준비도는 별도 blocker로
  남아 있다. 이번 Phase는 문서만 변경했으며 code, test, migration, server, Gate, Stage,
  handoff와 LIVE를 변경하지 않았다.

### 2026-08-31 Cresta v2 Phase 11A.4F Agent Memory Limit Correction 검증 중

- Phase 11A.4E의 cache-corrected fresh agent는 startup 약 72 MiB에서 normal collection 뒤 253.0~254.1 MiB로 상승했고 256 MiB hard limit에서 `memory.events max=518`, swap 약 116.94 MiB를 기록했다. 단기 runaway/OOM은 없었으나 resident pressure와 swap을 합친 실효 수요가 약 350~380 MiB여서 384 MiB는 운영 여유가 부족하다.
- N100·약 15 GiB host에서 정상 workload에 의미 있는 여유를 제공하도록 agent의 Compose `mem_limit`만 512 MiB로 교정한다. 이는 측정 기반 운영 allocation이며 application/trading semantic requirement가 아니다. CPU, reservation, explicit swap policy와 다른 service resource는 변경하지 않는다.
- local Compose/config regression과 서버의 agent-only fresh runtime 검증이 완료되기 전까지 Stage A memory blocker와 Stage A restart readiness는 OPEN이다. cache logic, provider semantics, backend runtime, migration, DB, Stage/Gate/handoff/LIVE는 변경하지 않는다.

### 2026-08-31 Cresta v2 Phase 11A.4D Agent Cache Retention Correction 완료

- Phase 11A.4C에서 agent RSS/PSS가 256 MiB limit 근처에 고정된 원인 후보 중 process-global official-source cache의 무제한 과거 세대 보존을 최소 교정한다. KRX daily cache는 현재 credential과 현재 `krx_lookback_days` 날짜 창의 KOSPI/KOSDAQ endpoint만 유지해 최대 `2 × lookback_days`(설정 상한 20)로 제한한다.
- Naver news cache는 매 collection에서 만료 항목과 현재 credential이 아닌 항목을 제거한다. 아직 유효한 현재 credential의 서로 다른 query는 TTL 동안 유지하며, 명세에 없는 임의 LRU·query 수 cap·응답 truncation은 추가하지 않는다.
- DART corp-code cache는 기존 lock 안에서 만료 항목과 현재 credential이 아닌 세대를 제거해 최대 1개 mapping만 유지한다. 교체되었거나 과거 날짜가 다시 필요하면 provider에서 재조회할 수 있으므로 source result·evidence·trading semantics는 변경하지 않는다.
- eviction이 실제 발생한 경우에만 cache 이름·제거 수·잔여 수를 DEBUG로 남기며 credential fingerprint, query, provider payload와 secret은 기록하지 않는다. deterministic cardinality 3/3, provider focused 25/25, AgentWorker/runtime 포함 focused 38/38과 backend runnable 전체 761/761이 PASS했다. PostgreSQL 전용 81건은 test DB 미사용 조건의 기존 skip이며 신규 skip은 없다. 전체 Ruff와 `git diff --check`도 PASS하고 migration head는 `20260829_0044`다.
- cache entry 제거가 Python allocator의 arena를 즉시 OS에 반환한다는 보장은 없고 unit test는 256 MiB 적합성을 증명하지 않는다. Stage A는 재시작하지 않았으며 fresh agent redeploy 이후 실제 RSS/PSS 재측정은 Phase 11A.4E의 필수 검증으로 남긴다. 서버·Compose·resource limit·DB·migration·trading authority는 변경하지 않았다.

### 2026-08-31 Cresta v2 Phase 11A.4A Fixed-Stop Correlation ID Capacity Correction 완료

- Ubuntu Stage A에서 broker worker의 `stop-{ISO timestamp}` correlation ID가 37자로 생성되어 PostgreSQL `stop_triggers.correlation_id varchar(36)` 최초 flush를 43회 거부한 원인을 교정했다. SQLite는 `String(36)` 길이를 강제하지 않아 같은 오류를 검출하지 못했다.
- fixed-stop worker producer와 direct-call fallback은 기존 canonical `uuid7()` helper를 사용한다. UUID 문자열은 정확히 36자이며 동일 평가 시각의 독립 실행도 서로 다른 ID를 만든다. correlation ID는 tracing metadata로만 유지되고 trigger idempotency unique, `STOP_TRIGGER` source identity, `order-authority-key-v1`, Approval과 Broker pre-send authority는 변경하지 않았다.
- 실제 local test-only PostgreSQL 17.11 격리 schema에서 기존 37자 producer가 `DataError`로 거부되는 persistence boundary를 재현한 뒤 UUIDv7 trigger와 RiskEvent가 정상 영속되는 것을 확인했다. PostgreSQL fixed-stop persistence/concurrency/authority/pre-send/PAUSE 5/5, SQLite worker·fixed-stop 20/20과 stage/pre-send fixed-stop 14/14가 PASS했다.
- backend 전체 758/758, Ruff와 `git diff --check`가 PASS했다. schema/migration은 변경하지 않았고 head는 `20260829_0044`다. 서버 접속·배포·Compose·DB·Stage·Gate·order mutation과 commit/push는 수행하지 않았으며 Stage A는 별도 redeploy/re-acceptance 전까지 재개하지 않는다.

### 2026-08-31 Cresta v2 Phase 11A.2 Disposable Runtime Data / Backup Policy Correction 완료

- 사용자 승인에 따라 현재 `MOCK`/development의 PostgreSQL·Redis와 Decision·Order·execution history를 disposable operational data로 분류했다. 실행 중 PostgreSQL의 transaction authority는 유지하지만 장기 보존은 요구하지 않으며, 데이터 유실은 의도적으로 수용한다.
- 현재 복구 source of truth는 Git repository와 migration chain이다. 복구 절차는 fresh PostgreSQL 생성 → Alembic head `20260829_0044` 적용 → runtime 재시작이며 backup·암호화·off-host copy·restore rehearsal은 deployment blocker가 아니다.
- 서버의 `cresta-pre-v2-runtime-20260831-011222.dump`는 삭제하지 않고 `OPTIONAL_PRE_DEPLOY_SNAPSHOT`으로 분류했다. 향후 LIVE backup·retention·RPO/RTO는 이 Phase에서 삭제하거나 추정하지 않고 LIVE readiness의 명시적 미결정 항목으로 남겼다.
- credential·password·API key의 Git commit 금지와 Docker secret/file permission 정책은 변경하지 않았다. production code, migration, DB, Compose, systemd와 running service는 변경하지 않았고 dependency도 갱신하지 않았다.
- 서버는 `refactor/v2-runtime` transition checkout을 유지한다. reboot 시 `cresta-boot.service`가 현재 checkout의 Compose reconciliation·one-shot migration을 수행할 수 있으므로 다음 maintenance를 즉시 이어가며, 지연 시 `master` 복귀가 더 안전하다는 경고를 기록했다. 이번 Phase에서는 server branch를 변경하지 않았다.
- 관련 명세·시험계획 정합성, migration head 불변과 `git diff --check`가 PASS했다. frontend npm audit high 1건은 정책과 무관한 OPEN risk이며 이번 Phase에서 수정하거나 migration/recreate blocker로 승격하지 않았다.

### 2026-08-31 Cresta v2 Phase 11A.1 Frontend Test Baseline Cleanup 완료

- 운영 휴장 component 시험의 고정 입력 날짜 `2026-08-13`이 현재 KST의 허용 최소 날짜보다 과거가 되어 native HTML date validation의 `rangeUnderflow`로 form submit 자체가 차단됐다. 명세와 production 구현은 모두 KST 오늘부터 730일 이내만 허용하므로 production bug나 비동기 race가 아니라 날짜 의존 stale test fixture로 판정했다.
- production 코드는 변경하지 않고 시험이 렌더링된 `min` 날짜를 사용하도록 최소 교정했다. 입력 validity, 생성 성공 메시지·행, 해제 성공·이력, CSRF, 무재인증과 실제 POST `market_date`를 유지·검증하며 timeout, sleep, skip, xfail은 추가하지 않았다.
- focused 1/1, frontend 전체 19/19, typecheck, production build와 backend 전체 757/757이 PASS했다. 전체 Ruff와 `git diff --check`도 PASS다. deployment topology·runtime, trading semantics와 migration은 변경하지 않았고 head는 `20260829_0044`, LIVE는 absent다. Phase 11A.1은 `COMPLETE`이며 checkpoint commit readiness는 `YES`다.

### 2026-08-31 Cresta v2 Phase 11A Deployment & Operational Readiness 완료

- production-like Compose topology를 PostgreSQL, Redis, one-shot migration, API, frontend, Nginx, Kiwoom broker worker, scheduler, agent, sourced-handoff로 명시했다. migration만 `alembic upgrade head`를 소유하고 API·worker는 `service_completed_successfully` 뒤 시작한다. API readiness는 PostgreSQL 연결과 exact Alembic head `20260829_0044`를 확인하며 liveness와 분리했다.
- PostgreSQL·Redis는 외부 port를 publish하지 않고 bind persistence를 유지한다. Redis는 cache/queue 성격의 비권위 dependency로 문서화했다. 모든 Compose service에 `json-file` 10 MiB × 5 bounded logging을 적용하고 gateway만 `127.0.0.1:7788`에 노출했다. `.env.example`은 Settings 전체의 비밀 제외 inventory와 MOCK/SHADOW/handoff OFF 안전 기본값을 제공한다.
- local test-only PostgreSQL 17.11의 격리 schema에서 fresh one-shot migration→0044, API `/readyz`, 실제 Uvicorn startup/shutdown, scheduler·agent·sourced-handoff process lifecycle과 handoff OFF idle, broker missing-config fail-fast를 검증했다. SQLite backend는 757/757, focused deployment·worker는 모두 PASS했고 Ruff와 diff 검사는 PASS했다. frontend typecheck와 production build는 PASS했으며 19개 UI test 중 18개 PASS, 기존 async 운영 휴장 override test 1개는 동일하게 실패했다.
- 로컬 Docker CLI가 없어 Compose 실제 config/build/up와 Linux container SIGTERM은 `NOT_RUN_LOCAL / SERVER_PREFLIGHT_REQUIRED`다. 대신 모든 Compose/overlay YAML parse와 topology·migration gate·logging·port·secret static contract를 통과했다. 실제 서버 배포와 multi-day soak는 수행하지 않았고 runbook에 Ubuntu preflight, start/stop/restart/rollback, 로그·restart 관찰, Stage A~C soak와 fail 조건을 기록했다.
- migration 추가는 없고 head는 `20260829_0044`다. LIVE endpoint·credential·account·order와 production DB는 사용하지 않았고 commit/push/branch 변경도 수행하지 않았다. Phase 11A는 repository readiness 기준 `COMPLETE`, checkpoint commit과 server build readiness는 `YES`, Phase 11B는 `NOT_STARTED`다.

### 2026-08-30 Cresta v2 Phase 10G.2 Production Sourced Handoff / Final MOCK System Acceptance 완료

- 기존 `cresta-worker` 별도 process/signal convention에 `sourced-handoff`를 추가하고 Kiwoom Compose overlay의 독립 service로 연결했다. `CRESTA_V7_SOURCED_HANDOFF_ENABLED`는 Pydantic boolean, default `false`이며 malformed 값은 startup validation failure다. disabled process는 DB sweep 없이 stop signal을 기다리고 enabled process만 기존 agent poll cadence로 bounded `reconcile_sourced_entry_executions()`를 실행한다.
- Finalizer/Arbiter에는 execution callback을 추가하지 않았다. committed sourced TRADING/ENTRY Decision만 별도 session의 다음 sweep에서 보이며 worker는 eligibility/action/stage/policy를 재구현하거나 Stage/Gate/Policy를 seed하지 않는다. exact-one은 PostgreSQL partial unique/canonical identity/unique-loser recovery가 보장하고 worker는 DecisionExecution authority까지만 인계하며 MOCK broker submit은 기존 Broker worker만 담당한다.
- 실제 local test-only PostgreSQL 17.11에서 기존 10G.1 69건과 runtime acceptance 11건을 함께 재실행해 80/80 PASS, FAIL 0, NOT_RUN 0을 확인했다. activation OFF, WAIT/REJECT/UNKNOWN, SHADOW, manual Approval과 automatic full MOCK broker E2E, 10회 반복 sweep, dual worker/restart exact-one, Finalizer uncommitted/commit/rollback boundary, DB outage recovery, fixed-stop 및 전체 기존 concurrency regression이 통과했다. 종료 후 `pg10g1_*` schema는 0개다.
- 별도 lifecycle unit test는 default/malformed setting, disabled no-sweep, enabled failure isolation/retry와 graceful stop을 검증했다. SQLite backend 751/751, 전체 Ruff와 `git diff --check`가 PASS했다. migration은 없고 head는 `20260829_0044` 그대로다. LIVE endpoint/credential/account/order는 사용하지 않았고 production DB·Stage seed·commit/push도 수행하지 않았다. Phase 10과 Phase 10G.2는 `COMPLETE`다.

### 2026-08-30 Cresta v2 Phase 10G.1 FINAL PostgreSQL Production Acceptance 완료

- Phase 10G.1A/B/C correction 이후 실제 local test-only PostgreSQL 17.11의 `127.0.0.1 / cresta_acceptance`에서 acceptance 69건 전체를 제외 없이 최종 재실행해 69 PASS, FAIL 0, NOT_RUN 0을 확인했다. 실행별 격리 schema/search_path를 사용했고 종료 후 `pg10g1_*` schema는 0개다.
- fresh→0044, 0040→0041→0042→0043→0044와 실제 PostgreSQL catalog의 FK/CHECK/partial unique/index/predicate/nullability/ON DELETE/BIGINT 및 `order_events.event_type varchar(64)`, `audit_logs.result varchar(64)`가 PASS했다.
- Finalizer/Gate/Stage/sourced concurrency·TOCTOU·ambiguity, typed Guard/financial, Approval create·sequential stale·approve↔approve·approve↔reject CAS, reauth, authority-key, fixed-stop, SKIP LOCKED/fencing, 모든 pre-send race, BROKER_SEND atomicity/DB retry, ambiguous send/reconciliation, source dispatch와 PostgreSQL MOCK E2E A~G가 모두 PASS했다. Approval raw `StaleDataError` leak와 duplicate authority/Broker call은 0이다.
- PostgreSQL과 분리한 SQLite backend 748/748, 전체 Ruff와 `git diff --check`도 PASS했다. migration/production code를 추가 변경하지 않았고 scheduler/handoff/Finalizer direct wiring, production Stage seed, LIVE와 production DB는 사용하지 않았다. Phase 10G.1은 `COMPLETE`, Phase 10G.2 readiness는 `YES`다.

### 2026-08-30 Cresta v2 Phase 10G.1C Approval Optimistic CAS Error Normalization 완료

- Phase 10G.1의 유일한 blocker인 sourced Approval approve↔approve 및 approve↔reject PostgreSQL optimistic-CAS loser 오류 계약을 최소 교정했다. 기존 canonical 계약 `ApprovalError / APPROVAL_VERSION_CONFLICT / HTTP 409 / retryable=false`를 그대로 재사용했고 새 오류·state·authority·proof semantics를 만들지 않았다.
- sourced approve preflight가 Approval을 identity map에 적재한 뒤 locking query가 stale version 객체를 재사용해 commit-time Approval UPDATE에서 `StaleDataError`가 발생한 것이 원인이었다. shared mutation helper는 Approval 객체만 먼저 flush하고 그 좁은 boundary의 `StaleDataError`만 전체 transaction rollback 후 canonical conflict로 변환한다. 이후 commit의 OperationalError·IntegrityError·다른 versioned entity 오류는 원형 그대로 전파한다.
- 실제 PostgreSQL 17.11 approve↔approve와 approve↔reject 경쟁은 각각 exactly-one winner, canonical loser, raw `StaleDataError` 0, Approval version 2와 state별 OrderIntent/Order/proof/Guard/audit exact side effect를 통과했다. 관련 PostgreSQL Approval/reauth 7건과 focused Approval/Phase 10D 22건이 PASS했다.
- sequential stale와 rollback-before-mapping, unrelated OperationalError non-normalization을 확인했고 SQLite backend 748/748, 전체 Ruff와 `git diff --check`가 PASS했다. migration/ORM schema, scheduler/handoff, LIVE와 production DB는 변경하지 않았다. Phase 10G.1 final rerun readiness는 `YES`다.

### 2026-08-30 Cresta v2 Phase 10G.1 PostgreSQL Production Acceptance Full Rerun — INCOMPLETE

- local test-only PostgreSQL 17.11의 `127.0.0.1 / cresta_acceptance`, 실행별 격리 schema/search_path와 repository/Alembic head `20260829_0044`를 사용했다. fresh→0044, 0040→0041→0042→0043→0044, 실제 catalog의 FK/CHECK/partial unique/index/predicate/nullability/ON DELETE/BIGINT 및 0043/0044 `varchar(64)` capacity는 모두 PASS했다. 비밀·전체 DSN, production DB와 LIVE는 사용하지 않았다.
- PostgreSQL acceptance 69건 중 67건이 PASS했다. Finalizer/Gate/Stage/sourced exact-one과 concurrency·TOCTOU·ambiguity, typed Guard invalid FK matrix, financial selection/freshness, Approval concurrent create, reauth double-consume/rollback, decision·stop authority key, fixed-stop concurrency, SKIP LOCKED, lease fencing, CREATED/send races, Stage/PAUSE/expiry races, BROKER_SEND atomicity/DB retry, ambiguous send/reconciliation, invalid source dispatch와 MOCK E2E A~G가 PASS했다.
- Approval CAS의 `APPROVE(expected=1)`↔`APPROVE(expected=1)` 및 `APPROVE(expected=1)`↔`REJECT(expected=1)` 두 실제 PostgreSQL 경쟁에서 loser가 결정론적 `ApprovalError` stale-version 결과로 닫히지 않고 `_approve_sourced()` commit에서 SQLAlchemy `StaleDataError`를 외부로 누출했다. winner의 optimistic version update와 exact-one DB 제약은 작동하지만 API/service 오류 계약을 만족하지 않으므로 Phase 10G.1은 완료하지 않는다.
- 요청된 validation-only 경계에 따라 `backend/app/approvals.py`, ORM과 migration을 수정하지 않았다. 후속 correction은 sourced approve/reject commit 경계에서 `StaleDataError` rollback 및 canonical stale-version `ApprovalError` 변환을 명시·구현하고 두 경쟁 시험을 재실행해야 한다.
- PostgreSQL과 분리한 SQLite backend 746건, 전체 Ruff와 `git diff --check`는 PASS했다. production scheduler/handoff/Finalizer direct wiring은 활성화하지 않았고 기존 dirty worktree를 reset/restore/clean하지 않았다. Phase 10G.2 readiness는 `NO`다.

### 2026-08-29 Cresta v2 Phase 10G.1B PostgreSQL Audit Result Capacity Correction 완료

- `audit_logs.result`에 실제 영속 가능한 server-owned exact result를 inventory한 결과 unique 93개, 최장 35자이며 모두 64자 이하다. additive `20260829_0044`는 0043 이후 해당 column만 `varchar(24)→varchar(64)`로 확대하고 ORM을 일치시켰다. `AUTOMATIC_AUTHORITY_REVOKED`(27)와 `APPROVAL_AUTHORITY_REVOKED`(26)를 포함한 literal과 authority semantics는 변경하지 않았다.
- downgrade는 24자를 초과하는 row가 있으면 명시적으로 거부해 schema/data를 보존한다. 실제 local test-only PostgreSQL 17.11에서 fresh→0044, 0043→0044, catalog 64, inventory 93개 전체 insert, safe downgrade/re-upgrade와 long-result refusal이 PASS했다.
- automatic/manual revocation에서 exact event/audit, Order INVALIDATED, DecisionExecution FAILED_SAFE, manual Approval `INVALIDATED / EXECUTION_AUTHORITY_REVOKED`, OrderIntent 불변과 Broker 0회를 확인했다. injected commit failure는 event/audit/Order/execution partial state를 모두 0으로 rollback했다. PostgreSQL Phase-focused 10건과 SQLite backend 746건, 전체 Ruff 및 `git diff --check`가 PASS했다.
- Phase 10G.1 full concurrency matrix는 실행하지 않았지만 알려진 0043/0044 capacity blocker는 모두 교정돼 full rerun readiness는 `YES`다. 0039~0043, scheduler/handoff/LIVE는 변경하지 않았고 commit/push하지 않았다.

### 2026-08-29 Cresta v2 Phase 10G.1A PostgreSQL Schema Capacity Correction 완료

- additive `20260829_0043`은 0042 이후 `order_events.event_type`만 `varchar(32)→varchar(64)`로 확대했고 ORM도 64로 일치시켰다. exact 35자 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND`는 rename/축약하지 않았으며 기존 row backfill도 없다. downgrade는 32자를 초과하는 row가 있으면 명시적으로 거부해 schema/data를 보존한다.
- 실제 local test-only PostgreSQL 17.11에서 fresh→0043, 0040→0041→0042→0043, catalog 64, short/exact event insert, safe downgrade/re-upgrade, long-event downgrade refusal을 포함한 Phase-focused 10건이 PASS했다. PAUSE_ENTRY automatic/manual Approval 회수에서 event, `CREATED→INVALIDATED`, `DecisionExecution FAILED_SAFE`, Approval `INVALIDATED`, OrderIntent 불변과 Broker 0회를 확인했다.
- SQLite backend 743건은 PASS했고 전체 Ruff와 `git diff --check`도 PASS했다. 0039~0042, event/authority semantics, scheduler/handoff/LIVE는 변경하지 않았고 commit/push하지 않았다.
- correction 범위 밖의 기존 `audit_logs.result varchar(24)`는 `AUTOMATIC_AUTHORITY_REVOKED` 같은 24자 초과 exact result를 PostgreSQL에서 거부한다. 이번 Phase의 PAUSE_ENTRY focused regression과 0043 완료를 막지는 않지만, 전체 Phase 10G.1 재실행 전에 별도 additive capacity correction이 필요하므로 rerun readiness는 `NO`다.

### 2026-08-29 Cresta v2 Phase 10G.1 PostgreSQL Production Acceptance — INCOMPLETE

- 이전 환경 blocker가 해소돼 비밀 미출력 환경 검사를 통과했다. 대상은 `127.0.0.1`의 test-only `cresta_acceptance`, 실제 server는 PostgreSQL `17.11`, user도 전용 `cresta_acceptance`다. 운영 DB·LIVE 계좌·production scheduler는 사용하지 않았고 실행별 격리 schema와 고정 `search_path`만 사용했다.
- repository/Alembic head `20260828_0042`, fresh→0042와 0040→0041→0042, 실제 catalog의 0040~0042 FK/CHECK/partial unique/index/BIGINT/nullability/ON DELETE 및 key capacity를 확인했다. catalog 계약 대부분은 일치했지만 규범 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND`는 35자인 반면 `order_events.event_type`은 PostgreSQL `varchar(32)`다.
- 이 mismatch 때문에 semantic pre-send revocation의 OrderEvent insert가 `StringDataRightTruncation`으로 rollback되고 order가 `CREATED`에 남는다. fail-closed로 Broker 호출은 0이지만 EXE-232/275, DB-223/245, ORD-053, STM-038의 원자 `CREATED→INVALIDATED` 계약을 만족하지 못한다. exact event 이름을 보존하려면 새 additive migration과 ORM column 확대가 필요하므로 0039~0042 수정·새 migration 금지 조건에 따라 production correction을 하지 않았다.
- blocker 비종속 PostgreSQL acceptance 17건은 PASS했다. fresh/incremental migration, catalog의 나머지 항목, ACTIVE partial unique, 실제 `FOR UPDATE SKIP LOCKED`, lease fencing, Finalizer concurrent exact-one, sourced Decision concurrent exact-one, WAIT/REJECT/UNKNOWN/SHADOW, manual Approval+outer rollback, automatic BUY CREATED, fixed-stop exact-one+rollback을 포함한다. event capacity와 그 revocation에 의존하는 automatic/manual/unclassified pre-send 4건은 FAIL이다.
- Approval/reauth/OrderIntent/fixed-stop의 별도 concurrent winner matrix, Gate/Stage service activation race, CREATED 상태 경합, stage·PAUSE_ENTRY·expiry race, ambiguous-send/reconciliation concurrency는 schema blocker 확인 뒤 실행을 중단해 `NOT_RUN`이다. 따라서 Phase 10G.1은 `INCOMPLETE`, Phase 10G.2 readiness는 `NO`다.
- PostgreSQL과 분리한 기존 SQLite backend 741건은 PASS했고 전체 Ruff와 `git diff --check`도 PASS했다. SQLite 결과를 PostgreSQL 증거로 사용하지 않았다.
- 이번 착수에서는 production Python, tests, migration 0039~0042, scheduler/Finalizer/startup wiring, Stage seed와 LIVE를 변경하지 않았고 기존 dirty worktree를 보존했다. 전체 Ruff와 `git diff --check`는 PASS했다.

### 2026-08-29 Cresta v2 Phase 10F Broker Pre-Send Authority / Unsent Revocation 완료

- Active Broker worker의 CREATED claim 뒤 Order→Intent→typed source를 추적하고 DECISION_EXECUTION/STOP_TRIGGER의 persisted source, frozen/current stage·action·Risk Policy, Decision expiry, PAUSE_ENTRY BUY, Approval, exact financial evidence, current market/position, strict MOCK와 conflict를 `SUBMITTING` 직전에 재검증한다. Activation Gate와 Broker financial refresh는 조회하지 않는다.
- authority PASS는 typed `BROKER_SEND` Guard와 최종 lease/fencing/gate 확인, `VALIDATING→SUBMITTING`을 한 transaction으로 먼저 commit한 뒤 외부 MOCK call을 수행한다. network 동안 row lock을 유지하지 않으며 기존 ACK/REJECTED/UNKNOWN, gate close, reconciliation과 no-blind-resend lifecycle을 보존했다.
- semantic authority 상실은 Broker 호출 0, `CREATED→INVALIDATED`, 결정론적 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` event/audit로 끝낸다. sourced execution은 `FAILED_SAFE / EXECUTION_AUTHORITY_REVOKED_BEFORE_SEND`, manual Approval은 `INVALIDATED / EXECUTION_AUTHORITY_REVOKED`다. Decision·AgentRun·OrderIntent와 immutable order terms는 변경하지 않는다.
- fixed-stop 회수는 trigger를 `FULFILLED`로 남기지 않고 `EXIT_PENDING`으로 되돌리며 RiskEvent ACTIVE를 유지한다. 자기 Order는 reserved/conflict 계산에서 제외하고 current available managed quantity가 기존 수량보다 작으면 축소하지 않고 회수한다. PAUSE_ENTRY는 risk-reduction SELL을 차단하지 않는다.
- null/unknown, LEGACY_EXECUTION의 증명되지 않은 grant와 BROKER_IMPORTED CREATED는 fail-closed한다. 기존 MOCK connection test는 typed privileged `BROKER_DIAGNOSTIC` 1주 source identity로 교정했고 import projection도 BROKER_IMPORTED provenance를 저장한다. internal unsent reconciliation helper는 idempotent 수동 foundation만 제공하며 자동 활성화하지 않았다.
- Phase 10F focused 32건, sender 13건, worker/lease 6건과 Phase 10E~9E 직접 회귀가 통과했고 backend 전체 741건, 전체 Ruff와 `git diff --check`가 PASS했다. SQLite는 PASS, PostgreSQL은 `NOT_RUN`이다. migration/ORM/0041/0042, scheduler, Finalizer hook, LIVE, commit/push는 변경하지 않았다.

### 2026-08-29 Cresta v2 Phase 10E MOCK_AUTOMATIC / Fixed-Stop Authority 완료

- sourced BUY의 effective MOCK_AUTOMATIC+AUTOMATIC에서 Phase 10D complete frozen/current PRE_ORDER Guard를 재사용하고 current Stage/mode를 Order 직전에 다시 확인한다. strict MOCK authority가 모두 유효할 때 Approval 없이 `approval_id=null` DECISION_EXECUTION `ordauth-` OrderIntent와 MOCK TradingOrder CREATED를 한 transaction으로 정확히 하나 만든다.
- frozen/current Risk Policy를 각각 Guard에 적용하고 server-owned entry sizing도 두 policy의 restrictive minimum을 사용한다. stage downgrade는 Order 0, mode downgrade는 아직 resource가 없을 때만 manual Approval path로 축소하며 same authority retry와 rollback은 중복·부분 상태를 남기지 않는다.
- fixed-stop은 process Settings stage와 implicit safe-default AUTOMATIC 권한을 제거했다. exact-one ACTIVE v7 Stage와 exact-one explicit versioned fixed-stop action policy를 사용하고, typed STOP_TRIGGER Guard와 current position version·managed/available/reserved quantity를 재검사한다.
- fixed-stop SHADOW는 evidence-only/Order 0, APPROVAL_ONLY는 synthetic Approval 없이 EXIT_PENDING/Order 0, MOCK_AUTOMATIC+AUTOMATIC+strict MOCK+Guard PASS만 STOP_TRIGGER `ordauth-` OrderIntent와 MOCK SELL CREATED를 만든다. PAUSE_ENTRY는 risk-reduction SELL을 차단하지 않고 retry/recovery는 initial authority 하나만 재사용한다.
- Phase 10E focused 11건과 직접 영향 회귀 41건, Phase 10C.2 재검증 13건 및 backend 전체 pytest 100%가 통과했다. 전체 Ruff와 `git diff --check`도 통과했다. SQLite migration 회귀는 PASS이며 PostgreSQL은 `NOT_RUN`이다.
- Broker submission/pre-send authority, CREATED 후 unsent revocation, production sourced sweep/Finalizer handoff, scheduler, LIVE와 production Stage seed는 여전히 열지 않았다. 0041/0042와 ORM/migration은 변경하지 않았고 commit/push도 수행하지 않았다.

### 2026-08-28 Cresta v2 Phase 10D Guard Completeness / Manual Approval Authority 완료

- sourced BUY를 APPROVAL_ONLY와 MOCK_AUTOMATIC까지 확장하고 PRE_ORDER 및 APPROVAL_REVALIDATION Guard에 current session/status/quote/active-order, frozen/current Risk Policy minimum, account funds와 exact BUY capacity freshness·cash-only 100% authority를 연결했다. stale/missing 금융 증거의 Broker refresh는 authority transaction 밖에서 수행하고 저장된 exact context를 transaction 안에서 다시 선택한다.
- APPROVAL_ONLY의 AUTOMATIC은 `FAILED_SAFE / AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY`, MOCK_AUTOMATIC의 AUTOMATIC은 `FAILED_SAFE / MOCK_AUTOMATIC_NOT_IMPLEMENTED`로 닫았다. 두 stage의 MANUAL_APPROVAL은 exact-one PENDING Approval을 만들며 SHADOW 권한은 종전대로 주문 0을 유지한다.
- sourced approve는 owner, PENDING/version CAS, expiry, full source lineage, current stage, PAUSE_ENTRY, price deviation, Guard 및 `<approval_id>:<expected_version>` 결합 `APPROVE_ORDER` one-time proof를 재검사한다. proof 소비, APPROVAL_REVALIDATION Guard, OrderIntent, CREATED TradingOrder, Approval/DecisionExecution 전이와 audit를 한 transaction으로 처리하고 rollback 시 부분 상태를 남기지 않는다.
- `order-authority-key-v1` canonical identity와 same-authority retry/reuse 및 immutable-term conflict fail-closed를 구현했다. Approval API는 approve/reject의 필수 `expected_version`, approve 전용 필수 `reauth_proof`, 조회 응답의 `version`을 노출한다. legacy APPROVAL_ONLY+AUTOMATIC BUY/SELL 직접 주문 P0 경로도 FAILED_SAFE로 닫았다.
- focused 10건과 backend 전체 회귀 100%가 통과했고 전체 Ruff 및 `git diff --check`가 통과했다. SQLite migration 회귀는 PASS, PostgreSQL은 환경 부재로 `NOT_RUN`이다.
- `MOCK_AUTOMATIC + AUTOMATIC`, fixed-stop 변경, Broker pre-send/send, scheduler/sweep/Finalizer hook, LIVE, production stage seed와 새 migration은 구현하지 않았다. 0041/0042는 변경하지 않았고 commit/push도 수행하지 않았다.

### 2026-08-28 Cresta v2 Phase 10D.2 Guard Freshness / Order Authority Identity 계약 완료 — SPEC_ONLY

- 이전 Phase 10D resume audit의 두 semantic blocker를 규범 명세로 닫았다. versioned Risk Policy에 `account_funds_stale_seconds=30`(1..300)과 `order_capacity_stale_seconds=10`(1..60)을 추가하고 legacy valid payload default, quote TTL 독립성, frozen/current minimum과 malformed-current fail-closed를 GRD-107~116·CFG-121~126으로 확정했다.
- Phase 10D.1B `received_at`을 server successful Broker response receipt/normalization UTC로 유지한다. inclusive TTL, future timestamp 거부, NULL-vs-zero, exact-context capacity, network-outside-transaction refresh와 short-transaction reselect, Approval 시 blind reuse 금지 및 Guard evidence provenance를 확정했다. cash는 buying power가 아니며 margin leverage는 열지 않았다.
- `order-authority-key-v1` exact four-field material(`schema_version`, `source_type`, `source_id`, `approval_id`), sorted compact UTF-8 JSON/explicit null, SHA-256와 `ordauth-<64 lowercase hex>`를 EXE-263~273의 단일 기준으로 확정했다. manual DECISION_EXECUTION은 execution+Approval, future automatic은 approval null이며 price·quantity·policy·stage 등 mutable revalidation terms는 제외한다.
- authority key와 request hash/TradingOrder idempotency·client order/fencing을 분리하고 same material retry reuse, same key conflicting immutable terms fail-closed, policy/stage 변화의 no-new-authority, no guessed backfill을 ORD-055~060·DB-242~244에 연결했다. 기존 128자 column이 72자 key를 수용하므로 migration은 필요하지 않다.
- T-V2-EXE-AUTH-017~035에 요청된 A~S 경계·결정성·충돌 계획을 추가했다. Phase 10B authority ordering, exact-one DecisionExecution/Approval/initial Order authority, Phase 10D.1B append-only persistence/exact selector와 LIVE 부재는 변경하지 않았다.
- 이 단계는 SPEC_ONLY다. production Python, ORM, migration, API/Guard/Approval/Order/Broker/scheduler, test code를 변경하거나 실행하지 않았다. 두 semantic blocker는 해소되어 Phase 10D 구현 재개가 가능하지만 implementation 자체는 아직 완료되지 않았다.

### 2026-08-28 Cresta v2 Phase 10D 재개 검토 — INCOMPLETE (two normative authority contracts missing)

- Phase 10D.1B의 `AccountFundsSnapshot`, exact request-bound `OrderCapacitySnapshot`, selector와 `20260828_0042`가 존재하므로 이전 buying-power persistence blocker는 해소됐다. sourced execution·Approval·reauth·OrderIntent/TradingOrder 경계도 다시 조사했다.
- 새 P0 semantic blocker 1: Phase 10B 문서는 `order_intents.authority_key`를 stable initial authority identity로 요구하고 unique foundation을 제공하지만, DECISION_EXECUTION manual-approved OrderIntent의 canonical material, schema discriminator/prefix, serialization과 hash 규칙을 정의하지 않는다. 원 Phase 10D 및 이번 resume 요청은 exact definition이 없으면 임의 builder를 만들지 말고 INCOMPLETE로 종료하도록 명시한다. execution ID, Approval ID 또는 ad-hoc hash 중 하나를 구현 편의로 선택하지 않았다.
- 새 P0 semantic blocker 2: `GUARD_RISK_SPEC`, `CONFIGURATION_SPEC`, `DECISION_EXECUTION_SPEC`와 `RiskPolicyPayload`에는 AccountFunds/OrderCapacity freshness threshold가 없다. 현재 `quote_stale_seconds`는 시장시세 전용이며 이를 금융 authority TTL로 재사용하거나 하드코딩한 초 값을 추가하면 명세 없는 위험 정책이 된다. 따라서 missing과 stale을 authoritative하게 구분해 Phase 10D Guard를 완료할 수 없다.
- 이 두 계약이 없으면 APPROVAL_REVALIDATION 성공 transaction의 typed OrderIntent를 생성하거나 account/capacity freshness PASS를 주장할 수 없으므로 complete PRE_ORDER/Approval code를 부분적으로 열지 않았다. 특히 legacy `APPROVAL_ONLY + AUTOMATIC` 경로, sourced higher-stage path, Approval API/CAS/reauth, fixed-stop, worker/pre-send와 scheduler는 변경하지 않았다.
- 재개 조건은 (1) DECISION_EXECUTION initial OrderIntent `authority_key` exact canonical contract, (2) account funds와 exact capacity 각각의 freshness threshold 및 policy provenance/default/허용범위 확정이다. 이후 같은 Phase 10D continuation에서 network-outside-transaction capacity refresh, full Guard, manual Approval transaction과 전체 acceptance를 구현한다.

### 2026-08-28 Cresta v2 Phase 10D.1B Kiwoom Financial Adapter & Authority Projection 완료

- `KiwoomMockClient`에 계좌 검증을 재사용하는 read-only `kt00001`/`kt00010` Adapter와 server-owned canonical DTO를 추가했다. `kt00001` reconciliation 정책은 explicit `qry_tp=3`이고 `kt00010`은 symbol/side/price 및 실제 supplied optional request context에 결합한다.
- signed zero-padded integer만 허용하는 strict normalizer를 공유한다. authoritative zero, missing/null/blank, signed negative를 구별하고 decimal/comma/alphabetic/non-string은 structured invalid response로 거부한다. amount 음수는 보존하고 capacity quantity 음수는 거부한다.
- 별도 append-only `account_funds_snapshots`와 `order_capacity_snapshots` ORM 및 additive migration `20260828_0042`를 추가했다. `BIGINT`, nullable financial fields, server UTC `received_at`, source/account/environment provenance와 selector index를 사용하며 backfill은 없다. evidence가 있으면 destructive downgrade를 거부한다.
- latest funds selector는 exact broker/account/environment, capacity selector는 exact broker/account/environment/symbol/side/price와 nullable io amount/requested quantity/expected buy price 전체를 맞춘 뒤 `received_at DESC, id DESC`로 선택한다. persisted freshness boolean이나 cross-context fallback은 없다.
- reconciliation은 성공한 `kt00001`을 기존 주문·체결·포지션 projection transaction에 append한다. 금융 조회 실패는 새 row를 만들거나 이전 금융 증거를 수정하지 않으며 기존 계좌 projection은 계속 처리하고 비밀 없는 failure code만 run summary에 기록한다.
- `query_and_persist_order_capacity()`는 network read/normalize 뒤 짧은 append transaction으로 매 성공 관측을 새 row로 저장한다. Guard, sourced execution, Approval, OrderIntent/TradingOrder, fixed-stop, Broker pre-send와 scheduler는 변경하지 않았다.
- OFFICIAL_SCHEMA_FIXTURE 기반 normalization/adapter, missing-vs-zero, exact selector, append semantics, failure preservation, 0041→0042/빈 backfill/destructive downgrade 시험을 추가했다. 실제 MOCK credential/fixture/call과 PostgreSQL 환경은 없어 실행하지 않았다.
- Phase-focused 71건과 지정 Phase 10C.2/10C.1/9E 및 broker/worker/reconciliation 회귀가 통과했고, 최종 backend 전체 687건, 전체 Ruff와 `git diff --check`가 통과했다. SQLite migration 검증은 PASS, PostgreSQL은 `NOT_RUN`이다.

### 2026-08-28 Cresta v2 Phase 10D.1A Kiwoom Broker Financial Source Contract 검증 완료

- 2026-08-28 키움 공식 REST API guide와 키움증권 공식 GitHub의 `kiwoom/_data/kiwoom_api_spec.json`을 대조해 account financial source를 확정했다. 세 TR은 모두 `POST /api/dostk/acnt`이고 공식 MOCK domain은 `https://mockapi.kiwoom.com`이다.
- `kt00001`은 `qry_tp`(`3` 추정/`2` 일반)만 받는 account-level 예수금 상세 조회다. canonical 후보는 `entr`(예수금), `pymn_alow_amt`(출금가능금액), `ord_alow_amt`(주문가능금액), margin-band별 `20/30/40/50/60/100stk_ord_alow_amt`, `d1_entra`/`d2_entra`, `d1_pymn_alow_amt`/`d2_pymn_alow_amt`다. 금액은 원 단위, optional String, 좌측 zero-padding과 부호를 포함하므로 zero·missing·negative를 서로 구분해야 한다.
- 원 요청의 TR 대응을 정정했다. `kt00009`는 주문인출가능금액이 아니라 `계좌별주문체결현황요청`이며 financial authority source가 아니다. 실제 `주문인출가능금액요청`은 `kt00010`이다.
- `kt00010`은 `stk_cd`, `trde_tp`(1 매도/2 매수), `uv`가 필수이고 `io_amt`, `trde_qty`, `exp_buy_unp`가 optional인 주문 시뮬레이션이다. response의 `profa_20/30/40/50/60/100ord_alow_amt`·동일 band `*_alowq`, `ord_alowa`(주문가능현금), `wthd_alowa`, `entr` 등은 그 request context에 결합한다. 따라서 이를 account-wide `available_buying_power` 하나로 flatten하지 않는다.
- 권장 모델은 Option B다. `AccountFundsSnapshot`에는 broker/account/environment, `entr`, generic `ord_alow_amt`, `pymn_alow_amt`, D+1/D+2 funds, source API/query type와 server `received_at`을 보존한다. 별도 `OrderCapacitySnapshot`에는 broker/account/environment, symbol, side, requested price/optional quantity·expected price, margin-band별 amount/quantity, `ord_alowa`, source API와 `received_at`을 함께 보존한다. 공식 response에 broker observation timestamp가 없으므로 `received_at`은 broker timestamp가 아닌 server observation time이다.
- BUY Guard는 account funds만으로 충분하지 않다. account-level generic ceiling과 exact intended BUY context의 `kt00010` capacity를 모두 확인하고, Cresta의 cash-only 정책에서는 100% margin amount/quantity와 requested notional/quantity를 비교하는 보수적 계약을 Phase 10D.1B 명세·구현에서 확정해야 한다. response field 누락·빈 문자열·parse 실패는 unknown이고, 공식적으로 signed인 값은 음수라는 이유만으로 malformed 처리하지 않는다.
- 로컬 shell에는 `CRESTA_KIWOOM_*` 설정과 `.env`가 없고 Docker CLI도 없어 MOCK credential을 안전하게 사용할 수 없었다. 따라서 MOCK 호출·fixture는 생성하지 않았으며 LIVE/주문 API는 호출하지 않았다. 공식 schema가 exact field semantics를 제공하므로 source contract는 verified로 종료하되, Phase 10D.1B focused test fixture는 공식 example과 이후 확보할 redacted MOCK response를 구분한다.

### 2026-08-28 Cresta v2 Phase 10D.1 착수 검토 — INCOMPLETE (verified broker financial source contract required)

- 현행 `KiwoomMockClient.get_account_snapshot()`은 `ka10075` open orders, `ka10076` fills, `kt00018` positions만 조회하고 `BrokerAccountSnapshot` DTO는 이 세 collection과 `observed_at`만 가진다. `ka00001`은 설정 계좌와 token 계좌의 식별 일치 검증에만 쓰이며 cash 또는 available buying power를 반환하는 repository contract가 아니다. ORM과 reconciliation projection에도 broker/account/environment별 financial snapshot은 없다.
- 2026-08-28 키움 REST API 공식 가이드에서 `kt00001`(예수금상세현황요청)과 `kt00010`(주문인출가능금액요청) TR의 존재까지는 확인했다. 그러나 repository에는 두 TR의 정확한 요청 조건, MOCK 지원 여부, raw response field 이름·단위·부호 규칙, `kt00010` 값이 account-wide인지 종목·가격·주문조건별인지에 관한 검증된 adapter 계약이나 fixture가 없다. 따라서 어느 값을 canonical `cash`와 `available_buying_power`로 채울지 authoritative하게 확정할 수 없다.
- Phase 10D.1 blocker rule에 따라 검증되지 않은 raw field를 추정하거나 `cash`, 0, risk-policy 금액으로 buying power를 합성하지 않았다. production Python·ORM·migration·test code를 변경하지 않았고 0041도 그대로 보존했다. persistence만 추가해도 source authority가 생기지 않으므로, 먼저 공식 response 계약과 MOCK 실제 응답 fixture를 확보해 adapter normalization을 확정해야 한다.
- 후속 재개 조건은 (1) cash source TR/field, (2) account-wide order-available amount의 source TR/field와 산정 조건, (3) request/account/environment provenance, (4) missing·malformed·negative·unavailable semantics, (5) broker 관측시각 또는 server receipt-time provenance를 공식 문서와 redacted MOCK 응답으로 검증하는 것이다. 그 뒤 0041 이후 additive migration, nullable financial projection, monotonic `observed_at` update와 focused tests를 구현한다.

### 2026-08-28 Cresta v2 Phase 10D 착수 검토 — INCOMPLETE (authoritative buying-power persistence blocker)

- Phase 10D의 필수 `GRD-099`, `GRD-102`, `EXE-242`는 BUY PRE_ORDER와 APPROVAL_REVALIDATION에서 current authoritative buying power, freshness와 requested notional 비교를 요구한다. 그러나 현행 ORM/0041에는 account cash·buying power projection이 없고 `BrokerAccountSnapshot`도 open orders·fills·positions·observed_at만 제공한다. reconciliation 역시 orders/fills/positions만 영속화한다.
- 따라서 현행 DB만으로 missing/stale/current buying power를 구별하거나 Approval transaction에서 같은 authoritative evidence를 재검증할 수 없다. 이를 닫으려면 0041 이후 additive account-authority snapshot persistence와 broker account snapshot normalization/reconciliation 범위 확장이 필요하지만, Phase 10D 요청은 새 migration, 0041 수정과 Broker 변경을 모두 금지한다.
- 원문의 semantic representation blocker 규칙에 따라 Phase 10D production Python·ORM·migration·test 변경을 시작하지 않았다. 불완전한 Guard를 complete authority로 연결하거나 Risk Policy 금액을 buying power로 대체하지 않았으며 Phase 10C.2 SHADOW 경계는 그대로 유지한다.
- 동시에 확인된 후속 구현 gap은 Approval API request의 필수 `expected_version`/reauth proof 부재, service owner/CAS 미검증, `create_approval()` 내부 commit, sourced APPROVAL_REVALIDATION의 잘못된 legacy subject와 `create_order()` IntegrityError 내부 rollback이다. 이들은 persistence blocker가 해소된 후 Phase 10D 구현에서 함께 닫아야 한다.

### 2026-08-28 Cresta v2 Phase 10C.2 Sourced Decision Execution Orchestrator Foundation 완료

- legacy `route_trading_decision()`과 분리된 server-owned `execute_sourced_entry_decision()`을 추가했다. Phase 9 persisted historical lineage validator를 그대로 재사용하며 current Activation Gate를 재검사하거나 Finalizer transaction/hook에서 실행하지 않는다.
- `entry-execution-identity-v1`의 policy-independent key와 0041 partial unique를 사용해 sourced Decision당 exact-one lifecycle을 lookup-first/create/requery로 처리한다. WAIT/REJECT/UNKNOWN은 stage·execution/risk policy 조회 없이 nullable provenance의 persistent NO_ACTION으로 끝나며 terminal audit도 정확히 한 번 남긴다.
- BUY는 source validation과 expiry를 stage보다 먼저 검사하고 current `V7_ENTRY_EXECUTION_STAGE` exact-one selector의 PASS provenance를 freeze한다. 부재·invalid·ambiguous·expired는 `FAILED_SAFE / EXECUTION_STAGE_UNAVAILABLE`, selector DB failure는 rollback/retry로 분리했다. higher-authority stage는 terminalize하거나 legacy router로 fallback하지 않고 Phase 10D/10E까지 deferred error로 남긴다.
- SHADOW에서 versioned Execution/Risk Policy를 freeze하고 frozen/current action-mode minimum을 적용한다. DISABLED는 Guard 없이 종료하고 MANUAL_APPROVAL/AUTOMATIC은 기존 BUY PRE_ORDER rule helper를 통해 typed `DECISION_EXECUTION` Guard를 저장해 PASS=`SHADOW_RECORDED`, BLOCK=`GUARD_BLOCKED`로 끝낸다. Approval, OrderIntent, TradingOrder와 Broker authority는 모두 0이다.
- deterministic candidate scan 기반 `reconcile_sourced_entry_executions()`는 수동 호출 helper로만 제공한다. startup/scheduler/periodic activation은 연결하지 않았다. Decision/AgentRun은 변경하지 않으며 Decision API nullable execution projection을 보정했다.
- Phase 10C.2 focused 13건, 관련 회귀 185건을 포함한 backend 전체 668건, Ruff와 `git diff --check`가 통과했다. 실제 PostgreSQL concurrent winner/loser, partial unique/typed FK/locking·TOCTOU는 Phase 10G `OPEN_BACKLOG`이며 SQLite 결과로 대체 주장하지 않는다.
- current BUY Guard는 기존 규칙 재사용 범위이고 buying power, active/UNKNOWN order completeness, phase별 frozen/current Risk Policy intersection은 아직 production-complete authority가 아니다. legacy APPROVAL_ONLY automatic direct-order 위험은 변경하지 않았고 Phase 10D 대상이다.

### 2026-08-28 Cresta v2 Phase 10C.1 Execution Persistence / Stage Control-plane Foundation 완료

- additive Alembic `20260828_0041`을 `20260827_0040` 위에 추가했다. sourced DecisionExecution discriminator, policy-independent `v7exe-<sha256>` identity foundation, sourced-only partial unique, nullable NO_ACTION/pre-selection FAILED_SAFE와 frozen stage ID/hash conditional representation을 구현했다. 기존 migration과 legacy execution key/row는 수정·backfill하지 않았다.
- `V7_ENTRY_EXECUTION_STAGE`의 strict `execution-stage-control-v1` Pydantic contract, canonical hash, stage별 structured evidence set, DRAFT→VALIDATED→ACTIVE lifecycle, exact current selector와 `PASS/ABSENT/INVALID/AMBIGUOUS/EXPIRED/DB_RETRYABLE_FAILURE` 분류를 구현했다. SHADOW/APPROVAL_ONLY/MOCK_AUTOMATIC 및 action mode minimum helper를 추가했으며 production ACTIVE stage seed는 없다.
- GuardEvaluation에 nullable StopTrigger FK와 typed subject CHECK를 추가하고 fixed-stop 저장을 실제 `stop_triggers.id` 참조로 교정했다. migration은 유효한 historical STOP_TRIGGER subject만 deterministic하게 옮기며 orphan이면 중단한다. legacy APPROVAL subject branch는 Phase 10D 전까지 보존한다.
- OrderIntent에 exact source enum, typed source/Guard/Approval/policy/stage provenance, stage hash와 partial-unique authority key를 추가했다. Approval의 기존 proof/order 문자열을 실제 RESTRICT FK로 정합화하고 TradingOrder `INVALIDATED` 상태를 추가했다. 신규 semantics가 존재하면 0041 downgrade를 거부한다.
- Phase 10C.1 focused 14건과 backend 전체 655건, Ruff, Alembic head/0040→0041/round-trip/downgrade guard 및 `git diff --check`가 통과했다. 인증·CSRF 기반 stage draft/validate/activate/current/history API와 stage activation 후 execution handoff 0건을 확인했다. SQLite FK-on fixed-stop 회귀를 확인했으며 실제 PostgreSQL DDL/partial unique/FK/concurrent insert는 `OPEN_BACKLOG`이다.
- `alembic check`에서 0041 신규 객체 drift는 발견되지 않았지만 기존 agent_runs basis/fusion unique 표현, emergency_stops FK ondelete, indicator snapshot unique index와 market-context index 이름의 pre-existing metadata drift가 남아 있어 별도 schema-alignment backlog로 기록한다. Phase 10C.1 migration에 unrelated repair를 섞지 않았다.
- Finalizer handoff, sourced execution row 자동 생성/sweep, initial BUY Guard/SHADOW routing, Approval runtime authority, MOCK automatic, fixed-stop stage routing, broker pre-send, scheduler와 LIVE는 구현하거나 활성화하지 않았다. 기존 APPROVAL_ONLY automatic/fixed-stop runtime은 여전히 UNSAFE이며 각각 Phase 10D/10E 대상이다.

### 2026-08-27 Cresta v2 Phase 10B Execution Authority Contract Finalization 완료

- Phase 10A의 semantic gap을 `entry-execution-identity-v1`과 `sourced-entry-execution-v1`로 닫았다. sourced Decision당 policy/stage와 무관한 `v7exe-<sha256>` authoritative lifecycle 정확히 하나, WAIT/REJECT/UNKNOWN persistent NO_ACTION, full Phase 9 lineage validation과 Decision/AgentRun 불변을 확정했다.
- current ExecutionStage는 `SYSTEM / MOCK / V7_ENTRY_EXECUTION_STAGE`의 strict `execution-stage-control-v1` ConfigurationVersion으로 정하고 selected ID/hash/stage를 DecisionExecution에 freeze한다. stage와 action mode는 frozen/current minimum만 적용하며 자동 promotion, missing/invalid default와 LIVE target을 금지했다.
- SHADOW/APPROVAL_ONLY/MOCK_AUTOMATIC×DISABLED/MANUAL_APPROVAL/AUTOMATIC matrix를 확정했다. APPROVAL_ONLY+AUTOMATIC은 `FAILED_SAFE / AUTOMATIC_NOT_ALLOWED_IN_APPROVAL_ONLY`, fixed-stop은 APPROVAL_ONLY에서 synthetic Approval 없이 EXIT_PENDING이며 automatic SELL은 MOCK_AUTOMATIC에만 허용한다.
- Decision expiry와 PAUSE_ENTRY는 BUY broker submission 직전까지 authority이고, pre-send revocation은 unsent Order `INVALIDATED`와 `ORDER_AUTHORITY_REVOKED_BEFORE_SEND` event로 종료한다. SUBMITTING 이후에는 existing UNKNOWN/reconciliation/order lifecycle을 유지한다.
- Guard phase를 PRE_ORDER/APPROVAL_REVALIDATION/BROKER_SEND로 고정하고 full BUY input, frozen/current Risk Policy intersection, DecisionExecution/StopTrigger typed subject를 확정했다. StopTrigger ID를 Guard execution FK에 넣는 현행 misuse는 Phase 10C.1 P0 migration blocker다.
- Approval owner/expected-version CAS/Approval-version-bound one-time APPROVE_ORDER proof, no helper commit, typed OrderIntent source/provenance와 source별 broker validator를 확정했다. migration 전 unclassified CREATED는 source를 추측하지 않고 runtime INVALIDATED로 닫는다.
- EXE-221~260, GRD-098~106, DB-214~230, CFG-112~120, PRD-048~050과 API/SEC/ORD/STM 연계 계약 및 T-V2-EXE-AUTH-001~016 계획을 추가했다. Phase 10B는 SPEC-ONLY이며 production Python·ORM·migration·test code·scheduler·Execution handoff·Approval/Order/Broker 구현은 변경하지 않았다.

### 2026-08-27 Cresta v2 Phase 9E Finalizer / Activation Production Acceptance 및 Phase 9 종료

- production-style SQLite 전체 경로에서 exact ACTIVE+OPEN Gate admission, Gate ID/hash freeze, upstream 7, DecisionContext, C/B/A Provider execution, provider-less ENTRY_ARBITER, live Gate revalidation과 Finalizer를 BUY/WAIT/REJECT/UNKNOWN 네 action으로 종합 재검증했다. 네 action은 exact sourced Decision과 `SUCCEEDED` lifecycle로 보존되고 Gate denial은 Decision action으로 변환되지 않는다.
- no Gate/CLOSED/INVALID admission, actual Policy/Scout·Decision Route/Model/Prompt snapshot mismatch, Gate mid-pipeline isolation과 frozen provenance 불변, Finalizer-time CLOSED/SUPERSEDED/INVALID/DB retryable, source expiry·stage/output/Context/C/B/A corruption, write-boundary expiry/Gate 변화와 identity conflict를 재검증했다.
- Phase 9E acceptance에서 sourced API가 persisted Arbiter row/hash만 검사하고 실제 Context/C/B/A 행을 끝까지 재검증하지 않던 correctness gap을 발견했다. historical completed-at 기준의 lock-free source validator를 API read path에 최소 연결해 current Gate/Policy/Route/Prompt 선택 없이 persisted run→Context→C/B/A→Arbiter chain과 exact immutable Decision을 검증하고 하위 lineage corruption을 fail-closed하도록 수정했다.
- exact seven-field finalization identity의 결정성·lineage sensitivity, retryable failure 뒤 exact-one recovery, opportunistic/idle reconciliation 중복 안전과 ambiguous retry, strict audit taxonomy를 확인했다. `ACTIVATION_GATE_DB_RETRYABLE_FAILURE`는 pre-run admission, `FINALIZATION_DB_RETRYABLE_FAILURE`는 Finalizer non-terminal retry로 분리돼 유지된다.
- sourced WAIT/REJECT/UNKNOWN을 실제 기존 execution router에 전달해 모두 `NO_ACTION`이고 GuardEvaluation·Approval·OrderIntent·TradingOrder가 0임을 검증했다. Finalizer 자체는 DecisionExecution을 만들지 않으며 BUY도 자동 실행되지 않고 Decision execution/config field는 null이다.
- Phase 9E focused 19건, Phase 3~9/migration 0040/Decision API·Execution/legacy 집중 회귀와 backend 전체 641건이 통과했고 전체 Ruff와 `git diff --check`를 통과했다. migration 0040, ORM schema, scheduler, Execution handoff, production Gate seed는 변경하지 않았다.
- local PostgreSQL test DSN·service·Docker CLI·PostgreSQL secret이 없어 PostgreSQL 검증은 `NOT_RUN`이다. Phase 9 application/runtime Finalization·Activation 계층은 CLOSED이며 migration 0039→0040, CHECK/nullability, Gate/run locking, concurrent admission/Finalizer, source partial unique와 TOCTOU는 별도 production validation `OPEN_BACKLOG`로 유지한다.

### 2026-08-27 Cresta v2 Phase 9D Server-owned Decision Finalizer 완료

- 별도 provider-less `decision_finalizer` service에 exact `entry-finalization-identity-v1` canonical builder, `v7fin-` evaluation identity, authoritative TRADING/ENTRY source validator와 immutable `sourced-entry-decision-v1` Decision builder를 구현했다. C/B/A와 ENTRY_ARBITER의 frozen Context·stage·output hash·status/action·policy·validity lineage를 Finalizer boundary에서 다시 검증하며 consensus나 confidence/risk를 재계산하지 않는다.
- Finalizer는 frozen/current Activation Gate를 live evidence와 DB-authoritative time으로 확인하고 insert flush 뒤 source expiry와 Gate를 다시 확인한다. PASS만 Decision을 commit하고 CLOSED/SUPERSEDED는 `CANCELLED`, invalid/source/identity failure는 `FAILED`, DB retryable failure는 `RUNNING`을 유지하며 exact `finalization-audit-v1` AuditLog와 completed_at 규칙을 적용한다.
- lookup-first exact comparator, run/source/evaluation uniqueness와 IntegrityError rollback/relookup으로 same identity retry, ambiguous commit recovery와 concurrent insert loser recovery foundation을 구현했다. Decision insert, success audit와 run `SUCCEEDED` transition은 한 transaction이며 post-flush failure와 write-boundary Gate/expiry 변화는 partial row 없이 rollback한다.
- Arbiter commit과 분리된 동일 reconciliation helper를 opportunistic post-Arbiter 및 idle/crash recovery에 연결했다. DIAGNOSTIC은 Finalizer 대상에서 제외하고 Arbiter action 네 종류의 정상 output은 run `SUCCEEDED`, `CONFLICTED/TIMED_OUT/FAILED`는 exact `ENTRY_ARBITER_*` failure로 별도 종료한다.
- production-style SQLite E2E에서 BUY/WAIT/REJECT/UNKNOWN을 exact Decision action/reason/validity와 legacy nullable field null로 보존하고 sourced list/detail API lineage 및 UNKNOWN round-trip을 확인했다. Finalization 중 LlmInvocation/Provider 추가 0건이며 BUY에서도 DecisionExecution·Approval·OrderIntent·TradingOrder·Broker 0건이다. 기존 Execution no-action 분류에는 `UNKNOWN`만 최소 추가했다.
- Phase 9D focused 26건, Phase 9C.1~9D·Phase 4~8·legacy Decision/Execution 집중 회귀와 backend 전체 622건이 통과했고 전체 Ruff와 `git diff --check`를 통과했다. migration, ORM schema, scheduler, Execution integration은 변경하지 않았고 0040을 유지했다. PostgreSQL migration/locking/exact-one/concurrent insert/TOCTOU/ambiguous-commit 실환경 검증은 Phase 9E backlog다.

### 2026-08-27 Cresta v2 Phase 9C.2 TRADING Admission / Runtime Propagation 완료

- 공개 API와 scheduler에 purpose 선택 surface를 추가하지 않고 internal/server-owned `create_v7_upstream_trading_run`을 구현했다. admission은 `purpose=TRADING` Scout input, 일곱 Scout/C/B/A route snapshot, 세 ACTIVE PolicyProfile map, 현재 exact-one ACTIVE+OPEN Gate를 한 transaction에서 선택하고 upstream 7 stage만 생성한다.
- 실제 선택한 Policy/Route/Model/Prompt와 DAG·Context·Result·Arbiter·consensus contract를 `VersionSnapshot`으로 재구성해 Gate snapshot/hash와 exact 비교한다. Gate 부재/CLOSED/invalid/DB retryable, snapshot mismatch, identity provenance mismatch는 partial input/run/stage 없이 fail-closed하며 strict `finalization-audit-v1` admission audit을 별도 transaction에 남긴다.
- AgentRun에 Gate ConfigurationVersion ID와 full payload hash를 freeze하고 같은 TRADING identity는 exact Gate/Policy/Route일 때만 재사용한다. Gate가 바뀐 같은 identity는 기존 run을 수정하지 않고 `ACTIVATION_GATE_SUPERSEDED`로 거부하며 DIAGNOSTIC input/run identity와 Gate NULL provenance는 분리해 유지한다.
- DecisionContext freeze, frozen Policy resolver, C/B/A materialization·Provider execution, Arbiter reconciliation/execution이 `purpose=TRADING`을 보존하도록 확장했다. admission 뒤에는 frozen Gate 구조/hash만 검증하고 current Gate를 다시 선택하지 않으므로 mid-pipeline supersede·CLOSED 뒤에도 evaluation은 ArbiterResult까지 완료되며 frozen Gate와 semantic inputs는 불변이다.
- production-style SQLite E2E에서 normal/superseded/CLOSED Gate 모두 upstream→Context→C/B/A→ENTRY_ARBITER를 통과했다. Gate는 LLM messages와 Arbiter input에 노출되지 않았고 Decision·DecisionExecution·Approval·OrderIntent·TradingOrder·Broker side effect는 0, run은 Finalizer 대기 `RUNNING` 상태를 유지했다.
- Phase 9C.2 focused 16건, Context promotion regression 포함 35건, Phase 4~9 및 legacy Decision/Execution 집중 회귀가 통과했다. workspace basetemp로 backend 전체 596건, Ruff와 `git diff --check`를 통과했다. migration은 추가·수정하지 않았고 0040을 유지했다. PostgreSQL exact-one/row-lock/TOCTOU/concurrent admission은 미검증이다.
- Decision Finalizer, live finalization Gate revalidation, terminal lifecycle/reconciliation, scheduler, Execution/Approval/Order/Broker 연결은 구현하지 않았으며 Phase 9D로 유지한다.

### 2026-08-27 Cresta v2 Phase 9C.1 Decision Persistence / Activation Gate Foundation 완료

- additive Alembic `20260827_0040`으로 Decision schema version 길이를 32로 확대하고 `UNKNOWN`, exact 아홉 nullable legacy field, nullable execution outcome, legacy/sourced conditional CHECK와 destructive downgrade guard를 구현했다. 기존 0039와 legacy row는 수정·backfill하지 않았다.
- Decision ORM과 server-owned representation validator가 source all-or-none, `sourced-entry-decision-v1`, TRADING/ENTRY/VALID, 네 action, exact null/execution/config 계약을 보존한다. 기존 legacy 생성/API는 non-null Scout/Core representation과 `DecisionResponse`를 유지한다.
- Decision API에 `SourcedEntryDecisionResponse` union과 strict schema/source discriminator를 추가했다. sourced branch는 legacy Scout/Core를 parse하지 않고 canonical Arbiter output에서 source run/stage/hash, Context, consensus pattern과 ordered C/B/A lineage를 read-time resolve한다.
- `activation-gate-v1` exact Pydantic/domain schema, canonical UTC JSON, snapshot/payload SHA-256, full acceptance evidence/artifact/freshness 검증, DB-backed PolicyProfile/Route/Model/Prompt target verifier를 구현했다. ConfigurationVersion `V7_ENTRY_ACTIVATION` DRAFT→VALIDATED→ACTIVE control plane과 ACTIVE+OPEN selector, CLOSED/INVALID/DB_RETRYABLE_FAILURE 분류 및 frozen Gate PASS/SUPERSEDED verifier를 제공한다.
- production Gate/Policy/Route seed, purpose=TRADING admission/run 생성, Gate freeze/propagation, Decision Finalizer, scheduler·Execution·Approval·Order·Broker 연결은 구현하지 않았다.
- SQLite focused/migration 17건, legacy Decision/Configuration 회귀 59건과 전체 backend 580건을 통과했고 Ruff와 `git diff --check`도 통과했다. PostgreSQL migration DDL/CHECK와 concurrent lifecycle은 환경 부재로 미검증이다.

### 2026-08-27 Cresta v2 Phase 9B Activation Gate / Decision Finalizer Contract Finalization 완료

- Phase 9A gap을 바탕으로 `activation-gate-v1`의 exact nine-field payload, nested version snapshot·Policy/일곱 Route map·safety evidence, OPEN/CLOSED·MOCK, null/list/time canonicalization, snapshot/payload SHA-256과 admission freeze/live revalidation을 CFG-104~111로 확정했다.
- `entry-finalization-identity-v1`의 exact seven-field material과 `v7fin-` identity, source run/Context/C/B/A/Arbiter application validation, all-four action preservation, separate reconciliation trigger와 12-step atomic transaction을 AI-276~286, MAO-256~262, DB-205~213으로 확정했다.
- sourced persistence/API discriminator를 `sourced-entry-decision-v1`로 정했다. confidence·aggregate risk·representative model/prompt·legacy Scout/Core·latency·execution/config는 null, Arbiter reason과 `validation_status=VALID`는 authoritative value이며 sentinel을 금지한다. legacy API는 그대로 유지하고 sourced response는 read-time lineage를 사용한다.
- 기존 AgentRun enum으로 DIAGNOSTIC success, TRADING finalization, Gate denial, invalid Gate/source/identity failure와 retryable DB failure의 state/error/completed_at을 고정했다. exact AuditLog action/result와 `finalization-audit-v1` metadata를 사용하며 terminal Decision 부재를 정상적으로 설명한다.
- Phase 9 acceptance를 기존 T-V2-ACT/DB-FIN/DB-GATE/DB-MIG/EXE에 연결하고 canonical Gate, purpose 분리, 네 action/null payload, idempotency/race/expiry/audit/recovery/API/lifecycle/Execution 0건 계획 시험을 추가했다.
- 이 단계는 **SPEC_ONLY**다. production Python, ORM, migration, test code, Activation Gate, TRADING admission, Decision Finalizer, scheduler, Execution integration을 구현하거나 변경하지 않았고 commit/push하지 않았다. 구현은 Phase 9C/9D, production closure는 Phase 9E 범위다.

### 2026-08-26 Cresta v2 Phase 8D ENTRY_ARBITER Production Acceptance 완료

- production-style v7 DIAGNOSTIC 전체 경로를 Market/Input부터 upstream 7 stage, DecisionContext freeze, C/B/A Provider worker execution, Arbiter reconciliation·provider-less worker execution과 canonical `entry-consensus-v1`까지 all-BUY와 mandatory-UNKNOWN으로 재검증했다. fixture가 DecisionAgentResult나 ArbiterResult/hash를 직접 삽입하지 않으며 실제 worker path가 생성한다.
- `T-V2-ARB-017~029`를 추가해 27개 success action truth table, C/B/A 각 role의 다섯 valid non-success 15개 case, structural corruption, pre/post expiry, UNKNOWN 성공과 stage failure 분리, canonical hash, crash recovery, fencing, internal failure, provider-less·authority·Finalizer boundary를 독립 추적했다.
- Context/result identity·hash·status/action·validity 변화는 canonical input/output hash에 반영되고 query/completion order는 semantic consensus를 바꾸지 않는다. confidence·entry/risk score와 Agent reason은 Arbiter input/Result에 없고, current ACTIVE Policy supersession 뒤에도 frozen Result provenance만 사용하며 terminal output JSON/hash가 불변임을 확인했다.
- 동일 run에 DecisionContext 1개, C/B/A stage/result 각 1개, ENTRY_ARBITER 1개가 남고 ordered stage IDs/output hashes/status/actions, Context ID/hash, consensus policy, validity와 Arbiter output hash만으로 Finalizer-ready lineage를 재구성할 수 있다. Arbiter 성공 뒤에도 Decision·Approval·OrderIntent·TradingOrder·Broker·Finalizer·Activation·Execution side effect는 0건이다.
- Phase 8D acceptance 39개, Phase 3B~8D 집중 회귀, v1~v6 Agent/LLM/provider/control-plane 집중 회귀 98개와 backend 전체 571개가 통과했다. 전체 Ruff와 `git diff --check`도 통과했다. 알려진 Starlette `httpx` deprecation warning 1개만 유지된다.
- production correctness gap과 spec deviation은 발견되지 않아 Phase 8D production code 변경은 없다. PostgreSQL 환경 부재로 migration 0039, Context/Policy/admission/C/B/A/Arbiter concurrent materialization·exact-one locking·claim/reclaim/fencing/completion은 미검증 backlog로 유지한다.
- Decision Finalizer, Activation Gate, v7 TRADING, production scheduler migration, Decision·Approval·Order·Broker·Execution 연결과 migration은 추가하지 않았다. ENTRY_ARBITER 계층은 이 범위에서 CLOSED다.

### 2026-08-26 Cresta v2 Phase 8C ENTRY_ARBITER Implementation 완료

- strict `entry-arbiter-input-v1`, normalized C/B/A input item, `entry-consensus-v1`, 여섯 `DecisionPattern`과 pattern/action/server reason 1:1 validation을 구현했다. exact schema는 extra field를 거부하며 canonical C/B/A order, Context ID/hash, stage ID/output hash/status/action, policy와 validity만 보존한다.
- persisted C/B/A `decision-agent-result-v1`을 same-run/context, terminal state, canonical output hash, strict schema, role/type, state/status, frozen Policy provenance와 Context 동일 validity로 다시 검증한다. valid non-success 다섯 상태는 정상 `MANDATORY_UNKNOWN/UNKNOWN` input이고 structural corruption·pre-materialization expiry는 Arbiter stage를 만들지 않는다.
- 별도 arbiter-stage reconciliation이 canonical input/hash로 `ENTRY_ARBITER`를 원자적·멱등 materialize하며 C/B/A 세 role AND dependency를 저장한다. same hash는 stage를 재사용하고 mismatch는 기존 row를 수정하지 않고 conflict로 종료한다.
- v7 logical/materializable/executable 11-role registry에서 ENTRY_ARBITER를 활성화하고 일반 `DEPENDENCY_OK`와 분리된 terminal structured-result eligibility, 기존 worker claim/lease/fencing과 explicit provider-less dispatch를 연결했다. route/invocation/Prompt/Model/Provider/network/web/tool/live/Broker는 모두 사용하지 않는다.
- claim/completion에서 Context·C/B/A identities/hashes/results, input hash, expiry와 null route/invocation을 다시 검증한다. post-materialization mismatch는 output 없는 `CONFLICTED`, expiry는 `TIMED_OUT`, injected evaluator failure는 `FAILED`, stale fencing completion은 write 0건이다. 정상 UNKNOWN consensus도 stage는 `SUCCEEDED`이며 canonical ArbiterResult/output hash를 남긴다.
- Phase 8C 집중 32개, Phase 3B~7E 집중 회귀 125개, v1~v6 Agent/LLM 집중 회귀 68개와 backend 전체 532개가 통과했다. 전체 Ruff와 `git diff --check`를 통과했으며 SQLite에서 production C/B/A→Arbiter BUY 및 structured non-success E2E를 검증했다.
- PostgreSQL Arbiter concurrent reconciliation/materialization/claim/reclaim/fencing과 exact-one locking은 환경 부재로 미검증이다. migration, Finalizer, Activation Gate, v7 TRADING, scheduler, Decision·Approval·Order·Broker 연결은 추가하지 않았다.

### 2026-08-25 Cresta v2 Phase 8B ENTRY_ARBITER Contract Finalization 완료

- `entry-arbiter-input-v1`의 exact Context ID/hash, `consensus-policy-v1`, C/B/A ordered stage ID/output hash/status/action와 validity field 및 canonical JSON/SHA-256 stage input hash를 AI-266~275, DB-197~204로 확정했다.
- `entry-consensus-v1`은 Context ID/hash, ordered `input_result_ids/input_results`, action, policy, 여섯 decision pattern, pattern별 server-owned reason 하나와 Context 동일 `valid_until`만 보존한다. confidence·score·PolicyProfile·Prompt·Model·Provider와 runtime timestamp는 금지했다.
- 유효한 non-success DecisionAgentResult는 정상 `MANDATORY_UNKNOWN/UNKNOWN` consensus input이며 Arbiter stage는 `SUCCEEDED`다. 구조 오류·cross-run/context·materialization 전 만료는 stage를 만들지 않고, 이후 mismatch는 output 없는 `CONFLICTED`, expiry는 `TIMED_OUT`, evaluator internal failure는 `FAILED`로 확정했다.
- C/B/A commit 뒤 별도 arbiter-stage reconciliation, 세 role AND dependency와 Arbiter-specific terminal structured-result eligibility, 기존 worker claim/lease/fencing의 provider-less 전용 dispatch, claim/completion integrity 재검증과 순서 독립 idempotency를 MAO-246~255로 확정했다.
- 기존 `T-V2-ARB-001~004`를 현행 계약으로 갱신하고 `T-V2-ARB-005~016`을 추가해 structural failure, expiry/fencing, dependency, provider-less, authority, lineage와 regression acceptance를 계획했다.
- 이 단계는 명세 전용이다. Python production/test code, migration, Arbiter·Finalizer·Activation·v7 TRADING·scheduler를 구현하거나 변경하지 않았다.

### 2026-08-25 Cresta v2 Phase 7E Three-Agent Production E2E Acceptance 완료

- production-style v7 DIAGNOSTIC 전체 경로를 Market/Input부터 upstream 7 stage, DecisionContext freeze, C/B/A reconciliation·worker Provider execution과 세 `decision-agent-result-v1`까지 재검증했다. fixture가 Decision Agent output을 직접 삽입하지 않으며 실제 worker dispatch가 결과를 생성한다.
- 세 Agent payload의 resolved DecisionContext canonical section과 ID/hash가 동일하고, 각 payload에는 자기 frozen Policy 하나와 자기 Route/Prompt/Model provenance만 존재함을 확인했다. 세 stage의 dependency는 Candidate Audit 하나뿐이며 `A→C→B` 역순 실행에서도 role별 canonical input hash가 변하지 않았다.
- BUY/WAIT/REJECT 네 success 조합, role별 `INSUFFICIENT_DATA/TIMED_OUT/FAILED/INVALID_OUTPUT/CONFLICTED`, 세 mixed-result E2E를 검증했다. 모든 non-success는 `UNKNOWN/0/null`, 모든 권위 terminal stage는 canonical output JSON/hash를 보존한다.
- Phase 7E에서 발견한 correctness gap 하나를 최소 수정했다. invocation 시작 뒤 lease가 만료된 Decision Agent를 일반 recovery가 처리할 때 null output으로 terminalize하던 경로를 `TIMED_OUT/UNKNOWN + DECISION_AGENT_CLAIM_OUTCOME_UNKNOWN` structured result/hash 저장으로 바꿨다. 해당 stage는 재호출되지 않으며 stale completion도 덮어쓰지 못한다.
- model FAILOVER fixture에서 primary 요청 실패 뒤 fallback model 성공을 실제 두 `LlmInvocation`으로 검증했다. Result는 primary requested profile, fallback actual provider/model, `fallback_used=true`를 보존하고 fallback path와 provenance 변화가 canonical hash에 반영된다.
- Context/Scout/EvidenceBundle/Candidate Audit, Policy, Prompt와 stage input corruption, Context expiry, Policy·Route·Prompt supersession, fencing, evidence namespace와 reason allowlist를 재검증했다. 정상 supersession은 frozen run을 바꾸지 않고 실제 corruption만 `CONFLICTED/UNKNOWN`으로 폐기한다.
- 동일 run에서 정확히 C/B/A stage/result 각 1개와 완전한 Context·Policy·Route·Prompt·model lineage/output hash가 남아 후속 Arbiter가 추가 table 없이 결정론적으로 조회할 수 있다. terminal 이후 reconciliation과 다른 Agent 완료는 기존 output JSON/hash를 변경하지 않는다.
- C/B/A all-BUY를 포함한 모든 case에서 ENTRY_ARBITER·Decision·Approval·OrderIntent·TradingOrder·Broker·외부 도구 호출은 0건이다. Arbiter, Finalizer, Activation Gate, v7 TRADING과 scheduler는 구현하지 않았다.
- Phase 7E 집중 21개, Phase 7E+7D 집중 42개, Phase 3B~7E·LLM control-plane/provider·v1~v6 집중 회귀 224개와 backend 전체 500개가 통과했다. Ruff와 `git diff --check`도 통과했다.
- SQLite에서 순차 E2E, transaction-state, recovery/fencing, supersession과 idempotency를 검증했다. PostgreSQL migration 0039, Context/Policy/admission/materialization/claim 동시성, concurrent reclaim/completion과 Provider-call transaction boundary는 계속 미검증 backlog다. Decision Agent layer는 이 범위에서 CLOSED다.

### 2026-08-25 Cresta v2 Phase 7D Decision Agent Execution Runtime 완료

- `CONSERVATIVE_DECISION`, `BALANCED_DECISION`, `AGGRESSIVE_DECISION`을 v7 materializable/executable role로 활성화하고 production worker에서 Scout/Core와 분리된 Decision Agent 전용 dispatch로 연결했다. `ENTRY_ARBITER`는 계속 materialize·claim·execute하지 않는다.
- claim commit 뒤 frozen DecisionContext·자기 PolicyProfile·7-route snapshot·Prompt·requested ModelProfile과 canonical `decision-agent-input-v1`을 준비하고, 별도 짧은 invocation transaction을 commit한 후 row lock과 DB transaction 없이 Provider를 호출한다. 완료는 stage/run 최소 row lock을 다시 획득하는 별도 transaction에서 수행한다.
- Provider request는 canonical Decision Agent input과 `decision-agent-model-output-v1` JSON Schema만 사용하고 tool policy `NONE`, allowed tools 빈 목록으로 고정했다. 요청/실제 provider·model, fallback, usage, latency, raw response hash와 제한된 model output capture를 기존 `LlmInvocation`에 보존한다.
- strict model output/status/action/confidence/score/reason/evidence 검증 뒤 server-owned `decision-agent-result-v1`을 canonical JSON/SHA-256으로 저장한다. timeout은 `TIMED_OUT`, 명확한 Provider failure는 `FAILED`, schema/reason/evidence 위반은 `INVALID_OUTPUT`, frozen provenance 불일치는 `CONFLICTED`이며 모두 `UNKNOWN/0/null` structured result와 output hash를 남긴다.
- completion 직전에 Context canonical hash·same-run·expiry, frozen Policy ID/hash/sequence/role, frozen Route/Prompt/Model과 stage input hash를 재검증한다. Policy 또는 route의 후속 lifecycle supersession 자체는 기존 run을 바꾸지 않으며 Context expiry는 성공 응답을 `TIMED_OUT/UNKNOWN`으로 폐기한다. lease owner·expiry·fencing token이 달라진 stale worker는 invocation/result/stage를 덮어쓰지 않는다.
- C/B/A는 Candidate Audit만 공통 dependency로 가지며 다른 Decision Agent output·Policy·Prompt를 입력에서 읽지 않는다. BUY result를 포함해 Decision·Approval·TradingOrder·Broker·ExecutionStage·Activation Gate·Arbiter resource를 만들지 않는다.
- Phase 7D 집중 16개, Phase 3B~7D·LLM control-plane·v1~v6 집중 회귀 198개와 backend 전체 474개가 통과했다. Ruff와 `git diff --check`도 통과했다.
- SQLite에서 canonical execution, transaction boundary 관찰, structured failure, completion race와 stale fencing write 차단을 검증했다. PostgreSQL migration 0039, Decision Agent claim/fencing 동시성, network-call transaction 분리와 concurrent completion/reclaim은 환경 부재로 미검증 backlog에 유지한다. migration, production seed, scheduler, Arbiter, Finalizer, Activation Gate, v7 TRADING과 주문 실행은 추가하지 않았다.

### 2026-08-25 Cresta v2 Phase 7C Decision Agent Foundation 완료

- `decision-agent-input-v1`, `decision-agent-stage-input-v1`, `decision-agent-model-output-v1`, `decision-agent-result-v1` strict Pydantic/domain 계약과 status/action/confidence/score, verified evidence subset, model/server reason allowlist 검증을 구현했다.
- C/B/A 공통 role↔agent type registry, 여섯 required semantic Policy parameter validator, frozen/SUPERSEDED own-policy resolver와 cross-role fail-closed를 구현했다. 실제 profile별 threshold 값은 production configuration에 남기고 코드에 넣지 않았다.
- schema/Prompt/Route/assignment control-plane이 C/B/A를 지원하며 Decision Agent WEB_SEARCH, Prompt threshold 숫자, role/prompt mismatch를 차단한다. production Prompt/Route/Model row를 seed하지 않았다.
- 신규 v7 admission은 Scout 4개+C/B/A 3개의 정확히 7개 route snapshot을 원자 freeze하고 upstream 7 stage만 만든다. historical 4-route run은 변경하지 않으며 C/B/A provenance가 없으면 materialization 대상이 아니다.
- final logical 11-role registry, Phase 7C materializable 10-role set과 executable upstream 7-role set을 분리했다. C/B/A stage는 committed Context 뒤 별도 reconciliation에서 Candidate Audit 단일 dependency로 원자적·멱등 생성되지만 claim/Provider 실행은 차단되고 ENTRY_ARBITER는 생성·claim·실행되지 않는다.
- immutable DecisionContext resolver, own Policy/Route/Prompt/Model resolver, canonical Provider input/hash와 stage input/hash builder를 구현했다. exact partial retry는 복구하고 stored hash/provenance mismatch는 기존 stage를 수정하지 않고 conflict로 종료한다.
- Phase 7C 집중 12개, Phase 5/6 포함 v7 집중 45개, Phase 3B/3C·LLM control-plane·v1~v6 회귀 98개와 backend 전체 458개가 통과했다. Ruff와 `git diff --check`도 통과했다.
- PostgreSQL migration 0039, DecisionContext/Policy snapshot/v7 admission/C/B/A materialization의 PostgreSQL 동시성은 환경 부재로 계속 미검증이다. 이번 Phase에서 migration, Provider 호출, DecisionAgentResult runtime 저장, completion transaction split, Arbiter, Finalizer, Activation Gate와 거래 resource는 구현하지 않았다.

### 2026-08-25 Cresta v2 Phase 7B Decision Agent Contract Finalization 완료

- AI-256~265, MAO-236~245, DB-191~196으로 `decision-agent-input-v1`, strict PolicyProfile, `decision-agent-stage-input-v1`, model/result 분리, status·score·evidence·reason·failure matrix와 completion revalidation을 확정했다.
- 신규 v7 admission의 일곱 route freeze, historical 네-route run 보존, committed Context 뒤 C/B/A atomic reconciliation, Candidate Audit 직접 dependency와 세 role 병렬 dispatch, claim/network/completion transaction 분리를 명세했다.
- C/B/A는 frozen input만 평가하며 외부 검색·Broker·거래·Arbiter 권한이 없다. Phase 7에서는 Arbiter가 logical registry에만 있고 materialize·execute되지 않는다.
- `TEST_PLAN.md`에 Phase 7C foundation, 7D execution, 7E acceptance/revalidation 계획 시험 `T-V2-DA-*` 22개를 등록해 요청된 26개 acceptance scenario를 개별 또는 table-driven 조합으로 모두 포함했다.
- 이 단계는 명세 전용이다. Python/Pydantic/runtime/API/route/prompt provisioning/migration/test code는 변경하거나 실행하지 않았고 Decision Agent·Arbiter·Finalizer도 실행하지 않았다.

### 2026-08-25 Cresta v2 Phase 6 News / Market / Position Risk Scout v7 Acceptance 완료

- 기존 News·Market·Position Risk의 route resolution, Provider 호출, `AgentScoutModelOutput` schema, reason allowlist, verified evidence subset, UNRATED candidate 저장, server-owned fallback과 `AgentAssessmentV2` normalization을 v7 production worker에서 재사용·재검증했다. 새 Scout runtime, persistence 또는 migration은 추가하지 않았다.
- v7 네 Scout Provider payload에 stage `input_hash`의 정확한 `scout-role-input-v1` material을 공통 전달하고, 역할별 payload/reference는 실제 입력만 남겼다. News에는 Technical indicator·position을, Market에는 indicator·position을 전달하지 않으며 PolicyProfile과 다른 Scout output은 모든 role hash 밖에 유지한다.
- web search는 News와 Market의 명시적으로 활성화된 route에만 허용하고 Technical·Position Risk·Core route에는 admission 방어 검사를 적용했다. 검색 결과는 run의 `UNRATED EvidenceItem` candidate로만 저장하며 현재 immutable EvidenceBundle에 편입하지 않고, verified allowlist 밖 candidate ID·URL을 `evidence_refs`로 반환하면 output을 거부한다.
- Market Scout는 실행 직전 frozen MarketContext의 ID, payload hash, quality와 `valid_until`을 현재 row와 재대조한다. 누락은 기존 server-owned `INSUFFICIENT_DATA/UNKNOWN`, 변조·quality conflict·expiry는 Provider 호출 전 `CONFLICTED/UNKNOWN`과 null score로 fail-closed 한다.
- v7 ENTRY의 Position Risk는 stage를 유지하면서 Provider·web search·추가 외부 조회 없이 explicit `NOT_APPLICABLE/UNKNOWN`, null score와 `OPEN_POSITION_NOT_FOUND`를 기록한다. stage 부재는 Context freeze 실패이며 frozen position provenance 변경은 Position role hash 변경으로 드러난다. Provider가 적용되는 기존 v6 POSITION 경로의 schema 실패도 기존 fail-closed 의미를 유지한다.
- Phase 6의 20개 acceptance ID를 parametrized 집중시험 17개로 구현했다. Phase 3B/3C·4C·5·6 및 기존 Agent Runtime/외부 Provider/v6 회귀 89개와 backend 전체 443개가 통과했다. Ruff와 `git diff --check`도 통과했다. PostgreSQL migration 0039, concurrent Context freeze, PolicyProfile transaction snapshot과 concurrent v7 admission은 환경 부재로 계속 미검증이다.

### 2026-08-25 Cresta v2 Phase 5 Technical Scout v7 Acceptance 완료

- 기존 v6 Technical Scout의 route resolution, Provider invocation, `AgentScoutModelOutput` schema 검증, reason-code allowlist, evidence subset 검증과 `AgentAssessmentV2` server normalization을 v7 production path에서 재사용·재검증했다. Technical Scout는 투자 행동이나 주문 권한을 만들지 않고 entry quality assessment만 기록한다.
- v7 Technical Provider payload에 stage `input_hash`의 정확한 `scout-role-input-v1` material을 포함해 `scout-input-v2`, EvidenceBundle, route/input-contract와 Indicator provenance를 실제 전달 입력에서 직접 재현할 수 있게 했다. PolicyProfile, 다른 Scout output, MarketContext·position 전용 data와 Decision Agent 이후 data는 Technical material에 포함하지 않는다.
- stage 실행 직전에 frozen MarketSnapshot/IndicatorSnapshot identity·hash·calculator provenance를 현재 참조와 다시 대조한다. Indicator 누락은 Provider 호출 없이 `INSUFFICIENT_DATA/UNKNOWN`과 null score, admission 이후 변조·불일치는 `CONFLICTED/UNKNOWN`과 null score로 fail-closed 한다.
- Technical route는 web search를 admission에서 방어적으로 거부하고 `web_search_enabled`를 route version snapshot/hash에 고정해 실행 시 재검증한다. 정상 v7 request는 tool policy `NONE`, allowed tools 빈 목록이며 추가 시세·뉴스·외부 API를 요청하지 않는다.
- Phase 5 집중시험 10개, Phase 4C upstream, Phase 3A~3C, 기존 Agent Runtime/외부 output/worker/v6 Core 회귀와 backend 전체 426개가 통과했다. Ruff와 `git diff --check`도 통과했다. PostgreSQL migration 0039 및 기존 locking/concurrency 항목은 환경 부재로 계속 미검증이다.

### 2026-08-25 Cresta v2 Phase 4C v7 Upstream Runtime 완료

- production `scout-input-v2` builder가 MarketSnapshot, IndicatorSnapshot, 선택적 MarketContextSnapshot과 공통 server configuration provenance를 canonical JSON/hash로 고정한다. Market·indicator·context 유효 경계의 최솟값을 사용하며 필수 source 누락·불일치·만료는 admission 전에 fail-closed 한다. PolicyProfile은 input과 DecisionContext 밖에 유지한다.
- `agent-dag-v7` DIAGNOSTIC/ENTRY 전용 admission이 input, 네 Scout route snapshot, C/B/A PolicyProfile map, AgentRun과 Intel·Verifier·네 Scout·Candidate Audit 7개 stage를 한 transaction에서 생성한다. CORE, C/B/A Decision Agent와 ENTRY_ARBITER stage는 만들거나 실행하지 않고 기존 v1~v6 stage plan·route·Core finalization은 유지한다.
- v7 Scout는 `AgentAssessmentV2`와 server-owned input path를 사용한다. ENTRY에 열린 position이 없으면 Position Risk는 Provider 호출 없이 명시적 `NOT_APPLICABLE/UNKNOWN`을 기록한다. Scout stage input hash는 EvidenceBundle, route, input snapshot과 역할별 indicator/context/position provenance를 포함한 `scout-role-input-v1` material로 claim·execute 시 검증한다.
- v7 Verifier는 configuration snapshot의 `dart_lookback_days`, `krx_lookback_days`, `naver_news_lookback_hours`로 `evidence-freshness-policy-v1` item validity와 최소 `valid_until`을 계산해 `evidence-verifier-v2`를 기록한다. Candidate Audit은 provider-free 기존 로직을 재사용해 `evidence-candidate-audit-v2` provenance와 candidate-set hash를 기록하며 EvidenceBundle을 변경하지 않는다.
- Candidate Audit commit 뒤 별도 worker reconciliation transaction이 eligible run의 DecisionContext를 freeze한다. 같은 manifest retry는 중복을 만들지 않고 conflict는 기존 Context를 보존한 채 fail-closed 한다. Context 완료 후 AgentRun은 `RUNNING` checkpoint를 유지하며 CORE lookup이나 terminal finalization을 수행하지 않는다.
- Phase 4C 집중시험 7개와 Phase 3A~3C·Agent Runtime·worker·v1~v6 finalization 회귀가 통과했다. backend 전체 416개, Ruff와 `git diff --check`가 통과했다. SQLite runtime logic만 검증했으며 PostgreSQL migration 0039, row locking/concurrent freeze, PolicyProfile transaction snapshot과 v7 admission concurrent conflict는 환경 부재로 계속 미검증이다.

### 2026-08-25 Cresta v2 Phase 4B v7 Upstream Runtime 계약 확정

- Phase 4A에서 기존 Intel/Evidence/네 Scout/Candidate Audit과 AgentAssessmentV2는 대부분 재사용 가능하지만 v7 contract registry, production `scout-input-v2`, Verifier/Audit v7 envelope, route set, role input hash, admission·checkpoint·Context reconciliation 연결이 없음을 확인했다. 기존 finalization의 CORE 전제와 4 Scout+CORE route 전제도 v7 gap으로 기록했다.
- Phase 4B는 최종 11-stage `agent-dag-v7`을 유지하면서 Phase 4 실행 범위를 upstream 7 stage와 server-owned DecisionContext Freeze로 한정했다. upstream stage만 materialize하고 C/B/A Decision Agent·ENTRY_ARBITER·CORE를 만들거나 실행하지 않으며 Context 성공 후 AgentRun은 `RUNNING` checkpoint를 유지한다.
- `scout-input-v2`, `evidence-verifier-v2`, `evidence-candidate-audit-v2`, `evidence-freshness-policy-v1`, `scout-role-input-v1`, DAG별 route-required set, atomic diagnostic admission과 Candidate Audit commit 후 별도 reconciliation 계약을 AI-251~255, MAO-221~235, DB-183~190으로 확정했다.
- Evidence freshness는 기존 `dart_lookback_days`, `krx_lookback_days`, `naver_news_lookback_hours`의 versioned configuration snapshot만 사용하며 새 숫자 TTL을 만들지 않는다. usable verified evidence 또는 필수 validity provenance가 없으면 v7 Context는 fail-closed 한다.
- Phase 4B는 명세와 계획 시험만 갱신했다. Python·SQLAlchemy·Alembic·테스트 코드·API/UI·Decision Agent·Arbiter·Finalizer·production scheduler를 변경하거나 구현하지 않았고 runtime 시험은 실행하지 않았다.

### 2026-08-25 Cresta v2 Phase 3C PolicyProfile Admission / Version Freeze 완료

- system-owned ConfigurationVersion category `V7_ENTRY_POLICY_CONSERVATIVE`, `V7_ENTRY_POLICY_BALANCED`, `V7_ENTRY_POLICY_AGGRESSIVE`와 `policy-schema-v1` payload 계약을 구현했다. `scope=SYSTEM`, 첫 target `MOCK`, category↔agent type, canonical payload/hash, ACTIVE lifecycle과 validation/activation timestamp를 fail-closed 검증한다.
- v7 ENTRY `DIAGNOSTIC` admission은 세 ACTIVE profile을 같은 transaction에서 잠금·선택하고 `CONSERVATIVE → BALANCED → AGGRESSIVE` 순서의 `policy-version-map-v1` canonical JSON/hash를 AgentRun 신규 provenance 컬럼에 INSERT 시 함께 고정한다. 누락·중복·schema/payload/type/hash/target 불일치 시 AgentRun을 만들지 않는다.
- 동일 input/DAG/ENTRY slot 재시도는 현재 선택 map이 최초 저장 map과 완전히 같을 때만 기존 run을 반환한다. ACTIVE policy가 교체되면 기존 run을 변경하거나 새 policy로 재해석하지 않고 conflict 처리하며, 새 input identity의 run은 새 ACTIVE map을 사용한다. 저장 ID/hash로 과거 `SUPERSEDED` profile provenance를 검증·복원할 수 있다.
- PolicyProfile map은 DecisionContext manifest/hash와 분리했고 v7 stage·Provider·Arbiter·Finalizer·Activation Gate·TRADING admission·scheduler/API를 구현하지 않았다. 기존 v1~v6 diagnostic과 POSITION advisory admission은 변경하지 않았다.
- Phase 3C 집중시험 18개와 Phase 3A/3B·AgentRun·worker·ConfigurationVersion 관련 회귀 62개가 통과했다. 전체 backend 409개와 Ruff·`git diff --check` 통과를 확인했으며 SQLite selection/version semantics만 검증했다. PostgreSQL migration 0039와 DecisionContext locking/concurrency는 환경 부재로 계속 미검증이고 기존 Alembic drift는 변경하지 않았다.

### 2026-08-25 Cresta v2 Phase 3B DecisionContext Freeze 완료

- `freeze_decision_context(session, run_id)` server-owned transaction이 `agent-dag-v7 + ENTRY + DIAGNOSTIC/TRADING` run을 잠그고 `scout-input-v2`, 같은-run EvidenceBundle·Verifier, 네 Scout, Candidate Audit과 선택적 MarketContext를 DB에서 직접 선택·검증한다. 호출자가 reference ID/hash를 주입하는 API는 만들지 않았다.
- `decision-context-v1` canonical reference manifest는 공용 canonical JSON/SHA-256 helper를 재사용하고 raw input/evidence/stage output과 PolicyProfile map을 복사하지 않는다. run/input/evidence/market/scout 유효시각의 최솟값을 Context `valid_until`으로 고정한다.
- 같은 run과 같은 manifest/hash의 반복 freeze는 기존 Context를 반환하고 다른 material은 `DECISION_CONTEXT_FREEZE_CONFLICT`로 fail-closed 한다. Context update/replacement path와 freeze용 AgentStage/LlmInvocation은 만들지 않았다.
- v7 C/B/A Decision Agent stage claim은 committed·미만료·hash-consistent DecisionContext가 없으면 건너뛴다. v1~v6 claim은 기존 동작을 유지하며 Decision Agent Provider 실행, Arbiter, Finalizer, PolicyProfile selection, Activation Gate와 scheduler migration은 구현하지 않았다.
- Phase 3B 집중시험 14개, Phase 3A persistence 8개와 기존 worker 3개, backend 전체 386개 및 Ruff·`git diff --check`가 통과했다. SQLite는 순차 idempotency/unique/claim 의미를 검증했지만 `FOR UPDATE SKIP LOCKED` 실제 동시성 및 PostgreSQL은 환경 부재로 미검증이다. 기존 Alembic drift는 동일하며 새 drift는 없다.

### 2026-08-25 Cresta v2 Phase 3A Persistence Schema / ORM Foundation 완료

- Alembic `20260825_0039`가 `decision_contexts` 1:1 reference manifest, AgentRun v7 nullable policy/gate provenance와 `TRADING` purpose, 네 v7 stage role, 세 Decision Agent route/prompt role, Decision source run/stage/output hash lineage를 추가했다.
- DB는 Context run unique와 기본 FK, Decision source all-or-none, source run/stage별 partial unique, 역사적 role allowlist와 `ON DELETE RESTRICT` lineage를 강제한다. Context same-run·manifest/hash, v7 admission policy/gate validity와 Finalizer role/run/hash 검증은 Phase 3B service 책임으로 남겼다.
- 기존 v1~v6 Core stage, POSITION `TRADING_ADVISORY` basis/fusion lineage와 legacy Decision은 rewrite/backfill 없이 보존하며 source/provenance 신규 필드는 NULL이다. v7 Context·run·role·route/prompt·source lineage가 존재하면 downgrade를 명시적으로 거부한다.
- Phase 3A 집중시험 8개, backend 전체 372개와 Ruff가 통과했다. SQLite에서 빈 DB 및 legacy fixture upgrade와 upgrade→downgrade→upgrade, destructive downgrade guard를 검증했다. 실제 PostgreSQL migration은 실행 환경 부재로 미검증이며 Phase 3B는 시작하지 않았다.

### 2026-08-25 Cresta v2 Phase 2 Domain/Persistence 계약 확정

- Phase 2A에서 현재 ORM, Agent Runtime persistence, ENTRY/POSITION/POSITION advisory Decision lineage와 migration 이력을 역설계했다.
- Phase 2B에서 새 DecisionRun·EvidenceSet을 만들지 않고 기존 AgentRun을 v7 evaluation root로 확장하며, AgentRun당 하나의 별도 immutable `decision_contexts` reference manifest를 두는 mapping을 결정했다.
- Phase 2C에서 `docs/DATABASE_SPEC.md` DB-157~182를 persistence 단일 기준으로 확정했다. DecisionAgentResult와 ArbiterResult는 기존 AgentStageRun output을 사용하고 PolicyProfile 3종과 Activation Gate는 system-owned ConfigurationVersion을 재사용한다.
- finalized v7 ENTRY Decision은 기존 Decision에 nullable source AgentRun·exact ENTRY_ARBITER stage/output hash lineage를 추가하고 기존 `evaluation_request_id` unique를 finalization idempotency로 재사용하도록 확정했다.
- v7 최초 slice는 admission부터 `DIAGNOSTIC`이고 ArbiterResult에서 종료한다. activation 이후 production run만 admission부터 `TRADING`이며 DIAGNOSTIC 결과 승격은 금지한다. Activation Gate와 ExecutionStage는 독립이다.
- Phase 2는 문서·계약만 완료했다. Python·SQLAlchemy·Alembic·DB·API/UI·테스트 코드는 변경하지 않았고 migration도 생성하지 않았으며 runtime은 검증하지 않았다. Phase 3 persistence 기반 구현을 대기한다.

### 2026-08-25 Cresta v2 ENTRY 의사결정 아키텍처 Phase 1 문서 정합성 보완

- 2026-08-23 Phase 0 정적 역설계를 바탕으로 작성한 Phase 1 명세의 신규 ENTRY 목표 구조를 `Scout → shared immutable DecisionContext → Conservative/Balanced/Aggressive Decision Agent → Deterministic Arbiter → ArbiterResult → Decision Finalizer → purpose=TRADING, decision_kind=ENTRY Decision → 기존 Execution Orchestrator`로 정합화했다.
- v7 최초 구현은 `SHADOW/DIAGNOSTIC`이며 `ArbiterResult`에서 종료한다. Decision Finalizer를 통한 TRADING Decision 생성은 별도 activation gate 이후에만 허용한다.
- 현재 ENTRY 신규매수 판단은 외부 LLM Agent가 아니라 `deterministic-mock-v2`가 생성하며, 기존 외부 ENTRY Agent run은 `DIAGNOSTIC/SHADOW`로 실제 BUY에 사용되지 않는다는 현행 상태를 유지한다.
- Cresta v2 1차 전환은 ENTRY 판단에 한정한다. 기존 POSITION의 `deterministic-position-v1`, `TRADING_ADVISORY`, `position-agent-fusion-v1` 흐름은 유지한다.
- 기존 `agent-dag-v1`~`agent-dag-v6`, Core 계약과 과거 구현·시험 기록은 소급 변경하지 않는다.
- Phase 1은 명세와 계획 시험 작성만 완료했다. 코드·DB migration·API/UI 변경과 runtime 검증은 수행하지 않았다.
- Phase 0에서 확인된 ExecutionStage 우선순위, 승인 후 stage downgrade, Broker 송신 직전 stage/provenance 재검사, 진단 주문 분리와 FIXED_STOP stage 우회 결함은 EXE-200~213과 계획 회귀시험으로 명문화했으며 아직 수정·검증되지 않았다.
- 단, Phase 0에서 APPROVAL_ONLY+AUTOMATIC 우회, APPROVAL_ONLY의 FIXED_STOP 자동 주문, stage downgrade 후 Approval/CREATED 주문 재검사 누락을 확인했으며 EXE-200~213 기준으로 재설계·회귀수정 예정이다.

### 2026-08-22 Console 상단 키움 상태 일치

- Console 상단의 `Paper Gate` 표시를 제거하고 시스템 상태 화면과 동일한 인증 Broker endpoint에서 `KIWOOM_MOCK_PRIMARY` worker·gate 상태를 조회하도록 통일했다. 연결된 환경에서 legacy Paper `STARTING`이 키움 gate 상태처럼 보이던 불일치를 제거했다.
- Broker 조회 실패는 `UNKNOWN`, 키움 미설정은 `NOT_CONFIGURED`로 구분하며 READY·대기·위험 상태에 맞는 상태점을 표시한다.
- 상태 READY·NOT_CONFIGURED·UNKNOWN과 인증·Broker 회귀를 묶은 집중 component 시험 5개, TypeScript와 production build가 통과했다. Console 전체 19개 중 이번 변경 관련 18개가 통과하고 기존 운영 휴장 비동기 시험 1개만 실패했다.

### 2026-08-18 키움 주문 거절 진단 정보 보존

- 키움 주문·취소 API가 HTTP 200과 업무 `return_code != 0`으로 명시적 거절을 반환하면 Adapter가 정규화된 결과 코드와 안전하게 정제된 사유를 전달하고, 주문 송신기가 기존 append-only `order_events.payload_json`에 두 필드만 보존한다. 응답 원문·계좌번호·토큰·자격증명은 저장하지 않는다.
- 신규 주문 거절은 기존대로 `REJECTED`, 취소 거절은 수량을 바꾸지 않고 `RECONCILING`과 닫힌 거래 gate를 유지한다. 진단 metadata는 상태 전이·재송신 판단을 바꾸지 않으며 응답 유실과 통신 오류에는 Broker 결과를 추정하지 않는다.
- 인증된 주문 상세 API는 nullable `broker_result_code`·`broker_result_message`만 다시 정제해 반환하고 Console은 결과가 존재하는 거절 이벤트 아래에만 표시한다. 기존 이벤트 JSON을 활용하므로 DB migration은 없다.
- Adapter·주문 송신기·주문 API 집중시험 51개, backend 전체 364개, Ruff, Console 집중시험 3개, TypeScript와 production build가 통과했다. Frontend 전체 16개 중 기존 운영 휴장 비동기 시험 1개만 실패하고 15개가 통과했다. 2026-08-22 Ubuntu 기능 브랜치에 `186b25a`를 배포해 서버 이미지 집중시험 51개, 전체 Compose health, Worker `READY`, 인증된 주문 상세와 과거 거절 이벤트의 빈 metadata 비표시를 확인했다. 실제 신규 키움 모의투자 거절의 안전한 코드·사유 영속과 표시만 다음 장중 검증으로 남긴다.

### 2026-08-18 AI 판단 Console 정보구조 개편

- AI 판단 화면을 `운영 판단`, `자동 포지션 분석`, `수동 진단`, `전체 이력` 네 탭으로 분리했다. 실제 TRADING Decision과 승인, scheduler 소유 `TRADING_ADVISORY`, 수동 Agent/Mock DIAGNOSTIC이 기본 화면에서 서로 섞이지 않는다.
- Decision과 Agent run은 최신순 요약 행으로 표시하고 처음 12개만 렌더링한다. `더 보기`는 12개씩 확장하며 전체 reason, DAG stage, provider 호출과 구조화 응답은 선택한 요약 행 바로 아래의 단일 인라인 상세 영역에서만 렌더링한다.
- `TRADING_ADVISORY`는 Console에서 `자동 포지션 분석`으로 표시하되 상세에는 원본 목적, `position-agent-fusion-v1`, fusion state/reason/결합 판단 ID를 유지한다. `ESCALATED`는 주문 성공이 아님을 탭 안내에 고정했다.
- 관련 component 시험 4개와 TypeScript·production build가 통과했다. 전체 component 16개 중 기존 운영 휴장 비동기 시험 1개만 실패하고 이번 변경 관련 15개는 통과했다. Ubuntu Console에는 `186b25a`까지 배포했으며 Compose health와 내부 root/healthz가 정상이다. 2026-08-22 인증된 실제 데이터에서 운영 Decision과 자동 포지션 Agent run 상세가 선택한 요약 행의 바로 다음 `ARTICLE.decision-detail.inline`으로 열리는 것을 확인했다.

### 2026-08-17 외부 Agent POSITION 판단 안전 결합 1차

- scheduler는 열린 포지션의 결정론적 `TRADING/POSITION` 판단을 먼저 기존 실행 권한·Guard 계층에 전달한 뒤, 5개 ACTIVE SHADOW route가 모두 준비된 경우에만 그 판단을 basis로 갖는 별도 `TRADING_ADVISORY` Agent run을 생성한다. 수동 `DIAGNOSTIC` run은 계속 주문·승인과 완전히 격리된다.
- 외부 Core 출력은 직접 행동 코드나 주문수량을 만들지 않는다. 서버 소유 `position-agent-fusion-v1`이 동일 사용자·종목·시장·시세 snapshot·포지션 canonical hash와 position version, basis 유효시간, 필수 Scout/Core 성공을 재검증한 뒤 `EXIT_RISK_ELEVATED`를 최소 `PARTIAL_SELL(0.5)`, `EXIT_RISK_HIGH`를 `FULL_SELL`로만 상향할 수 있다. 신뢰도 기준은 0.70이며 `HOLD_SUPPORTIVE/NEUTRAL`, 낮은 신뢰도와 기존 판단보다 약한 결과는 원 판단을 유지한다.
- 결합 결과는 원 판단을 수정하지 않고 `CRESTA_FUSION / position-agent-fusion-v1`의 새 불변 TRADING Decision으로 기록된다. 이후에도 행동별 `DISABLED/MANUAL_APPROVAL/AUTOMATIC`, 전체 Guard, 승인과 공통 Order Creation Service를 반드시 거친다. Agent 실패·timeout·schema 오류·필수 역할 누락·만료·포지션 version 변경은 `FAILED_SAFE/EXPIRED/NO_ESCALATION`으로 기록하며 결정론적 판단과 고정손절에는 영향을 주지 않는다.
- migration `20260817_0038`에 basis/fusion provenance와 상태 제약을 추가했다. 로컬 집중시험 14개, backend 전체 364개와 Ruff, SQLite migration `upgrade→downgrade→upgrade`가 통과했다. Frontend TypeScript·production build는 통과했고 component 전체 15개 중 이번 변경과 무관한 기존 운영 휴장 비동기 시험 1개가 실패해 14개가 통과했다. Ubuntu PostgreSQL 적용, 실제 외부 Provider POSITION advisory와 키움 모의 SELL 송신은 아직 미검증이다.

### 2026-08-17 보유 포지션 정기 판단 1차

- scheduler가 같은 종목의 `OPEN` 포지션을 발견하면 ENTRY 대신 `decision_kind=POSITION` 판단을 생성한다. 단일계좌·단일사용자 MVP에서는 감시 목록에서 해제된 열린 포지션도 KRX 분석 대상으로 유지하며, 활성 사용자가 둘 이상이면 계좌 포지션을 임의 사용자에게 귀속하지 않는다.
- `scout-input-v1`의 기존 position 영역에 ID·version·수량·평균단가·현재가·미실현손익·고정손절 거리·고점 낙폭과 Risk Policy provenance를 canonical JSON으로 고정했다. 같은 슬롯의 ENTRY와 POSITION은 별도 evaluation request ID를 사용하고 반복 tick은 최초 판단을 유지한다.
- 서버 소유 `deterministic-position-v1 / position-policy-v1`은 정상 입력에서 고정 가중치 exit risk를 계산해 70점 미만 `HOLD`, 70~89점 `PARTIAL_SELL(0.5)`, 고정손절 도달 또는 90점 이상 `FULL_SELL`을 생성한다. 데이터 부족·stale·지표 누락은 `HOLD/DATA_INSUFFICIENT`로 축소하며 외부 Provider DIAGNOSTIC 결과는 TRADING으로 승격하지 않는다.
- 새 판단은 기존 행동별 실행 권한·Guard·승인/자동 SELL 계층으로만 전달되고 주문수량은 최신 Cresta 관리수량에서 다시 계산된다. 로컬 집중시험 34개, backend 전체 358개와 Ruff가 통과했다. DB migration은 없으며 실제 scheduler 연속운전·키움 모의 SELL 접수는 두 단계 묶음 배포 때 검증한다.

### 2026-08-15 판단 기반 부분·전량매도 1차 연결

- TRADING `PARTIAL_SELL`과 `FULL_SELL`을 행동별 `DISABLED / MANUAL_APPROVAL / AUTOMATIC` 정책에 연결했다. 부분매도는 `floor(매도가능 관리수량 × sell_ratio)`, 전량매도는 예약수량을 제외한 매도가능 관리수량을 사용하며 1주 미만·순수 외부 포지션·활성/불명 주문은 fail-closed로 차단한다.
- 매도 승인은 판단 snapshot의 매수 1호가와 position ID/version·정확한 수량을 고정하고, 승인 직전에 최신 stream·가격편차·position version·매도가능 관리수량·거래세션·gate를 다시 검사한다. 통과한 주문은 `SELL / LIMIT / 최우선 매수호가`로 생성하며 임의 재호가나 시장가 fallback은 하지 않는다.
- 자동형과 승인형 모두 공통 Order Creation Service를 사용하며 worker만 실제 Broker 송신을 소유한다. Console 승인 카드·확인창은 BUY 고정 표현을 제거하고 `PARTIAL_SELL`·`FULL_SELL`과 Cresta 관리수량 제한을 표시한다.
- 이번 단계는 이미 생성된 TRADING 매도 판단을 안전하게 실행하는 계층까지다. 현행 scheduler의 결정론적 판단은 ENTRY 중심이므로, 실제 보유 포지션을 주기 분석해 `PARTIAL_SELL`·`FULL_SELL` 판단을 생성하는 POSITION 파이프라인은 다음 핵심 단계로 남는다.
- 신규 매도 집중시험을 포함한 관련 backend 38개 시험과 Ruff, Frontend TypeScript·production build·신규 SELL 승인 component 시험이 통과했다. DB 변경은 없다. 전체 backend 실행은 Windows pytest 임시 디렉터리 권한 문제를 우회해 구간 검증 중이다. Frontend 전체 15개 중 이번 변경과 무관한 기존 운영 휴장 비동기 시험 1개는 계속 실패하고 14개가 통과했다. 실제 장중 키움 SELL 접수·부분체결·취소 경쟁은 미검증이다.

### 2026-08-14 신규매수 미체결 잔량 자동취소 1차

- 승인형·자동 `BUY` 주문은 `CANCEL / 10초 / 시장가 전환 없음` 정책을 주문 row에 영속하고, Broker 접수 시 `next_action_at`을 계산한다. worker는 만료된 주문을 한 건씩 잠근 뒤 `CANCEL_PENDING`을 commit하고 실제 남은 수량만 `kt10003`으로 한 번 요청한다.
- 정상 취소 응답도 완료로 오인하지 않고 `CANCEL_PENDING`을 유지한다. 다음 키움 계좌 snapshot에서 원주문이 미체결 목록에서 사라진 경우에만 늦은 체결을 먼저 반영하고 나머지를 `CANCELLED`로 확정한다. 응답 유실은 `UNKNOWN`, 명시적 거절은 `RECONCILING`으로 보존하고 거래 gate를 닫는다.
- 주문 조회 API와 Console에 미체결 정책·timeout·재호가 횟수·다음 자동처리 시각을 추가했다. 손절·일반매도 재호가와 시장가 fallback, 종목 board·상품구분 기반 호가단위 보정은 아직 열지 않았다.
- migration `20260814_0037` upgrade→downgrade→upgrade, 주문 송신·취소·부분체결·reconciliation 집중시험 43개, backend 전체 344개, Ruff, Frontend TypeScript·production build·집중 component 시험이 통과했다. 전체 회귀에서 발견한 기존 LLM fallback 호출 표시 순서도 primary→fallback으로 결정론화했다. Frontend 전체 14개 중 이번 변경과 무관한 기존 운영 휴장 비동기 시험 1개는 계속 실패하고 13개가 통과했다. 2026-08-15 Ubuntu PostgreSQL에 `0037`을 적용하고 SHADOW 격리 집중시험, 주문 API 계약, worker `READY`를 확인했다. 기존 주문 3건은 migration 안전 기본값인 `NONE/0`으로 보존됐다. 장중 키움 `kt10003` 취소·부분체결 경쟁 인수시험은 미수행 상태다.

### 2026-08-14 Broker 권위 수량과 Cresta 관리수량 분리

- 키움 snapshot의 `quantity`, `available_quantity`, `average_price`를 계좌의 최종 사실로 유지하면서, `BROKER_IMPORTED`가 아닌 Cresta 주문의 확정 체결만 재생해 `managed_quantity`와 `managed_average_price`를 별도로 계산한다. 총수량 중 관리수량이 0이면 `EXTERNAL`, 전부면 `CRESTA_MANAGED`, 일부면 `MIXED`로 분류하고 반복 reconciliation에서도 같은 결과를 낸다.
- FIXED_STOP은 Broker 전체 평균단가가 아니라 Cresta 관리평균단가로 발화 가격을 계산하며, 자동 SELL 수량은 `min(managed_quantity, available_quantity)`를 넘지 않는다. 따라서 `MIXED`의 외부 보유분과 순수 `EXTERNAL` 포지션은 자동 매도하지 않는다.
- `/positions`와 Console은 총수량·매도가능수량·Broker 평균단가·Cresta 관리수량·관리평균단가·외부수량을 함께 표시한다. Paper 체결은 전량 `CRESTA_MANAGED`로 유지한다.
- migration `20260814_0036` upgrade→downgrade→upgrade, backend 전체 337개 회귀, Ruff, Frontend TypeScript·집중 component 시험·production build가 통과했다. Frontend 전체 14개 중 기존 운영 휴장 비동기 시험 1개는 이번 변경과 무관한 timeout으로 남아 있다. 2026-08-15 Ubuntu PostgreSQL 적용과 키움 snapshot 재대조 후 `005930` 1주가 `managed_quantity=1`, `external_quantity=0`, `CRESTA_MANAGED`로 결정론적으로 재분류됐고 포지션 API 계약과 관련 집중시험이 통과했다.

### 2026-08-14 키움 projection Console 조회 보정

- `/positions`와 대시보드 집계가 legacy `PAPER`에만 고정되어 키움 기준 `KIWOOM_MOCK_PRIMARY` 포지션을 숨기던 조회 경계를 수정했다. MOCK Console은 두 원장을 명시적으로 포함하며 포지션 관리 출처를 화면에 표시한다.
- backend 포지션·reconciliation 집중시험 20개, Ruff, 포지션 UI 집중시험과 TypeScript 검사가 통과했다. 전체 Frontend 14개 중 기존 운영 휴장 비동기 시험 1개는 이번 변경과 무관하게 timeout됐다. Ubuntu 서버에 `5e1e04e`를 배포해 API·Frontend·Nginx health와 worker `READY`, DB의 `KIWOOM_MOCK_PRIMARY / 005930 / 1주 / 269000원 / OPEN / EXTERNAL` projection을 확인했다.

### 2026-08-14 키움 Broker 기준 계좌 projection

- 키움 모의·실거래 계좌에서는 키움 account snapshot을 주문·체결·포지션의 최종 사실로 삼고, Cresta DB를 운영용 projection으로 갱신하는 계층을 추가했다. Broker-only open order는 `BROKER_IMPORTED` intent와 함께 가져오고, Broker에 없어진 position row는 `CLOSED`로 전환한다. 관리수량과 origin의 현행 계산은 위 2026-08-14 후속 항목을 따른다.
- 확정 체결은 결정론적 key로 멱등 저장한다. 전량체결은 주문을 `FILLED`로 종료하지만, 부분체결 후 open order에서 사라진 경우는 취소·거절을 추측하지 않고 `RECONCILING/HALTED`를 유지한다. 주문수량 초과 체결도 로컬 수량 불변식을 훼손하지 않고 mismatch로 차단한다.
- projection과 같은 snapshot의 재비교, position/order 감사 event, 과거 mismatch의 `RESOLVED` 전환을 같은 transaction 경계에서 처리한다. projection 실패 시 부분 변경을 rollback하고 gate를 `DEGRADED/RECONCILIATION_FAILED`로 닫는다.
- reconciliation 집중시험 14개, backend 전체 334개 시험, Ruff와 `git diff --check`가 로컬에서 통과했다. 2026-08-14 Ubuntu 모의투자 서버에 `9244edc`를 적용해 기존 주문 `0087482`를 `FILLED(1/1)`로 확정하고 키움 보유 1주를 `EXTERNAL` position으로 생성했으며, 기존 세 mismatch 유형을 모두 `RESOLVED`로 전환해 OPEN mismatch 0건과 worker `READY`를 확인했다. 첫 재기동 중 세 run은 원인 세부정보 없이 `FAILED` 후 자동 재연결됐지만 이후 startup·periodic 대조는 projection 변경 0건과 mismatch 0건으로 연속 성공했다. 실패 상세 원인 영속·관측 보강은 후속 운영 과제다.

### 2026-08-14 승인 시점 최신 snapshot 재검사 경쟁 해소

- 판단 snapshot은 승인 기준가격·수량을 고정하는 불변 reference로 유지하고, 승인 transaction은 `market_stream_states` 행을 잠근 뒤 승인 시점 최신 snapshot으로 freshness·품질·spread·가격편차를 다시 검사한다. 정상적인 stream 전진과 snapshot ID 변경만으로 승인을 무효화하지 않는다.
- 최신 가격이 허용편차 안이면 승인 당시 수량을 늘리지 않고 최신 매도 1호가로 주문한다. stale·비정상 품질·가격편차 초과는 fail-closed로 승인과 execution을 무효화하며 주문을 생성하지 않는다.
- 승인 Guard evaluation과 승인 성공·무효화 감사 로그에 reference snapshot ID와 승인 시점 snapshot ID를 분리 기록한다. 집중시험 40개, backend 전체 328개 시험과 Ruff·`git diff --check`가 통과했다.

### 2026-08-13 장중 인수시험 근거 정리와 Risk Guard 테스트 결정론 보강

- Ubuntu 모의투자 장중 시험 결과를 구현 완료로 과장하지 않고 `통과/부분 통과/미검증`으로 재분류했다. 전체 BUY Guard 위험 주입과 SHADOW 회귀는 통과했고, 승인형 BUY는 키움 업무 거절까지 도달했으며, 실제 FIXED_STOP 발화·SELL 송신·체결은 미검증으로 기록했다.
- 당시 키움 BUY 거절 원인은 안전한 업무 오류 코드·사유가 영속되지 않아 미확인으로 정정했다. 후속 2026-08-18 진단 metadata 구현 이후 발생하는 명시적 거절부터 안전한 코드·사유를 보존하며, 과거 원인을 추정하지 않는다.
- Risk Guard 통합시험 8개가 고정 fixture 시각을 만들고도 실행 함수에는 시스템 현재 시각을 사용하던 문제를 수정했다. 모든 호출에 동일한 `NOW`를 주입해 만료·stale 부수 차단이 목표 규칙 시험을 거짓 양성으로 만들지 않게 했다.
- 현재 backend 전체 325개 시험과 Ruff가 통과했고, 승인·주문 생성·Risk Guard·고정손절 집중시험 37개도 통과했다.

### 2026-08-13 전체 Risk Guard 보강 (milestone #2)

- BUY Guard에 명세 `GUARD_RISK_SPEC`의 4가지 위험을 모두 추가했다: 일일 손실(REALIZED_PLUS_UNREALIZED 기본, REALIZED_ONLY 선택), 종목/전체 노출 한도, 일일 진입 횟수, 연속 손실 횟수, spread 한도, 연결 위험(BrokerWorkerState READY + WebSocket + heartbeat + gate READY), 활성 일일손실 risk_event 차단. 차단 시 `risk_events` 원장에 scope별(DAILY_LOSS/EXPOSURE/SPREAD/CONNECTION) ACTIVE row를 영속한다(GRD-080).
- 일일 손실 한도 도달 시 `ENTRY_HALT`(계좌 전체 신규매수 중지)로 `risk_events` ACTIVE row가 영속해 재시작 후에도 유지되며, 회복 전까지 BUY를 막는(`NO_ACTIVE_DAILY_LOSS_EVENT` 규칙). 매도·손절에는 신규매수 한도를 차단 사유로 쓰지 않는다(GRD-083).
- `RiskPolicyPayload`에 `daily_loss_limit_pct`(0.1~20%, 기본 5), `daily_loss_basis`(REALIZED_ONLY/REALIZED_PLUS_UNREALIZED, 기본 후자), `max_consecutive_losses`(1~10, 기본 3) 필드를 추가하고 안전 기본값·검증·활성화에 반영했다. 기존 활성 정책의 payload는 그대로 두고 누락 필드는 기본값으로 채운다(migration `20260813_0035`는 payload-only no-op).
- 공통 risk 계산 서비스 `app/risk_calc.py`: 일일 실현 손실(당일 SELL Fill), 미실현 손실(OPEN position 최신가), 일일 손실 %, per-symbol/전체 노출, 일일 진입 횟수, 연속 손실 횟수, spread, broker 연결 상태를 읽기 전용으로 계산한다.
- `guard.py`의 `persist_guard_evaluation`에 `phase` 파라미터를 추가해 APPROVAL_CREATION/PRE_ORDER/BROKER_SEND 단계를 구분한다(GRD-080). Console RiskPolicyPanel에 일일 손실 한도·기준·연속 손실 입력을 추가했다.
- 구현 시점 backend 전체 회귀 325개 통과(신규 20: risk_calc 11, risk guard 통합 8, risk policy 필드 1), Ruff lint 통과, migration `20260813_0035` 왕복 통과, Frontend TypeScript·14개 component 시험·production build 통과. Ubuntu PostgreSQL에도 `20260813_0035`를 적용했고 2026-08-13 장중 모의투자에서 clean Guard 통과, spread·종목/전체 노출·일일 진입 횟수·일일 손실·Broker 연결 위험의 차단과 회복을 확인했다. 일일 손실은 `REALIZED_PLUS_UNREALIZED`에서 차단되고 같은 입력이 `REALIZED_ONLY`에서 통과하는 분기도 확인했다.

### 2026-08-13 승인형 BUY 주문 + FIXED_STOP SELL 주문 연결 (milestone)

- 진입(BUY)과 손절 청산(FIXED_STOP SELL)을 한 쌍으로 열어 포지션이 무방비 상태로 남지 않게 했다. 두 경로는 공통 **Order Creation Service**(`app/order_creation.py`)를 공유하며, 생성된 `CREATED` 주문은 기존 Broker worker FIFO 송신기가 자동으로 키움 모의투자로 보낸다.
- 승인형 BUY: `MANUAL_APPROVAL` 모드에서 Guard 통과 시 `Approval(PENDING)`을 만들고 주문은 생성하지 않는다. 유저가 승인하면 Guard·가격편차(`max_price_deviation_pct`)·position version을 재검사 후 `OrderIntent`+`TradingOrder(CREATED)`를 원자 생성한다. `AUTOMATIC` 모드는 승인 없이 직접 생성한다. 단, `execution_stage=SHADOW`에서는 여전 주문·승인 0건이고, `APPROVAL_ONLY`에서만 생성된다(기본값 `SHADOW`).
- FIXED_STOP SELL 자동: 손절 trigger가 `SHADOW_RECORDED`에서 `FULFILLED`로 전환되며 매도 `TradingOrder(CREATED)`를 만든다. `execution_policy.fixed_stop_loss` 기본값이 `AUTOMATIC`이므로 `APPROVAL_ONLY`에서도 승인 없이 자동 발화한다. 현행 대상과 수량은 origin 문자열이 아니라 `managed_quantity`로 제한하며 외부 보유분은 자동 주문하지 않는다.
- 가격 산정은 이 milestone에서 간소화했다: BUY는 MARKETABLE_LIMIT(매도 1호가), 수량은 `entry_order_amount / 가격` 정수다. 호가단위 보정은 후속이며, 전체 Risk Guard(일일손실·spread·연결위험·전체 노출)는 후속 milestone #2에서 구현됐다.
- 승인 API: `GET/POST /api/v1/approvals` (목록·상세·승인·거절, `require_csrf` + `Idempotency-Key`, TOTP 재인증 없음). Console DecisionsPage에 승인 카드·confirm-modal 추가.
- 구현 시점 backend 전체 회귀 305개 통과(신규 16: order creation 4, approvals 7, stop trigger SELL 3, position provenance 2), Ruff lint 통과, migration `20260813_0034` upgrade→downgrade→upgrade 왕복 통과. Frontend TypeScript·14개 component 시험·production build 통과. 2026-08-13 Ubuntu 모의투자에서 `Approval(PENDING→APPROVED)`·BUY `CREATED→VALIDATING→SUBMITTING→REJECTED`까지 확인했다. 당시에는 키움의 정확한 업무 거절 코드·사유가 영속되지 않았으므로 호가단위 문제로 확정하지 않았다. 이 공백은 2026-08-18 후속 구현으로 보완했지만 과거 이벤트는 소급 추정하지 않는다. 실제 FIXED_STOP 가격 도달 후 SELL 송신·체결은 미검증이다. 당시 확인한 stream 최신 snapshot과 판단 snapshot 간 경쟁 조건은 2026-08-14 최신 snapshot 재검사 분리로 수정했다.

### 2026-08-12 고정 손절 trigger SHADOW 구현

- 평균 매입가와 활성 `RISK_POLICY`의 `fixed_stop_loss_pct`로 손절가를 계산하고 최신 정상 KRX 매수호가가 도달하면 발화하는 고정 손절 trigger를 구현했다. Core 판단이 아닌 결정론적 규칙 trigger로 `Decision`을 만들지 않고 `StopTrigger` 독립 상태머신으로 관리한다.
- trigger는 position version·risk policy version에 결합해 idempotency unique 제약으로 중복 생성을 막고, position version 변경 시 기존 활성 trigger를 `SUPERSEDED`로 전환한다. 가용성 차단(BROKER/재동기화/활성주문/stale 시세/세션)은 `EXIT_PENDING` + `risk_events` ACTIVE row로 영속해 데이터 단절과 재시작 후에도 신호를 지운다.
- `risk_events`는 범용 위험 원장(`scope`로 손절·일일손실·spread·연결위험 구분)으로 도입했고, 고정손절 차단 기록을 먼저 채운다. `recover_exit_pending`이 gate READY 후 매 tick 재평가해 통과 시 `SHADOW_RECORDED`로, risk_event를 `RESOLVED`로 전환한다.
- 현재 `SHADOW` 단계이므로 trigger는 평가·기록만 하고 `OrderIntent`·`TradingOrder`·`Decision`·`Approval` 0건을 유지한다. 매도 Guard는 `PAUSE_ENTRY`로 차단하지 않으며(신규매수 전용), `ENVIRONMENT_NOT_MOCK`을 검사한다. Broker worker 루프가 10초 간격으로 trigger runner를 try/except 격리해 호출한다.
- backend 전체 회귀 289개 통과, Ruff lint 통과, SQLite migration `20260812_0033` upgrade→downgrade→upgrade 왕복 통과. Ubuntu PostgreSQL `20260812_0033` 적용 완료, 웹 Console·신규 API(`/system/stop-triggers`, `/system/risk-events`) 정상 응답 확인. 실제 장중 발화·EXIT_PENDING 회복·FIXED_STOP 주문 생성(다음 단계)은 대기 중이다.

### 2026-08-12 핵심 모의투자 우선순위 전환

- 휴장일·운영 자동화 확장은 보류하고 `AI 판단 → Guard → 승인/자동 권한 → 키움 모의주문 → 체결/포지션` 경로를 우선한다.
- 첫 안전 게이트인 서버 재시작 후에도 유지되는 `PAUSE_ENTRY`를 구현했다. 신규 BUY Guard와 시스템 준비 상태에 직접 연결하며 기존 포지션 조회·청산 판단은 막지 않는다.
- 세션·CSRF·멱등키 기반 활성화/해제 API와 대시보드 제어를 추가했고, 상태 변경은 감사 로그에 남는다. 백엔드 전체 회귀·Ruff, migration `20260812_0032` 왕복, Frontend 14개 시험·TypeScript·production build가 통과했다.
- 다음 핵심 순서는 고정손절 trigger, 승인형 MOCK 주문 생성, 제한적 MOCK 자동주문이다.

### 2026-08-12 거래 캘린더 운영 휴장 override

- KST 날짜별 `OPERATIONAL_CLOSURE`만 허용하는 fail-closed 운영 override를 추가했다. 오늘부터 730일 이내 날짜와 사유·공개 출처 참조가 필수이며 강제 개장과 세션 시간 변경은 지원하지 않는다.
- `market_calendar_overrides`는 날짜별 활성 행 하나와 `ACTIVE→REVOKED` append-only 이력을 보존한다. 생성·해제 감사 로그를 남기고 SHADOW 평가에는 적용된 override ID, `CLOSED/OPERATIONAL_CLOSURE`와 `krx-calendar-v2`를 canonical input·DB·API에 고정한다.
- 전략·설정 Console에서 운영 휴장을 등록·해제하고 활성·해제 이력을 확인할 수 있다. 로그인 세션과 CSRF만 사용하며 주문·승인·OrderIntent는 생성하지 않는다.
- Backend 전체 회귀·Ruff, migration `20260812_0031` upgrade→downgrade→upgrade, Frontend 13개 component 시험·TypeScript·production build가 로컬에서 통과했다. Ubuntu PostgreSQL 적용과 Console 수동 인수시험은 대기 중이다.

### 2026-08-12 KRX·NXT 공통 거래일 캘린더

- 기존 평일 판정에 대한민국 공휴일·대체공휴일, 근로자의 날과 KRX 연말 휴장을 추가한 공통 캘린더를 구현했다. 운영 휴장 override 통합 후 현행 버전은 `krx-calendar-v2`이며 캘린더 판정 실패는 `UNKNOWN/CALENDAR_UNAVAILABLE`로 닫힌다.
- 거래시장 SHADOW 평가에는 거래일 상태·사유·캘린더 정책 버전을 canonical input hash와 불변 DB 이력에 포함하고 API·Console에 같은 값을 표시한다. venue 정책은 `venue-selection-v2`로 올렸으며 주문 생성 금지 경계는 유지한다.
- migration `20260812_0030` 왕복과 Ubuntu PostgreSQL 적용을 확인했다. 개장시간 변경과 공식 일정 자동 동기화는 후속 범위다.

### 2026-08-12 거래시장 SHADOW 평가 Console

- 감시 종목 화면에 등록 종목·방향·수량·주문 유형·긴급도를 입력하는 SHADOW 진단과 최근 평가 이력을 추가했다.
- 결과는 선택 venue, 세션, NXT 적격 상태, KRX·NXT 매수/매도 1호가와 유효성, reason code를 분리 표시한다. 호가·평가 시각·실행 venue는 서버만 결정한다.
- 화면과 API client는 `SHADOW · 주문 없음`, `order_creation_allowed=false` 경계를 유지하며 Approval·OrderIntent·TradingOrder 생성 컨트롤을 추가하지 않았다.
- Frontend TypeScript, 12개 component 시험과 production build가 통과했다. 실서버 배포 후 실제 KRX·NXT 평가 카드 확인은 대기 중이다.

### 2026-08-12 키움 KRX·NXT 시세 stream과 관측 기반 적격 상태

- 활성 감시 종목마다 키움 실시간 KRX item과 NXT `_NX` item을 함께 구독하고 wire item을 6자리 종목과 `KRX/NXT` 시장으로 정규화한다. 체결·호가 cache와 PostgreSQL stream은 시장별로 격리한다.
- 정상 NXT quote 수신을 `instrument_venue_states`에 `VERIFIED/QUOTE_OBSERVED`로 저장하고 Venue Selection 진단이 이를 우선 사용한다. quote 부재는 계속 `UNKNOWN`이며 권위 있는 목록 없이 `INELIGIBLE`로 판정하지 않는다.
- MOCK의 NXT 시세는 분석·SHADOW 선택에만 사용하며 키움 주문 Adapter의 KRX-only 경계는 변경하지 않았다.
- backend 전체 회귀, Ruff lint와 SQLite migration `20260812_0029` upgrade/downgrade/upgrade가 통과했다. Ubuntu PostgreSQL 적용과 실제 장중 `_NX` payload 검증은 대기 중이다.

### 2026-08-12 KRX·NXT 자동 거래시장 선택 SHADOW 기반

- 사용자가 거래시장을 고정하지 않고 KST 세션, NXT 적격성, KRX·NXT 최신 호가와 표시 수량, 주문 긴급도 및 Broker SOR 지원 여부로 `KRX/NXT/SOR/WAIT`를 결정하는 `venue-selection-v1` 엔진을 구현했다.
- 진단 API와 `venue_selection_evaluations` 감사 원장을 추가했다. 입력 호가와 평가 시각은 서버 상태에서만 가져오며 `execution_stage=SHADOW`, `order_creation_allowed=false`를 DB 제약과 응답 계약으로 고정해 Decision·Approval·OrderIntent·TradingOrder를 만들지 않는다.
- NXT snapshot 부재를 미지원으로 오판하지 않도록 적격성을 `VERIFIED/INELIGIBLE/UNKNOWN`으로 구분한다. 권위 있는 NXT 종목 전체 적격 목록 수집, SOR 주문 매핑, Guard 직전 재선택은 후속 단계다.
- 집중 테스트·Ruff와 SQLite migration `20260812_0028` upgrade/downgrade/upgrade가 통과했다. Ubuntu PostgreSQL 적용과 실제 NXT 데이터 검증은 미완료다.

## 1. 목적

명세된 요구사항의 계획, 구현, 검증 상태를 구분해 관리한다. `명세 완료`는 코드 구현이나 시험 완료를 의미하지 않는다.

### 2026-08-11 불완전 Scout의 결정론적 Core 축소

- 신규 `agent-dag-v6`에서 필수 Scout가 하나라도 불완전하면 Core Provider를 호출하지 않고 서버가 `WAIT/UNKNOWN`, confidence 0, HIGH risk와 정확한 incomplete roles를 기록한다. 기존 v5 run은 당시 의미와 idempotency key로 보존한다.
- Scout 원본 상태와 Provider 응답은 치환하지 않으며, 완전한 입력에서는 기존 Core Provider 호출을 유지한다.
- 실서버에서 확인된 `LLM_CORE_SHADOW_ASSESSMENT_MISMATCH` 재현 조건을 외부 Provider fixture로 고정하고 `PARTIAL` 종료·Core invocation 0건·주문 0건을 검증했다.
- Backend 집중시험·전체 회귀·Ruff와 Frontend TypeScript·12개 component 시험·production build가 통과했다. 실서버 재배포 후 동일 종목 수동 재검증은 대기 중이다.

### 2026-08-11 서버 소유 Agent 판단 입력 v1

- 신규 DIAGNOSTIC admission을 `agent-dag-v5`와 `agent-server-input-v1`로 전환했다.
- 포지션의 원가·평가금액·미실현손익·수익률·고점 하락률·고정 손절 가격/거리·보유시간·freshness와 적용 Risk Policy provenance를 admission 시 계산해 불변 snapshot으로 고정한다.
- 검증된 내부 `market-context-v1` snapshot만 지수·업종·시장 breadth 입력으로 고정하며, 부재·stale·INCOMPLETE 상태에서는 Market Scout가 Provider를 호출하거나 값을 추정하지 않고 `INSUFFICIENT_DATA`로 축소한다.
- migration `20260811_0027` 왕복, backend 전체 회귀·Ruff, frontend TypeScript·12개 component 시험을 로컬에서 통과했다. Ubuntu PostgreSQL 적용과 실제 시장 context source 연결은 미검증이다.

### 2026-08-11 Agent SHADOW 판단 계약 v2

- 신규 DIAGNOSTIC admission을 `agent-dag-v4`로 전환하고 ENTRY/POSITION context와 position snapshot hash를 불변으로 고정했다.
- `agent-assessment-v2`, `agent-core-v2`, `score-policy-v1`, ENTRY의 `NOT_APPLICABLE`과 Core의 별도 `shadow_assessment`를 구현했다.
- 기존 v1 schema 선언 route는 재생성 없이 사용할 수 있으며 run에는 선언 schema와 실제 v2 검증 schema를 함께 남긴다. 기존 v1~v3 run은 nullable 신규 field로 그대로 조회한다.
- migration `20260811_0026` 왕복, backend 전체 회귀·Ruff, frontend TypeScript·12개 component 시험·production build를 로컬에서 통과했다. Ubuntu PostgreSQL 적용과 Console 수동 확인은 미검증이다.

### 2026-08-11 LLM 구조화 출력 기본 여유 상향

- 신규 Model Profile의 API·ORM·DB server default와 Provider 미명시 fallback을 `8192`로 상향했다.
- Console의 신규 역할 변경 후보는 `max output=8192`를 명시적 override 기본값으로 사용한다.
- 기존 Model Profile, 활성 Route 및 Invocation 이력은 자동 변경하지 않는다.
- Backend 전체 시험·Ruff, Frontend component 시험·TypeScript 검사와 `20260811_0024` upgrade/downgrade/upgrade가 통과했다. Ubuntu PostgreSQL도 `20260811_0025` head까지 적용해 server default를 확인했다.

### 2026-08-11 LLM 생성 파라미터 안전화

- 신규 sampling·seed 기본값을 Adapter 상속으로 변경하고 Gemini 3.x sampling 생략·thinkingLevel 변환, routed OpenAI reasoning 판정을 구현했다.
- 신규 Route 기본 120초, Flex 권장 300초, 연결 제한 10초와 Provider별 service tier 검증을 반영했다.
- 일일 호출 횟수를 실제 Invocation 경계에서 집행하며 비용 제한은 가격 산정 미구현 상태에서 양수 설정을 거부한다.
- 기존 활성 Route와 Model Profile 값은 자동 변경하지 않는다. Backend 전체 회귀·Ruff, Frontend component·TypeScript, SQLite migration 왕복과 Ubuntu PostgreSQL `20260811_0025` 적용을 확인했다.

## 2. 상태 정의

- `미명세`: 요구사항이 아직 문서화되지 않음
- `명세 완료`: 기준 문서와 인수 조건이 작성됨
- `구현 중`: 코드 또는 인프라 구현이 진행 중
- `구현 완료/미검증`: 구현됐지만 시험 근거가 없음
- `검증 완료`: `TEST_PLAN.md`의 대응 시험을 통과함
- `보류`: 외부 환경 또는 결정이 필요함

## 3. 현재 상태

| 영역 | 기준 문서 | 상태 | 비고 |
| --- | --- | --- | --- |
| 제품 범위와 실행 권한 | `docs/PRODUCT_REQUIREMENTS.md` | 구현 중 | 기본 행동 8종 mode 설정 구현; 거래/진단 판단 분리와 SHADOW→승인→MOCK 자동 단계 명세 완료, 실행 연결 미구현 |
| 거래 세션과 감시 일정 | `docs/TRADING_SESSION_SPEC.md` | 명세 완료 | NXT는 키움 모의투자 검증 불가 |
| 주문 가격과 미체결 처리 | `docs/ORDER_EXECUTION_SPEC.md` | 구현 중 | Paper와 키움 CREATED polling·ACK/REJECTED/UNKNOWN, 신규 BUY 접수 10초 후 잔량 1회 취소·snapshot 확정 구현; 승인형 BUY·결정 기반 PARTIAL/FULL SELL·FIXED_STOP SELL 주문 생성 연결(MARKETABLE_LIMIT 간소화, 호가단위 보정·매도 재호가는 후속), 실제 장중 취소 경쟁 미검증 |
| 주문 상태 머신과 키움 매핑 | `docs/ORDER_STATE_MACHINE_SPEC.md` | 구현 중 | Paper·키움 송신 전이 구현; BUY/PARTIAL_SELL/FULL_SELL 승인 생명주기(PENDING→APPROVED/REJECTED/EXPIRED/INVALIDATED)·원자 주문 생성 구현, TAKE_PROFIT 주문은 후속 |
| 계좌·주문 재동기화 | `docs/RECONCILIATION_SPEC.md` | 구현 중 | snapshot 대조와 상시 worker READY·재시작 fencing은 실서버 통과; `00`·`04` 이벤트 즉시 gate 차단·debounce·BROKER_EVENT 대조 로컬 통과, Broker 총수량과 Cresta 관리수량 분리·`EXTERNAL/MIXED/CRESTA_MANAGED` 재분류 실서버 통과(장애주입 미검증) |
| 시스템 아키텍처 | `docs/SYSTEM_DESIGN.md` | 구현 중 | Backend·Console·gateway·키움 worker·ENTRY/POSITION AI scheduler·별도 Agent worker·Watch와 SHADOW 실행 구현; 공통 Order Creation Service·승인 경로·FIXED_STOP 자동 매도 연결 |
| HTTP/WebSocket API | `docs/API_SPEC.md` | 구현 중 | 인증·상태·주문/체결·포지션·quote·승인 조회·승인/거절 구현; 거래 명령·stream 미구현 |
| UI 콘셉트 참고자료 | `stitch_cresta_ai_intraday_trading_system/` | 참고자료 | 실제 Console 구현물이 아님 |
| 키움 모의투자 Adapter | `docs/KIWOOM_BROKER_SPEC.md` | 구현 중 | 인증·snapshot·worker는 실서버 통과; 주문 Adapter·FIFO polling·UNKNOWN 대조·계좌 event gate·Web MOCK 1주 진단 API 자동시험 통과, 실제 모의주문 미검증 |
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 구현 중 | BUY 전체 Risk Guard(일일손실 REALIZED_PLUS_UNREALIZED/종목·전체 노출/일일진입/연속손실/spread/연결위험/활성손실이벤트)와 고정 손절 trigger 매도 Guard·승인 시점 재검사 구현; risk_events 원장 scope별 영속; ENTRY_HALT; 비상정지(EMERGENCY_LIQUIDATE 전체)는 미구현 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 구현 중 | 실행 권한, Guard 사용자 기본 위험 설정, fail-closed 운영 휴장과 Provider/Model/역할별 배정 UI/API 구현; 종목별 위험 override·영향 미리보기·예약 적용 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, 감시 종목·KRX/NXT SHADOW venue 평가·운영 휴장·Paper 조회·Broker 진단·실행 권한·Guard 위험 설정, Provider 모델·역할·프롬프트·FAILOVER 배정, stage 결과·구조화 응답 조회와 BUY/PARTIAL_SELL/FULL_SELL 승인 카드 구현; Guard 평가 상세 결과 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인 TOTP·세션·CSRF·실패제한 구현; 현재 개발 단계의 로그인 이후 설정·Provider·역할 배정·MOCK 시험 재인증은 제거하고 향후 위험 분석 시 선택적 재도입 예정, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 감시 종목·키움 `0B`·`0D`, 1분봉과 v2 VWAP·SMA5·상대 거래량·실현 변동성·고점 하락률·spread 영속화 로컬 검증 완료; 체결강도와 v2 실제 장중 수신 미검증 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 구현 중 | 불변 `scout-input-v1`, ENTRY `deterministic-mock-v2`, POSITION `deterministic-position-v1`, 외부 Provider DIAGNOSTIC 판단과 `agent-server-input-v1` 포지션 파생값을 로컬 검증 완료; scheduler 연속운전·실서버 POSITION 검증 대기 |
| 다중 에이전트 오케스트레이션 | `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md` | 구현 중 | Agent Runtime v6의 Intel·Verify·4개 Scout·Candidate Auditor·Core, 서버 입력과 불완전 Scout의 결정론적 Core 축소 구현; v6 로컬 회귀 완료, 실서버 검증 대기 |
| Cresta v2 ENTRY Decision Architecture | `docs/AI_DECISION_SPEC.md`, `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md`, `docs/SYSTEM_DESIGN.md`, `docs/DECISION_EXECUTION_SPEC.md`, `docs/DATABASE_SPEC.md` | Phase 10F Broker pre-send 완료 | Phase 3~9 Decision/Finalizer와 0041 foundation 위에 exact-one sourced handoff, complete BUY Guard, manual/automatic CREATED authority와 typed Broker pre-send/unsent revocation 구현. production handoff/PostgreSQL은 Phase 10G 대기 |
| LLM Provider·Gateway | `docs/LLM_PROVIDER_GATEWAY_SPEC.md` | 구현 중 | 40개 Provider template, 35개 단일-key 등록, Native·OpenAI-compatible Adapter, 모델 동기화·역할·Prompt·FAIL_STOP/단일 FAILOVER·service tier·웹 검색·호출 이력 구현; OpenAI·LLM Gateway 실제 SHADOW 호출 검증 완료, 복합 인증 5종·가격 기반 비용 집계 미구현 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | Phase 10C.1 SQLite 검증 완료·PostgreSQL 대기 | 현행 head `20260828_0041`; sourced execution discriminator/partial unique, stage provenance, typed Guard/OrderIntent provenance, Approval FK와 INVALIDATED foundation 구현. PostgreSQL DDL/FK/locking/concurrency 검증 대기 |
| 판단 실행·승인 | `docs/DECISION_EXECUTION_SPEC.md` | Phase 10F Broker pre-send 완료 | sourced manual/automatic BUY와 fixed-stop의 live stage/mode/policy/financial/position `BROKER_SEND` Guard, strict MOCK, SUBMITTING commit 및 unsent INVALIDATED 회수 구현. scheduler/production handoff/PostgreSQL은 Phase 10G 대기 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 전 서비스 `unless-stopped`, core healthcheck와 선택형 source overlay 부팅 조정 unit 구현; 현재 MOCK runtime data는 disposable이며 optional snapshot은 배포 blocker가 아님. 향후 LIVE backup·retention과 경보·복구훈련은 미정/미완료 |
| 구현 착수 준비도 | `docs/IMPLEMENTATION_READINESS_REVIEW.md` | 역사적 검토 | 2026-08-06 Foundation·Agent Runtime v1 착수 게이트 기록이며 현재 상태는 이 문서를 기준으로 한다. |
| Backend·Docker 골격 | `docs/SYSTEM_DESIGN.md`, `docs/OPERATIONS_RUNBOOK.md` | 검증 완료 | API source UID `10001` 소유권·PostgreSQL·Redis·API·Frontend·gateway 기동과 HTTPS/내부 health 실서버 확인 |

## 4. 구현 완료 조건

기능별 완료는 다음 조건을 모두 만족해야 한다.

1. 관련 요구사항 ID가 기준 문서에 존재한다.
2. 구현이 요구사항과 일치한다.
3. 대응 테스트 ID가 `TEST_PLAN.md`에 존재한다.
4. 자동 또는 수동 시험 결과가 기록된다.
5. 미검증 사항과 외부 제약이 숨김없이 표시된다.
6. 관련 문서와 `AGENTS.md` 색인이 갱신된다.

## 5. 미결정·보류 항목

- 키움 모의투자 계정·API 사용신청·고정 출구 IP와 REST 인증·시세·10자리 계좌 일치는 실제 서버 확인 완료
- NXT/SOR 실거래 검증 환경
- 외부 LLM Provider SHADOW 호출은 활성화됐지만 모델·Gateway별 실제 응답 편차를 계속 검증해야 한다.
- OpenDART 실제 키·고정 출구 IP 호출과 삼성전자 최근 3일 공시 6건 수집을 Ubuntu 서버에서 확인했다. KRX 전 거래일 공식 일별매매 Adapter와 NAVER API HUB News Adapter는 구현했지만 KRX·NAVER 실제 자격증명 및 서버 호출은 아직 미검증이다.
- DART·KRX secret과 NAVER credential 쌍을 감지하는 선택 overlay 부팅 조정은 구현했지만 신규 source overlay를 포함한 실제 Ubuntu 재부팅 자동복구 인수시험은 미검증이다.

## 6. 다음 구현 작업

구현 순서는 아래 단계로 고정한다. 각 단계는 해당 요구사항·자동시험·운영 확인을 모두 충족한 뒤 다음 단계로 넘어가며, 외부 LLM 결과를 곧바로 주문으로 연결하지 않는다.

### 6.1 완료 — Agent SHADOW 판단 계약 v2

범위:

- admission 시 `ENTRY/POSITION` analysis context와 position snapshot을 불변으로 고정
- ENTRY의 포지션 위험 역할에 `NOT_APPLICABLE` 의미 추가
- `agent-assessment-v2`, `agent-core-v2`, `score-policy-v1`, `agent-dag-v4` 도입
- Core의 실행 `WAIT`와 별도 `shadow_assessment` 분리
- API·Console에서 context, 계약 version, N/A와 null 점수를 명확히 표시

완료 gate:

- `T-AGENT-SHADOW-001`~`010` 통과
- migration `20260811_0026_agent_shadow_contract_v2` 왕복 검증
- 기존 v1~v3 run 조회·멱등성 회귀 통과
- DIAGNOSTIC run의 Decision·Approval·OrderIntent·TradingOrder 0건 확인

구현 순서:

1. `backend/app/models.py`와 신규 migration에 run context·snapshot hash·N/A 상태·shadow assessment 저장 구조를 추가한다.
2. `backend/app/agents/contracts.py`, `reason_codes.py`, `runtime.py`에 v2 schema와 v4 admission·멱등 계약을 추가한다.
3. Agent worker의 dependency·필수 역할·Core 입력 조립을 context-aware 방식으로 변경한다.
4. `backend/app/schemas.py`와 Agent run API에 version·context·assessment 응답을 추가한다.
5. Console에 WAIT/평가 분리, `해당 없음`, null 점수와 version 표시를 추가한다.
6. 신규 집중시험 후 backend 전체 회귀·Ruff, frontend component·TypeScript·production build와 migration 왕복을 실행한다.

### 6.2 완료 — 서버 소유 판단 입력 확장

범위:

- Position Risk 입력에 미실현 손익, 고점 대비 하락, stop 거리, 보유시간과 잔여 수량을 서버 계산값으로 추가
- Market Sector 입력에 KRX 지수·업종·시장 breadth의 검증 snapshot을 추가
- 각 값에 관측시각, freshness와 source reference를 부여

완료 gate:

- 동일 원시 snapshot의 계산 재현성과 stale/missing 경계 시험 통과
- 모델이 서버 계산값을 덮어쓰거나 추정값을 evidence로 승격할 수 없음
- ENTRY와 POSITION fixture 모두에서 v2 계약 통과

검증 결과:

- `T-AGENT-INPUT-001`~`005`, `T-MARKET-CONTEXT-001`~`003` 자동시험 통과
- migration `20260811_0027_server_owned_agent_inputs` upgrade/downgrade/upgrade 통과
- backend 전체 회귀·Ruff와 frontend TypeScript·12개 component 시험 통과
- 모든 DIAGNOSTIC 경로의 Decision·Approval·OrderIntent·TradingOrder 0건 유지

### 6.3 진행 중 — 검증된 외부 증거 coverage

범위:

- OpenDART·KRX·NAVER API HUB source Adapter의 실제 운영 자격증명 검증과 시장·업종 coverage 확장
- Provider citation은 계속 `UNRATED`로 보존하고 독립 검증을 통과한 항목만 EvidenceBundle에 편입
- 출처별 freshness, 중복 제거, 장애와 quota 정책 확정

현재 결과:

- 공식 KRX OPEN API의 KOSPI·KOSDAQ 일별매매정보를 최근 전 거래일 증거로 수집하는 선택 Adapter를 구현했다.
- 정확한 종목코드 매칭, 7일 freshness 상한, 일자·시장 cache, 정상 무자료와 장애 분리, DART와의 복합 EvidenceBundle 편입 경계를 적용했다.
- DART·KRX secret 존재 여부에 따라 선택 overlay를 포함하는 boot reconcile 스크립트와 systemd unit을 구현했다.
- NAVER API HUB News Adapter와 선택 overlay를 구현했다. KRX·NAVER 실제 키 호출 및 DART·KRX·NAVER 재부팅 인수시험은 남아 있다.

완료 gate:

- 뉴스·공시·시장 source의 성공·빈 결과·stale·장애 시험 통과
- Core가 허용된 evidence ID 외 URL·자유문장을 근거로 사용할 수 없음
- DART overlay를 포함한 재부팅 자동복구 인수시험 통과

### 6.4 4순위 — replay·평가·모델 채택

범위:

- 동일 입력에 대한 모델·prompt·tier별 schema 통과율, latency, 비용과 5분·10분·30분 수익률, MFE·MAE 수집
- score-policy와 shadow assessment의 방향성·일관성 검증
- 운영 역할별 primary·fallback·timeout·tier 추천 근거 생성

완료 gate:

- 최소 표본수와 평가 기간을 별도 eval 계획에서 확정
- 결과 재현 가능한 replay report와 모델 교체 근거 존재
- 이 gate 전에는 조건부 고성능 Core, 복수 모델 투표와 자동 모델 교체를 구현하지 않음

### 6.5 진행 중 — TRADING Guard·승인·MOCK 주문 연결

범위:

- Guard 전체 노출·예수금·일일진입·spread와 손절 trigger 완성
  - 고정 손절 trigger SHADOW 구현 완료(`StopTrigger` + `risk_events` + 매도 Guard, 주문 0건)
  - FIXED_STOP APPROVAL_ONLY direct SELL P0는 Phase 10E에서 EXIT_PENDING/Order 0으로 교체했고 validated MOCK_AUTOMATIC+explicit AUTOMATIC에서만 CREATED를 허용한다.
  - BUY manual Approval의 owner/CAS/target-bound one-time reauth/current-stage 재검사는 Phase 10D에서 완료했고 sourced automatic MOCK CREATED는 Phase 10E에서 완료했다.
  - **전체 Risk Guard 완료**(#2): 일일손실(REALIZED_PLUS_UNREALIZED)·종목/전체 노출·일일진입·연속손실·spread·연결위험·활성손실이벤트 BUY 차단, risk_events scope별 영속, ENTRY_HALT
  - 비상정지 전체(EMERGENCY_LIQUIDATE)·호가단위 보정은 후속
- 기능별 `AUTOMATIC/MANUAL_APPROVAL/DISABLED` 실행 권한 적용
  - normative `V7_ENTRY_EXECUTION_STAGE` control-plane은 sourced/Approval/fixed-stop CREATED authority와 Broker worker pre-send 재검사·unsent revocation까지 적용됐다. production handoff와 PostgreSQL acceptance는 Phase 10G 대기
- 승인 카드, 만료·거절, 재평가와 원자 OrderIntent·TradingOrder 생성
  - 승인 카드·만료·거절·가격편차·position version 무효화·원자 주문 생성 완료
- 외부 AI 결과가 실패·불완전·만료일 때 신규매수 fail-closed

완료 gate:

- 고정 손절·비상정지·장마감 청산이 AI와 독립적으로 동작
- 승인·자동 경로의 멱등성, 중복 주문, UNKNOWN 대조와 재시작 복구 시험 통과
- 키움 모의투자 소액 주문·부분체결·취소·거절의 장중 실서버 검증

### 6.6 6순위 — 운영 안정화와 제한 자동매매 준비

- DART 포함 systemd boot profile, 경보와 운영 dashboard 완성; backup·restore drill은 향후 LIVE readiness에서 정책 확정 후 계획
- 실제 장중 Watch 지표, 체결 event와 reconciliation 장애 주입 시험
- 거래일별 손익·모델 판단·Guard 차단·주문 결과 review report
- 실거래 credential과 권한은 별도 승인 전까지 추가하지 않음

### 보류 기준

- 조건부 Core 모델 승격, factor별 evidence 강제 매핑과 가격 기반 비용 차단은 4순위 평가 결과가 생긴 뒤 결정한다.
- NXT/SOR 실거래, 복합 인증 Provider 5종과 Ollama 운영 모델은 현재 핵심 경로를 막지 않으므로 별도 backlog로 유지한다.
- 단계 순서를 바꾸려면 관련 명세와 인수 gate를 먼저 수정하고 변경 이유를 이 문서에 기록한다.

## 2026-08-10 역할별 LLM 서비스 정책

- 역할 후보에서 전체 구조화 응답 timeout을 1–600초로 설정하며 신규 기본값은 120초다.
- `DEFAULT`, `PRIORITY`, `FLEX` 서비스 티어를 route version에 저장하고 외부 Adapter 요청에 전달한다.
- 진단 run 유효시간은 선택된 route timeout 합계와 안전 여유시간을 반영하여 10초 경계 응답이 run 자체의 1분 만료와 충돌하지 않도록 조정했다.
- 현재 전송은 non-streaming이며 최종 JSON을 받은 뒤 서버 계약 검증을 통과해야만 stage 성공으로 채택한다.

## 2026-08-11 Provider web search and runtime clock

- APIchat Adapter 동작을 읽기 전용으로 분석해 OpenAI Responses, Anthropic Messages, Gemini generateContent 및 LLM Gateway 호환 웹 검색 변환을 추가했다.
- 역할 route에 versioned 웹 검색 권한을 추가하고 뉴스·공시/시장·업종 Scout만 SHADOW에서 허용하도록 fail-closed 검증을 적용했다.
- 모든 LLM 호출에 UTC와 Asia/Seoul 현재 시각 및 최신성 지침을 주입하고 invocation 감사 이력에 실행 시각과 검색 사용 여부를 저장한다.
- Provider 검색 결과의 citation/source metadata를 공통 후보로 수집하는 경계는 구현했지만 검증된 EvidenceBundle로 승격하는 Verifier는 아직 구현되지 않았으므로 검색 결과는 주문·승인 경계 밖에 있다.

## 2026-08-11 APIchat-compatible request normalization

- APIchat의 실제 Provider parameter policy와 OpenAI-compatible request builder를 읽기 전용으로 대조했다.
- GPT-5/o계열 토큰 한도 필드, reasoning sampling 억제, reasoning effort 전달, strict schema 요청과 모델별 capability 동기화를 구현했다.
- 로컬 전체 backend 시험을 통과했고 Ubuntu 서버에서 OpenAI GPT-5 계열과 LLM Gateway 경유 Gemini SHADOW 호출·schema 재검증을 확인했다.

## 2026-08-11 OpenAI Responses reasoning normalization

- 서버 SHADOW 결과에서 OpenAI 공식 `gpt-5-mini`가 DEFAULT tier에서도 즉시 거부되는 경로를 분리했다.
- Responses Adapter가 GPT-5/o계열의 `temperature/top_p`를 reasoning 기본값에서도 제거하도록 APIchat 모델 정책을 공통 적용했다.
- 집중 시험과 backend 전체 회귀·Ruff를 통과했고 Ubuntu 서버의 Technical Scout에서 OpenAI 공식 응답의 schema 통과를 확인했다.

## 2026-08-11 역할 비종속 Provider 출처 후보 수집

- OpenAI Responses, Anthropic Messages, Gemini generateContent와 OpenAI-compatible 응답의 알려진 citation 위치를 공통 `EvidenceSourceCandidate`로 정규화한다.
- 공개 HTTPS URL만 run별 중복 없이 `UNRATED EvidenceItem`으로 저장하며 원문 응답, private·loopback URL과 credential은 저장하지 않는다.
- Scout의 일반 `allowed_input_refs`와 검증 근거 전용 `allowed_evidence_refs`를 분리했다. 검증된 근거가 없으면 모델은 빈 `evidence_refs`를 반환해야 하며 URL이나 임의 ID를 반환하면 fail-closed 처리한다.
- schema, evidence reference와 Core incomplete-role 계약 오류를 구분된 안전 코드로 기록한다. 집중 시험 25개, backend 전체 188개 시험 및 Ruff를 통과했다.

## 2026-08-11 Evidence Candidate Auditor

- `agent-dag-v2` 고정 DAG를 8개 stage로 확장하고 네 Scout 이후 Core 전에 내부 `EVIDENCE_CANDIDATE_AUDITOR`를 배치했다. 신규 버전은 기존 v1 멱등 run과 분리되며 진행 중 v1은 감사 없음으로 안전하게 마무리한다.
- Auditor는 외부 호출 없이 현재 run의 `UNRATED EvidenceItem`을 중복 제거된 내부 ID와 Provider별 개수로 집계한다. URL·Provider 원문 응답은 Core에 전달하지 않는다.
- 기존 EvidenceBundle은 수정하지 않으며 Core에는 감사 stage ref, 후보 개수, reason code와 검증 근거 개수만 전달한다. Bundle이 `VERIFIED`가 아니면 run은 계속 `PARTIAL`이다.
- `20260811_0022` migration의 upgrade→downgrade→upgrade, backend 전체 188개 시험과 Ruff를 통과했다. 실제 출처를 `PRIMARY/SECONDARY`로 승격하는 정책은 DART·거래소·뉴스 수집 방식 확정 후 구현한다.

## 2026-08-11 역할별 reason code 계약

- `reason-code-policy-v1`에서 4개 Scout와 Core가 반환할 수 있는 reason code를 역할별 allowlist로 고정했다.
- 각 LLM 요청의 구조화 입력과 JSON Schema enum에 동일한 정책 버전과 허용 목록을 전달하며, 서버가 응답을 다시 검증한다.
- 등록되지 않은 code는 Provider의 schema 성공 여부와 무관하게 `INVALID_OUTPUT/FAILED` 및 `LLM_REASON_CODE_NOT_ALLOWED`로 fail-closed 처리한다.
- Mock 판단도 같은 용어를 사용하도록 정리했으며, 원문 응답 저장은 이번 범위에서 제외하고 별도 로깅 단계로 남겼다.
- 로컬 backend 전체 196개 테스트와 Ruff lint가 통과했다.

## 2026-08-11 OpenDART PRIMARY evidence Adapter

- `agent-dag-v3`에서 선택형 OpenDART 공시검색 Adapter를 `INTEL_COLLECTOR`에 연결했다. 공식 endpoint, KST 최근 3일, 최대 page와 정확한 종목코드 필터를 적용한다.
- 검증된 공시는 공식 viewer URL과 안전한 메타데이터만 `DART_DISCLOSURE/PRIMARY`로 저장하며 키와 원문 응답은 보존하지 않는다.
- `000`과 `013`을 정상 처리하고 인증·IP·한도·HTTP·timeout·page cap 오류는 `DART_*` code로 fail-closed 처리한다.
- 검증 evidence ID는 Scout allowlist에 전달하지만 계약 뉴스 coverage가 없으므로 Bundle과 run은 `PARTIAL`, 주문은 0건으로 유지한다.
- 선택형 `deploy/compose.dart.yaml`과 secret 권한 준비 절차를 추가했다. 활성화 상태에서 secret이 유효하지 않으면 run admission을 409로 차단한다. 로컬 회귀시험과 Ubuntu 실제 OpenDART 호출·공시 6건 수집을 확인했다. 이후 선택 overlay 감지 boot reconcile을 구현했으며 실제 재부팅 인수시험은 남아 있다.

## 2026-08-11 구조화 LLM 응답 이력

- Adapter가 추출한 구조화 JSON을 역할 계약 검증 전에 canonical form·SHA-256 hash·capture 시각으로 invocation에 저장한다. 성공뿐 아니라 reason code·evidence ref 등 서버 계약 실패 응답도 프롬프트 개선을 위해 보존한다.
- Provider raw body, prompt, tool payload와 credential은 저장하지 않는다. 64 KiB 초과 또는 민감 key 이름을 포함한 output은 보관하지 않고 전용 오류로 fail-closed 처리한다.
- run 목록에는 output을 포함하지 않고 소유권을 확인하는 개별 API와 Console의 지연 로딩 버튼으로만 조회한다.
- `20260811_0023` upgrade→downgrade→upgrade, backend 전체 회귀·Ruff, frontend TypeScript·component 시험·production build가 통과했다. Ubuntu PostgreSQL 적용과 실제 외부 구조화 응답 Console 조회도 확인했다.

## 2026-08-11 Guard 사용자 기본 위험 설정

- 기존 `configuration_versions`에 독립 category `RISK_POLICY`를 사용해 안전 기본값 조회, DRAFT→VALIDATED→ACTIVE, 이전 ACTIVE→SUPERSEDED와 stale base 충돌 검사를 구현했다.
- 진입금액·1회/종목/전체 금액·최대 보유종목·일일진입·고정손절·시세 지연·spread·가격편차 범위와 금액 순서를 서버에서 검증한다. 활성 버전이 없으면 `entry_order_amount=null`로 BUY 차단을 유지한다.
- Guard SHADOW 평가와 execution에 사용한 risk version ID를 기록하고, Console에서 source·active version·미설정 차단을 표시하며 변경안 검증·확정을 수행한다.
- backend 전체 209개 테스트·Ruff, frontend TypeScript·12개 component 테스트·production build가 통과했다. 전체 계좌 노출 계산·영향 미리보기·종목별 override는 후속 구현이다.

## 2026-08-11 KRX PRIMARY 전 거래일 시장 증거 Adapter

- 공식 KRX OPEN API의 유가증권·코스닥 일별매매정보를 선택형 `INTEL_COLLECTOR` source로 추가했다. KST 실행일 이전 최근 7일, 정확한 6자리 종목코드와 공식 응답 필드만 채택한다.
- KRX 행은 `KRX_DAILY_MARKET/PRIMARY`로 저장하며 인증키·원문 응답은 보존하지 않는다. 일자·시장·credential fingerprint별 메모리 cache로 key당 일 10,000회 한도를 보호한다.
- 정상 무자료와 HTTP·timeout·형식 장애를 구분하고, 활성 secret이 잘못되면 run admission을 409로 차단한다. DART와 KRX 증거는 같은 불변 Bundle에 들어가지만 계약 뉴스 coverage 전까지 `PARTIAL`, 주문 0건을 유지한다.
- DART·KRX secret 존재 여부로 선택 overlay를 조합하는 `boot-reconcile.sh`와 systemd unit을 추가했다. Ruff와 backend 전체 회귀시험을 통과했으며 KRX 실제 키 호출과 Ubuntu 재부팅 인수시험은 남아 있다.

## 2026-08-11 NAVER API HUB SECONDARY 뉴스 증거 Adapter

- 현행 NAVER API HUB News Search 공식 endpoint를 사용하는 선택형 Adapter를 `INTEL_COLLECTOR`에 추가했다. 같은 실행에서 DART·KRX가 확인한 회사명을 우선 검색하며 정확한 종목 연관성, 공개 HTTPS URL과 72시간 freshness를 통과한 결과만 채택한다.
- 기사 본문과 검색 요약은 저장하지 않고 정제된 제목·원문 URL·게시시각·host·검색 identity만 `NEWS/SECONDARY` EvidenceItem으로 저장한다. 인증·권한·quota·timeout·HTTP·응답 형식 오류는 `NAVER_NEWS_*` 코드로 fail-closed 처리한다.
- 두 credential 파일이 모두 있을 때만 `compose.naver-news.yaml`을 적용하도록 secret 준비와 boot reconcile을 확장했다. DART·KRX·NAVER 조회 완료와 최신 KRX 증거를 Bundle `VERIFIED`의 최소 coverage로 고정했으며, 정상 빈 뉴스는 coverage 완료일 뿐 긍정 신호가 아니다.
- 로컬 Adapter·통합·설정·배포 회귀시험은 통과했다. NAVER 실제 credential 호출과 Ubuntu 재부팅 자동복구 인수시험은 남아 있다.
