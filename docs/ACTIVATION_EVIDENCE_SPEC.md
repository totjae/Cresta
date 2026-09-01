# Activation Evidence Artifact 및 Resolver 명세

## 1. 목적

이 문서는 `V7_ENTRY_ACTIVATION` Gate가 참조하는 acceptance evidence artifact의 독립적인
body schema, 발행 권한, 영속 저장소, revision binding과 production resolver 계약을 정의한다.
Gate payload와 runtime PolicyProfile·route·model·prompt snapshot의 기존 계약은 변경하지
않으며, 이 문서의 artifact는 그 두 계약을 대체하지 않는다.

현재 명세 버전은 `activation-evidence-spec-v1`이다. Activation evidence가 사용하는 현재
normative specification-set identifier는 `cresta-v2-activation-spec-set-2026-09-01.1`, 현재
test-plan identifier는 `cresta-v2-activation-test-plan-2026-09-01.1`이다. 저장소에는 이보다
상위인 전역 문서 버전 체계가 없으므로 두 identifier는 Activation Gate evidence에만
적용한다. 이 문서 또는 이 문서가 열거한 Gate normative 문서의 관련 계약이 바뀌면
`spec_version`을, required acceptance ID·binding·acceptance 의미가 바뀌면
`test_plan_version`을 반드시 올린다.

## 2. 적용 범위와 우선순위

이 문서는 다음 범위의 단일 상세 기준이다.

- acceptance ID별 artifact body와 canonical bytes
- content-addressed filesystem store와 `evidence_ref`
- publisher와 machine-readable test binding
- code/test-plan/spec/migration/environment/freshness binding
- production read-only resolver와 장애 분류
- artifact publication, 보존과 trust boundary

Gate의 판단·실행 권한은 `AI_DECISION_SPEC.md`, Gate payload·ConfigurationVersion lifecycle은
`CONFIGURATION_SPEC.md`, DB 영속 경계는 `DATABASE_SPEC.md`, v7 DAG와 acceptance 의미는
`MULTI_AGENT_ORCHESTRATION_SPEC.md`, 실제 ID와 시험 상태는 `TEST_PLAN.md`를 따른다. 이
문서는 해당 문서의 artifact 상세를 구체화하며 충돌 시 더 엄격한 fail-closed 조건을
적용하고 문서 충돌을 같은 변경에서 해소한다.

현재 normative specification set은 다음 파일의 Activation Gate 관련 계약이다.

- `docs/ACTIVATION_EVIDENCE_SPEC.md`
- `docs/AI_DECISION_SPEC.md`
- `docs/CONFIGURATION_SPEC.md`
- `docs/DATABASE_SPEC.md`
- `docs/DECISION_EXECUTION_SPEC.md`
- `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md`
- `docs/OPERATIONS_RUNBOOK.md`

`TEST_PLAN.md`는 별도 `test_plan_version` authority이고 위 spec set에 중복 포함하지 않는다.

## 3. Artifact body 계약

### 3.1 저장 단위와 schema

저장 단위는 `REQUIRED_ACTIVATION_TEST_IDS`의 acceptance ID 하나당 artifact 하나다. 118개
결과를 하나의 가변 aggregate file로 만들지 않는다. Gate version이 서로 독립적인 118개
artifact reference를 묶어 완전한 acceptance set을 고정한다.

artifact body schema의 고정 이름은 `activation-evidence-artifact-v1`이고
`schema_version`은 `1.0`이다. body는 다음 exact top-level field만 허용한다.

```yaml
schema_version: "1.0"
artifact_type: activation-evidence-artifact-v1
test_id: T-V2-...
requirement_ids: []
result: PASSED
test_nodeids: []
code_revision: <40 lowercase hexadecimal Git commit SHA>
test_plan_version: cresta-v2-activation-test-plan-...
spec_version: cresta-v2-activation-spec-set-...
migration_revision: <exact Alembic revision>
environment: MOCK
required_acceptance_set_hash: <64 lowercase hexadecimal SHA-256>
executed_at: <UTC RFC3339 timestamp>
freshness_contract: EXACT_REVISION
runner:
  name: <non-secret tool identity>
  version: <non-secret exact version>
backend: POSTGRESQL | SQLITE | STATIC | DEPLOYMENT | MIXED
result_summary:
  collected: <non-negative integer>
  passed: <non-negative integer>
  failed: <non-negative integer>
  skipped: <non-negative integer>
  xfailed: <non-negative integer>
  xpassed: <non-negative integer>
  errors: <non-negative integer>
  duration_ms: <non-negative integer>
```

