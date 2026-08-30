from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.activation_gate import ActivationGateError
from app.agents.runtime import AgentRuntimeError
from app.api.activation import router as activation_router
from app.api.agent_runs import router as agent_runs_router
from app.api.approvals import router as approvals_router
from app.api.auth import router as auth_router
from app.api.decisions import router as decisions_router
from app.api.execution_stage import router as execution_stage_router
from app.api.llm import router as llm_router
from app.api.orders import router as orders_router
from app.api.positions import router as positions_router
from app.api.quotes import router as quotes_router
from app.api.risk import router as risk_router
from app.api.risk_settings import router as risk_settings_router
from app.api.settings import router as settings_router
from app.api.system import router as system_router
from app.api.venue_selections import router as venue_selections_router
from app.api.watchlist import router as watchlist_router
from app.approvals import ApprovalError
from app.auth.service import AuthenticationError, CsrfError, ReauthProofError
from app.broker.mock_order_test import MockOrderTestError
from app.calendar_overrides import CalendarOverrideError
from app.db import engine
from app.emergency_stop import EmergencyStopError
from app.errors import ResourceNotFoundError
from app.execution_policy import ExecutionPolicyError
from app.execution_stage import ExecutionStageError
from app.ids import uuid7
from app.llm.profiles import LlmProfileError
from app.llm.prompts import LlmPromptError
from app.mock_ai import MockDecisionError
from app.risk_policy import RiskPolicyError
from app.watchlist import WatchlistError

logger = logging.getLogger("cresta.api")
EXPECTED_MIGRATION_HEAD = "20260829_0044"


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

    @application.exception_handler(ActivationGateError)
    async def activation_gate_error(
        request: Request, exc: ActivationGateError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "v7 ENTRY Activation Gate 설정을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(ExecutionStageError)
    async def execution_stage_error(
        request: Request, exc: ExecutionStageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "실행 단계 설정을 안전하게 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": exc.code == "EXECUTION_STAGE_DB_RETRYABLE_FAILURE",
                }
            },
        )

    @application.exception_handler(RiskPolicyError)
    async def risk_policy_error(request: Request, exc: RiskPolicyError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "Guard 위험 설정을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(EmergencyStopError)
    async def emergency_stop_error(
        request: Request, exc: EmergencyStopError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "비상정지 요청을 안전하게 처리하지 못했습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(MockDecisionError)
    async def mock_decision_error(request: Request, exc: MockDecisionError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "Mock AI 판단을 생성할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(LlmProfileError)
    async def llm_profile_error(request: Request, exc: LlmProfileError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "LLM Provider 설정을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(LlmPromptError)
    async def llm_prompt_error(request: Request, exc: LlmPromptError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "LLM 프롬프트 설정을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(AgentRuntimeError)
    async def agent_runtime_error(request: Request, exc: AgentRuntimeError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "Agent Runtime 요청을 안전하게 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(WatchlistError)
    async def watchlist_error(request: Request, exc: WatchlistError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "감시 종목 요청을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(CalendarOverrideError)
    async def calendar_override_error(
        request: Request, exc: CalendarOverrideError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "거래 캘린더 운영 휴장 설정을 처리할 수 없습니다.",
                    "correlation_id": request.state.request_id,
                    "retryable": False,
                }
            },
        )

    @application.exception_handler(ApprovalError)
    async def approval_error(request: Request, exc: ApprovalError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": "승인 요청을 처리할 수 없습니다.",
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

    @application.get("/readyz", include_in_schema=False)
    def readiness() -> JSONResponse:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                current = connection.scalar(text("SELECT version_num FROM alembic_version"))
        except SQLAlchemyError:
            logger.warning("API readiness failed code=DATABASE_UNAVAILABLE_OR_UNMIGRATED")
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "code": "DATABASE_UNAVAILABLE_OR_UNMIGRATED"},
            )
        if current != EXPECTED_MIGRATION_HEAD:
            logger.warning("API readiness failed code=MIGRATION_HEAD_MISMATCH")
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "code": "MIGRATION_HEAD_MISMATCH"},
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "migration_head": EXPECTED_MIGRATION_HEAD},
        )

    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(activation_router, prefix="/api/v1")
    application.include_router(approvals_router, prefix="/api/v1")
    application.include_router(decisions_router, prefix="/api/v1")
    application.include_router(execution_stage_router, prefix="/api/v1")
    application.include_router(llm_router, prefix="/api/v1")
    application.include_router(agent_runs_router, prefix="/api/v1")
    application.include_router(orders_router, prefix="/api/v1")
    application.include_router(positions_router, prefix="/api/v1")
    application.include_router(quotes_router, prefix="/api/v1")
    application.include_router(risk_router, prefix="/api/v1")
    application.include_router(settings_router, prefix="/api/v1")
    application.include_router(risk_settings_router, prefix="/api/v1")
    application.include_router(system_router, prefix="/api/v1")
    application.include_router(venue_selections_router, prefix="/api/v1")
    application.include_router(watchlist_router, prefix="/api/v1")
    return application


app = create_app()
