# 시장데이터 및 Cresta Watch 명세

## 1. 목적

키움에서 수신한 국내주식 시세·호가·체결을 일관된 내부 모델로 정규화하고, 데이터 최신성·완전성·세션을 검증해 Scout, Core와 Guard에 신뢰 가능한 입력을 제공한다.

## 2. 적용 범위

- KRX 모의투자 시세와 분석용 NXT 시세
- 현재가, 호가, 체결, 거래량과 분봉
- 지표 계산, 데이터 지연·단절·이상 감지
- 장중 실시간 캐시와 시계열 저장
- Watch 이벤트와 소비자별 데이터 게이트

## 3. 상세 명세

### 3.1 원본과 정규화 모델

| ID | 요구사항 |
| --- | --- |
| MKT-001 | 모든 시세 이벤트는 `symbol`, `market`, `source`, `event_at`, `received_at`, `sequence_or_hash`를 포함한다. |
| MKT-002 | 가격·수량은 부동소수점이 아닌 정수 또는 고정소수점으로 정규화한다. |
| MKT-003 | KRX와 NXT 데이터는 같은 종목이어도 별도 stream으로 보존하며 암묵적으로 합산하지 않는다. |
| MKT-004 | 원본 이벤트에는 수신 시각과 해시를 부여하고 정규화 실패 원인을 기록한다. 비밀정보는 저장하지 않는다. |
| MKT-005 | 종목코드·시장·거래상태가 검증되지 않은 이벤트는 거래 판단 입력에서 제외한다. |

정규화 quote:

```yaml
quote:
  symbol:
  market: KRX | NXT
  last_price:
  open_price:
  high_price:
  low_price:
  cumulative_volume:
  best_bid_price:
  best_bid_quantity:
  best_ask_price:
  best_ask_quantity:
  trading_status:
  event_at:
  received_at:
  source_sequence:
```

### 3.2 순서·중복·갭 처리

| ID | 요구사항 |
| --- | --- |
| MKT-010 | 제공되는 순번이 있으면 시장·종목·채널 단위로 순서를 검사하고, 없으면 원본 해시와 핵심 필드 조합으로 중복을 억제한다. |
| MKT-011 | 늦게 도착한 이벤트는 원본 이력에는 남길 수 있지만 현재 스냅샷을 과거 값으로 되돌리지 않는다. |
| MKT-012 | 순번 갭이나 누적 거래량 감소를 발견하면 해당 종목을 `GAP_DETECTED`로 표시하고 snapshot 조회 또는 재구독을 요청한다. |
| MKT-013 | 재연결 후 snapshot을 기준으로 현재 상태를 다시 만들고 버퍼 이벤트를 시간·순번 순으로 재생한다. |
| MKT-014 | 중복 제거 판단 근거와 복구 결과를 메트릭·구조화 로그에 기록한다. |

### 3.3 최신성과 거래 게이트

기본값:

```yaml
freshness:
  quote_stale_seconds: 2
  orderbook_stale_seconds: 2
  trade_stale_seconds: 5
  recovery_stable_seconds: 5
```

| ID | 요구사항 |
| --- | --- |
| MKT-020 | 최신성은 `received_at`과 단조 시계를 사용해 평가하며 서버 벽시계 이상 여부를 별도로 검사한다. |
| MKT-021 | 신규매수는 주문시장 quote와 orderbook이 모두 최신이고 정상 거래상태일 때만 허용한다. |
| MKT-022 | 손절 판단에 필요한 데이터가 지연되면 마지막 가격으로 체결을 추정하지 않고 신규매수를 차단하며 포지션 위험 경보를 발생시킨다. |
| MKT-023 | 데이터가 복구돼도 설정된 안정 구간 동안 정상 이벤트가 유지되고 재동기화가 완료되기 전 게이트를 열지 않는다. |
| MKT-024 | NXT 시세는 첫 버전에서 표시·분석용이며 KRX 모의주문 가격이나 Guard 주문 가능성 검사의 대체 데이터로 사용하지 않는다. |

### 3.4 분봉과 지표

지원 기본 지표:

```text
OHLCV 1분봉
VWAP
단기 이동평균
상대 거래량
고점·저점 및 고점 대비 하락률
호가 스프레드
매수·매도 체결 강도
실현 변동성
```