Unknown field, implicit default와 JSON null은 허용하지 않는다. 각 field의 의미는 다음과 같다.

| field | 계약 |
| --- | --- |
| `test_id` | 현재 required set에 정확히 포함된 ID 하나다. |
| `requirement_ids` | 해당 acceptance가 직접 증명하는 normative requirement ID의 비어 있지 않은 정렬·중복 제거 목록이다. |
| `result` | Gate OPEN용 artifact에서는 exact literal `PASSED`만 허용한다. |
| `test_nodeids` | publisher가 소비한 exact pytest node ID 또는 아래의 authoritative non-pytest ID를 정렬·중복 제거한 비어 있지 않은 목록이다. |
| `code_revision` | 시험 대상 exact candidate의 full 40-character lowercase Git commit SHA다. prefix·branch·tag는 금지한다. |
| `test_plan_version` | publisher binding과 Gate policy가 요구하는 exact Activation test-plan identifier다. |
| `spec_version` | 위 normative specification set의 exact identifier다. |
| `migration_revision` | 시험과 배포가 요구하는 exact Alembic head다. 현재 값은 `20260829_0044`다. |
| `environment` | artifact가 증명하는 exact 환경이다. v1 Gate target에서는 `MOCK`만 허용한다. |
| `required_acceptance_set_hash` | 3.4의 정확한 required ID set digest다. |
| `executed_at` | 결과가 확정된 UTC 시각이다. publisher 시각이나 파일 생성시각으로 대체하지 않는다. |
| `freshness_contract` | 현재 artifact class에서는 exact literal `EXACT_REVISION`이다. |
| `runner` | credential, host path와 command line을 제외한 실행 도구 이름과 exact version이다. exact 두 field만 허용한다. |
| `backend` | 시험의 authoritative boundary다. 둘 이상이면 `MIXED`이며 추측으로 `POSTGRESQL`을 부여하지 않는다. |
| `result_summary` | bound result의 작은 정수 요약이다. `PASSED`에는 `collected = passed = test_nodeids`의 결과 수이고 나머지 outcome count가 0이어야 한다. |

pytest가 아닌 version-controlled check ID는 `static::<tool>::<check-id>` 또는
`deployment::<check-id>` 형식만 허용한다. 사람이 임의로 작성한 설명은 authoritative ID가
아니다. parametrized pytest는 실행된 full node ID를 각각 기록한다.

### 3.2 금지 내용과 크기

artifact는 proof metadata이며 원시 log archive가 아니다. password, API key, bearer token,
cookie, TOTP/계좌/provider secret, raw environment dump, credential을 포함할 수 있는 provider
payload, prompt 전문, host absolute path, 임의 대용량 log를 넣지 않는다.

canonical artifact bytes의 최대 크기는 정확히 `65,536` bytes다. 기존 구조화 LLM 결과의
64 KiB 안전 상한과 같은 크기를 재사용하며 이 proof metadata에는 충분하다. publisher는
초과 body를 발행하지 않고 resolver는 상한을 넘는 파일을 끝까지 읽거나 반환하지 않는다.

### 3.3 Canonical JSON과 SHA-256

canonical bytes는 다음 규칙의 UTF-8 JSON이다.

- object key는 모든 nesting level에서 Unicode code point 기준 사전순이다.
- separator는 `,`와 `:`이며 insignificant whitespace가 없다.
- array는 schema가 지정한 순서를 유지한다. 현재 `requirement_ids`와 `test_nodeids`는 문자열
  오름차순이며 중복이 없다.
- UTF-8 BOM, trailing newline과 trailing whitespace가 없다.
- Unicode는 UTF-8 문자로 직렬화하며 ASCII escape로 형태를 임의 변경하지 않는다.
- duplicate JSON key, NaN, positive/negative Infinity와 floating-point number는 invalid다.
- timestamp는 UTC RFC3339 `YYYY-MM-DDTHH:MM:SS[.fraction]Z`다. 불필요한 소수 초의 trailing
  zero는 제거하고 offset 표기나 naive timestamp는 허용하지 않는다.

