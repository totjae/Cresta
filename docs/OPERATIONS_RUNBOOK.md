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

초기 저장공간 예산은 PostgreSQL 100GiB, optional local snapshot 최대 50GiB, 로그 15GiB, 시험·진단 artifacts 10GiB, 이미지·빌드 cache 15GiB로 제한하고 최소 60GiB를 운영 여유 공간으로 남긴다. optional snapshot 용량은 보존 예약이나 내구성 보장이 아니다. 실제 사용량에 따라 조정하되 여유 공간 20% 미만에서는 경고, 10% 미만에서는 신규매수와 고용량 수집을 차단한다.

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
→ one-shot migration 20260829_0044 success
→ Redis healthy
→ API·scheduler·agent·sourced-handoff·Broker worker start
→ API /readyz·Console ready
→ Active Broker lease 획득
→ 키움 인증·계좌조회
→ 계좌 전체 재동기화
→ Watch 구독 및 데이터 안정 구간
→ Guard 점검
→ READY
```

배포 process inventory:

| service | entrypoint | dependency | host port | durable state | health/restart |
| --- | --- | --- | --- | --- | --- |
| postgres | upstream PostgreSQL 17 | secret file·bind volume | 없음 | `data/postgres` | `pg_isready`, unless-stopped |
| redis | `redis-server --appendonly yes` | bind volume | 없음 | `data/redis` AOF | `redis-cli ping`, unless-stopped |
| migration | `alembic upgrade head` | PostgreSQL healthy | 없음 | schema only | successful completion, restart no |
| api | backend image default Uvicorn | migration success·Redis healthy | 없음 | PostgreSQL | `/healthz`, `/readyz`, unless-stopped |
| frontend | `node server.js` | API healthy | 없음 | 없음 | HTTP root, unless-stopped |
| nginx | `nginx` | API·Frontend healthy | `127.0.0.1:7788` | 없음 | proxied `/healthz`, unless-stopped |
| worker | `cresta-worker kiwoom` | migration success·Redis healthy·MOCK secrets | 없음 | PostgreSQL | process/Broker state, unless-stopped |
| scheduler | `cresta-worker scheduler` | migration success·Redis healthy | 없음 | PostgreSQL | scheduler lease/state, unless-stopped |
| agent | `cresta-worker agent` | migration success·Redis healthy | 없음 | PostgreSQL | claim lease/fencing, unless-stopped |
| sourced-handoff | `cresta-worker sourced-handoff` | migration success | 없음 | PostgreSQL | process/log counters, unless-stopped |

| ID | 요구사항 |
| --- | --- |
| OPS-010 | 컨테이너 프로세스가 실행 중인 상태와 주문 가능한 `READY` 상태를 구분한다. |
| OPS-011 | 위 의존 단계 중 하나라도 실패하면 신규 주문 게이트를 열지 않는다. |
| OPS-012 | 종료 시 먼저 신규 작업을 중단하고 큐를 drain한 뒤 Broker lease를 반납한다. 결과 불명 주문이 있으면 `UNKNOWN`으로 남겨 재시작 대조 대상으로 만든다. |
| OPS-013 | 비상정지, 미해결 불일치와 설정 활성 버전은 컨테이너 재시작에도 유지한다. |
| OPS-014 | 서버의 매일 06:00 예정 재부팅과 예기치 않은 Docker daemon 재시작 뒤 모든 Compose 서비스는 운영자 로그인 없이 다시 시작한다. API·Console·gateway는 자체 healthcheck를 가지며 컨테이너 재시작 정책은 `unless-stopped`로 통일한다. |
| OPS-015 | 부팅 시 `cresta-boot.service`가 Docker와 network-online 이후 기본 Compose와 키움 overlay를 `up -d --wait`로 한 번 조정한다. 180초 안에 user-facing health가 준비되지 않으면 최대 5회 재시도하고 실패 상태를 남기되, 거래 게이트는 열지 않는다. |
| OPS-016 | 부팅 완료 판정은 컨테이너의 단순 `running`이 아니라 PostgreSQL·Redis·API·Console·gateway health 통과를 요구한다. Broker worker는 프로세스 기동 후 자체 재연결·재동기화로 `READY`를 회복하며, 외부 키움 장애 때문에 Web Console까지 중단시키지 않는다. |
| OPS-017 | AI scheduler는 별도 `scheduler` 컨테이너로 실행하고 `unless-stopped`, DB 의존성, 256MiB·0.25 CPU 상한을 적용한다. scheduler 장애는 Broker worker와 Console을 중단시키지 않으며 신규 AI 판단만 중단한다. |
| OPS-018 | scheduler 정상 상태는 유효 lease와 최근 heartbeat로 판정한다. 장외 `IDLE`은 정상이며 장중 heartbeat가 lease 기준을 넘으면 `STALE`로 경보한다. |
| OPS-019 | Agent DAG는 별도 `agent` 컨테이너에서 실행하고 `unless-stopped`, DB 의존성, 256MiB·0.25 CPU 상한을 적용한다. agent 장애는 Broker·API를 중단하지 않으며 PENDING/RUNNING stage는 lease 만료 후 fencing 규칙으로 복구한다. |

재부팅 복구 구성은 두 층으로 나눈다. Docker의 `unless-stopped` 정책은 개별 컨테이너의 종료와 Docker daemon 재시작을 복구하고, systemd oneshot은 부팅 때 누락·수동 정지된 컨테이너까지 Compose 정의와 일치시키며 준비 상태를 기다린다. systemd는 컨테이너 프로세스를 상시 감시하거나 개별 재시작하지 않으므로 Docker 재시작 정책과 역할이 중복되지 않는다.

```text
network-online + docker active
→ cresta-boot.service: compose config 검증
→ compose up -d --wait --wait-timeout 180
→ PostgreSQL healthy → migration success → Redis healthy
→ API·Console healthy
→ gateway /healthz healthy
→ boot unit active (exited)
→ Broker worker가 독립적으로 READY 복구
```

호스트 최초 1회 설치:

```bash
cd /home/totquf4171/cresta
sudo install -o root -g root -m 0644 \
  deploy/cresta-boot.service \
  /etc/systemd/system/cresta-boot.service
