# Cresta Console

Next.js App Router 기반 Web Console이다. 같은 origin의 FastAPI 인증 API를 사용하며 세션 token은 HttpOnly cookie, CSRF token은 페이지 메모리에만 유지한다.

현재 범위는 ID·비밀번호·TOTP 로그인, 세션 복구, 실제 Paper 원장 조회, 키움 MOCK 연결시험, 행동별 실행 권한과 결정론적 Mock AI 판단 화면이다. Mock 판단은 최신 영속 시세와 활성 설정 버전을 표시하지만 주문·승인을 만들지 않는다. 실제 AI 모델·승인함·상세 Watch 화면은 후속 구현 전까지 비활성 상태로 표시한다.

```bash
npm ci
npm run typecheck
npm test
npm run build
```