`artifact_hash`는 canonical artifact bytes의 SHA-256 lowercase hexadecimal이며 정확히
64자다. Gate `SafetyEvidence.evidence_hash`는 이 값과 같고 `evidence_ref`의 digest와도
같아야 한다. 입력 JSON을 parse한 뒤 strict schema로 검증하고 다시 canonicalize한 bytes가
저장 bytes와 다르면 artifact는 non-canonical corruption으로 거부한다.

### 3.4 Required acceptance set hash

`required_acceptance_set_hash`는 중복 제거한 exact required ID를 문자열 오름차순으로
정렬한 JSON array를 3.3의 compact UTF-8 JSON으로 직렬화한 bytes의 SHA-256이다. prefix나
newline은 hash material에 포함하지 않는다. 현재 118개 set의 값은 다음과 같다.

```text
d740a14dbcc471e588fc2a03776a216e7bc4c2e6053497d604f3e9804cca913e
```

publisher binding, 모든 artifact와 Gate validation policy가 이 값을 exact 비교한다. ID가
추가·삭제·변경되면 test-plan version과 이 hash를 함께 갱신하고 기존 set을 재사용하지 않는다.

## 4. Content-addressed filesystem store

### 4.1 Root와 layout

v1 store는 PostgreSQL이 아닌 deployment-owned persistent filesystem이다. server-owned
configuration key는 `CRESTA_ARTIFACT_ROOT`이며 Gate payload나 request가 값을 제공할 수 없다.
Activation evidence root는 다음과 같다.

```text
<CRESTA_ARTIFACT_ROOT>/activation-evidence/
```

표준 deployment mapping은 deployment root와 인접한 persistent `artifacts/` directory를
`CRESTA_ARTIFACT_ROOT`로 명시적으로 설정·mount한다. application은 사용자 home, repository
위치 또는 current working directory를 추정하지 않는다. configuration 부재, 빈 값, root
부재·비-directory·접근 불가는 fail-closed다. source repository에서는 generated
`artifacts/`를 계속 Git에서 제외한다.

content-addressed layout은 고정한다.

```text
activation-evidence/
  sha256/
    ab/
      <64hex>.json
```

`ab`는 digest의 첫 두 문자다. caller는 directory, filename 또는 확장자를 제공하지 않는다.

### 4.2 `evidence_ref` namespace와 안전한 mapping

v1 reference syntax는 다음 정규식만 허용한다.

```text
^sha256:[0-9a-f]{64}$
```

예: `sha256:0123456789abcdef...<64 hex total>`이다. reference 길이는 71자다.
Activation `SafetyEvidence.evidence_ref`는 Pydantic non-empty string이고 Gate payload는 DB
`Text`에 저장된다. 저장소에서 발견된 다른 `evidence_ref` column의 128자 상한보다도 짧으므로
field widening이나 migration이 필요하지 않다.

`evidence_ref`는 path가 아니다. resolver는 digest만 추출해 위 layout을 구성한다. `../`,
separator, absolute path, drive/UNC path, URL, percent-encoded path, `file:`, `http:`와
`https:`는 모두 `INVALID_REFERENCE`다. resolved path가 configured root 아래인지 확인하되
string prefix만으로 경계를 판단하지 않는다. symlink와 non-regular file은 허용하지 않는다.

### 4.3 Write-once와 retention

발행은 create-only다. target이 없을 때만 temporary file을 같은 filesystem에 만들고 canonical
bytes를 완전히 쓴 뒤 create-only atomic publication으로 최종 이름을 획득한다. 기존 target과
identical bytes이면 idempotent success다. 같은 digest path에 다른 bytes가 있으면 overwrite,
rename replacement 또는 repair를 시도하지 않고 corruption/security failure로 중단한다.

filesystem 자체에 cryptographic WORM 기능을 요구하지 않는다. 안전성은 content-addressed
path, Gate의 frozen `evidence_hash`, validation/revalidation마다 수행하는 SHA-256으로 구성된다.
저장 bytes 수정은 hash 또는 canonical validation mismatch를 일으켜 fail-closed한다.

MOCK/development artifact는 무기한 또는 명시적 operator cleanup까지 보존할 수 있다. 어떤
`ConfigurationVersion`이라도 참조하는 artifact는 자동 또는 수동 cleanup 대상이 아니다.
자동 garbage collection과 Stage B용 deletion API는 만들지 않는다. 향후 LIVE archival,
retention, off-host copy와 삭제 승인은 별도 LIVE readiness 정책이다.