| ID | 요구사항 |
| --- | --- |
| MKT-030 | 1분봉은 거래소 이벤트 시각과 세션 캘린더 경계로 집계하며 수신 시각으로 봉 경계를 만들지 않는다. |
| MKT-031 | 빈 분봉은 임의 가격·거래량으로 생성하지 않고 `NO_TRADE`로 구분한다. |
| MKT-032 | 정정·늦은 체결로 확정 봉이 바뀌면 지표 버전을 증가시키고 영향받은 구간을 재계산한다. |
| MKT-033 | VWAP과 거래량 지표는 시장별 거래를 혼합하지 않으며 사용한 시장과 세션을 명시한다. |
| MKT-034 | 지표 결과는 계산 버전, 입력 범위, 기준시각과 데이터 품질 상태를 포함한다. |

### 3.5 거래상태와 특수 이벤트

| ID | 요구사항 |
| --- | --- |
| MKT-040 | 거래정지, VI, 동시호가, 장전·장후와 장종료를 내부 거래상태로 정규화한다. |
| MKT-041 | 거래상태가 불명확하면 신규매수는 fail-closed 처리한다. |
| MKT-042 | 액면분할·병합·권리락처럼 가격 연속성을 깨는 기업행동은 해당 거래일 분석 기준을 초기화하거나 별도 조정 데이터로 처리한다. |
| MKT-043 | 상·하한가 또는 호가 부재는 0원 호가로 해석하지 않고 별도 상태로 표시한다. |

### 3.6 저장과 배포

| ID | 요구사항 |
| --- | --- |
| MKT-050 | Redis에는 최신 snapshot과 짧은 계산 윈도만 저장하고 거래 판단에 사용된 snapshot은 PostgreSQL에 불변 참조로 남긴다. |
| MKT-051 | 원본 tick의 기본 온라인 보존은 30일, 1분봉·판단 입력 snapshot은 1년으로 하며 운영자가 보존 정책을 변경할 수 있다. |
| MKT-052 | 보존 만료로 삭제해도 판단·주문 감사에 연결된 최소 snapshot과 해시는 삭제하지 않는다. |
| MKT-053 | Watch는 종목별 단일 writer 원칙을 사용하고 장애 승계 시 snapshot 복원 후 이벤트 처리를 시작한다. |

### 3.7 첫 Watch 영속 기반

키움 WebSocket 연결 전 단계에서는 결정론적 내부 ingestion service와 fixture로 정규화 계약을 검증한다. 운영 Web에는 시세 주입 endpoint를 만들지 않고, 인증된 최신 snapshot 조회만 제공한다.

| ID | 요구사항 |
| --- | --- |
| MKT-060 | 정규화된 quote와 시장·종목별 stream 상태를 PostgreSQL에 분리 저장하고 현재 snapshot은 stream 상태가 가리키는 정상 snapshot으로 결정한다. |
| MKT-061 | 같은 `source + market + symbol + sequence_or_hash`와 같은 payload는 중복으로 무시하며 내용이 다르면 충돌로 격리한다. |
| MKT-062 | 이벤트 시각 또는 숫자 순번이 현재보다 과거면 `LATE` 이력으로 저장하되 현재 snapshot을 바꾸지 않는다. |
| MKT-063 | 숫자 순번 갭이나 누적 거래량 역행은 stream을 `GAP_DETECTED`로 만들고 이전 정상 snapshot을 유지한다. 일반 이벤트만으로 자동 복구하지 않는다. |
| MKT-064 | 명시적인 복구 snapshot만 `GAP_DETECTED`를 해제할 수 있으며 이후 최신성 안정 구간은 Guard 연동 단계에서 별도로 적용한다. |
| MKT-065 | 첫 조회 API는 최신 정상 snapshot, stream 품질과 서버 기준 경과시간을 반환하고 주문 가능 여부를 추정하지 않는다. |
| MKT-066 | 운영 HTTP API에는 quote·순번·stream 품질을 임의로 생성하거나 수정하는 endpoint를 제공하지 않는다. |

### 3.8 키움 REST 복구 snapshot 정규화

| ID | 요구사항 |
| --- | --- |
| MKT-070 | 키움 `ka10001` 응답의 `cur_prc`, `open_pric`, `high_pric`, `low_pric`, `trde_qty`를 내부 `QuoteEvent`로 정규화한다. |
| MKT-071 | 가격 문자열의 `+`·`-`는 전일 대비 방향 표기이므로 내부 절대 가격에는 부호를 제거한다. 빈 값, 0 이하 가격, 음수 거래량과 종목 불일치는 저장 전에 거부한다. |
| MKT-072 | REST 응답에는 거래소 event timestamp와 안정적인 sequence가 없으므로 수신시각을 event 시각으로 사용하고 정규화 payload hash를 `sequence_or_hash`로 사용한다. |
| MKT-073 | REST snapshot의 거래 상태는 응답에서 추정하지 않고 세션 관리자에게 명시적으로 전달받는다. 상태가 불명확하면 신규매수에 사용할 수 없다. |
| MKT-074 | REST snapshot은 WebSocket 시작 전 seed 또는 gap 복구 입력이며 정상 실시간 stream을 대체하지 않는다. |

