# Cresta 문서 관리 지침

이 파일은 프로젝트 명세 내용을 직접 담지 않는다. 개발 에이전트가 어떤 문서를 확인·작성·갱신해야 하는지 안내하는 **문서 색인과 문서화 규칙**만 관리한다.

## 1. 기본 규칙

1. 개발을 시작하기 전에 아래 문서 목록에서 관련 기준 문서를 확인한다.
2. 모든 설계·구현·테스트 작업은 명세를 기준으로 수행한다. 관련 명세가 없거나 요구사항이 불명확하면 코드나 테스트를 먼저 작성하지 않는다.
3. 기능을 개발할 때 API, DB, 상태, 보안, 설정, 운영 방법 등 필요한 내용을 관련 문서에 **먼저 작성하고 검토 가능한 상태로 만든 뒤** 구현한다.
4. 테스트는 명세의 요구사항 ID와 인수 조건을 기준으로 작성하며, 명세에 없는 동작을 테스트가 임의로 제품 요구사항으로 만들지 않는다.
5. 구현 결과가 기존 명세와 달라져야 한다면 구현을 정답으로 간주하지 않는다. 차이와 이유를 먼저 명세에 반영한 뒤 구현과 테스트를 맞춘다.
6. 여러 문서가 충돌하면 더 구체적인 상세 명세를 우선하되, 충돌을 그대로 두지 않고 같은 변경에서 상위 문서도 갱신한다.
7. 관련 상세 문서가 없으면 새 문서를 만들고, 같은 변경에서 이 파일의 문서 목록에 등록한다.
8. 신규 문서는 아래의 문서 등록 형식을 따른다.
9. 문서 갱신, 구현 상태와 시험 근거가 빠진 기능은 완료된 것으로 처리하지 않는다.
10. 계획, 구현 완료, 검증 완료와 외부 환경 미검증 상태를 명확히 구분한다.
11. 실제 비밀번호, API key, token, TOTP secret, DB URL과 private key를 문서에 기록하지 않는다.
12. 외부 API 규격, 거래시간과 제도처럼 변경 가능한 사실은 공식 자료로 확인하고 확인일 또는 미검증 상태를 명시한다.
13. 과거 착수 검토·단계별 구현 기록은 역사적 스냅샷으로 표시하고 현행 요구사항을 덮어쓰지 않게 한다. 현재 상태는 `IMPLEMENTATION_STATUS.md`, 현재 검증 근거는 `TEST_PLAN.md`의 최신 날짜 기록을 우선한다.
14. 같은 제약을 여러 문서에 독립적으로 복제하지 않는다. 한 상세 명세를 기준으로 두고 다른 문서는 링크·요약만 유지하며, 임시 또는 보류 정책에는 적용 기간과 우선순위를 명시한다.

### 1.1 작업 순서

모든 변경은 다음 순서를 따른다.

```text
관련 문서 확인
→ 요구사항과 미결정 사항 식별
→ 명세 작성·갱신
→ 구현
→ 명세 기반 테스트
→ 구현 상태와 시험 결과 갱신
→ 문서·코드 일치 확인
```

요청이 조사·설명만을 요구하면 파일을 변경하지 않아도 되지만, 구현을 시작할 때는 반드시 위 순서를 적용한다.

## 2. 현재 문서 목록

