# 배포·운영·장애복구 명세

## 1. 목적

Ubuntu 서버에서 Cresta를 안전하게 배포·운영하고, 장애·재시작·데이터 손상 시 신규 주문을 차단한 상태에서 복구·검증·재개하는 절차를 정의한다.

## 2. 적용 범위

- `/home/totquf4171/cresta` Docker Compose 배포
- 호스트 Nginx HTTPS reverse proxy, 내부 Web 포트와 파일 권한
- 서비스 시작·종료·업데이트·롤백
- 모니터링, 경보, 백업과 복원
- 장중 장애, 키움 연결 장애와 보안 사고

## 3. 상세 명세

### 3.1 배포 환경과 권한

확정 대상 서버 기준:

```yaml
host:
  os: Ubuntu Server
  processor: Intel N100
  memory_gib: 16
  available_ssd_gib: 250
```

확정 서비스 진입점:

```yaml
public_url: https://trade.mihoservice.xyz
internal_upstream: http://127.0.0.1:7788
tls_termination: host_nginx
```

호스트 Nginx만 인터넷의 80·443 포트를 수신한다. Docker Compose의 Cresta Web gateway는 호스트 루프백 `127.0.0.1:7788`에만 바인딩하며, DB·Redis·API 포트는 호스트에 게시하지 않는다. 따라서 7788은 인터넷 방화벽에서 개방하지 않는다.

N100 환경에서는 Scout·Core 모델을 서버에 직접 적재하지 않고 외부 모델 adapter 또는 mock adapter를 사용한다. 동시 감시 종목은 MVP 상한 3개를 유지하고, CPU 집약 분석 작업은 기본 동시 실행 1개로 제한한다.

초기 저장공간 예산은 PostgreSQL 100GiB, 암호화 백업 50GiB, 로그 15GiB, 시험·진단 artifacts 10GiB, 이미지·빌드 cache 15GiB로 제한하고 최소 60GiB를 운영 여유 공간으로 남긴다. 실제 사용량에 따라 조정하되 여유 공간 20% 미만에서는 경고, 10% 미만에서는 신규매수와 고용량 수집을 차단한다.

| ID | 요구사항 |
| --- | --- |
| OPS-001 | 배포 루트는 `/home/totquf4171/cresta`로 고정하고 앱·설정·비밀·데이터·로그·백업 디렉터리를 분리한다. |
| OPS-002 | API 컨테이너는 root가 아닌 전용 고정 UID/GID `10001:10001`로 실행하고 이미지에는 비밀값을 포함하지 않는다. 호스트의 API용 secret 파일도 같은 소유자로 설정한다. |
| OPS-003 | 공개 서비스 주소는 `https://trade.mihoservice.xyz`로 고정하고 인터넷에는 호스트 Nginx의 HTTPS 443만 노출한다. Cresta gateway는 `127.0.0.1:7788`에만 바인딩하며 DB·Redis·API·worker 포트는 Docker 내부망에만 둔다. |
| OPS-004 | SSH 접근은 키 기반과 방화벽 허용 정책을 사용하며 Cresta Web 인증과 별도로 관리한다. |
| OPS-005 | 운영 Compose는 `MOCK` 환경과 `live_trading_enabled=false`를 명시하고 실거래 secret이 발견되면 시작을 거부한다. |
| OPS-006 | N100·16GiB 환경에서 API·Broker·Watch의 총 리소스 제한을 설정하고 PostgreSQL·OS를 위한 메모리 6GiB 이상을 예약한다. |
| OPS-007 | 가용 SSD 20% 미만 경고와 10% 미만 거래·수집 차단 기준을 적용한다. |
| OPS-008 | API 이미지의 애플리케이션·migration 파일은 빌드 호스트의 소유권과 무관하게 실행 UID/GID `10001:10001`이 읽을 수 있도록 이미지에 복사한다. 임의의 root 실행으로 권한 오류를 우회하지 않는다. |