sudo systemctl daemon-reload
sudo systemctl enable --now cresta-boot.service
```

저장소에서 unit 파일이 변경된 배포는 위 `install`과 `daemon-reload`를 다시 실행한다. 예정 재부팅 후 다음 명령이 모두 성공해야 한다.

```bash
sudo systemctl is-enabled docker cresta-boot.service
sudo systemctl --no-pager --full status cresta-boot.service
sudo docker compose \
  -f /home/totquf4171/cresta/deploy/compose.yaml \
  -f /home/totquf4171/cresta/deploy/compose.kiwoom.yaml \
  ps
curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:7788/healthz
```

`compose ps`에서 PostgreSQL·Redis·API·Frontend·Nginx는 `healthy`, worker는 `Up`이어야 한다. 이어서 인증된 Console의 Broker 상태가 `READY`로 복구되는지 확인한다. Broker가 `DEGRADED`여도 Console과 API가 healthy이면 부팅 복구 자체는 성공이며, 거래 게이트를 닫은 채 키움 연결 원인을 별도로 처리한다.

### 3.3 배포·업데이트·롤백

배포 절차:

```text
명세·migration·시험 결과 확인
→ 이미지 digest 고정 및 취약점 검사
→ 필요 시 optional convenience DB snapshot
→ 장외 또는 신규진입 중지 상태 전환
→ one-shot migration 실행 및 성공 확인
→ 서비스 교체
→ health·인증·재동기화·시세 검사
→ 수동 READY 승인
```

| ID | 요구사항 |
| --- | --- |
| OPS-020 | 운영 이미지는 변경 가능한 tag만 쓰지 않고 digest 또는 불변 버전으로 배포한다. |
| OPS-021 | 장중 무중단 자동 배포를 첫 버전에서 지원하지 않으며 신규진입 중지와 미체결 확인 후 배포한다. |
| OPS-022 | schema 비호환 또는 health 실패 시 거래 게이트를 닫는다. 현재 `MOCK`/development는 호환되는 이전 이미지로 롤백하거나, 데이터 유실을 수용하고 fresh database에 migration을 재적용해 재구축할 수 있다. 향후 LIVE rollback과 destructive migration 복구 정책은 LIVE readiness에서 별도로 정한다. |
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

Phase 11A.2에서 확정한 현재 `MOCK`/development 정책:

```yaml
scope: mock_development
postgres_runtime_data: disposable
redis_runtime_data: disposable
decision_order_execution_history: disposable
backup: optional
encrypted_backup_required: false
off_host_copy_required: false
restore_drill_required: false
recovery: fresh_database_then_alembic_head_then_runtime_restart
data_loss_accepted: true
```

Git repository와 migration chain이 재구축 source of truth다. PostgreSQL이 실행 중일 때의 transaction authority와 장기 보존 의무는 구분한다. 이 정책은 LIVE에 자동 적용하지 않으며 LIVE backup·retention·RPO/RTO는 향후 LIVE readiness에서 명시적으로 다시 결정한다.

| ID | 요구사항 |
| --- | --- |
| OPS-040 | 현재 `MOCK`/development PostgreSQL snapshot은 선택 사항이며 암호화·off-host copy·restore rehearsal을 요구하지 않는다. 생성한 snapshot은 Git에 포함하지 않고 접근 권한을 최소화한다. |
| OPS-041 | 현재 `MOCK`/development에서는 PostgreSQL·Redis와 runtime history 유실을 의도적으로 허용하며 backup 부재나 동일 host 유실 가능성을 deployment blocker로 취급하지 않는다. |
| OPS-042 | 현재 복구 절차는 fresh PostgreSQL 생성 → 전체 Alembic migration 적용 → runtime 재시작이다. 기존 runtime row 복원은 완료 조건이 아니다. |
| OPS-043 | Redis는 disposable cache/queue state이며 유실 시 PostgreSQL과 외부 source를 기준으로 필요한 상태를 다시 구성한다. PostgreSQL까지 유실된 경우 fresh database recovery를 사용한다. |
| OPS-044 | fresh database로 재구축한 서버는 migration head, MOCK 환경, secret 준비, Broker 계좌 재동기화와 사용자 수동 확인 전 주문 권한을 얻지 않는다. |

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
- 필요 시 생성한 optional convenience snapshot의 상태 확인; snapshot·원격 복제 부재는 현재 MOCK 운영 blocker가 아님

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
  up -d --build api worker scheduler agent sourced-handoff

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
- 현재 MOCK/development에는 runtime data RPO/RTO를 두지 않으며 데이터 유실을 허용한다. LIVE RPO/RTO는 향후 별도 정의한다.
- 백업 복원본을 기존 서버와 동시에 같은 계좌의 Active worker로 실행하지 않는다.
- TLS 인증서 만료 임박 시 갱신 실패 경보를 발생시키며 만료 후 HTTP 우회 접속을 열지 않는다.
- 호스트 Nginx가 `Host`, `X-Forwarded-Proto`, `X-Forwarded-For`, `X-Request-Id`를 전달하지 않거나 HTTPS 원본을 보장하지 못하면 인증 서비스 공개를 중지한다.

### 4.1 LLM Provider·Ollama 운영 계약

| ID | 요구사항 |
| --- | --- |
| OPS-070 | 외부 LLM secret이 없거나 provider가 비활성이면 core Compose 기동은 실패하지 않고 AI route만 `NOT_CONFIGURED/SHADOW_DISABLED`로 유지한다. |
| OPS-071 | 활성 provider secret은 `deploy/prepare-secrets.sh` 또는 동등한 절차로 UID/GID `10001:10001`, mode `0400`을 확인하고 값은 출력하지 않는다. |
| OPS-072 | Provider health, circuit, rate/cost limit, schema 실패율과 p95 지연을 모니터링하고 신규매수 차단 여부를 함께 경보한다. |
| OPS-073 | Ollama는 외부 포트로 공개하지 않고 Cresta 내부 network 또는 loopback에서만 접근한다. N100/16GB에서 메모리 여유 20% 경고·10% 신규 호출 차단과 동시 호출 1개를 기본으로 한다. |
| OPS-074 | provider profile·route·prompt·schema 변경 배포는 SHADOW rollback 경로와 이전 ACTIVE version을 보존하고 진행 중 Core run의 model을 중간에 변경하지 않는다. |
| OPS-075 | LLM 장애 대응은 신규 AI 매수 판단을 중지하되 Broker worker·재동기화·실시간 Guard를 재시작하거나 중단시키지 않는다. |
| OPS-076 | `backend/app/agents`, 공통 LLM Adapter 또는 Agent migration 변경 배포는 `api`, `scheduler`뿐 아니라 Compose `agent` 이미지를 반드시 build·recreate한다. 배포 후 `agent` 컨테이너의 source marker와 신규 DAG stage 수를 확인한다. |
| OPS-077 | OpenDART는 선택형 `deploy/compose.dart.yaml`로만 활성화한다. `secrets/dart_api_key`가 없거나 유효하지 않으면 기본 Compose는 계속 기동하되 DART 활성 run은 생성·수집하지 않는다. |
| OPS-078 | OpenDART를 활성 운영하는 호스트의 부팅 조정 명령에는 `compose.dart.yaml`을 포함해야 한다. `boot-reconcile.sh`가 secret을 감지해 이를 포함하지만 실제 재부팅 인수시험 전에는 DART가 자동복구 검증됐다고 표시하지 않는다. |
| OPS-079 | `deploy/boot-reconcile.sh`는 기본·키움 Compose를 고정하고, 비어 있지 않은 `secrets/dart_api_key`와 `secrets/krx_api_key`가 존재할 때만 각 선택 overlay를 추가한다. 선택 secret 일부가 없다는 이유로 기본 MOCK 스택을 중단하지 않는다. |
| OPS-080 | KRX OPEN API는 선택형 `deploy/compose.krx.yaml`로 활성화한다. 인증키와 유가증권·코스닥 일별매매 서비스 승인이 모두 준비되지 않으면 활성화하지 않는다. key당 일 10,000회 한도 보호를 위해 일자·시장 캐시를 사용하며 오류 반복 시 신규 Agent run을 중지한다. |
| OPS-081 | 부팅 unit 변경 후 `boot-reconcile.sh --check`, systemd daemon-reload, 재부팅 인수시험을 수행한다. DART·KRX secret 또는 완전한 NAVER credential 쌍이 있는 호스트에서는 재부팅 뒤 API와 Agent 컨테이너에 해당 overlay 설정이 모두 복원되어야 한다. |
| OPS-082 | NAVER API HUB News는 선택형 `deploy/compose.naver-news.yaml`로 활성화한다. Client ID·Secret 두 파일이 모두 존재할 때만 boot reconcile이 overlay를 포함하며 일부만 존재하면 설정 오류로 중단한다. |
| OPS-083 | 뉴스 검색은 run당 최대 1회·20건, 기본 5분 cache와 72시간 freshness를 사용한다. 공식 일 25,000회 한도 또는 비용 경보가 발생하면 신규 Agent run을 중지하고 DART·KRX·Broker·Guard는 유지한다. |
| OPS-084 | NAVER News 운영 전 NAVER Cloud에서 NAVER API HUB 신청, News Search 권한, 비용·한도 알림을 설정한다. `401/403`, `429`, timeout·5xx를 각각 인증·한도·Provider 장애로 구분해 대응한다. |
| OPS-085 | `sourced-handoff`는 키움 Compose overlay의 별도 장기 실행 service이며 `CRESTA_V7_SOURCED_HANDOFF_ENABLED`가 unset/false이면 sweep 없이 정상 종료한다. historical eligible Decision 처리 영향을 검토한 뒤에만 `.env`에서 true로 명시한다. |
| OPS-086 | handoff 활성화 전 PostgreSQL migration head, exact-one execution/Approval/Order acceptance, MOCK target과 current Stage/Policy를 확인한다. worker는 Stage/Gate/Policy를 seed하지 않으며 LIVE endpoint·credential을 사용하지 않는다. |
| OPS-087 | worker started/stopped, sweep attempted, candidate/completed/deferred/failed count와 unexpected tick failure를 비밀·Decision body·DB URL 없이 기록한다. DB 장애 후 Decision이 그대로 eligible인지와 복구 sweep을 확인한다. |
| OPS-088 | Compose `migration` one-shot service만 `alembic upgrade head`를 실행한다. PostgreSQL health 뒤 시작하고 성공 종료 전 API와 모든 worker를 시작하지 않으며 실패 시 자동 downgrade/rollback 없이 운영자 개입을 요구한다. |
| OPS-089 | API `/healthz`는 dependency mutation 없는 process liveness, `/readyz`는 PostgreSQL 연결과 `alembic_version=20260829_0044`를 읽기 전용 확인하는 readiness다. schema 부재·drift·DB 오류는 HTTP 503이고 주문·Agent·handoff·migration을 실행하지 않는다. |
| OPS-090 | Redis는 현재 배포 topology의 cache/queue 준비 service지만 Phase 11A production Python의 authority source가 아니다. Compose startup은 Redis health를 기다리며 Redis 유실은 DB authority를 변경하지 않고 운영 restart/rebuild 대상으로 처리한다. |
| OPS-091 | 모든 Compose service는 `json-file` driver의 `max-size=10m`, `max-file=5` 상한을 사용한다. 새 log stack은 도입하지 않고 secret, 전체 DB URL, credential, Decision body와 proof를 기록하지 않는다. |
| OPS-092 | startup 순서는 PostgreSQL healthy → migration success 및 Redis healthy → API/workers → API ready → Frontend → gateway다. `depends_on` completion/health 조건과 application `/readyz`를 함께 사용한다. |
| OPS-093 | PostgreSQL과 Redis는 host port를 publish하지 않고 각각 bind-mounted `data/postgres`, `data/redis`에 durable state/AOF를 둔다. gateway만 `127.0.0.1:7788`에 publish하며 외부 TLS는 host Nginx가 소유한다. |
| OPS-094 | Phase 11A server preflight는 secret permission, Compose config, one-shot migration, `/readyz`, process status와 MOCK-safe env를 확인한다. local Docker 부재 시 image build/start는 `NOT_RUN_LOCAL`로 두고 Ubuntu에서 완료한다. |

### 4.5 Phase 11A MOCK soak baseline

Stage A는 sourced handoff OFF로 API/Frontend/PostgreSQL/Redis와 모든 worker의 uptime, restart count, CPU·memory·disk·log·DB connection/size를 관찰한다. Stage B는 current Stage를 SHADOW로 별도 검증한 뒤 handoff만 ON으로 전환해 DecisionExecution과 Order 0을 확인한다. Stage C는 검증된 MOCK authority에서 CREATED→BROKER_SEND→SUBMITTING→MOCK 결과와 reconciliation을 관찰한다. LIVE는 모든 단계에서 금지한다.

soak fail 조건은 unexpected crash 또는 무제한 restart 증가, memory/DB connection leak, duplicate DecisionExecution/Order, blind resend, persistent reconciliation backlog, migration drift, uncontrolled log growth, LIVE call과 safety control OFF 상태의 authority 생성이다. 서버별 CPU/RAM 임계값은 실제 host baseline을 얻기 전 임의로 정하지 않는다.

### 4.6 Phase 11A Ubuntu build·운영 절차

사전 조건은 Ubuntu host, Docker Engine과 Compose plugin, `/home/totquf4171/cresta`, host Nginx/TLS, 충분한 disk와 fresh PostgreSQL에 migration head를 적용할 수 있는 검증된 경로다. 현재 MOCK/development의 backup/restore 경로는 필수 사전조건이 아니다. 실제 IP·추가 mount·경보 채널은 host preflight에서 기록하며 repository에 새 값으로 추정하지 않는다.

2026-08-31 생성한 `/home/totquf4171/cresta/backups/cresta-pre-v2-runtime-20260831-011222.dump`는 `OPTIONAL_PRE_DEPLOY_SNAPSHOT`이다. PostgreSQL custom archive 검증과 SHA-256 `277bb1aa5c6c68e069905d464dd5a4b7f3e5f6b82787478b53afdb867cab07ef` evidence는 편의상 유지하지만 암호화·off-host copy·restore rehearsal을 요구하지 않으며 삭제하지 않는다.

Phase 11A.2 시점 서버 checkout은 `refactor/v2-runtime`이고 `cresta-boot.service`는 host reboot 시 현재 checkout으로 Compose reconciliation과 one-shot migration을 수행할 수 있다. 다음 maintenance phase를 즉시 이어가는 동안에는 이를 deployment transition state로 취급한다. 작업이 지연되면 이전 `master` checkout으로 돌아가는 편이 더 안전하다는 운영 경고를 적용하되, 이 Phase에서는 branch·systemd·Compose를 변경하지 않는다.

1. 검토된 checkpoint를 `/home/totquf4171/cresta`에 clone 또는 fast-forward하고 branch/ref와 dirty state를 확인한다. 운영 ref는 review에서 확정한 immutable commit만 사용한다.
2. `deploy/.env.example`을 `deploy/.env`로 복사한 뒤 `MOCK`, `LIVE=false`, `V7_SOURCED_HANDOFF_ENABLED=false`를 유지한다. 실제 secret은 `.env`에 넣지 않는다.
3. `secrets/postgres_password`, `secrets/totp_encryption_key`와 필요한 MOCK/provider file만 준비하고 `sudo deploy/prepare-secrets.sh`를 실행한다.
4. 다음 명령으로 구성과 이미지를 준비한다.

```bash
cd /home/totquf4171/cresta
sudo deploy/boot-reconcile.sh --check
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml \
  build api migration worker scheduler agent sourced-handoff frontend