### 3.9 감시 종목과 키움 실시간 stream

2026-08-04 키움 공식 가이드에서 국내주식 WebSocket `REG`/`REMOVE`, 주식체결 `0B`, 주식호가잔량 `0D` 계약과 모의투자 KRX 전용 제약을 확인했다.

참고 자료:

- <https://openapi.kiwoom.com/guide/apiguide?jobTpCode=15>
- <https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom_docs/%EC%8B%A4%EC%8B%9C%EA%B0%84%EC%8B%9C%EC%84%B8.md>

| ID | 요구사항 |
| --- | --- |
| MKT-080 | 사용자별 활성 감시 종목은 최대 3개이며 숫자 6자리 종목코드와 시장을 중복 없이 저장한다. 첫 키움 모의투자 연결은 공식 제약에 따라 KRX만 등록할 수 있다. |
| MKT-081 | 감시 종목 변경 API는 인증·CSRF 검사를 통과해야 하며 등록·해제 결과를 감사 로그에 남긴다. 이미 등록된 종목과 3개 초과 등록은 변경 없이 거부한다. |
| MKT-082 | 단일 Broker worker는 계좌 이벤트 그룹과 분리된 그룹으로 활성 KRX 종목의 `0B`·`0D`를 등록한다. 시작·재연결 때 전체 목록을 재등록하고 실행 중에는 5초 이내 변경을 반영한다. |
| MKT-083 | `0B`의 FID `20`, `10`, `13`, `16`, `17`, `18`, `27`, `28`을 각각 체결시각·현재가·누적거래량·시가·고가·저가·최우선 매도·매수호가로 해석한다. 가격의 부호는 방향 표시이므로 절대값으로 정규화한다. |
| MKT-084 | `0D`의 FID `21`, `41`, `61`, `51`, `71`을 호가시각·매도1호가·매도1수량·매수1호가·매수1수량으로 해석한다. 최근 `0B`가 있는 경우에만 거래 snapshot과 결합하며 가격·수량이 완전하지 않은 호가는 사용하지 않는다. |
| MKT-085 | 키움 실시간 payload에 안정적인 sequence가 없으므로 `type + item + values`의 정규화 hash를 `sequence_or_hash`로 사용한다. 같은 payload 재수신은 중복으로 처리한다. |
| MKT-086 | 각 정상화된 `0B` 및 결합 가능한 `0D` 이벤트는 기존 `ingest_quote`를 통해 PostgreSQL에 저장한다. 잘못된 필드 하나는 해당 이벤트만 폐기하고 worker 연결·주문 게이트를 임의로 READY로 변경하지 않는다. |
| MKT-087 | Web UI는 감시 종목, 남은 슬롯, 최신 snapshot 가격·품질·경과시간과 WebSocket 데이터 대기 상태를 표시한다. 화면에서 임의 시세를 주입하는 기능은 제공하지 않는다. |

### 3.10 1분봉과 1차 지표 계산

