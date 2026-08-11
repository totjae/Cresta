# Cresta 인증 및 보안 명세

> **현재 유효 정책:** 개발 단계에서는 `SEC-DEV-001~004`가 재인증 관련 일반 요구사항보다 우선한다. TOTP는 로그인에만 사용하며 로그인 이후 설정 변경, Provider·역할 배정, MOCK 연결 시험에는 별도 TOTP를 요구하지 않는다. 아래 재인증 계약은 서비스 완성 후 위험 분석을 거쳐 명시적으로 다시 활성화하기 전까지 미래 후보 계약이다.

## 1. 목적

Cresta Web UI와 API 접근을 개인용 계정의 `사용자 ID + 비밀번호 + TOTP` 로그인으로 보호한다. 거래 승인·위험 설정·비상정지 해제 같은 고위험 행동의 추가 재인증은 향후 위험 분석 대상으로 유지한다.

사용자 ID는 계정을 식별하는 값이며 인증 요소는 비밀번호와 TOTP다. 따라서 이 방식은 비밀번호 기반 요소와 소유 기반 요소를 결합한 2단계 인증이다.

## 2. 적용 범위

- Web UI 로그인·로그아웃과 세션 관리
- 최초 관리자 계정 생성과 TOTP 등록
- 로그인 실패 제한, 계정 잠금과 복구
- 거래·설정 관련 고위험 행동의 재인증
- 비밀번호, TOTP secret, 복구 코드와 세션 비밀의 저장
- 인증·인가 감사 로그

키움 API 자격증명의 저장과 주입은 [키움 Broker Adapter 명세](KIWOOM_BROKER_SPEC.md)를 함께 따른다.

## 3. 상세 명세

### 3.1 계정과 접근 원칙

| ID | 요구사항 |
| --- | --- |
| SEC-001 | 첫 버전은 개인용 관리자 계정 1개를 지원하며 익명·게스트 접근을 허용하지 않는다. |
| SEC-002 | 로그인하지 않은 사용자는 로그인, 정적 로그인 자산과 최소 상태 확인 경로 외의 Web UI·API·WebSocket에 접근할 수 없다. |
| SEC-003 | 정상 로그인은 등록된 사용자 ID, 비밀번호와 유효한 TOTP를 모두 요구한다. |
| SEC-004 | ID 존재 여부, 비밀번호 오류와 TOTP 오류를 외부 응답에서 구분하지 않는다. |
| SEC-005 | 모든 인증·거래 트래픽은 HTTPS로만 제공하고 HTTP 요청은 HTTPS로 전환한다. |
| SEC-006 | 인증 검사는 Console 클라이언트가 아니라 FastAPI 서버에서 수행한다. |

### 3.2 로그인 흐름

```text
ID·비밀번호 제출
→ 서버의 통합 실패 제한 검사
→ 비밀번호 검증
→ 임시 로그인 도전(challenge) 발급
→ TOTP 제출
→ TOTP 검증 및 challenge 1회 사용 처리
→ 서버 세션 발급
→ 대시보드 이동
```

| ID | 요구사항 |
| --- | --- |
| SEC-010 | 비밀번호 검증 성공 전에는 TOTP 검증용 challenge를 발급하지 않는다. |
| SEC-011 | 로그인 challenge는 5분 이내 만료되고 한 번만 사용할 수 있으며 성공·실패 횟수를 서버가 관리한다. |
| SEC-012 | TOTP는 RFC 6238 호환 6자리, 30초 주기를 사용하고 서버 시각 기준 앞뒤 1개 시간 구간까지만 허용한다. |
| SEC-013 | 같은 TOTP 시간 구간의 코드를 동일 계정 로그인에 재사용할 수 없다. |
| SEC-014 | 로그인 성공 시 기존 실패 횟수를 초기화하고 성공 시각·요청 IP·사용자 에이전트를 감사 기록한다. |
| SEC-015 | 로그인 화면은 비밀번호와 TOTP를 URL, 브라우저 로그, 분석 도구 또는 오류 추적 데이터에 포함하지 않는다. |

비밀번호와 TOTP는 한 화면에서 입력할 수 있지만 서버에서는 위 순서대로 검증한다. 비밀번호 검증 여부를 화면 문구로 노출하지 않는다.

### 3.3 최초 등록과 복구

