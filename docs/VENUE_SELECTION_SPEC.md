# 거래시장 자동 선택 명세

## 1. 목적

KRX 상장 종목을 사용자가 KRX 또는 NXT에 고정 배정하지 않고, 주문 시점의 거래 세션·종목 적격성·양 시장 호가·유동성·주문 긴급도와 Broker SOR 지원 여부에 따라 Cresta가 `KRX`, `NXT`, `SOR`, `WAIT` 중 하나를 결정하는 규칙을 정의한다.

첫 구현은 `SHADOW` 진단 평가만 영속화하며 주문·승인·OrderIntent를 만들지 않는다. 키움 모의투자는 KRX 주문만 지원하므로 NXT/SOR 추천을 만들 수는 있지만 실제 송신 권한은 부여하지 않는다.

## 2. 핵심 모델

- 종목의 `listing_market`은 `KRX`로 유지한다.
- `available_venues`는 시각과 종목별 NXT 거래 가능 여부에 따라 계산한다.
- `selected_venue`는 주문 평가마다 새로 계산하며 종목 설정으로 고정하지 않는다.
- KRX와 NXT snapshot은 같은 종목이어도 별도 stream으로 보존한다.
- 선택 엔진 입력은 서버가 영속화한 snapshot만 사용하고 사용자가 임의 호가를 제출할 수 없다.

## 3. 세션 정책

공식 운영시간을 KST 기준 반개구간으로 적용한다.

| 구간 | 시간 | 선택 가능 시장 | 기본 처리 |
| --- | --- | --- | --- |
| `CLOSED` | 00:00~08:00 | 없음 | `WAIT` |
| `NXT_PRE` | 08:00~08:50 | NXT | NXT 적격·정상 호가일 때 `NXT` |
| `KRX_OPENING_AUCTION` | 08:50~09:00 | KRX 동시호가 | 첫 버전 `WAIT` |
| `KRX_ONLY` | 09:00~09:00:30 | KRX | 정상 호가일 때 `KRX` |
| `DUAL_CONTINUOUS` | 09:00:30~15:20 | KRX·NXT | SOR 또는 양 시장 비교 |
| `KRX_CLOSING_AUCTION` | 15:20~15:30 | KRX 동시호가 | 첫 버전 `WAIT` |
| `NXT_AFTER_AUCTION` | 15:30~15:40 | NXT 호가접수·시가 단일가 | 첫 버전 `WAIT` |
| `NXT_AFTER` | 15:40~20:00 | NXT | NXT 적격·정상 호가일 때 `NXT` |
| `CLOSED` | 20:00~24:00 | 없음 | `WAIT` |

## 4. 선택 규칙

