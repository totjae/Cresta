# Cresta 테스트 계획

## 1. 목적

제품·운영·주문 명세의 요구사항을 검증 가능한 시험으로 연결한다. 구현 전에는 계획 상태로 유지하고, 실제 실행 후 결과와 근거를 추가한다.

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
| T-MAO-010 | MAO-090~098, DB-124~127, API-135, UI-118~119 | 5개 Mock route로 DIAGNOSTIC DAG 실행·중복 요청·route 변조 | run 1개, stage 7개·invocation 5개 provenance, Core WAIT, decision·approval·order 0건 | 통과 (2026-08-06, API·component fixture) |
| T-MAO-011 | MAO-100~107, DB-132~134, API-143~144, UI-127 | 비동기 admission, stage claim·lease 만료·재claim과 이전 fencing 완료 시도, scheduler ACTIVE route admission | stage 단일 소유·fencing 증가·늦은 완료 거부, UI 비동기 상태 갱신, 최종 PARTIAL/WAIT, decision·approval·order 0건 | 통과 (2026-08-06, 자동 DB·API·component fixture) |

### 3.12.2 LLM Provider 및 Gateway

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-LLM-001 | LLM-001~005, LLM-080~083 | 공식·Gateway·Ollama·사용자 endpoint profile 검증 | Mock Adapter 선택과 무통신 검증, 비허용 scheme·credential URL 거부; 외부 Adapter는 미구현 오류 | 부분 통과 (2026-08-05, 자동 fixture) |
| T-LLM-002 | LLM-010~014, LLM-080~084, CFG-090~095 | 발견 모델·미검증 capability·route 이중 활성화 | 자동 활성화 없음, Mock fixture보다 넓은 capability와 SHADOW 외 route 거부 | 부분 통과 (2026-08-05, 자동 fixture) |
| T-LLM-003 | LLM-020~024, LLM-083 | Mock Adapter canonical fixture와 외부 Adapter 선택 | deterministic 내부 result, 외부 Adapter는 호출 전에 차단 | 부분 통과 (2026-08-05, Mock만) |
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
| T-DB-009 | DB-070~073 | 암호화 백업 복원·보존 삭제 | 불변조건 통과, hold 데이터 보존 | 계획 |

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
| T-OPS-005 | OPS-040~044 | 암호화 백업 월간 복원 훈련 | RPO/RTO 측정, 수동 확인 전 주문 금지 | 계획 |
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
| T-WATCH-010 | MKT-082~086, KIW-138~141 | 시작·목록 변경 구독과 공식 `0B`·`0D` fixture | 그룹 분리, KRX 종목 전체 동기화, 체결·호가 정규화와 snapshot 영속 | 통과 (2026-08-04, 자동 fixture); 실제 장중·재연결 대기 |
| T-UI-014 | UI-046~048 | 빈 목록·등록·시세 대기·최신 snapshot·삭제 | 슬롯과 데이터 상태를 오인 없이 표시하고 mutation은 CSRF 사용 | component 등록 통과; 삭제 수동 대기 |
| T-WATCH-011 | MKT-090~095, DB-097~098 | 같은 분·다음 분 체결, 호가만 변경, 거래일 변경, gap·late 입력 | 결정론적 OHLCV·turnover와 VWAP·SMA5·drawdown·spread, 비정상 입력 제외 | 통과 (2026-08-04, 자동 fixture) |
| T-UI-015 | MKT-096, API-105, UI-049 | 지표 없음과 최신 지표가 있는 감시 카드 조회 | 계산 전 null과 지표 값을 구분해 표시 | 통과 (2026-08-04, API·component) |

### 5.2 Guard·판단 실행·승인 추가 시험

