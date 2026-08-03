# Cresta 구현 상태

## 1. 목적

명세된 요구사항의 계획, 구현, 검증 상태를 구분해 관리한다. `명세 완료`는 코드 구현이나 시험 완료를 의미하지 않는다.

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
| 제품 범위와 실행 권한 | `docs/PRODUCT_REQUIREMENTS.md` | 명세 완료 | 코드 미구현 |
| 거래 세션과 감시 일정 | `docs/TRADING_SESSION_SPEC.md` | 명세 완료 | NXT는 키움 모의투자 검증 불가 |
| 주문 가격과 미체결 처리 | `docs/ORDER_EXECUTION_SPEC.md` | 구현 중 | Paper 부분체결·취소·정정 구현, 호가·가격정책·timeout worker 미구현 |
| 주문 상태 머신과 키움 매핑 | `docs/ORDER_STATE_MACHINE_SPEC.md` | 구현 중 | Paper 전이·수량 불변·멱등성 구현, 키움 이벤트 필드 매핑은 잠정 |
| 계좌·주문 재동기화 | `docs/RECONCILIATION_SPEC.md` | 구현 중 | 키움 미체결·당일체결·잔고 읽기 전용 snapshot 대조와 run/mismatch/gate 영속화 로컬 검증; 상시 worker·자동 복구 미구현 |
| 시스템 아키텍처 | `docs/SYSTEM_DESIGN.md` | 구현 중 | Backend·Console·gateway 골격 구현, trading worker 미구현 |
| HTTP/WebSocket API | `docs/API_SPEC.md` | 구현 중 | 인증·system health(키움 구성 상태 포함)·주문/체결·포지션·최신 quote 조회 구현, 거래 명령·WebSocket stream 미구현 |
| UI 콘셉트 참고자료 | `stitch_cresta_ai_intraday_trading_system/` | 참고자료 | 실제 Console 구현물이 아님 |
| 키움 모의투자 Adapter | `docs/KIWOOM_BROKER_SPEC.md` | 구현 중 | 인증·시세·계좌 일치 실서버 통과, `ka10075`·`ka10076`·`kt00018` 연속조회/정규화 로컬 검증; 신규 조회 실서버·WebSocket·주문 미검증/미구현 |
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 명세 완료 | MVP 기본값·허용범위 확정, 모의시험 후 조정 가능 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 명세 완료 | Web UI 연계 명세 완료, 코드 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, Paper 읽기 전용 화면과 Watch stream 상태 표시 구현·로컬 검증, 주문 생성·승인·설정·상세 Watch 화면 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인·세션·CSRF·실패제한·재인증 기반 17개 로컬 시험 통과, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 정규화·KRX/NXT 분리·중복/역순/갭·복구 snapshot·최신성 조회와 키움 REST 복구 snapshot mapping 로컬 검증, WebSocket 수신·분봉·지표 미구현 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 명세 완료 | 모델 제공자 미선정, mock interface 구현 가능 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | reconciliation run/mismatch `20260803_0004` 포함 SQLite migration 왕복 완료; 신규 migration 실서버 PostgreSQL 적용·동시성 시험 미완료 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 키움 secret overlay·실제 인증·API source UID 권한 실서버 검증 완료; 신규 reconciliation migration/CLI 실서버 검증과 백업·경보·복구훈련 미완료 |
| 구현 착수 준비도 | `docs/IMPLEMENTATION_READINESS_REVIEW.md` | 명세 완료 | 키움 출구 IP·MOCK 인증·시세 실서버 확인 반영, 계좌·주문 외부 통합 게이트 유지 |
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