Compose의 `file` 기반 secret은 호스트 파일을 bind mount하며 `uid`, `gid`, `mode` 재매핑을 지원하지 않는다. 따라서 최초 배포와 secret 교체 후 다음 사전 점검을 반드시 실행한다.

```bash
cd /home/totquf4171/cresta
sudo deploy/prepare-secrets.sh
sudo docker compose -f deploy/compose.yaml run --rm api \
  sh -c 'test -r /run/secrets/postgres_password && test -r /run/secrets/totp_encryption_key'
```

준비 스크립트는 비밀 내용을 출력하지 않고 두 파일의 소유자를 `10001:10001`, 권한을 읽기 전용 `0400`으로 제한한다. 이 검사가 실패하면 migration, 관리자 생성과 API 시작을 진행하지 않는다.

API 이미지 빌드 후에는 실행 사용자와 주요 소스의 읽기 가능 여부를 확인한다.

```bash
sudo docker compose -f deploy/compose.yaml run --rm --no-deps api \
  sh -c 'test "$(id -u):$(id -g)" = "10001:10001" && test -r /app/app/broker/kiwoom.py'
```

### 3.2 서비스와 의존 순서

```text
PostgreSQL healthy
→ Redis healthy
→ API·Console ready
→ Active Broker lease 획득
→ 키움 인증·계좌조회
→ 계좌 전체 재동기화
→ Watch 구독 및 데이터 안정 구간
→ Guard 점검
→ READY
```

| ID | 요구사항 |
| --- | --- |
| OPS-010 | 컨테이너 프로세스가 실행 중인 상태와 주문 가능한 `READY` 상태를 구분한다. |
| OPS-011 | 위 의존 단계 중 하나라도 실패하면 신규 주문 게이트를 열지 않는다. |
| OPS-012 | 종료 시 먼저 신규 작업을 중단하고 큐를 drain한 뒤 Broker lease를 반납한다. 결과 불명 주문이 있으면 `UNKNOWN`으로 남겨 재시작 대조 대상으로 만든다. |
| OPS-013 | 비상정지, 미해결 불일치와 설정 활성 버전은 컨테이너 재시작에도 유지한다. |

### 3.3 배포·업데이트·롤백

배포 절차:

```text
명세·migration·시험 결과 확인
→ 이미지 digest 고정 및 취약점 검사
→ DB 암호화 백업
→ 장외 또는 신규진입 중지 상태 전환
→ migration 실행
→ 서비스 교체
→ health·인증·재동기화·시세 검사
→ 수동 READY 승인
```

| ID | 요구사항 |
| --- | --- |
| OPS-020 | 운영 이미지는 변경 가능한 tag만 쓰지 않고 digest 또는 불변 버전으로 배포한다. |
| OPS-021 | 장중 무중단 자동 배포를 첫 버전에서 지원하지 않으며 신규진입 중지와 미체결 확인 후 배포한다. |
| OPS-022 | schema 비호환 또는 health 실패 시 거래 게이트를 닫은 채 이전 호환 이미지로 롤백한다. destructive migration은 복원 절차를 사용한다. |
| OPS-023 | 배포자, 이미지·설정·schema 버전, 시작·종료 시각과 검증 결과를 기록한다. |

### 3.4 상태 확인과 모니터링

필수 상태:

```text
Nginx/TLS, Console, API, PostgreSQL, Redis
Broker lease, 키움 token/REST/WebSocket
Watch 시세 최신성·갭, queue 지연
재동기화, UNKNOWN 주문, 포지션 불일치
비상정지·거래 게이트, 디스크·메모리·CPU·서버 시각
```