| 테스트 ID | 관련 요구사항 | 시나리오 | 기대 결과 | 상태 |
| --- | --- | --- | --- | --- |
| T-EXE-001 | EXE-001~014, AI-075~079 | 진단/거래 판단과 Core·Guard 행동 전체 조합 | 진단·비행동 주문/승인 0건, BUY 안전 차단 | 부분 통과 (2026-08-05, 자동; 매도·미지원 전체 조합 대기) |
| T-EXE-002 | EXE-020~025, DB-100~107 | 동일 판단을 반복 라우팅 | execution·Guard 최대 1개, 기존 결과 재조회 | 부분 통과 (2026-08-05, 자동; 동시성·commit 응답 유실 대기) |
| T-EXE-003 | EXE-030~035, CFG-080~084 | `DISABLED`, `MANUAL_APPROVAL`, `AUTOMATIC`과 3개 실행 단계 조합 | 기록만/승인/주문 분기, 상위 단계 gate 우선, 미준비 BUY 차단 | 부분 통과 (2026-08-05, SHADOW·미설정 주문금액 차단; 상위 단계 대기) |
| T-GRD-010 | GRD-080~088, EXE-050~056 | BUY Guard 각 규칙 단독·복합 실패와 경계 금액 | 결정론적 복수 reason, 하나라도 blocking이면 주문 0건 | 계획 |
| T-GRD-011 | GRD-016~017, EXE-013, ORD-042 | 진입금액 없음·최소 미만·한도 초과·1주 미만·정상금액 | 임의 수량 생성 금지, Decimal 기반 정상 수량만 통과 | 계획 |
| T-GRD-012 | GRD-083~085, EXE-011~012·052~053 | 부분/전량매도·고정손절에서 예약수량·position version·데이터 단절 | 초과매도 0건, stale 승인 무효화, trigger와 EXIT_PENDING 유지 | 계획 |
| T-APR-001 | EXE-040~045, STM-006~009, API-112~121 | 승인·거절·만료·가격/상태 변경·동시 탭·TOTP 재사용 | 한 번만 terminal 전이, 유효 승인만 CREATED 주문과 원자 commit | 계획 |
| T-ORD-010 | EXE-060~064, ORD-039~043 | Guard 통과 후 주문 생성과 Broker polling 경쟁 | intent·CREATED·감사 원자 생성, worker만 송신, 활성/UNKNOWN 중복 차단 | 계획 |
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

Local evidence: 외부 Adapter fixture로 4 Scout·Core 유효 응답의 stage 채택, server-owned provenance, strict-schema 필수 필드, request ID·usage 영속화, 계약 오류의 `INVALID_OUTPUT/FAIL_STOP` 및 주문 0건을 검증했다. backend 168개 회귀 시험·Ruff, frontend TypeScript·11개 component 시험·production build가 통과했다. 실제 유료 Provider의 요청·응답은 Ubuntu 서버에서 별도 검증한다.

- `T-AGENT-EXT-005`: Adapter가 정규화한 terminal 상태와 `LLM_*` 오류는 `AGENT_LLM_FAIL_STOP` stage 처리 후에도 그대로 보존되고, 완료되지 않은 invocation만 `AGENT_INVOCATION_OUTCOME_UNKNOWN`으로 격리되는지 검증한다.

# 역할별 timeout·service tier 시험 (2026-08-10)

- `T-LLM-ROUTE-006`: route API가 1–600초 timeout과 `DEFAULT/PRIORITY/FLEX`를 검증·영속화·조회하고 migration이 기존 route를 `DEFAULT`로 보존하는지 검증한다.
- `T-LLM-ADAPTER-007`: `DEFAULT`는 service tier 필드를 생략하고 명시적 `PRIORITY/FLEX`는 native/compatible Adapter 요청에 소문자로 전달되는지, 완성 응답이라도 전체 제한시간을 넘으면 결과를 폐기하는지 fixture로 검증한다.
- `T-LLM-UI-008`: 역할별 배정에서 timeout과 tier를 후보에 저장하고 route 요약에서 확인할 수 있으며 `FLEX` 선택 시 600초 권장값이 적용되는지 검증한다.

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

Local evidence: OpenAI 호환·Responses Adapter와 parameter policy 집중 시험 20개, backend 전체 183개 시험 및 Ruff가 통과했다. 실제 OpenAI와 LLM Gateway 외부 모델의 SHADOW 호출은 Ubuntu 서버 검증 항목이다.

# Provider 출처 후보와 evidence reference 경계 시험 (2026-08-11)

- `T-EVIDENCE-001`: OpenAI Responses, Anthropic, Gemini와 OpenAI-compatible 응답의 알려진 citation 위치가 동일한 canonical 후보로 정규화되는지 확인한다.
- `T-EVIDENCE-002`: HTTPS 공개 URL만 `UNRATED EvidenceItem`으로 저장하고 같은 run의 중복 URL, private/loopback URL과 원문 응답을 저장하지 않는지 확인한다.
- `T-EVIDENCE-003`: 빈 검증 Bundle에서는 모델에 `allowed_evidence_refs=[]`와 빈 배열 반환 규칙을 전달하고 URL·임의 ID 참조를 `LLM_EVIDENCE_REF_NOT_ALLOWED`로 거부하는지 확인한다.
- `T-EVIDENCE-004`: schema, evidence reference와 Core incomplete-role 불일치를 서로 다른 안전한 invocation 오류 코드로 기록하면서 승인·주문은 생성하지 않는지 확인한다.