```

5. migration만 먼저 실행하고 성공·head를 확인한다. 실패 시 runtime을 시작하지 않으며 자동 downgrade하지 않는다.

```bash
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml \
  up --no-deps migration
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml \
  run --rm --no-deps migration alembic current
```

6. 전체 MOCK-safe stack을 시작하고 상태·liveness·readiness를 확인한다.

```bash
sudo deploy/boot-reconcile.sh --up
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml ps
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:7788/healthz
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).read().decode())"
```

7. bounded logs와 restart 상태를 확인한다.

```bash
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml \
  logs --tail=200 migration api worker scheduler agent sourced-handoff
sudo docker inspect --format '{{.Name}} restart={{.RestartCount}}' \
  $(sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml ps -q)
```

일반 restart는 `docker compose ... restart <service>`, clean stop은 `docker compose ... stop`, stack 제거는 volume 삭제 옵션 없이 `docker compose ... down`을 사용한다. application rollback은 먼저 handoff OFF와 Console의 PAUSE_ENTRY로 신규진입을 중지하고, 현재 schema head를 기록한 뒤 review된 이전 호환 image/commit을 checkout·build하여 같은 health 절차로 교체한다. optional snapshot은 있으면 보존하지만 필수 rollback 자산이 아니다. DB migration은 application image와 함께 자동 downgrade하지 않는다. 이전 image가 current schema와 호환되지 않으면 현재 MOCK/development에서는 별도 승인 아래 fresh database를 만들고 migration을 재적용하거나 review된 forward correction을 사용한다.

자동 handoff 긴급 중지는 `deploy/.env`의 `CRESTA_V7_SOURCED_HANDOFF_ENABLED=false`를 확인한 후 다음처럼 해당 service만 recreate한다.

```bash
sudo docker compose -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml \
  up -d --force-recreate sourced-handoff
