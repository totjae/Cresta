# Cresta Console

Next.js App Router 기반 Web Console이다. 같은 origin의 FastAPI 인증 API를 사용하며 세션 token은 HttpOnly cookie, CSRF token은 페이지 메모리에만 유지한다.

현재 범위는 ID·비밀번호·TOTP 로그인, 세션 복구, 로그아웃, 실제 Paper 원장 조회, 키움 MOCK 연결시험과 행동별 실행 권한 설정 화면이다. 실행 권한은 안전 기본값 또는 서버의 불변 활성 버전을 표시하고 초안·검증·TOTP 활성화 절차를 사용한다. AI 판단·승인함·상세 Watch 화면은 후속 구현 전까지 비활성 상태로 표시한다.

```bash
npm ci
npm run typecheck
npm test
npm run build
```