| ID | 요구사항 |
| --- | --- |
| SEC-020 | 최초 관리자 계정은 서버 로컬 관리 명령으로만 생성하고 공개 Web UI 회원가입을 제공하지 않는다. |
| SEC-021 | 최초 로그인 전에 로컬 관리 절차로 TOTP secret을 생성하고 사용자가 인증 앱으로 등록한 뒤 연속된 유효 코드로 소유를 확인한다. |
| SEC-022 | QR 코드와 수동 입력용 TOTP secret은 등록 절차에서만 표시하고 이후 Web UI·API에서 다시 조회할 수 없다. |
| SEC-023 | 복구 코드는 일회용으로 생성하고 해시만 저장한다. 사용자는 생성 직후 오프라인으로 보관한다. |
| SEC-024 | TOTP 분실 시 일반 로그인 우회는 허용하지 않는다. 서버 로컬 관리 명령과 복구 코드로 본인 확인 후 기존 secret·세션을 폐기하고 새 TOTP를 등록한다. |
| SEC-025 | TOTP 재등록, 비밀번호 초기화와 복구 코드 재발급은 모두 감사 기록하고 활성 세션을 전부 폐기한다. |

### 3.4 비밀번호와 비밀 저장

| ID | 요구사항 |
| --- | --- |
| SEC-030 | 비밀번호는 최소 14자이며 흔한 비밀번호와 사용자 ID를 포함한 비밀번호를 거부한다. |
| SEC-031 | 비밀번호는 Argon2id와 사용자별 무작위 salt로 해시하고 평문·복호화 가능한 형태로 저장하지 않는다. 파라미터는 배포 서버에서 조정하고 버전과 함께 저장한다. |
| SEC-032 | TOTP secret은 애플리케이션 데이터 암호화 키로 암호화해 저장하고 암호화 키는 DB와 분리된 `/home/totquf4171/cresta/secrets` 하위 Docker secret으로 주입한다. |
| SEC-033 | TOTP secret, 사용자·DB·Broker 비밀번호, 복구 코드와 세션 토큰은 로그·감사 이벤트·오류 응답·백업 보고서에 기록하지 않는다. |
| SEC-034 | 비밀값 비교는 가능한 경우 상수 시간 비교를 사용하며 비밀값을 클라이언트 상태 저장소나 `localStorage`에 저장하지 않는다. |

Argon2id 초기 최소값은 memory 64 MiB, iterations 3, parallelism 1로 한다. 배포 서버에서 로그인 검증 목표 250~750ms 범위로 상향 조정할 수 있지만 이 최소값 아래로 낮추지 않는다.

### 3.5 실패 제한과 잠금

| ID | 요구사항 |
| --- | --- |
| SEC-040 | ID·비밀번호와 TOTP 실패를 계정·IP 기준의 하나의 인증 실패 예산으로 집계한다. |
| SEC-041 | 5회 연속 실패 시 15분 동안 계정을 잠그며 반복 잠금은 지수적으로 대기시간을 늘린다. |
| SEC-042 | IP 단위 요청 제한을 별도로 적용하고 제한 여부를 계정 존재 정보로 사용할 수 없게 동일한 오류 형식을 사용한다. |
| SEC-043 | 잠금 중에도 공개 응답은 일반 인증 실패와 동일하게 유지하되 관리자용 감사 로그에는 원인을 구분한다. |
| SEC-044 | 잠금 해제 Web API는 제공하지 않으며 자동 만료 또는 서버 로컬 관리 절차만 허용한다. |

### 3.6 세션과 WebSocket

| ID | 요구사항 |
| --- | --- |
| SEC-050 | 인증 성공 시 추측 불가능한 서버 측 세션을 발급하고 브라우저에는 `Secure`, `HttpOnly`, `SameSite=Strict` 쿠키만 저장한다. |
| SEC-051 | 세션은 30분 비활동 시 만료되고 로그인 후 최대 8시간을 넘지 않는다. 로그아웃하면 서버 세션과 WebSocket을 즉시 폐기한다. |
| SEC-052 | 로그인 성공과 권한 변경 시 세션 식별자를 회전해 세션 고정을 방지한다. |
| SEC-053 | 상태 변경 API는 세션 인증과 CSRF 토큰 검증을 모두 요구하며 `Origin`도 허용 목록과 대조한다. |
| SEC-054 | WebSocket 연결 시 활성 세션을 검증하고 세션 만료·로그아웃·강제 폐기 시 연결을 종료한다. |
| SEC-055 | 인증된 페이지는 민감한 계좌·거래 정보가 공유 캐시에 저장되지 않도록 `Cache-Control: no-store`를 사용한다. |

### 3.7 인가와 고위험 행동 재인증

