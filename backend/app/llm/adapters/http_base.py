from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.llm.contracts import LlmRequest, LlmResult, ModelCapabilities, ProviderHealth


class ExternalHttpAdapter(ABC):
    adapter_type: str
    provider_name: str
    capabilities = ModelCapabilities(structured_output=True, usage_reporting=True)

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._client = client

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            status="READY",
            adapter_type=self.adapter_type,
            external_network_used=False,
            capabilities=self.capabilities,
            message_code="ADAPTER_CONTRACT_READY",
        )

    @abstractmethod
    def _request_parts(
        self, request: LlmRequest, model_id: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]: ...

    @abstractmethod
    def _parse_success(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, int | None, int | None]: ...

    def generate_structured(self, request: LlmRequest, model_id: str) -> LlmResult:
        url, headers, body = self._request_parts(request, model_id)
        started = time.monotonic()
        response_timeout_seconds = request.timeout_ms / 1000
        http_timeout = httpx.Timeout(
            response_timeout_seconds,
            connect=min(3.0, response_timeout_seconds),
        )
        try:
            if self._client is None:
                with httpx.Client(timeout=http_timeout) as client:
                    response = client.post(url, headers=headers, json=body)
            else:
                response = self._client.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=http_timeout,
                )
        except httpx.TimeoutException:
            return self._failure(request, "TIMED_OUT", started)
        except httpx.RequestError:
            return self._failure(request, "AMBIGUOUS", started)

        if (time.monotonic() - started) * 1000 > request.timeout_ms:
            return self._failure(request, "TIMED_OUT", started)

        provider_request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or response.headers.get("x-goog-request-id")
        )
        gateway_request_id = response.headers.get("x-vercel-id")
        if response.status_code == 429:
            return self._failure(
                request,
                "RATE_LIMITED",
                started,
                provider_request_id=provider_request_id,
                gateway_request_id=gateway_request_id,
            )
        if response.status_code >= 500:
            return self._failure(
                request,
                "PROVIDER_ERROR",
                started,
                provider_request_id=provider_request_id,
                gateway_request_id=gateway_request_id,
            )
        if response.status_code >= 400:
            return self._failure(
                request,
                "PROVIDER_ERROR",
                started,
                provider_request_id=provider_request_id,
                gateway_request_id=gateway_request_id,
            )
        try:
            payload = response.json()
            output, actual_model, input_tokens, output_tokens = self._parse_success(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._failure(
                request,
                "INVALID_OUTPUT",
                started,
                provider_request_id=provider_request_id,
                gateway_request_id=gateway_request_id,
                response_content=response.content,
            )
        return LlmResult(
            invocation_id=request.invocation_id,
            status="SUCCEEDED",
            actual_provider=self.provider_name,
            actual_model=actual_model or model_id,
            provider_request_id=provider_request_id,
            gateway_request_id=gateway_request_id,
            output_json=output,
            raw_response_hash=hashlib.sha256(response.content).hexdigest(),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            retry_count=0,
            schema_validation="PASSED",
        )

    def _failure(
        self,
        request: LlmRequest,
        status: str,
        started: float,
        *,
        provider_request_id: str | None = None,
        gateway_request_id: str | None = None,
        response_content: bytes | None = None,
    ) -> LlmResult:
        return LlmResult(
            invocation_id=request.invocation_id,
            status=status,
            actual_provider=self.provider_name,
            provider_request_id=provider_request_id,
            gateway_request_id=gateway_request_id,
            raw_response_hash=(
                hashlib.sha256(response_content).hexdigest() if response_content is not None else None
            ),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            retry_count=0,
            schema_validation="NOT_RUN",
        )


def parse_json_text(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise TypeError("provider output is not text")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("provider output is not an object")
    return parsed


def safe_model_id(model_id: str) -> str:
    if not model_id or any(item in model_id for item in ("..", "?", "#", "\\")):
        raise ValueError("unsafe provider model id")
    return model_id
