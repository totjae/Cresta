# Cresta 구현 상태

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
- 키움 BUY 거절 원인은 현재 안전한 업무 오류 코드·사유가 영속되지 않아 미확인으로 정정했다. 호가단위 문제는 관측 근거가 확보되기 전까지 원인으로 확정하지 않는다.
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
- 구현 시점 backend 전체 회귀 305개 통과(신규 16: order creation 4, approvals 7, stop trigger SELL 3, position provenance 2), Ruff lint 통과, migration `20260813_0034` upgrade→downgrade→upgrade 왕복 통과. Frontend TypeScript·14개 component 시험·production build 통과. 2026-08-13 Ubuntu 모의투자에서 `Approval(PENDING→APPROVED)`·BUY `CREATED→VALIDATING→SUBMITTING→REJECTED`까지 확인했다. 키움의 정확한 업무 거절 코드·사유는 현재 영속되지 않으므로 호가단위 문제로 확정하지 않는다. 실제 FIXED_STOP 가격 도달 후 SELL 송신·체결은 미검증이다. 당시 확인한 stream 최신 snapshot과 판단 snapshot 간 경쟁 조건은 2026-08-14 최신 snapshot 재검사 분리로 수정했다.

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
| 주문 가격과 미체결 처리 | `docs/ORDER_EXECUTION_SPEC.md` | 구현 중 | Paper와 키움 CREATED polling·ACK/REJECTED/UNKNOWN, 신규 BUY 접수 10초 후 잔량 1회 취소·snapshot 확정 구현; 승인형 BUY·FIXED_STOP SELL 주문 생성 연결(MARKETABLE_LIMIT 간소화, 호가단위 보정·매도 재호가는 후속), 실제 장중 취소 경쟁 미검증 |
| 주문 상태 머신과 키움 매핑 | `docs/ORDER_STATE_MACHINE_SPEC.md` | 구현 중 | Paper·키움 송신 전이 구현; 승인 생명주기(PENDING→APPROVED/REJECTED/EXPIRED/INVALIDATED)·원자 주문 생성 구현, PARTIAL_SELL·FULL_SELL·TAKE_PROFIT 주문은 후속 |
| 계좌·주문 재동기화 | `docs/RECONCILIATION_SPEC.md` | 구현 중 | snapshot 대조와 상시 worker READY·재시작 fencing은 실서버 통과; `00`·`04` 이벤트 즉시 gate 차단·debounce·BROKER_EVENT 대조 로컬 통과, Broker 총수량과 Cresta 관리수량 분리·`EXTERNAL/MIXED/CRESTA_MANAGED` 재분류 실서버 통과(장애주입 미검증) |
| 시스템 아키텍처 | `docs/SYSTEM_DESIGN.md` | 구현 중 | Backend·Console·gateway·키움 worker·AI scheduler·별도 Agent worker·Watch와 SHADOW 실행 구현; 공통 Order Creation Service·승인 경로·FIXED_STOP 자동 매도 연결 |
| HTTP/WebSocket API | `docs/API_SPEC.md` | 구현 중 | 인증·상태·주문/체결·포지션·quote·승인 조회·승인/거절 구현; 거래 명령·stream 미구현 |
| UI 콘셉트 참고자료 | `stitch_cresta_ai_intraday_trading_system/` | 참고자료 | 실제 Console 구현물이 아님 |
| 키움 모의투자 Adapter | `docs/KIWOOM_BROKER_SPEC.md` | 구현 중 | 인증·snapshot·worker는 실서버 통과; 주문 Adapter·FIFO polling·UNKNOWN 대조·계좌 event gate·Web MOCK 1주 진단 API 자동시험 통과, 실제 모의주문 미검증 |
| Guard 리스크·비상정지 | `docs/GUARD_RISK_SPEC.md` | 구현 중 | BUY 전체 Risk Guard(일일손실 REALIZED_PLUS_UNREALIZED/종목·전체 노출/일일진입/연속손실/spread/연결위험/활성손실이벤트)와 고정 손절 trigger 매도 Guard·승인 시점 재검사 구현; risk_events 원장 scope별 영속; ENTRY_HALT; 비상정지(EMERGENCY_LIQUIDATE 전체)는 미구현 |
| 사용자 설정·적용 | `docs/CONFIGURATION_SPEC.md` | 구현 중 | 실행 권한, Guard 사용자 기본 위험 설정, fail-closed 운영 휴장과 Provider/Model/역할별 배정 UI/API 구현; 종목별 위험 override·영향 미리보기·예약 적용 미구현 |
| Web UI | `docs/WEB_UI_SPEC.md` | 구현 중 | 인증 Console, 감시 종목·KRX/NXT SHADOW venue 평가·운영 휴장·Paper 조회·Broker 진단·실행 권한·Guard 위험 설정, Provider 모델·역할·프롬프트·FAILOVER 배정, stage 결과·구조화 응답 조회 구현; 승인 카드·Guard 평가 상세 결과 미구현 |
| 인증·세션·TOTP | `docs/SECURITY_SPEC.md` | 구현 중 | 로그인 TOTP·세션·CSRF·실패제한 구현; 현재 개발 단계의 로그인 이후 설정·Provider·역할 배정·MOCK 시험 재인증은 제거하고 향후 위험 분석 시 선택적 재도입 예정, 복구·운영 검증 미완료 |
| 시장데이터·Watch | `docs/MARKET_DATA_SPEC.md` | 구현 중 | 감시 종목·키움 `0B`·`0D`, 1분봉과 v2 VWAP·SMA5·상대 거래량·실현 변동성·고점 하락률·spread 영속화 로컬 검증 완료; 체결강도와 v2 실제 장중 수신 미검증 |
| Scout·Core AI 계약 | `docs/AI_DECISION_SPEC.md` | 구현 중 | 불변 `scout-input-v1`과 `deterministic-mock-v2`, 외부 Provider DIAGNOSTIC 판단, context별 v2 출력 계약과 `agent-server-input-v1` 포지션 파생값을 로컬 검증 완료; 실서버 v5 검증 대기 |
| 다중 에이전트 오케스트레이션 | `docs/MULTI_AGENT_ORCHESTRATION_SPEC.md` | 구현 중 | Agent Runtime v6의 Intel·Verify·4개 Scout·Candidate Auditor·Core, 서버 입력과 불완전 Scout의 결정론적 Core 축소 구현; v6 로컬 회귀 완료, 실서버 검증 대기 |
| LLM Provider·Gateway | `docs/LLM_PROVIDER_GATEWAY_SPEC.md` | 구현 중 | 40개 Provider template, 35개 단일-key 등록, Native·OpenAI-compatible Adapter, 모델 동기화·역할·Prompt·FAIL_STOP/단일 FAILOVER·service tier·웹 검색·호출 이력 구현; OpenAI·LLM Gateway 실제 SHADOW 호출 검증 완료, 복합 인증 5종·가격 기반 비용 집계 미구현 |
| DB 스키마·영속성 | `docs/DATABASE_SPEC.md` | 구현 중 | 분봉·v2 지표·Scout 입력, LLM Foundation·Agent Runtime v6, Evidence·Market Context, venue 평가·적격 상태·캘린더 override·승인(`order_id`/`result_code`)·position origin provenance·Risk Guard 원장과 제한된 구조화 응답 이력을 `20260813_0035`까지 구현; Ubuntu PostgreSQL도 `0035` 적용 확인 |
| 판단 실행·승인 | `docs/DECISION_EXECUTION_SPEC.md` | 구현 중 | DIAGNOSTIC/TRADING 경계, scheduler 인계, 멱등 SHADOW execution, 전체 BUY Guard, `APPROVAL_ONLY` BUY 승인·자동 주문·FIXED_STOP 자동 매도와 승인 시 최신 snapshot 재평가 구현; PARTIAL_SELL/FULL_SELL/TAKE_PROFIT·비상정지는 후속 |
| 운영·장애복구 | `docs/OPERATIONS_RUNBOOK.md` | 구현 중 | 전 서비스 `unless-stopped`, core healthcheck와 선택형 DART·KRX overlay 감지 부팅 조정 unit 구현; 2026-08-05 기본·키움 재부팅 복구 통과, 신규 source overlay 재부팅 인수시험·백업·경보·복구훈련 미완료 |
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
  - **FIXED_STOP 자동 매도 주문 연결 완료**(`APPROVAL_ONLY`에서 `FULFILLED` → SELL `CREATED` 주문, Cresta-managed position만)
  - **승인형 BUY 주문 생성 완료**(`MANUAL_APPROVAL` → `Approval(PENDING)` → 승인 시 `CREATED` 주문; `AUTOMATIC` → 직접 `CREATED`)
  - **전체 Risk Guard 완료**(#2): 일일손실(REALIZED_PLUS_UNREALIZED)·종목/전체 노출·일일진입·연속손실·spread·연결위험·활성손실이벤트 BUY 차단, risk_events scope별 영속, ENTRY_HALT
  - 비상정지 전체(EMERGENCY_LIQUIDATE)·호가단위 보정은 후속
- 기능별 `AUTOMATIC/MANUAL_APPROVAL/DISABLED` 실행 권한 적용
  - BUY `MANUAL_APPROVAL`/`AUTOMATIC`, FIXED_STOP `AUTOMATIC` 적용 완료(stage 게이트)
- 승인 카드, 만료·거절, 재평가와 원자 OrderIntent·TradingOrder 생성
  - 승인 카드·만료·거절·가격편차·position version 무효화·원자 주문 생성 완료
- 외부 AI 결과가 실패·불완전·만료일 때 신규매수 fail-closed

완료 gate:

- 고정 손절·비상정지·장마감 청산이 AI와 독립적으로 동작
- 승인·자동 경로의 멱등성, 중복 주문, UNKNOWN 대조와 재시작 복구 시험 통과
- 키움 모의투자 소액 주문·부분체결·취소·거절의 장중 실서버 검증

### 6.6 6순위 — 운영 안정화와 제한 자동매매 준비

- DART 포함 systemd boot profile, backup·restore drill, 경보와 운영 dashboard 완성
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