| ID | 요구사항 |
| --- | --- |
| SEC-060 | 첫 버전의 관리자 계정은 Console 기능을 사용할 수 있지만 Guard와 무결성 규칙을 우회할 권한은 갖지 않는다. |
| SEC-061 | 주문 승인, 자동매매 활성화, 투자·손실 한도 완화, TOTP 변경과 `EMERGENCY_LIQUIDATE` 해제는 최근 5분 이내 TOTP 재인증을 요구한다. |
| SEC-062 | 고위험 행동 재인증은 대상 행동·요청 식별자에 결합하며 다른 행동에 재사용하지 않는다. |
| SEC-063 | 비상정지 활성화는 로그인된 세션에서 즉시 가능하게 하되, 비상정지 해제는 TOTP 재인증과 재동기화를 모두 요구한다. |
| SEC-064 | 인증·인가 실패로 요청이 거부되면 주문이나 설정 변경이 일부라도 적용되어서는 안 된다. |
| SEC-065 | Web MOCK 주문 시험은 대상 ID에 결합된 TOTP 재인증 증명을 주문 생성 transaction에서 1회 소비하고, 성공 시 사용자·종목·유형·수량을 감사하되 TOTP·proof 원문은 기록하지 않는다. |

### 3.8 감사와 개인정보 최소화

| ID | 요구사항 |
| --- | --- |
| SEC-070 | 로그인 성공·실패, 잠금, 로그아웃, 세션 폐기, TOTP 등록·재설정과 고위험 재인증 결과를 감사 로그에 기록한다. |
| SEC-071 | 감사 로그에는 actor, event, result, occurred_at, IP, user-agent 요약과 correlation_id를 기록하되 입력한 인증값은 기록하지 않는다. |
| SEC-072 | 인증 감사 로그는 거래 감사 로그와 동일한 변조 방지·접근 통제를 적용하고 보존 기간은 운영 명세에서 확정한다. |

### 3.9 LLM Provider 비밀과 외부 전송

| ID | 요구사항 |
| --- | --- |
| SEC-080 | LLM API key, Gateway token과 service account private key는 DB가 아닌 Docker secret 또는 동등한 비밀 저장소에 보관하고 애플리케이션 UID 10001만 읽는다. |
| SEC-081 | Provider credential 등록·교체는 write-only 입력, TOTP 재인증, CSRF와 감사 기록을 요구하고 성공 응답에도 원문을 반환하지 않는다. |
| SEC-082 | LLM prompt와 호출 metadata에는 사용자 ID·계좌번호·세션·TOTP·Broker credential·Authorization header를 포함하지 않는다. |
| SEC-083 | 사용자 지정 Provider endpoint는 SSRF allowlist, TLS 검증, 리디렉션·응답 크기 제한을 적용하며 Ollama profile 외 loopback/private endpoint를 기본 거부한다. |
| SEC-084 | 웹·공시·뉴스 원문은 비신뢰 입력으로 표시하고 모델이 포함된 지시나 내부 URL·비밀 요청을 실행할 수 없게 도구 권한을 분리한다. |
| SEC-085 | Provider·Gateway별 데이터 보존·학습·지역 정책 확인 상태를 저장하고 미확인 외부 전송 profile은 활성 route에 사용할 수 없다. |
| SEC-086 | 간편 Provider 등록 TOTP proof는 사용자·연결 이름·Adapter에 결합하고 실제 모델 목록 조회 전에 한 번 소비한다. 인증·조회 실패 시 Provider DB 행과 secret 파일을 만들지 않는다. |
| SEC-087 | 공식 Provider 모델 조회는 서버 고정 HTTPS endpoint, redirect 금지, 15초 timeout, 5 MiB 응답 제한을 적용하며 원문 upstream 오류를 UI·API·로그에 전달하지 않는다. |
| SEC-088 | OpenDART API key는 Docker secret 파일로만 API·Agent에 주입하고 query log, DB, stage 출력, evidence facts·hash와 오류에 포함하지 않는다. endpoint는 공식 HTTPS host로 고정한다. |
| SEC-089 | 프롬프트 개선 이력은 구조화 model output만 저장한다. key 이름에 secret·token·credential·authorization·password·TOTP가 포함되거나 64 KiB를 넘으면 저장과 채택을 모두 거부한다. |
| SEC-090 | KRX OPEN API 인증키는 Docker secret 파일로만 API·Agent에 주입하고 `AUTH_KEY` header, DB, stage 출력, evidence facts·hash와 오류에 포함하지 않는다. endpoint는 공식 HTTPS host와 승인된 일별매매 path로 고정한다. |

## 4. 오류·예외 또는 경계 조건