| ID | 요구사항 |
| --- | --- |
| OPS-030 | liveness는 프로세스 교착 여부, readiness는 의존성과 역할 수행 가능 여부를 분리한다. |
| OPS-031 | 인증 없이 노출하는 health 응답은 `ok` 수준만 제공하고 Broker·계좌·버전 상세를 포함하지 않는다. |
| OPS-032 | 주문 응답 `UNKNOWN`, 계좌 불일치, 비상정지, 시각 오차, DB 저장 실패와 디스크 85% 이상은 즉시 경보한다. |
| OPS-033 | 로그는 구조화하고 correlation_id로 판단·승인·주문·체결·복구를 연결하며 비밀 마스킹 검사를 적용한다. |
| OPS-034 | 로그 파일은 크기·기간 기준으로 회전하고 디스크 고갈 전에 오래된 비감사 진단 로그를 정책에 따라 제거한다. |

### 3.5 백업과 복원 목표

초기 운영 목표:

```yaml
database:
  full_backup: daily_after_market
  wal_archive_or_incremental: 15_minutes
  off_host_copy: daily
  rpo_target: 15_minutes
  rto_target: 60_minutes
restore_drill: monthly
```

| ID | 요구사항 |
| --- | --- |
| OPS-040 | PostgreSQL 백업은 인증·TOTP·거래 데이터를 포함하므로 암호화하고 접근을 최소화한다. |
| OPS-041 | 백업 키는 백업 파일과 분리하고 서버 한 대의 디스크 고장으로 DB와 모든 백업이 함께 소실되지 않게 한다. |
| OPS-042 | 월 1회 격리 환경 복원 후 schema version, 주문 수량 불변조건, 감사 연결과 비밀 미노출을 검증한다. |
| OPS-043 | Redis는 필수 복구 원본이 아니며 복원 후 PostgreSQL과 키움으로 캐시·큐를 재구성한다. |
| OPS-044 | 복원된 서버는 키움 전체 재동기화와 사용자 수동 확인 전 주문 권한을 얻지 않는다. |

### 3.6 장중 장애 대응

| 장애 | 즉시 조치 | 복구 후 재개 조건 |
| --- | --- | --- |
| 키움 REST/WebSocket 단절 | 신규매수 차단, 진행 주문 UNKNOWN 가능성 표시 | 재연결·전체 대조·구독 안정 |
| PostgreSQL 쓰기 실패 | 모든 신규 주문 차단, Broker 요청 추가 전송 금지 | DB 정상·미확정 주문 대조 |
| Redis 유실 | 작업 intake 중지, DB 기반 큐·캐시 재구성 | 재동기화·중복 검사 |
| 시세 지연·갭 | 영향 종목 또는 전체 신규매수 중지 | snapshot·안정 구간 통과 |
| Broker lease 충돌 | 계좌 주문 전체 중지 | 단일 owner·fencing token·전체 대조 |
| 서버 시각 오차 | 로그인 재인증과 신규매수 차단 | NTP 정상·TOTP/시장시각 검사 |
| 디스크 부족 | 신규 주문·고용량 수집 차단, 경보 | 안전 공간 확보·DB 무결성 확인 |

| ID | 요구사항 |
| --- | --- |
| OPS-050 | 장애 중 자동 재시작 횟수를 제한하고 반복 실패 서비스를 무한 재시작해 외부 API를 폭주시키지 않는다. |
| OPS-051 | 주문 전송 여부가 불명확하면 동일 주문을 재전송하지 않고 재동기화한다. |
| OPS-052 | 기존 포지션이 있을 때 장애 사실과 수동 대체 거래 필요성을 최우선으로 표시·경보한다. |
| OPS-053 | 장애 해제는 원인 제거, health, 계좌 대조, 시세 안정, Guard 검사와 사용자 확인 순서를 따른다. |

### 3.7 보안 사고 대응

