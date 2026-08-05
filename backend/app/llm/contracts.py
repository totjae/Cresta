from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCapabilities(ContractModel):
    structured_output: bool = False
    tool_calling: bool = False
    web_search: bool = False
    streaming: bool = False
    reasoning: bool = False
    seed: bool = False
    usage_reporting: bool = False
    local_execution: bool = False


class LlmRequest(ContractModel):
    schema_version: Literal["llm-request-v1"] = "llm-request-v1"
    invocation_id: str
    role: str
    model_profile_id: str
    prompt_version: str
    input_schema_version: str
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    messages: list[dict[str, Any]]
    output_json_schema: dict[str, Any]
    timeout_ms: int = Field(ge=1000, le=60000)
    max_output_tokens: int = Field(gt=0, le=32768)
    temperature: float = Field(ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    seed: int | None = None
    tool_policy: Literal["NONE", "ALLOWLIST"] = "NONE"
    allowed_tools: list[str] = Field(default_factory=list)


class LlmResult(ContractModel):
    schema_version: Literal["llm-result-v1"] = "llm-result-v1"
    invocation_id: str
    status: Literal[
        "SUCCEEDED",
        "REFUSED",
        "TIMED_OUT",
        "RATE_LIMITED",
        "PROVIDER_ERROR",
        "INVALID_OUTPUT",
        "AMBIGUOUS",
    ]
    actual_provider: str | None = None
    actual_model: str | None = None
    output_json: dict[str, Any] | None = None
    raw_response_hash: str | None = None
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_path: list[str] = Field(default_factory=list)
    schema_validation: Literal["PASSED", "FAILED", "NOT_RUN"] = "NOT_RUN"


class ProviderHealth(ContractModel):
    status: Literal["READY", "DEGRADED", "AUTH_FAILED", "DISABLED"]
    adapter_type: str
    external_network_used: bool
    capabilities: ModelCapabilities
    message_code: str


class LlmProviderAdapter(Protocol):
    adapter_type: str

    def healthcheck(self) -> ProviderHealth: ...

    def generate_structured(self, request: LlmRequest, model_id: str) -> LlmResult: ...