- 서버 시각 동기화가 허용 오차를 벗어나면 신규 로그인과 고위험 재인증을 fail-closed 처리하고 운영 경보를 발생시킨다.
- DB·Redis 장애로 실패 횟수, challenge 또는 세션 상태를 확인할 수 없으면 로그인을 허용하지 않는다.
- 이미 로그인된 사용자가 TOTP를 재설정하면 모든 기기에서 로그아웃된다.
- 브라우저 새로고침이나 WebSocket 재연결은 새 로그인을 요구하지 않지만 활성 서버 세션을 다시 검증한다.
- 로그인 UI는 서버 오류 시 비밀번호나 TOTP 값을 자동 재전송하지 않는다.

## 5. 검증·인수 조건

- 올바른 ID·비밀번호만으로는 로그인할 수 없고 유효한 TOTP까지 검증해야 대시보드에 접근할 수 있다.
- 미인증 사용자는 보호된 REST API와 WebSocket을 사용할 수 없다.
- 재사용·만료·허용 시간 밖의 TOTP가 거부된다.
- 실패 제한, 계정 잠금과 일반화된 오류가 계정 열거를 방지한다.
- 쿠키·CSRF·세션 만료·로그아웃 동작이 명세와 일치한다.
- 고위험 행동은 대상에 결합된 최신 TOTP 재인증 없이는 실행되지 않는다.
- 저장소, DOM, 로그와 API 응답에서 비밀번호·TOTP secret·복구 코드·세션 토큰 원문이 발견되지 않는다.
- Provider credential과 Authorization header가 DB·prompt·로그·API·UI·감사 metadata에 나타나지 않는다.

참고 표준:

- TOTP: <https://www.rfc-editor.org/rfc/rfc6238>
- Argon2: <https://www.rfc-editor.org/rfc/rfc9106>

## 6. 미결정·보류 항목

- 인증 감사 로그는 거래 감사와 함께 기본 5년 보존한다.
- RFC 6238 호환 TOTP 앱을 지원하며 QR issuer는 `Cresta`, 계정 표시는 login ID를 사용한다.
- 관리자 복구는 서버 로컬 OS 계정 접근과 미사용 복구 코드를 모두 요구한다. 복구 코드도 없으면 자동 우회하지 않고 별도 운영 복구 절차가 마련될 때까지 계정을 잠금 상태로 유지한다.
## 외부 LLM credential 경계

- Provider API key는 DB, API 응답, 감사 metadata와 애플리케이션 로그에 저장하지 않는다.
- secret ref는 서버가 Provider UUID로 생성하며 사용자 입력 path와 `..`, 절대경로를 허용하지 않는다.
- API는 `/run/cresta-llm-secrets`를 write 가능하게, Agent는 동일 경로를 read-only로 mount한다.
- credential 교체는 Provider version에 묶인 TOTP 재인증 proof를 한 번 소비하고 Provider를 다시 `DRAFT`로 전환한다.
# Provider deletion and template boundary (2026-08-07)

- Provider endpoints are resolved only from server templates; template fields accept only bounded alphanumeric, underscore, and hyphen values.
- Provider deletion is a TOTP-bound destructive action and is rejected while an ACTIVE route references the Provider.
- Credential files are deleted before the Provider tombstone is committed; deletion failure closes the operation.
- Prompt validation rejects credential/TOTP/header extraction, arbitrary shell/tool execution, direct Broker calls, and direct order execution instructions. Runtime structured data is never interpolated into the system prompt.

## 2026-08-08 개발 단계 TOTP 적용 범위

- `SEC-DEV-001`: 현재 개발 단계에서 TOTP는 로그인 완료에만 요구한다. 로그인 후 설정 변경, Provider credential 등록·삭제, 역할 배정, 실행 권한 활성화와 MOCK 주문 시험은 인증 세션과 CSRF를 요구하지만 별도 TOTP 재인증 proof를 요구하지 않는다.
- `SEC-DEV-002`: `SEC-061`~`SEC-065`, `SEC-081`, `SEC-086`의 행동별 재인증 요구는 서비스 완성 후 위험 분석을 거쳐 선택적으로 재도입할 때까지 보류한다. 이 절이 해당 요구사항보다 우선한다.
- `SEC-DEV-003`: 재인증 기반시설과 감사 스키마는 향후 재도입을 위해 유지할 수 있으나 현재 Console의 로그인 이후 흐름에서는 호출하지 않는다.
- `SEC-DEV-004`: TOTP 제거가 세션 소유권, CSRF, 변경 사유, 상태 전이, 낙관적 잠금, 원자성, 비밀 원문 비노출 또는 Guard 검사를 완화하지 않는다.
