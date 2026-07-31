# Cresta Console

Next.js App Router 기반 Web Console이다. 같은 origin의 FastAPI 인증 API를 사용하며 세션 token은 HttpOnly cookie, CSRF token은 페이지 메모리에만 유지한다.

현재 범위는 ID·비밀번호·TOTP 로그인, 세션 복구, 로그아웃, 실제 Paper 원장 조회와 Watch stream 상태를 표시하는 MOCK 대시보드다. 주문 생성·설정·상세 Watch 화면은 관련 Backend 흐름 구현 전까지 비활성 상태로 표시한다.

```bash
npm ci
npm run typecheck
npm test
npm run build
```