| 문서 | 위치 | 작성·관리할 내용 |
| --- | --- | --- |
| 프로젝트 안내 | [README.md](README.md) | 프로젝트 소개, 실행 방법과 주요 문서 진입점 |
| Backend 개발 안내 | [backend/README.md](backend/README.md) | FastAPI 개발 실행, migration과 관리자 생성 방법 |
| Frontend 개발 안내 | [frontend/README.md](frontend/README.md) | Next.js Console 범위와 개발·시험·빌드 방법 |
| 제품 요구사항 | [docs/PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md) | MVP 범위, 키움 모의투자, 행동별 실행 권한과 익일 보유 정책 |
| 거래 세션 명세 | [docs/TRADING_SESSION_SPEC.md](docs/TRADING_SESSION_SPEC.md) | 장 전 점검, KRX·NXT 세션, 감시 주기, 신규매수와 장 마감 정책 |
| 주문 실행 명세 | [docs/ORDER_EXECUTION_SPEC.md](docs/ORDER_EXECUTION_SPEC.md) | 주문 가격, 승인 범위, 미체결·부분체결·취소·재주문 정책 |
| 주문 상태 머신 명세 | [docs/ORDER_STATE_MACHINE_SPEC.md](docs/ORDER_STATE_MACHINE_SPEC.md) | Cresta 주문 상태, 키움 주문·체결 매핑과 수량 불변조건 |
| 계좌 재동기화 명세 | [docs/RECONCILIATION_SPEC.md](docs/RECONCILIATION_SPEC.md) | 시작·재연결·응답유실 시 주문·체결·잔고 대조와 외부 주문 처리 |
| 키움 Broker Adapter 명세 | [docs/KIWOOM_BROKER_SPEC.md](docs/KIWOOM_BROKER_SPEC.md) | 키움 인증, 배포 경로, 단일 worker, 환경 분리, 호출 제한과 연결 정책 |
| Guard 리스크 명세 | [docs/GUARD_RISK_SPEC.md](docs/GUARD_RISK_SPEC.md) | 투자·손실·손절·연결 위험, 중지와 비상정지 정책 |
| 사용자 설정 명세 | [docs/CONFIGURATION_SPEC.md](docs/CONFIGURATION_SPEC.md) | Web UI 설정 범위, 우선순위, 버전, 적용 시점과 영향 미리보기 |
| Web UI 명세 | [docs/WEB_UI_SPEC.md](docs/WEB_UI_SPEC.md) | 콘셉트 적용 기준, 화면 구조, 설정·승인·비상정지와 접근성 |
| 인증 및 보안 명세 | [docs/SECURITY_SPEC.md](docs/SECURITY_SPEC.md) | ID·비밀번호·TOTP 로그인, 세션, 재인증, 비밀 저장과 감사 |
| 시장데이터 명세 | [docs/MARKET_DATA_SPEC.md](docs/MARKET_DATA_SPEC.md) | KRX·NXT 시세 정규화, 최신성, 분봉·지표와 Watch 동작 |
| AI 판단 계약 | [docs/AI_DECISION_SPEC.md](docs/AI_DECISION_SPEC.md) | Scout·Core 입력·출력, 호출·실패·검증과 재현 기준 |
| 다중 에이전트 오케스트레이션 명세 | [docs/MULTI_AGENT_ORCHESTRATION_SPEC.md](docs/MULTI_AGENT_ORCHESTRATION_SPEC.md) | Intel·Verify·복수 Scout·Core의 DAG, 증거 계약, 실패 격리와 SHADOW 활성화 기준 |
| LLM Provider 및 Gateway 명세 | [docs/LLM_PROVIDER_GATEWAY_SPEC.md](docs/LLM_PROVIDER_GATEWAY_SPEC.md) | 공식 API·Gateway·Ollama Adapter, 모델 기능, route, 비밀·비용·fallback 정책 |
| 판단 실행·승인 명세 | [docs/DECISION_EXECUTION_SPEC.md](docs/DECISION_EXECUTION_SPEC.md) | AI·Guard 신호의 실행 권한 분기, Guard 재검사, 승인과 주문 생성 경계 |
| 데이터베이스 명세 | [docs/DATABASE_SPEC.md](docs/DATABASE_SPEC.md) | 테이블, 제약조건, 트랜잭션, migration, 보존과 Redis 경계 |
| 운영·장애복구 명세 | [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) | 배포, 모니터링, 백업·복원과 장애 대응 |
| 구현 준비도 검토 | [docs/IMPLEMENTATION_READINESS_REVIEW.md](docs/IMPLEMENTATION_READINESS_REVIEW.md) | 2026-07-31~08-06 구현 전 명세 공백·초기 착수 게이트를 보존한 역사적 스냅샷 |
| 시스템 설계 | [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | 전체 아키텍처, 모듈 책임, 데이터 모델과 장애·보안 원칙 |
| API 명세 | [docs/API_SPEC.md](docs/API_SPEC.md) | HTTP API, 멱등성·동시성·오류와 실시간 이벤트 계약 |
| 구현 상태 | [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | 영역별 계획·구현·검증·보류 상태 |
| 테스트 계획 | [TEST_PLAN.md](TEST_PLAN.md) | 요구사항 ID별 시험, 시험 환경과 실행 결과 |

## 3. 개발하면서 작성할 상세 문서

현재 구현 전 필수 상세 문서는 모두 생성됐다. 새 기능이 기존 문서 범위를 벗어나면 예정 문서의 기본 위치·생성 시점·작성 범위를 이 절에 먼저 기록하고, 생성 즉시 **현재 문서 목록**에 등록한다.


## 4. 개발 시 문서 갱신 기준

| 변경 내용 | 확인·갱신할 문서 |
| --- | --- |
| 제품 범위, 자동·승인, 익일 보유 | `PRODUCT_REQUIREMENTS.md`, `CONFIGURATION_SPEC.md`, `TEST_PLAN.md`, `IMPLEMENTATION_STATUS.md` |
| 투자·손실·손절·비상정지 | `GUARD_RISK_SPEC.md`, `CONFIGURATION_SPEC.md`, `API_SPEC.md`, `WEB_UI_SPEC.md`, `TEST_PLAN.md` |
| 거래시간, 감시 주기, 동시호가, 장 마감 | `TRADING_SESSION_SPEC.md`, `CONFIGURATION_SPEC.md`, `TEST_PLAN.md` |
| 가격 산정, 미체결, 부분체결, 재주문 | `ORDER_EXECUTION_SPEC.md`, `ORDER_STATE_MACHINE_SPEC.md`, `TEST_PLAN.md` |
| 키움 API·필드·오류·호출 제한 | `KIWOOM_BROKER_SPEC.md`, `ORDER_STATE_MACHINE_SPEC.md`, `API_SPEC.md`, `TEST_PLAN.md` |
| 주문 상태나 전이 | `ORDER_STATE_MACHINE_SPEC.md`, `DATABASE_SPEC.md`, `API_SPEC.md`, `TEST_PLAN.md` |
| 계좌·주문·잔고 복구 | `RECONCILIATION_SPEC.md`, `SYSTEM_DESIGN.md`, `OPERATIONS_RUNBOOK.md`, `TEST_PLAN.md` |
| DB 테이블·제약·migration | `DATABASE_SPEC.md`, 관련 기능 명세, `IMPLEMENTATION_STATUS.md` |
| HTTP/WebSocket 계약 | `API_SPEC.md`, 관련 기능 명세, `TEST_PLAN.md` |
| UI 화면·사용자 동작 | `WEB_UI_SPEC.md`, 관련 기능 명세, `TEST_PLAN.md` |
| 인증·권한·비밀·감사 | `SECURITY_SPEC.md`, `SYSTEM_DESIGN.md`, `TEST_PLAN.md` |
| 시세·호가·분봉·지표 | `MARKET_DATA_SPEC.md`, `SYSTEM_DESIGN.md`, `TEST_PLAN.md` |
| Scout·Core 입력·출력·모델 | `AI_DECISION_SPEC.md`, `SYSTEM_DESIGN.md`, `TEST_PLAN.md` |
| 에이전트 역할·DAG·증거·병렬 실행 | `MULTI_AGENT_ORCHESTRATION_SPEC.md`, `AI_DECISION_SPEC.md`, `SYSTEM_DESIGN.md`, `DATABASE_SPEC.md`, `TEST_PLAN.md` |
| LLM provider·gateway·model·route·비용 | `LLM_PROVIDER_GATEWAY_SPEC.md`, `SECURITY_SPEC.md`, `CONFIGURATION_SPEC.md`, `DATABASE_SPEC.md`, `API_SPEC.md`, `WEB_UI_SPEC.md`, `TEST_PLAN.md` |
| 판단 실행, Guard 분기, 승인·주문 연결 | `DECISION_EXECUTION_SPEC.md`, `GUARD_RISK_SPEC.md`, `ORDER_EXECUTION_SPEC.md`, `ORDER_STATE_MACHINE_SPEC.md`, `API_SPEC.md`, `DATABASE_SPEC.md`, `WEB_UI_SPEC.md`, `TEST_PLAN.md` |
| Docker·배포·모니터링·복구 | `OPERATIONS_RUNBOOK.md`, `SYSTEM_DESIGN.md`, `TEST_PLAN.md` |

## 5. 신규 문서 등록 형식

새 문서를 만들면 **현재 문서 목록**에 다음 형식의 행을 추가한다.

```markdown
| 문서 표시 이름 | [FILE_NAME.md](FILE_NAME.md) | 이 문서에서 작성·관리하는 내용 |
```

하위 폴더에 만들 경우 실제 상대 경로를 사용한다.

```markdown
| Web UI 명세 | [docs/WEB_UI_SPEC.md](docs/WEB_UI_SPEC.md) | 화면 구조, 상태, 사용자 동작과 접근성 기준 |
```

신규 문서는 최소한 다음 내용을 포함한다.

```markdown
# 문서 제목

## 1. 목적

## 2. 적용 범위

## 3. 상세 명세

## 4. 오류·예외 또는 경계 조건

## 5. 검증·인수 조건

## 6. 미결정·보류 항목
```

문서 성격상 필요 없는 절은 생략할 수 있지만, 목적·상세 명세·검증 기준·미결정 항목은 구분해서 작성한다.

## 6. 작업 완료 전 확인

- 구현 내용과 관련 문서가 일치하는가?
- 신규 문서를 이 파일의 문서 목록에 등록했는가?
- 구현·시험 결과를 `IMPLEMENTATION_STATUS.md`와 `TEST_PLAN.md`에 반영했는가?
- 계획, 구현 완료와 미검증 상태를 구분했는가?
- 문서 링크가 실제 파일을 가리키는가?
- 문서와 예시에 비밀값이 없는가?
