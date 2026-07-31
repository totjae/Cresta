# Cresta

**Cresta — AI-Assisted Intraday Trading System**은 사용자가 선택한 국내 주식의 진입과 청산을 분석하고, 규칙 기반 리스크 엔진을 통해 주문을 통제하는 개인용 단기매매 시스템입니다.

이 저장소는 제품 정의와 MVP 시스템 설계, 그리고 이를 검토하기 위한 반응형 Console 프로토타입을 포함합니다. 첫 제품 버전은 키움 REST API 모의투자 주문 연결을 목표로 하지만, 현재 화면은 실제 주문을 전송하지 않는 정적 데모입니다.

## 빠른 실행

```bash
python3 -m http.server 8080
```

브라우저에서 `http://localhost:8080`을 엽니다.

## 문서

- [제품 요구사항](docs/PRODUCT_REQUIREMENTS.md)
- [거래 세션 및 감시 운영 명세](docs/TRADING_SESSION_SPEC.md)
- [주문 가격 및 미체결 재처리 명세](docs/ORDER_EXECUTION_SPEC.md)
- [주문 상태 머신 및 키움 매핑 명세](docs/ORDER_STATE_MACHINE_SPEC.md)
- [계좌·주문 재동기화 명세](docs/RECONCILIATION_SPEC.md)
- [키움 Broker Adapter 명세](docs/KIWOOM_BROKER_SPEC.md)
- [Guard 리스크 및 비상정지 명세](docs/GUARD_RISK_SPEC.md)
- [사용자 설정 및 적용 명세](docs/CONFIGURATION_SPEC.md)
- [Web UI 명세](docs/WEB_UI_SPEC.md)
- [인증 및 보안 명세](docs/SECURITY_SPEC.md)
- [시장데이터 및 Watch 명세](docs/MARKET_DATA_SPEC.md)
- [Scout·Core AI 판단 계약](docs/AI_DECISION_SPEC.md)
- [데이터베이스 및 영속성 명세](docs/DATABASE_SPEC.md)
- [배포·운영·장애복구 명세](docs/OPERATIONS_RUNBOOK.md)
- [구현 착수 준비도 검토](docs/IMPLEMENTATION_READINESS_REVIEW.md)
- [MVP 제품 및 시스템 설계](docs/SYSTEM_DESIGN.md)
- [HTTP 및 WebSocket API 명세](docs/API_SPEC.md)
- [구현 상태](IMPLEMENTATION_STATUS.md)
- [테스트 계획](TEST_PLAN.md)

## 안전 원칙

실거래보다 분석 전용 → 자체 모의매매 → 증권사 모의투자 → 승인형 실거래 순서로 검증합니다. AI의 출력은 주문이 아니라 제한된 행동 제안이며, 모든 주문은 Cresta Guard의 결정론적 검사를 통과해야 합니다.
