"""Contract tests for Model Gateway generation, embedding, and streaming DTOs (G5-01, G5-02)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest
from pydantic import ValidationError

from agent_contracts.model_contracts import (
    ContextLimits,
    ModelCapabilities,
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelGenerationRequest,
    ModelGenerationResponse,
    ModelMessage,
    ModelPricing,
    ModelProfile,
    ModelResponseMessage,
    ModelStreamingDelta,
    ModelToolCall,
    ModelToolDefinition,
    ModelToolFunction,
    ModelUsage,
    TokenMeasurement,
    ToolCallDelta,
)
from agent_runtime.llm.errors import ModelContextTooLargeError
from agent_runtime.llm.profile import TokenProfiler

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "jsonschema"


def _load_schema(filename: str) -> dict:
    with open(SCHEMAS_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


class TestModelProfileContract:
    def test_valid_profile_passes_schema_and_pydantic(self) -> None:
        schema = _load_schema("model-profile.v1.schema.json")
        payload = {
            "schemaVersion": "1.0",
            "provider": "anthropic",
            "modelId": "claude-3-5-sonnet",
            "capabilities": {
                "supportsTools": True,
                "supportsJsonSchema": True,
                "supportsStream": True,
                "supportsEmbeddings": False,
            },
            "contextLimits": {
                "contextWindow": 200000,
                "maxOutputTokens": 8192,
                "outputReserveMargin": 1000,
                "maxInputTokens": 190000,
            },
            "pricing": {
                "pricingVersion": "2026-11",
                "costPerPromptTokenUsd": "0.00000300",
                "costPerCompletionTokenUsd": "0.00001500",
            },
        }
        jsonschema.validate(payload, schema)
        profile = ModelProfile.model_validate(payload)
        assert profile.provider == "anthropic"
        assert profile.context_limits.context_window == 200000
        assert profile.pricing is not None
        assert profile.pricing.cost_per_prompt_token_usd == Decimal("0.00000300")

    def test_allowance_calculation_mc_core_formula(self) -> None:
        profile = ModelProfile(
            provider="openai",
            model_id="gpt-4o",
            capabilities=ModelCapabilities(
                supports_tools=True,
                supports_json_schema=True,
                supports_stream=True,
                supports_embeddings=False,
            ),
            context_limits=ContextLimits(
                context_window=128000,
                max_output_tokens=4096,
                output_reserve_margin=1000,
                max_input_tokens=120000,
            ),
        )
        # A = min(W - O - S, L) = min(128000 - 4096 - 1000, 120000) = min(122904, 120000) = 120000
        assert profile.calculate_allowance() == 120000
        # Custom reserve O=8000, S=2000 -> 128000 - 8000 - 2000 = 118000 < 120000 -> 118000
        assert profile.calculate_allowance(output_reserve=8000, safety_margin=2000) == 118000

    def test_context_admission_evaluation(self) -> None:
        profiler = TokenProfiler()
        profile = ModelProfile(
            provider="openai",
            model_id="gpt-4o",
            capabilities=ModelCapabilities(
                supports_tools=True,
                supports_json_schema=True,
                supports_stream=True,
                supports_embeddings=False,
            ),
            context_limits=ContextLimits(
                context_window=1000,
                max_output_tokens=200,
                output_reserve_margin=50,
            ),
        )
        # Allowance = 1000 - 200 - 50 = 750
        assert profiler.evaluate_admission(profile, required_input_tokens=500) == 750
        with pytest.raises(ModelContextTooLargeError) as exc_info:
            profiler.evaluate_admission(profile, required_input_tokens=800)
        assert "exceeds context allowance" in str(exc_info.value)

    def test_non_positive_allowance_raises_error(self) -> None:
        profiler = TokenProfiler()
        profile = ModelProfile(
            provider="test",
            model_id="small-model",
            capabilities=ModelCapabilities(
                supports_tools=False,
                supports_json_schema=False,
                supports_stream=False,
                supports_embeddings=False,
            ),
            context_limits=ContextLimits(
                context_window=200,
                max_output_tokens=150,
                output_reserve_margin=100,
            ),
        )
        # 200 - 150 - 100 = -50
        with pytest.raises(ModelContextTooLargeError) as exc_info:
            profiler.evaluate_admission(profile, required_input_tokens=10)
        assert "non-positive input allowance" in str(exc_info.value)


class TestModelGenerationContracts:
    def test_valid_generation_request_parity(self) -> None:
        schema = _load_schema("model-generation-request.v1.schema.json")
        req_id = str(uuid4())
        tenant_id = str(uuid4())
        workspace_id = str(uuid4())
        cfg_id = str(uuid4())
        payload = {
            "requestId": req_id,
            "tenantId": tenant_id,
            "workspaceId": workspace_id,
            "modelConfigId": cfg_id,
            "requestedProvider": "anthropic",
            "requestedModel": "claude-3-5-sonnet",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello!"},
            ],
            "settings": {
                "temperature": 0.7,
                "top_p": 0.95,
                "max_output_tokens": 1024,
            },
        }
        jsonschema.validate(payload, schema)
        req = ModelGenerationRequest.model_validate(payload)
        assert str(req.request_id) == req_id
        assert req.requested_model == "claude-3-5-sonnet"
        assert len(req.messages) == 2

    def test_generation_response_requested_vs_returned_model_distinction(self) -> None:
        schema = _load_schema("model-generation-response.v1.schema.json")
        req_id = str(uuid4())
        payload = {
            "requestId": req_id,
            "requestedModel": "claude-3-5-sonnet",
            "returnedModel": "claude-3-5-sonnet-20241022",
            "finishReason": "stop",
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you today?",
                "toolCalls": None,
                "refusal": None,
            },
            "usage": {
                "inputTokens": 25,
                "outputTokens": 10,
                "usageSource": "provider",
            },
            "providerRequestId": "msg_01AbcDef",
        }
        jsonschema.validate(payload, schema)
        res = ModelGenerationResponse.model_validate(payload)
        assert res.requested_model == "claude-3-5-sonnet"
        assert res.returned_model == "claude-3-5-sonnet-20241022"
        assert res.usage.usage_source == "provider"

    def test_generation_response_missing_usage_source_fails(self) -> None:
        payload = {
            "requestId": str(uuid4()),
            "requestedModel": "claude-3-5-sonnet",
            "finishReason": "stop",
            "message": {"role": "assistant", "content": "Hi"},
            "usage": {"inputTokens": 10, "outputTokens": 5},  # missing usageSource
        }
        with pytest.raises(ValidationError):
            ModelGenerationResponse.model_validate(payload)


class TestModelEmbeddingContracts:
    def test_valid_embedding_request_parity(self) -> None:
        schema = _load_schema("model-embedding-request.v1.schema.json")
        payload = {
            "requestId": str(uuid4()),
            "tenantId": str(uuid4()),
            "workspaceId": str(uuid4()),
            "modelId": "intfloat/multilingual-e5-small",
            "inputs": ["query: where is Hanoi?", "query: what is capital of Vietnam?"],
            "purpose": "query",
        }
        jsonschema.validate(payload, schema)
        req = ModelEmbeddingRequest.model_validate(payload)
        assert len(req.inputs) == 2
        assert req.purpose == "query"

    def test_embedding_request_rejects_empty_inputs_or_batch_over_32(self) -> None:
        req_id = uuid4()
        t_id = uuid4()
        w_id = uuid4()
        # Empty inputs
        with pytest.raises(ValidationError):
            ModelEmbeddingRequest(
                request_id=req_id,
                tenant_id=t_id,
                workspace_id=w_id,
                model_id="e5",
                inputs=[],
                purpose="query",
            )
        # > 32 inputs
        with pytest.raises(ValidationError):
            ModelEmbeddingRequest(
                request_id=req_id,
                tenant_id=t_id,
                workspace_id=w_id,
                model_id="e5",
                inputs=[f"text {i}" for i in range(33)],
                purpose="query",
            )

    def test_valid_embedding_response_validates_finite_dimensions(self) -> None:
        schema = _load_schema("model-embedding-response.v1.schema.json")
        payload = {
            "requestId": str(uuid4()),
            "modelId": "intfloat/multilingual-e5-small",
            "dimensions": 384,
            "count": 2,
            "embeddings": [
                [0.1] * 384,
                [-0.05] * 384,
            ],
            "usage": {
                "inputTokens": None,
                "usageSource": "unknown",
            },
        }
        jsonschema.validate(payload, schema)
        res = ModelEmbeddingResponse.model_validate(payload)
        assert res.dimensions == 384
        assert res.count == 2
        assert len(res.embeddings) == 2

    def test_embedding_response_rejects_non_finite_values(self) -> None:
        bad_vector = [0.1] * 383 + [float("nan")]
        with pytest.raises(ValidationError) as exc_info:
            ModelEmbeddingResponse(
                request_id=uuid4(),
                model_id="e5",
                dimensions=384,
                count=1,
                embeddings=[bad_vector],
                usage=ModelUsage(usage_source="unknown"),
            )
        assert "Non-finite value" in str(exc_info.value)


class TestModelStreamingDeltaContracts:
    def test_valid_streaming_delta(self) -> None:
        schema = _load_schema("model-streaming-delta.v1.schema.json")
        payload = {
            "requestId": str(uuid4()),
            "sequence": 1,
            "deltaType": "content",
            "content": "Hello world",
        }
        jsonschema.validate(payload, schema)
        delta = ModelStreamingDelta.model_validate(payload)
        assert delta.sequence == 1
        assert delta.content == "Hello world"
