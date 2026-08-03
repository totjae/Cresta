from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.orders import router as orders_router
from app.api.positions import router as positions_router
from app.api.quotes import router as quotes_router
from app.api.settings import router as settings_router
from app.api.system import router as system_router
from app.auth.service import AuthenticationError, CsrfError, ReauthProofError
from app.broker.mock_order_test import MockOrderTestError
from app.errors import ResourceNotFoundError
from app.execution_policy import ExecutionPolicyError
from app.ids import uuid7

logger = logging.getLogger("cresta.api")


def create_app() -> FastAPI:
    application = FastAPI(title="Cresta API", version="0.1.0")

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        inbound_id = request.headers.get("X-Request-Id")
        try:
            request.state.request_id = str(uuid.UUID(inbound_id)) if inbound_id else uuid7()
        except (ValueError, AttributeError):
            request.state.request_id = uuid7()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(AuthenticationError)
    async def auth_error(request: Request, _: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "AUTHENTICATION_FAILED",
                    "message": "인증 정보를 확인할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(CsrfError)
    async def csrf_error(request: Request, _: CsrfError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "CSRF_VALIDATION_FAILED",
                    "message": "요청을 확인할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(ReauthProofError)
    async def reauth_proof_error(request: Request, _: ReauthProofError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "REAUTH_PROOF_INVALID",
                    "message": "최근 TOTP 재인증을 확인할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(MockOrderTestError)
    async def mock_order_test_error(request: Request, exc: MockOrderTestError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "키움 모의주문 연결 시험을 실행할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(ExecutionPolicyError)
    async def execution_policy_error(
        request: Request, exc: ExecutionPolicyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "실행 권한 설정을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "요청 형식을 확인할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(ResourceNotFoundError)
    async def resource_not_found(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error correlation_id=%s", request.state.request_id, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "요청을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(orders_router, prefix="/api/v1")
    application.include_router(positions_router, prefix="/api/v1")
    application.include_router(quotes_router, prefix="/api/v1")
    application.include_router(settings_router, prefix="/api/v1")
    application.include_router(system_router, prefix="/api/v1")
    return application


app = create_app()