## 5. 생성 권한과 test binding

### 5.1 Publisher authority

유일한 normative creation role은 **Cresta Activation Acceptance Publisher**다. 이는 명시적
deployment/operator tool이며 Gate API, Finalizer, agent, broker, sourced-handoff와 scheduler는
artifact를 생성·수정·삭제할 수 없다. Production API의 store mount와 resolver는 read-only다.

publisher는 다음 순서만 수행한다.

1. exact candidate commit과 deployment authority 값을 고정한다.
2. version-controlled binding에서 required test ID를 찾는다.
3. bound authoritative node 전체의 실행 결과를 소비한다.
4. 하나라도 FAIL, ERROR, SKIP, XFAIL, XPASS, missing, unbound, PARTIAL, PLANNED 또는 NOT_RUN이면
   그 ID의 PASSED artifact를 만들지 않는다.
5. exact revision metadata와 non-secret result summary로 strict body를 구성한다.
6. canonical JSON, size bound와 SHA-256을 계산한다.
7. content-addressed target에 create-only로 발행한다.
8. `evidence_ref`와 `evidence_hash`를 반환한다.

`--force-pass`, operator assertion, manually typed PASS와 fuzzy name matching은 금지한다.

### 5.2 Machine-readable binding

implementation은 `backend/tests/activation_acceptance_bindings.json`에 version-controlled
`activation-acceptance-bindings-v1` document를 둔다. 문서는 exact
`test_plan_version`, `required_acceptance_set_hash`와 test ID 오름차순의 binding을 가지며 각
binding은 `test_id`, `requirement_ids`, `test_nodeids`, `backend` exact field만 가진다. 한 ID가
여러 node를 요구하면 모두 통과해야 하며 combined proof가 필요한 이유는 `TEST_PLAN.md`의
해당 ID acceptance 의미로 추적 가능해야 한다. binding set은 required 118개와 exact 일치한다.

## 6. Revision, environment와 freshness authority

### 6.1 Exact authority binding

`ActivationValidationPolicy`는 deployment가 신뢰하는 다음 값을 명시적으로 받아 모든 Gate
`SafetyEvidence`와 resolved artifact body에 exact equality를 요구한다.

- deployed full Git commit SHA
- `test_plan_version`
- `spec_version`
- expected Alembic migration revision
- deployment environment
- `required_acceptance_set_hash`

artifact와 `SafetyEvidence`에 공통인 `test_id`, `requirement_ids`, `result`, `code_revision`,
`test_plan_version`, `spec_version`, `executed_at`, `freshness_contract`도 exact 일치해야 한다.
한 artifact를 다른 evidence descriptor에 재지정할 수 없다. 현재처럼 evidence item끼리 값이
같은지만 검사하는 것은 충분하지 않다.

현재 Stage B authority는 다음과 같다.

```yaml
code_revision: <deployed exact 40-character commit>
test_plan_version: cresta-v2-activation-test-plan-2026-09-01.1
spec_version: cresta-v2-activation-spec-set-2026-09-01.1
migration_revision: 20260829_0044
environment: MOCK
required_acceptance_set_hash: d740a14dbcc471e588fc2a03776a216e7bc4c2e6053497d604f3e9804cca913e
```

commit이 하나라도 바뀌면 기존 artifact는 호환되지 않는다. prefix, ancestor, cherry-pick 내용
동등성이나 branch name으로 대체하지 않는다. DB actual readiness의 `/readyz`와 migration head
검사는 이 artifact binding과 별도의 필수 안전 경계다.

deployment-owned exact code authority key는 `CRESTA_DEPLOYED_REVISION`이며 full 40-character
lowercase Git commit SHA만 허용한다. application은 container 안에서 `git`을 실행하거나 image
tag, branch, cwd와 repository path에서 이 값을 추정하지 않는다. artifact store key는 기존
`CRESTA_ARTIFACT_ROOT`다. 둘 중 하나라도 부재·malformed이면 production Gate evidence
validation은 `ACTIVATION_GATE_EVIDENCE_UNAVAILABLE`로 fail-closed한다. test-plan/spec-set/
migration/required-set identity는 이 명세와 application의 version-controlled exact constant를
사용하고 environment는 기존 `Settings.environment`를 사용한다.

### 6.2 Freshness

