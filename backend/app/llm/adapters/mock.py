from __future__ import annotations

import hashlib
import json

from app.llm.contracts import (
    LlmRequest,
    LlmResult,
    ModelCapabilities,
    ProviderHealth,
)

MOCK_CAPABILITIES = ModelCapabilities(
    structured_output=True,
    seed=True,
    usage_reporting=True,
    local_execution=True,
)


class MockProviderAdapter:
    adapter_type = "MOCK"

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            status="READY",
            adapter_type=self.adapter_type,
            external_network_used=False,
            capabilities=MOCK_CAPABILITIES,
            message_code="MOCK_CONTRACT_READY",
        )

    def generate_structured(self, request: LlmRequest, model_id: str) -> LlmResult:
        output = {
            "status": "SHADOW_ONLY",
            "role": request.role,
            "input_hash": request.input_hash,
            "model_id": model_id,
        }
        canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return LlmResult(
            invocation_id=request.invocation_id,
            status="SUCCEEDED",
            actual_provider="CRESTA_MOCK",
            actual_model=model_id,
            output_json=output,
            raw_response_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            schema_validation="PASSED",
        )
