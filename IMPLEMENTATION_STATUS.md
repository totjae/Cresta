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
| 계좌·주문 재동기화 | `docs/RECONCILIATION_SPEC.md` | 구현 중 | STARTING gate·UNKNOWN 종목 차단 구현, snapshot 대조 worker 미구현 |
| 시스템 아키텍처 | `docs/SYSTEM_DESIGN.md` | 구현 중 | Backend·Console·gateway 골격 구현, trading worker 미구현 |
| HTTP/WebSocket API | `docs/API_SPEC.md` | 구현 중 | 인증·health·주문/체결 조회 구현, 거래 명령·stream 미구현 |
| UI 콘셉트 참고자료 | `stitch_cresta_ai_intraday_trading_system/` | 참고자료 | 실제 Console 구현물이 아님 |
| 키움 모의투자 Adapter | `docs/KIWOOM_BROKER_SPEC.md` | 명세 완료 | 계좌 secret·고정 출구 IP 운영 확인 필요 |
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 명세 완료 | MVP 기본값·허용범위 확정, 모의시험 후 조정 가능 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 명세 완료 | Web UI 연계 명세 완료, 코드 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 로그인·TOTP·세션 복구·로그아웃·반응형 MOCK Console 구현, 거래 화면 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인·세션·CSRF·실패제한·재인증 기반 17개 로컬 시험 통과, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 명세 완료 | 키움 실제 이벤트 순번 미검증, 코드 미구현 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 명세 완료 | 모델 제공자 미선정, mock interface 구현 가능 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | 인증·주문·체결·포지션 migration과 SQLite 왕복 검증, DB 비밀번호 URL 보간 수정; PostgreSQL 재검증 대기 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 실서버 DB·Redis healthy·secret 읽기 확인, 노출된 초기 DB secret 폐기 및 migration 재검증·호스트 Nginx·TLS·백업·경보 미완료 |
| 구현 착수 준비도 | `docs/IMPLEMENTATION_READINESS_REVIEW.md` | 명세 완료 | 내부 구현 시작 가능, 외부 통합 게이트 유지 |
| Backend·Docker 골격 | `docs/SYSTEM_DESIGN.md`, `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | Backend·Frontend·gateway와 N100 자원 제한 반영, PostgreSQL·Redis 실서버 기동 성공; secret 권한 수정 후 API·gateway 재검증 대기 |

## 4. 구현 완료 조건

기능별 완료는 다음 조건을 모두 만족해야 한다.

1. 관련 요구사항 ID가 기준 문서에 존재한다.
2. 구현이 요구사항과 일치한다.
3. 대응 테스트 ID가 `TEST_PLAN.md`에 존재한다.
4. 자동 또는 수동 시험 결과가 기록된다.
5. 미검증 사항과 외부 제약이 숨김없이 표시된다.
6. 관련 문서와 `AGENTS.md` 색인이 갱신된다.

## 5. 미결정·보류 항목

- 키움 모의투자 계정과 API 사용신청 완료 여부
- NXT/SOR 실거래 검증 환경