| ID | 요구사항 |
| --- | --- |
| VEN-001 | NXT 단독 세션에서 NXT 미지원 종목은 KRX로 우회하지 않고 `WAIT/NXT_SYMBOL_INELIGIBLE`로 종료한다. |
| VEN-002 | 호가 snapshot은 `quality=NORMAL`, 평가시각 이하, 기본 2초 이내여야 한다. stale·미래·비정상·매수/매도 가격 누락 호가는 선택 후보에서 제외한다. |
| VEN-003 | `DUAL_CONTINUOUS`에서 실거래 SOR 지원이 검증되고 양 시장 호가가 모두 유효하면 `SOR`를 우선 추천한다. MOCK에서는 SOR를 추천하지 않고 두 시장을 결정론적으로 비교한다. |
| VEN-004 | 일반 매수는 낮은 매도호가, 일반 매도는 높은 매수호가를 우선한다. 가격이 같으면 해당 가격 잔량이 큰 시장, 잔량도 같으면 KRX를 선택한다. |
| VEN-005 | `EMERGENCY` 주문은 체결 가능성을 우선하여 해당 방향의 표시 잔량이 큰 시장을 선택한다. 잔량이 같으면 가격 우위, 모두 같으면 KRX를 선택한다. |
| VEN-006 | 한 시장의 호가만 유효하면 그 시장을 선택할 수 있으나 `SINGLE_FRESH_VENUE`를 기록한다. 양쪽 모두 유효하지 않으면 `WAIT/NO_FRESH_EXECUTABLE_QUOTE`로 종료한다. |
| VEN-007 | 모든 평가에는 세션, 양 시장 snapshot ID·시각·가격·잔량, NXT 적격성, SOR 지원, 환경, 선택 결과, reason code와 입력 hash를 영속화한다. API 응답과 로그에 자격증명은 포함하지 않는다. |
| VEN-008 | 진단 endpoint는 `SHADOW` 고정이며 Decision·Approval·OrderIntent·TradingOrder를 생성하지 않는다. |
| VEN-009 | 실제 주문 연결 단계에서는 Guard가 송신 직전 동일 정책으로 재평가한다. snapshot 또는 세션이 바뀌면 이전 선택을 재사용하지 않는다. |
| VEN-010 | KRX OPEN API는 venue 선택의 필수 입력이 아니다. 거래시점 시장 데이터는 키움 KRX/NXT stream을 사용하고 KRX OPEN API는 선택형 전일 검증·백필 source로만 유지한다. |
| VEN-011 | NXT 종목 적격성은 `VERIFIED`, `INELIGIBLE`, `UNKNOWN`으로 구분한다. 정상화된 NXT quote 수신은 `VERIFIED/QUOTE_OBSERVED` 근거가 되며 별도 venue 상태 원장에 보존한다. snapshot·원장 부재를 미지원으로 단정하지 않는다. `UNKNOWN`은 NXT 단독 세션에서 `WAIT/NXT_ELIGIBILITY_UNVERIFIED`로 종료한다. |
| VEN-012 | KRX와 NXT의 장중 세션을 계산하기 전에 공통 국내주식 거래일 캘린더를 적용한다. 토요일·일요일, 대한민국 공휴일과 대체공휴일, 근로자의 날, KRX 연말 휴장일은 시간대와 관계없이 `CLOSED/WAIT`다. |
| VEN-013 | 거래일 판정은 정책 버전, `OPEN/CLOSED/UNKNOWN` 상태와 `WEEKDAY/WEEKEND/PUBLIC_HOLIDAY/LABOR_DAY/YEAR_END_CLOSURE/CALENDAR_UNAVAILABLE` 근거를 평가 입력 hash·DB·API에 함께 기록한다. 캘린더 라이브러리 오류나 판정 불능은 거래일로 추정하지 않고 `CALENDAR_UNAVAILABLE/WAIT`로 종료한다. |

## 5. API 계약

`POST /api/v1/venue-selections/diagnostic`

요청은 종목, 매수/매도, 수량, 주문 유형과 긴급도만 받는다. 평가시각·호가·NXT 적격성·SOR 지원 여부는 서버 상태에서 결정한다.

응답은 `selection_id`, `session`, `selected_venue`, `state`, `reason_codes`, 양 시장 quote 진단, `execution_stage=SHADOW`, `order_creation_allowed=false`를 반환한다.

응답은 추가로 `calendar_policy_version`, `trading_day_status`, `calendar_reason`을 반환한다. 이는 화면 표시와 감사용이며 클라이언트가 세션을 다시 계산하지 않는다.

## 6. 검증 조건

- 08:00·08:49:59에는 NXT, 08:50에는 opening auction, 09:00에는 KRX, 09:00:30에는 dual, 15:20에는 closing auction, 15:40에는 NXT after, 20:00에는 closed로 분류한다.
- NXT 단독 구간의 미지원 종목, stale quote와 양 시장 무자료는 `WAIT`다.
- 일반 주문은 가격, 긴급 주문은 잔량을 우선한다.
- MOCK에서는 NXT/SOR 추천이 주문으로 전환되지 않는다.
- 동일 입력 snapshot과 평가시각의 canonical hash가 재현 가능하다.
- 평일·주말뿐 아니라 공휴일·근로자의 날·연말 휴장일에도 시간대와 무관하게 `CLOSED/WAIT`가 재현된다.

## 7. 후속 단계

- 권위 있는 키움 NXT 전체 적격 목록 동기화와 명시적 `INELIGIBLE` 판정. NXT 실시간 호가 stream과 관측 기반 `VERIFIED`는 우선 구현한다.
- KRX 임시 휴장·개장시간 변경 공지를 반영하는 운영 override와 공식 일정 자동 동기화
- 실거래 키움 SOR 주문 코드·체결시장 매핑 인수시험
- Web UI에 양 시장 비교와 선택 근거 표시
- Guard 송신 직전 재선택 및 기존 미체결 주문의 venue 전환 정책
