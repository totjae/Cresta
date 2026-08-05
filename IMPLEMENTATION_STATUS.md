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
| 제품 범위와 실행 권한 | `docs/PRODUCT_REQUIREMENTS.md` | 구현 중 | 기본 행동 8종 mode 설정 구현; 거래/진단 판단 분리와 SHADOW→승인→MOCK 자동 단계 명세 완료, 실행 연결 미구현 |
| 거래 세션과 감시 일정 | `docs/TRADING_SESSION_SPEC.md` | 명세 완료 | NXT는 키움 모의투자 검증 불가 |
| 주문 가격과 미체결 처리 | `docs/ORDER_EXECUTION_SPEC.md` | 구현 중 | Paper와 키움 CREATED polling·ACK/REJECTED/UNKNOWN 구현; Guard 평가·수량·원자 주문 생성 계약 완료, 실제 전략 주문 미구현 |
| 주문 상태 머신과 키움 매핑 | `docs/ORDER_STATE_MACHINE_SPEC.md` | 구현 중 | Paper·키움 송신 전이 구현; execution·승인과 주문 상태 분리·원자 전이 명세 완료, 승인 구현 미착수 |
| 계좌·주문 재동기화 | `docs/RECONCILIATION_SPEC.md` | 구현 중 | snapshot 대조와 상시 worker READY·재시작 fencing은 실서버 통과; `00`·`04` 이벤트 즉시 gate 차단·debounce·BROKER_EVENT 대조 로컬 통과, 실제 체결·장애주입 미검증 |
| 시스템 아키텍처 | `docs/SYSTEM_DESIGN.md` | 구현 중 | Backend·Console·동적 DNS gateway·키움 worker polling·Watch 실시간 수집과 진단용 Mock AI, SHADOW 판단 실행 기반 구현; 정기 AI scheduler와 주문 생성 Guard 미구현 |
| HTTP/WebSocket API | `docs/API_SPEC.md` | 구현 중 | 인증·상태·주문/체결·포지션·quote 조회 구현; Guard·승인·실행 단계 REST/event 계약 완료, 거래 명령·stream 미구현 |
| UI 콘셉트 참고자료 | `stitch_cresta_ai_intraday_trading_system/` | 참고자료 | 실제 Console 구현물이 아님 |
| 키움 모의투자 Adapter | `docs/KIWOOM_BROKER_SPEC.md` | 구현 중 | 인증·snapshot·worker는 실서버 통과; 주문 Adapter·FIFO polling·UNKNOWN 대조·계좌 event gate·Web MOCK 1주 진단 API 자동시험 통과, 실제 모의주문 미검증 |
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 명세 완료 | BUY·부분/전량매도·고정손절 1차 평가 규칙과 기능 gate 명세 완료; 구현·모의시험 미착수 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 구현 중 | 실행 권한 버전 UI/API 구현; Guard 1차 위험 설정·entry order amount·실행 단계 계약 완료, 위험 설정 UI/API 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, 감시 종목·Paper 조회·Broker 진단·실행 권한·진단 판단 목적과 SHADOW 단계 표시 구현; 승인 카드·Guard 상세 결과 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인·세션·CSRF·실패제한·재인증 기반 17개 로컬 시험 통과, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 감시 종목·키움 `0B`·`0D`, 1분봉·VWAP·SMA5·고점 하락률·spread 영속화 로컬 검증 완료; 상대 거래량·체결강도·변동성과 실제 장중 수신 미검증 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 구현 중 | `deterministic-mock-v1` 진단 구현; DIAGNOSTIC/TRADING 분리와 내부 실행 인계 명세 완료, scheduler·승인·Guard 실행 미구현 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | 분봉·지표 `20260804_0009` 적용; execution·Guard evaluation·승인 원자성 테이블/제약 명세와 기존 중복 요구사항 ID 정리 완료, 신규 migration 미구현 |
| 판단 실행·승인 | `docs/DECISION_EXECUTION_SPEC.md` | 구현 중 | DIAGNOSTIC/TRADING 경계, 멱등 SHADOW execution, 불변 Guard 평가와 안전 차단 구현; scheduler·승인·주문 생성은 미구현 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 전 서비스 `unless-stopped`, core healthcheck와 부팅 Compose 조정 unit 구현; 2026-08-05 Ubuntu 재부팅 후 9초 내 전체 health·worker READY 복구 통과, 백업·경보·복구훈련 미완료 |
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