| ID | 요구사항 |
| --- | --- |
| MKT-090 | 정상 `0B` 체결 이벤트만 Asia/Seoul 기준 1분 경계로 OHLCV 봉에 반영한다. `0D` 호가 이벤트는 봉을 변경하지 않고 최신 지표의 spread만 갱신한다. |
| MKT-091 | 분봉 거래량은 같은 거래일의 직전 정상 누적거래량과 현재 누적거래량 차이로 계산한다. 첫 관측과 거래일 변경 직후에는 알 수 없는 과거 거래량을 0으로 두며 음수 차이를 봉에 반영하지 않는다. |
| MKT-092 | 분봉은 `market + symbol + bucket_start`로 하나만 유지하고 open은 최초 체결, high·low는 극값, close는 마지막 체결, turnover는 `체결가 × 거래량 증분` 합계로 결정한다. |
| MKT-093 | `watch-indicators-v1`은 현재 snapshot마다 당일 VWAP, 최근 완성·진행 봉 5개의 SMA5, 당일 고가, 고점 대비 하락률과 최우선 호가 spread를 계산해 입력 snapshot에 결합한 불변 행으로 저장한다. |
| MKT-094 | 거래량이 아직 관측되지 않은 VWAP은 현재가를 사용하고, 봉이 5개 미만인 SMA5와 완전한 양방향 호가가 없는 spread는 null로 표시한다. |
| MKT-095 | `LATE`·`GAP_DETECTED` 이벤트는 분봉·지표의 정상 입력으로 사용하지 않는다. KST 거래일이 바뀐 정상 이벤트의 누적거래량 감소는 전일 대비 gap으로 판정하지 않는다. |
| MKT-096 | 감시 종목 조회는 최신 snapshot에 연결된 1차 지표와 최근 분봉 개수를 함께 제공하되 주문 가능 여부를 계산하지 않는다. |
| MKT-097 | `watch-indicators-v2`는 v1 지표에 현재가의 VWAP 대비 비율, SMA5 기울기, 최근 5개 봉과 직전 5개 봉의 상대 거래량, 최근 최대 10개 봉 close 수익률의 실현 변동성을 추가한다. |
| MKT-098 | 상대 거래량은 두 5개 구간이 모두 존재하고 직전 구간 거래량 합계가 0보다 클 때만 계산한다. SMA5 기울기는 6개 봉 이상, 실현 변동성은 3개 봉 이상일 때만 계산하며 부족한 값은 0이 아니라 null이다. |
| MKT-099 | v2 지표는 market snapshot과 1:1인 불변 행에 계산 버전·입력 범위와 함께 저장한다. 매수·매도 체결강도는 키움 aggressor-side 신뢰 필드가 검증되기 전까지 계산하지 않는다. |

### 3.11 시장·업종 Context snapshot

| ID | 요구사항 |
| --- | --- |
| MKT-100 | `market-context-v1`은 종목이 속한 시장 index와 sector 식별자·등락률, 상승·하락·보합 종목 수를 trusted internal Adapter가 정규화한 불변 snapshot이다. 공식 또는 계약된 source Adapter가 없는 값은 null 또는 snapshot 부재로 남긴다. |
| MKT-101 | breadth 비율은 `(상승 종목 수 / (상승+하락+보합)) × 100`으로 서버가 계산한다. 분모가 0이면 null이며 원시 count는 음수일 수 없다. |
| MKT-102 | snapshot은 `source + market + symbol + source_ref`로 중복을 억제하고 canonical payload hash가 다른 동일 identity는 충돌로 거부한다. source credential과 원문 body는 저장하지 않는다. |
| MKT-103 | `observed_at ≤ received_at`, `observed_at < valid_until`, 지원 market, 6자리 종목코드, 유효 Decimal 범위와 `NORMAL/INCOMPLETE` 품질을 검증한다. `INCOMPLETE` snapshot은 판단 입력으로 선택하지 않는다. |
| MKT-104 | 운영 HTTP API에는 Market Context 생성·수정 endpoint를 제공하지 않는다. 초기 구현은 내부 service와 fixture로 저장·선택 계약을 검증하고 실제 source Adapter 선정 전 운영 데이터 부재를 정상 결측으로 취급한다. |
| MKT-105 | Agent admission에 사용된 Market Context ID와 hash는 PostgreSQL run에 남아 원본 snapshot이 이후 추가돼도 기존 run 입력이 변하지 않아야 한다. |

## 4. 오류·예외 또는 경계 조건

- 거래량이 역행하거나 가격이 유효 범위를 벗어나면 해당 이벤트를 격리하고 이전 정상 snapshot을 유지한다.
- 단일 종목 데이터 오류는 기본적으로 그 종목만 중지하되 공통 채널·시각 이상은 계좌 신규진입 전체를 중지한다.
- 재처리로 지표가 변경돼도 이미 전송한 주문을 과거 상태로 되돌리지 않고 수정 사실을 감사한다.
- 시세 최신성과 Broker 계좌 재동기화는 서로 독립적으로 통과해야 거래가 재개된다.

## 5. 검증·인수 조건

- 중복·역순·누락 이벤트에서도 현재 snapshot과 누적 거래량이 일관된다.
- KRX·NXT 데이터가 혼합되지 않고 KRX 모의주문은 KRX 호가만 사용한다.
- 최신성 기준 초과 시 신규매수가 차단되고 복구 안정 구간 전에는 재개되지 않는다.
- 1분봉과 지표가 고정 fixture에서 재현 가능하게 계산된다.
- 판단과 주문에서 사용한 데이터 시점·시장·품질·계산 버전을 추적할 수 있다.

## 6. 미결정·보류 항목

- 키움 WebSocket이 제공하는 시세별 전역·종목 순번의 실제 안정성
- 종목별 기업행동 데이터 공급원
- 원본 tick 보존량 측정 후 압축·파티션 최종값