v1 기본 contract는 `EXACT_REVISION`이며 wall-clock TTL이 아니다. code, test-plan, spec,
migration, environment 또는 required-set hash 중 하나라도 expected authority와 다르면 stale다.
현재 required 118개에서 이 계약보다 별도의 artifact wall-clock expiry를 요구하는 기존
normative acceptance는 발견되지 않았으므로 예외는 없다.

Gate payload 자체의 `validated_at < valid_until`과 DB-authoritative expiry는 그대로 유지한다.
미래의 시간 민감 artifact class는 기존 `valid_until` 지원을 사용할 수 있지만 해당 test ID,
시간 authority와 duration source를 명세한 뒤에만 허용한다. `EXACT_REVISION`에 임의 24시간,
7일 또는 publisher-selected TTL을 합성하지 않는다.

## 7. Production resolver

### 7.1 Interface와 책임

resolver interface는 `Callable[[str], bytes]`를 유지한다. input은 v1 `evidence_ref`, output은
store의 exact canonical bytes다. resolver는 read-only이며 다음만 수행한다.

- exact reference syntax와 digest를 검증한다.
- configured root 아래의 deterministic path를 계산하고 traversal/symlink를 차단한다.
- regular file, 접근 가능성과 65,536-byte 상한을 검사한다.
- bounded read로 exact bytes를 반환한다.

Gate validator는 strict artifact schema/canonical bytes, SHA-256, artifact/descriptor identity,
revision, freshness, complete acceptance set과 runtime snapshot consistency를 소유한다. resolver가
PASSED 또는 Gate eligibility를 판단하지 않는다.

### 7.2 Failure taxonomy

내부 category는 다음 exact set이다.

| category | 의미 | public mapping |
| --- | --- | --- |
| `INVALID_REFERENCE` | namespace, digest 또는 safe-path 규칙 위반 | `ACTIVATION_GATE_INVALID`, 422 |
| `NOT_FOUND` | content-addressed regular file 부재 | `ACTIVATION_GATE_INVALID`, 422 |
| `UNREADABLE` | 존재하지만 permission/type/size/complete-read 조건 위반 | `ACTIVATION_GATE_EVIDENCE_UNAVAILABLE`, 503 |
| `CORRUPT_OR_HASH_MISMATCH` | non-canonical body, digest path 또는 expected hash 불일치 | `ACTIVATION_GATE_INVALID`, 422 |
| `STORE_UNAVAILABLE` | root 미설정·부재·접근 불가 또는 storage I/O 장애 | `ACTIVATION_GATE_EVIDENCE_UNAVAILABLE`, 503 |

모든 category는 OPEN create/validate/activate와 live revalidation을 fail-closed한다. 503은 동일
authority로 재시도 가능한 infrastructure 상태일 뿐 bypass나 기존 검증 결과 재사용을
허용하지 않는다. public response, audit와 log는 host path나 artifact body를 포함하지 않고
safe category, correlation ID와 digest prefix 이하의 최소 식별자만 기록한다.

configuration이 없으면 `_unavailable_evidence_loader`가 계속 fail-closed default다. Concrete
filesystem resolver는 `CRESTA_ARTIFACT_ROOT`와 read-only mount가 명시적으로 배포됐을 때만
dependency로 선택한다. 임의 cwd 또는 repository scan fallback은 없다.

## 8. Publication lifecycle와 snapshot boundary

Stage B evidence lifecycle은 다음 순서를 고정한다.

```text
exact candidate commit
→ version-controlled ID binding
→ authoritative acceptance execution
→ all-bound-nodes PASS
→ Cresta Activation Acceptance Publisher
→ create-only content-addressed artifacts
→ Gate DRAFT의 118 evidence references
→ resolver + Gate validation
→ VALIDATED
→ live evidence/snapshot revalidation
→ ACTIVE + OPEN
```

필수 ID 하나라도 fail, skip, error, missing, unbound 또는 stale이면 complete set을 발행하거나
OPEN할 수 없다. Gate API는 artifact publishing의 일부가 아니며 artifact를 쓰지 않는다.

evidence artifact와 frozen runtime snapshot은 독립적인 Gate prerequisite다. 118개 artifact가
모두 유효해도 C/B/A PolicyProfile `3/3 ACTIVE`, Scout route `4/4 ACTIVE`, Decision route
`3/3 ACTIVE`, 각 route의 required VALIDATED model/prompt/fallback provenance와 live DB hash가
모두 맞지 않으면 Gate는 OPEN할 수 없다. artifact는 runtime configuration을 생성하거나
권한을 부여하지 않는다.