```

이 flag는 새 Decision→Execution sweep만 멈춘다. `PAUSE_ENTRY`는 Console의 비상정지 동작으로 BUY authority를 차단하며, TradingGate는 Broker readiness/계좌 대조 상태다. 세 control을 서로 대체하거나 같은 권한으로 해석하지 않는다. 이미 `CREATED/SUBMITTING/UNKNOWN`인 주문은 flag 변경으로 삭제하지 않고 기존 Broker/reconciliation 절차로 처리한다.

soak 동안 `docker compose ps`, `docker stats`, `docker system df`, PostgreSQL connection/DB size, Redis memory, container restart count, handoff sweep count/error, duplicate execution/order, UNKNOWN, reconciliation backlog, log directory 증가와 migration head를 매일 기록한다. Stage A→B→C 전환은 각 단계의 failure 0과 review 후에만 수행한다.

### 4.2 OpenDART 공시 수집 활성화

공식 OpenDART에서 발급한 40자리 키를 `/home/totquf4171/cresta/secrets/dart_api_key`에 저장한다. 값은 터미널 출력, Git, 문서 또는 채팅에 남기지 않는다. `vi`로 저장한 뒤 다음과 같이 권한만 적용·확인한다.

```bash
cd /home/totquf4171/cresta
sudo deploy/prepare-secrets.sh
sudo stat -c '%u:%g %a %s-byte %n' secrets/dart_api_key
```

정상 기대값은 `10001:10001 400 41-byte`이며 마지막 1 byte는 줄바꿈일 수 있다. 활성 배포에는 기본·키움·DART Compose 파일을 모두 사용한다.

`cresta-boot.service`는 `deploy/boot-reconcile.sh`를 통해 비어 있지 않은 DART secret을 감지하고 overlay를 자동 포함한다. 업데이트 뒤 unit을 다시 설치하고 `sudo deploy/boot-reconcile.sh --check`를 통과시킨 다음 실제 재부팅 인수시험을 수행해야 자동복구 검증이 완료된다.

```bash
sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  -f deploy/compose.dart.yaml \
  build api agent