| ID | 요구사항 |
| --- | --- |
| OPS-060 | Web 계정·키움·DB secret 유출 의심 시 신규 주문과 외부 접근을 차단하고 관련 secret·세션·token을 폐기한다. |
| OPS-061 | 증거 보존 전 로그·DB를 임의 삭제하지 않고 비밀이 포함된 진단자료의 공유를 금지한다. |
| OPS-062 | TOTP 또는 관리자 계정 복구는 보안 명세의 로컬 절차를 사용하고 모든 세션을 폐기한다. |
| OPS-063 | 사고 종료 후 영향 주문·설정·접근 범위와 재발 방지 조치를 기록한다. |

초기 설치 중 DB 비밀번호가 오류 출력에 노출되고 아직 업무 schema·계정이 생성되지 않았다면 PostgreSQL을 중지하고 정확한 `data/postgres` 디렉터리를 격리한 뒤, 노출된 secret을 폐기·재생성하여 새 데이터 디렉터리로 초기화한다. 운영 데이터가 존재하면 데이터 디렉터리를 삭제하지 않고 별도의 승인된 `ALTER ROLE`·secret 교체·서비스 재시작 절차를 사용한다.

### 3.8 거래일 운영 점검표

장 전:

- 출구 IP, TLS, 서버 시각, 디스크 확인
- 키움 인증·계좌·미체결·포지션 대조
- 활성 설정·비상정지·감시 종목 확인
- 시세 구독과 최신성 확인

장 후:

- 익일 보유 정책과 잔여 포지션 확인
- 미체결·UNKNOWN·불일치 0건 또는 승인된 예외 확인
- 당일 주문·체결·손익·감사 로그 대조
- 백업 성공과 원격 복제 확인

## 4. 오류·예외 또는 경계 조건

### 3.8 키움 모의투자 secret 준비

키움 기능을 켜기 전 아래 세 파일을 `/home/totquf4171/cresta/secrets`에 만들고 컨테이너 실행 UID `10001`만 읽을 수 있게 한다. 값 자체를 터미널 기록, 문서 또는 채팅에 붙여넣지 않는다.

```bash
install -d -m 700 /home/totquf4171/cresta/secrets
sudo chown 10001:10001 /home/totquf4171/cresta/secrets/kiwoom_mock_app_key \
  /home/totquf4171/cresta/secrets/kiwoom_mock_app_secret \
  /home/totquf4171/cresta/secrets/kiwoom_mock_account_id
sudo chmod 0400 /home/totquf4171/cresta/secrets/kiwoom_mock_app_key \
  /home/totquf4171/cresta/secrets/kiwoom_mock_app_secret \
  /home/totquf4171/cresta/secrets/kiwoom_mock_account_id
```

세 파일을 만든 뒤에는 `sudo deploy/prepare-secrets.sh`로 기존 DB·TOTP secret과 함께 권한을 검사·적용할 수 있다. 키움 파일이 하나라도 존재하면 세 파일 모두 비어 있지 않아야 통과한다.

`kiwoom_mock_account_id`는 키움 `ka00001`이 반환하는 분류값 포함 숫자 10자리와 정확히 같아야 한다. 8자리 기본계좌만 저장한 기존 배포는 점검 전에 10자리 값으로 교체한다. 전체 계좌번호를 터미널 출력이나 작업 기록에 남기지 않는다.

계좌 부트스트랩 점검:

```bash
sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  exec -T api cresta-admin kiwoom-check
```

성공 상태는 `ACCOUNT_VERIFIED`이며 이는 일회성 점검 결과다. 상시 Broker worker의 lease·WebSocket·구독·재동기화까지 정상이어야 `READY`다.

읽기 전용 계좌 스냅샷 대조는 migration 적용 후 다음 명령으로 실행한다.

```bash
sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  exec -T api cresta-admin kiwoom-reconcile-check
```

수동 정상 대조는 `RECONCILING`을 반환한다. `HALTED` 또는 `DEGRADED`이면 신규 주문을 허용하지 않고 mismatch/error code만 진단 기록에 남긴다.

상시 worker 배포와 상태 확인:

```bash
sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  up -d --build api worker

sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  exec -T api cresta-admin kiwoom-worker-status

sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  logs --tail=100 worker
```

정상 출력은 `state=READY`, `gate_status=READY`, `lease_valid=true`, `websocket_connected=true`, `subscriptions_ready=true`다. status 명령은 READY가 아니면 종료 코드 5를 반환한다. 실제 token·전체 계좌번호·owner ID는 출력하지 않는다. worker를 이중 기동해도 lease를 가진 하나만 키움에 연결하며 대기 인스턴스는 직접 조회하지 않는다.

2026-08-04 운영 서버에서 위 상태가 모두 정상이고 종료 코드가 0인 것을 확인했다. worker 재시작 후에도 `READY`로 복귀했으며 fencing token이 1에서 2로 증가했다. 이는 재시작 승계 검증이며 두 컨테이너의 동시 경쟁 시험을 대체하지 않는다.

배포 worker는 `READY` 상태에서 내부 키움 MOCK 계좌의 검증된 `CREATED` 주문만 polling한다. 아직 주문 생성 API·Guard·승인 경로가 없으므로 DB 직접 조작이나 임시 CLI로 주문을 만들지 않는다. 실제 모의주문 시험은 종목·수량·가격과 취소 계획을 정하고 사용자 확인을 받은 별도 절차로 수행한다.

Docker Compose에서 API 또는 Frontend 컨테이너만 재생성하면 고정 upstream 주소를 시작 시 한 번만 해석한 gateway Nginx가 이전 컨테이너 IP를 유지해 `502 Bad Gateway`를 반환할 수 있다. gateway는 Docker embedded DNS `127.0.0.11`을 짧은 유효기간으로 사용해 `api`·`frontend` 서비스명을 다시 해석해야 한다. 배포 후에는 `/healthz`와 로그인 session endpoint를 확인하며, 동적 해석이 적용되지 않은 이전 이미지에서는 Nginx를 함께 재시작한다.

`CRESTA_KIWOOM_ENABLED=true`는 세 secret이 준비되고 출구 IP를 별도로 확인한 뒤에만 적용한다. 이번 단계의 `CONFIGURED`는 파일 준비 상태이며 키움 인증·계좌조회 성공을 뜻하지 않는다.

- 키움이나 네트워크 장애 시 Cresta가 시장 체결을 보장하지 않는다는 경고를 유지한다.
- RPO/RTO는 운영 목표이며 거래 결과 보장을 의미하지 않는다.
- 백업 복원본을 기존 서버와 동시에 같은 계좌의 Active worker로 실행하지 않는다.
- TLS 인증서 만료 임박 시 갱신 실패 경보를 발생시키며 만료 후 HTTP 우회 접속을 열지 않는다.
- 호스트 Nginx가 `Host`, `X-Forwarded-Proto`, `X-Forwarded-For`, `X-Request-Id`를 전달하지 않거나 HTTPS 원본을 보장하지 못하면 인증 서비스 공개를 중지한다.

## 5. 검증·인수 조건

- 깨끗한 Ubuntu 환경에서 문서화된 순서로 MOCK 서비스를 배포할 수 있다.
- 의존 서비스 장애별로 신규주문 차단과 복구 후 재동기화가 재현된다.
- 암호화 백업을 격리 환경에 복원하고 핵심 불변조건을 검증한다.
- 동일 계좌 worker 이중 실행과 복원 서버 동시 실행이 차단된다.
- 장 전·장 후 점검 결과와 장애 대응 이력이 감사 가능하다.
- 외부에서는 `https://trade.mihoservice.xyz`로만 접근할 수 있고, 원격 호스트에서 서버의 7788 포트로 직접 접근할 수 없다.

## 6. 미결정·보류 항목

- 외부 백업 매체와 암호화 키 보관 위치
- 경보 전달 채널과 야간 알림 정책
- TLS 인증서 발급·자동 갱신 도구와 갱신 실패 알림 방식
