# 구현 착수 준비도 검토

## 1. 목적

현재 Cresta 명세가 구현을 시작하기에 충분한지 점검하고, 발견한 공백·충돌·외부 검증 항목과 구현 착수 조건을 기록한다. 검토 기준일은 2026-07-31이다.

## 2. 검토 결과

### 2.1 이번 검토에서 보완한 치명적 공백

| 영역 | 기존 위험 | 조치 |
| --- | --- | --- |
| 데이터베이스 | 멱등성·체결·포지션 트랜잭션과 migration 기준 부재 | [데이터베이스 명세](DATABASE_SPEC.md) 작성 |
| 시장데이터 | KRX/NXT 분리, 지연·갭·분봉·지표 기준 부재 | [시장데이터 명세](MARKET_DATA_SPEC.md) 작성 |
| Scout·Core | 모델 입력·출력·실패·재현 계약 부재 | [AI 판단 계약](AI_DECISION_SPEC.md) 작성 |
| API | endpoint 목록만 있고 멱등성·version·오류·WebSocket 복구 계약 부족 | [API 명세](API_SPEC.md) 보강 |
| 운영 | 배포·백업·복원·장애 시 거래 게이트 부재 | [운영 명세](OPERATIONS_RUNBOOK.md) 작성 |
| Guard | 수치 허용범위와 재인증 시점 미확정 | 범위·TOTP·변경 사유 기준 확정 |
| 주문가격 | 호가단위 반올림과 손절 재호가 경계 미확정 | 보수적 보정 규칙과 단일 MVP 정책 확정 |
| 문서 상태 | 존재하지 않는 정적 Console 파일을 구현 완료로 표시 | 참고 콘셉트 상태로 정정 |

### 2.2 구현 시작 가능 범위

다음은 현재 명세를 기준으로 구현을 시작할 수 있다.

- 저장소·Docker 개발환경 기본 골격
- PostgreSQL migration과 Redis 역할 분리
- 인증, TOTP, 서버 세션과 감사 기반
- 설정 버전·검증·영향 미리보기
- 내부 주문 상태 머신, paper broker와 재동기화 시뮬레이터
- Watch 정규화 모델, fixture 기반 분봉·지표·최신성 검사
- Scout·Core 인터페이스, JSON Schema 검증과 mock model
- 다중 에이전트 DAG·증거·stage schema와 LLM Provider Registry의 SHADOW 기반
- API·WebSocket 계약과 Console 화면 골격

### 2.3 외부 확인 전 활성화 금지 범위

| 항목 | 상태 | 제한 |
| --- | --- | --- |
| 키움 모의계좌·App Key | secret 주입과 MOCK 인증·`ka10001`·10자리 `ka00001` 일치 확인 완료 | 상시 worker·재동기화 전 Broker `READY` 금지 |
| 고정 출구 IP `180.68.4.149` | 2026-08-03 실제 서버 출구 확인 완료 | IP 변경 감지와 상시 worker 게이트는 후속 구현 |
| 키움 주문·체결 필드 | 실제 mock capture 전 잠정 | production adapter mapping 확정 금지 |
| 키움 호출 제한·WebSocket heartbeat | 실측 전 초기값 | 측정 가능한 설정으로 유지 |
| Scout·Core 모델 제공자 | 다중 provider·gateway 명세 완료, 계정·모델 미선정 | 실제 AI 호출과 운영 route 활성화 금지, deterministic Mock Adapter·SHADOW만 사용 |
| NXT/SOR 주문 | 키움 모의투자 미지원 | 표시·분석 외 주문 금지 |
| 도메인·내부 upstream | `trade.mihoservice.xyz`·`127.0.0.1:7788` 확정 | 호스트 Nginx·TLS 실서버 검증 전 외부 공개 완료 처리 금지 |
| TLS 자동 발급·갱신·외부 백업·알림 채널 | 운영값 미정 | 외부 공개 배포 완료 처리 금지 |

외부 확인 항목은 내부 구현 전체를 막지 않지만 해당 adapter 또는 운영 게이트를 활성화하면 안 된다.

## 3. 명세 간 우선순위와 경계

```text
제품 요구사항
→ 영역별 상세 명세
→ API·DB 계약
→ 운영·테스트 명세
```