sudo docker compose \
  -f deploy/compose.yaml \
  -f deploy/compose.kiwoom.yaml \
  -f deploy/compose.dart.yaml \
  up -d --force-recreate api agent nginx
```

새 DIAGNOSTIC run의 `INTEL_COLLECTOR`가 `source_mode=OPENDART_PRIMARY`, `source_policy_version=opendart-list-v1`을 기록하면 활성화된 것이다. 공시가 없으면 `DART_QUERY_COMPLETE_NO_MATCHES`, 있으면 Bundle에 `DART_PRIMARY_EVIDENCE_VERIFIED`와 `DART_DISCLOSURE/PRIMARY` evidence ID가 나타난다. Bundle은 다른 출처 coverage가 없으므로 계속 `PARTIAL`이다. `DART_STATUS_010/011/012/020`, `DART_TIMED_OUT`, `DART_PROVIDER_ERROR`가 발생하면 키·출구 IP·호출 한도·네트워크를 확인하고 빈 성공으로 우회하지 않는다.

### 4.3 KRX 전 거래일 공식 시장 증거 활성화

KRX Data Marketplace에서 인증키 발급과 `유가증권 일별매매정보`, `코스닥 일별매매정보` 이용 승인을 완료한 뒤 40자리 키를 `/home/totquf4171/cresta/secrets/krx_api_key`에 저장한다. `sudo deploy/prepare-secrets.sh` 적용 후 `deploy/compose.krx.yaml`을 배포 명령에 추가한다. 이 Adapter는 실시간 시세를 대체하지 않고 최근 전 거래일의 공식 OHLC·거래량·거래대금만 PRIMARY 증거로 제공한다.

새 run의 INTEL 출력에서 `KRX_DAILY_PRIMARY`, `krx-stock-daily-v1`과 `KRX_PRIMARY_EVIDENCE_VERIFIED`를 확인한다. `KRX_QUERY_COMPLETE_NO_MATCH`는 정상 무자료이며 `KRX_TIMED_OUT`, `KRX_PROVIDER_ERROR`, `KRX_RESPONSE_INVALID`는 장애다. 배포 전후 `deploy/boot-reconcile.sh --check`로 선택 overlay 구성을 검증한다.

### 4.4 NAVER API HUB 뉴스 증거 활성화

NAVER Cloud Platform에서 NAVER API HUB와 뉴스 검색 권한을 활성화한 뒤 Client ID와 Client Secret을 각각 `/home/totquf4171/cresta/secrets/naver_api_hub_client_id`, `/home/totquf4171/cresta/secrets/naver_api_hub_client_secret`에 저장한다. `sudo deploy/prepare-secrets.sh` 적용 후 `deploy/compose.naver-news.yaml`을 배포 명령에 추가한다.

새 run의 INTEL 출력에서 `NAVER_NEWS_SECONDARY`와 `naver-api-hub-news-v1`을 확인한다. 72시간 이내 종목 연관 기사만 허용 evidence가 되며 stale 결과는 별도 ID로 감사한다. `NAVER_NEWS_AUTH_FAILED`, `NAVER_NEWS_QUOTA_EXCEEDED`, `NAVER_NEWS_TIMED_OUT`, `NAVER_NEWS_PROVIDER_ERROR`를 빈 검색 결과로 취급하지 않는다.

두 credential 파일 중 하나만 존재하면 설정 오류이므로 배포와 부팅 조정을 중단한다. 둘 다 준비된 호스트에서는 `deploy/boot-reconcile.sh --check` 결과에 `compose.naver-news.yaml`이 포함되어야 한다. 실제 운영 전에는 뉴스가 있는 종목, 정상 빈 결과, stale-only 결과, 401/403, 429와 timeout을 각각 인수시험하고 DB에 기사 본문·검색 요약·credential이 남지 않는지 확인한다.

## 5. 검증·인수 조건

- 깨끗한 Ubuntu 환경에서 문서화된 순서로 MOCK 서비스를 배포할 수 있다.
- 의존 서비스 장애별로 신규주문 차단과 복구 후 재동기화가 재현된다.
- 현재 MOCK/development에서 fresh database에 전체 migration을 적용하고 runtime을 재시작할 수 있다.
- 동일 계좌 worker 이중 실행과 복원 서버 동시 실행이 차단된다.
- 장 전·장 후 점검 결과와 장애 대응 이력이 감사 가능하다.
- 외부에서는 `https://trade.mihoservice.xyz`로만 접근할 수 있고, 원격 호스트에서 서버의 7788 포트로 직접 접근할 수 없다.
- LLM provider 미설정·장애·비용 한도와 Ollama 과부하에서 core 서비스와 Guard가 유지되고 AI 신규매수만 fail-closed된다.

## 6. 미결정·보류 항목

- 향후 LIVE의 backup·retention, 암호화, off-host 매체, RPO/RTO와 restore drill
- 경보 전달 채널과 야간 알림 정책
- TLS 인증서 발급·자동 갱신 도구와 갱신 실패 알림 방식