2026-09-01 읽기 전용 배포 확인 기준으로 PolicyProfile은 `0/3`, Scout ACTIVE route는 `4/4`,
Decision ACTIVE route는 `0/3`, Decision prompt profile은 `0/3`이므로 현재 배포는 snapshot
prerequisite를 충족하지 않는다. 이 사실은 artifact contract의 완성 여부와 독립적이다.

## 9. Trust model과 장애 행동

신뢰 경계는 다음과 같다.

Trusted:

- repository-controlled acceptance binding
- reviewed publisher code와 exact candidate checkout
- authorized deployment operator
- explicitly configured artifact root
- deployment-supplied validation authority와 Gate validator

Untrusted until validated:

- request가 제공한 `evidence_ref`
- artifact bytes와 body metadata
- manually supplied PASS text
- caller-provided path/URL
- 현재 DB snapshot과 다른 historical configuration assertion

artifact 수정은 canonical/hash mismatch, 삭제는 `NOT_FOUND`, revision 변경은
`EXACT_REVISION` mismatch, store 장애는 `STORE_UNAVAILABLE` 또는 `UNREADABLE`로 분류하며
모두 fail-closed한다. 마지막 성공 bytes를 process memory나 다른 path에서 대신 사용하지
않고 missing artifact를 합성하지 않는다.

## 10. 검증·인수 조건

- one-ID/one-artifact와 exact 118-item set이 machine binding으로 재현된다.
- canonical serializer가 key order, Unicode, timestamp, duplicate key, NaN/Infinity, newline과
  65,536-byte 경계를 결정론적으로 검증한다.
- publisher가 non-PASS와 unbound node를 발행하지 않고 identical publish만 idempotent하다.
- reference parser와 resolver가 traversal, symlink, URL, missing, unreadable, oversized,
  corruption과 unavailable root를 taxonomy대로 fail-closed한다.
- Gate policy가 deployed code/test-plan/spec/migration/environment/required-set hash를 artifact와
  exact 비교한다.
- production API는 artifact root를 read-only로 사용하고 Gate endpoint에서 write가 0건이다.
- PostgreSQL migration 없이 기존 `ConfigurationVersion` Gate lifecycle과 exact-one/CAS를
  유지한다.
- evidence complete 상태와 runtime snapshot complete 상태를 각각 독립적으로 검증한다.

## 11. 미결정·보류 항목

- 향후 LIVE artifact archival, off-host replication, retention, deletion approval와 incident hold
- cryptographic signature, transparency log 또는 external attestations
- wall-clock freshness가 필요한 미래 acceptance class와 해당 time authority
- SHA-256 collision 대응을 포함한 future namespace/schema migration

위 항목은 현재 MOCK Stage B implementation을 막지 않으며 명세 없이 v1에 자동 적용하지 않는다.

## 12. 후속 implementation slice

Exact-revision artifact는 마지막 code/test 변경 뒤에만 발행할 수 있으므로 구현 순서를 다음과
같이 고정한다.

1. **11B.0B1 — Evidence foundation:** strict artifact model, canonical serializer, 64 KiB
   content-addressed store, create-only publisher, binding document schema와 safe reference parser.
2. **11B.0B2 — Candidate closure:** 118개 machine binding 완성, PLANNED/PARTIAL acceptance 및
   PostgreSQL gap 해소, concrete resolver, deployment authority binding, Gate API/resolver/
   concurrency test까지 구현한다. 이 slice 뒤 candidate code를 동결한다.
3. **11B.0B3 — Exact evidence publication:** 동결된 exact commit에서 118개 bound acceptance를
   실행하고 artifact를 발행하며 production read-only resolver와 revision/migration/environment
   binding을 deployment에서 검증한다. 이후 code나 normative document가 바뀌면 이 slice를
   다시 수행한다.
4. **11B.0B4 — Runtime snapshot/Gate acceptance:** PolicyProfile 3/3, Scout route 4/4,
   Decision route/prompt 3/3을 기존 control-plane으로 준비하고 DRAFT→VALIDATED→ACTIVE/OPEN,
   PostgreSQL exact-one/CAS와 deployment fail-closed를 검증한다.

11B.0B4 완료 전에는 Stage B handoff를 활성화하지 않는다.