- 주문 안전성 충돌은 Guard, 주문 상태 머신, 재동기화 명세 순으로 보수적인 규칙을 적용한다.
- 외부 키움 실제 상태는 내부 예상보다 우선하지만 확인 없이 외부 데이터를 자동 전략 편입하지 않는다.
- UI는 API 응답과 상태를 표시할 뿐 주문 성공을 추정하지 않는다.
- 구현이 명세와 달라져야 하면 코드를 먼저 맞추지 않고 관련 명세와 테스트를 함께 변경한다.

## 4. 구현 착수 게이트

각 기능 구현 전 다음을 확인한다.

1. 기준 문서와 요구사항 ID가 존재한다.
2. DB/API/event schema와 오류 상태가 정의돼 있다.
3. 정상·실패·응답유실·재시작 테스트가 `TEST_PLAN.md`에 연결돼 있다.
4. 외부 미확정값은 fixture 또는 interface 뒤에 격리돼 있다.
5. 비밀값·실거래 기능이 개발 기본값으로 활성화되지 않는다.

### 4.1 완료된 구현 slice: Provider Registry 기반

외부 모델 연결 전에 구현 경계를 만드는 `LLM Foundation v1`은 2026-08-05 로컬 구현·검증을 완료했다.

포함 범위:

1. `llm_provider_profiles`, `llm_model_profiles`, `llm_role_routes`, `llm_invocations` migration
2. canonical request/result와 capability schema
3. deterministic Mock Provider Adapter와 registry/router
4. provider/model/route 조회·초안·검증 API
5. Console의 Provider·Model·Role Route 읽기 및 초안 화면
6. secret 원문 미저장·redaction·route 이중 활성화·SHADOW 주문 0건 자동시험

제외 범위:

- 실제 OpenAI·Anthropic·Gemini credential 등록 및 외부 호출
- Intel 웹 수집과 실제 뉴스·공시 사용
- Core 운영 route 전환, 승인 또는 주문 생성
- Vercel·Ollama 성능 비교

`T-LLM-001~003`, `T-LLM-007~009` 중 외부 네트워크가 필요 없는 fixture 범위, migration `20260805_0013`, API·Console 회귀시험을 통과했다. 실제 PostgreSQL 적용과 외부 Adapter는 미검증 상태다.

### 4.2 완료된 구현 slice: Agent Runtime v1

Foundation의 Mock route만 사용하는 DIAGNOSTIC 다중 에이전트 runtime은 2026-08-06 로컬 구현·검증을 완료했다. `agent_runs`, `agent_stage_runs`, evidence 저장과 Intel→Verify→4 Scout→Core DAG, API·Console, 멱등 재요청과 주문 0건을 검증했다. 실제 웹 수집·외부 LLM·승인·주문은 연결하지 않았다.

### 4.3 완료된 구현 slice: 역할별 모델 배정 관리

Provider·Model 카탈로그와 역할별 현재 배정·이력을 분리하고 같은 검증 모델을 여러 역할에서 재사용하도록 구현했다. generation parameter 상속·override, 중복 `VALIDATED` 후보 명시 선택, 5개 역할의 TOTP 1회 원자 활성화와 ACTIVE route 기반 DIAGNOSTIC 준비도를 로컬 검증했다. 외부 credential과 실제 Adapter는 연결하지 않았다.

### 4.4 다음 구현 slice: Agent Worker v2

실서버 PostgreSQL에서 `0015`, 역할 배정 일괄 활성화와 수동 DIAGNOSTIC run을 확인한 뒤 stage claim·lease·fencing·timeout과 scheduler admission을 설계한다. 이 단계도 deterministic Mock만 사용하며 외부 source·provider 연결은 별도 게이트로 유지한다.

## 5. 검증·인수 조건

- `AGENTS.md`에 모든 기준 문서가 등록돼 있다.
- 요구사항 그룹별 테스트 계획이 존재한다.
- 문서의 로컬 링크가 실제 파일을 가리킨다.
- 구현 상태가 명세·구현·검증을 구분한다.
- 외부 미검증 항목이 활성 기능으로 표현되지 않는다.

## 6. 미결정·보류 항목

구현을 전면 차단하는 내부 명세 공백은 이번 검토 범위에서 해소했다. 위 2.3의 외부 확인 항목은 해당 통합 단계 전에 반드시 확정한다.
