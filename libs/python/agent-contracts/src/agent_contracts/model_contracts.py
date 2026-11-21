"""Typed domain DTOs for Model Gateway generation, embedding, and streaming contracts."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    supports_tools: bool = Field(alias="supportsTools")
    supports_json_schema: bool = Field(alias="supportsJsonSchema")
    supports_stream: bool = Field(alias="supportsStream")
    supports_embeddings: bool = Field(alias="supportsEmbeddings")


class ContextLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    context_window: int = Field(alias="contextWindow", ge=1)
    max_output_tokens: int = Field(alias="maxOutputTokens", ge=1)
    output_reserve_margin: int = Field(default=0, alias="outputReserveMargin", ge=0)
    max_input_tokens: int | None = Field(default=None, alias="maxInputTokens", ge=1)


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pricing_version: str = Field(alias="pricingVersion", min_length=1)
    cost_per_prompt_token_usd: Decimal = Field(alias="costPerPromptTokenUsd", ge=0)
    cost_per_completion_token_usd: Decimal = Field(alias="costPerCompletionTokenUsd", ge=0)


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0"] = Field(default="1.0", alias="schemaVersion")
    provider: str = Field(min_length=1)
    model_id: str = Field(alias="modelId", min_length=1)
    capabilities: ModelCapabilities
    context_limits: ContextLimits = Field(alias="contextLimits")
    pricing: ModelPricing | None = None

    def calculate_allowance(
        self,
        output_reserve: int | None = None,
        safety_margin: int | None = None,
    ) -> int:
        """Calculate max allowable input tokens according to MC-CORE-001 budget formula.

        A = min(W - O - S, L)
        """
        w = self.context_limits.context_window
        o = (
            output_reserve
            if output_reserve is not None
            else self.context_limits.max_output_tokens
        )
        s = (
            safety_margin
            if safety_margin is not None
            else self.context_limits.output_reserve_margin
        )
        base = w - o - s
        if self.context_limits.max_input_tokens is not None:
            return min(base, self.context_limits.max_input_tokens)
        return base


TokenCountBasis = Literal["test_exact", "provider_exact", "validated_upper_bound", "unknown"]


class TokenMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    input_tokens: int = Field(alias="inputTokens", ge=0)
    basis: TokenCountBasis
    profile_revision: str = Field(alias="profileRevision", min_length=1)


UsageSource = Literal["provider", "estimated", "unknown"]


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    input_tokens: int | None = Field(default=None, alias="inputTokens", ge=0)
    output_tokens: int | None = Field(default=None, alias="outputTokens", ge=0)
    usage_source: UsageSource = Field(alias="usageSource")


class ModelToolFunction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    arguments: str


class ModelToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: ModelToolFunction


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    tool_calls: list[ModelToolCall] | None = Field(default=None, alias="toolCalls")

    @model_validator(mode="after")
    def check_content_or_tool_calls(self) -> ModelMessage:
        if self.role == "assistant" and not self.content and not self.tool_calls:
            raise ValueError("Assistant message must contain content or toolCalls")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("Tool message must have toolCallId")
        return self


class ModelToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["function"] = "function"
    function: dict[str, Any]


class ModelGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    tenant_id: UUID = Field(alias="tenantId")
    workspace_id: UUID = Field(alias="workspaceId")
    model_config_id: UUID = Field(alias="modelConfigId")
    requested_provider: str = Field(alias="requestedProvider", min_length=1)
    requested_model: str = Field(alias="requestedModel", min_length=1)
    messages: list[ModelMessage] = Field(min_length=1)
    tools: list[ModelToolDefinition] | None = None
    response_format: dict[str, Any] | None = Field(default=None, alias="responseFormat")
    settings: dict[str, Any] | None = None
    deadline: datetime | None = None


FinishReason = Literal["stop", "length", "tool_calls", "refusal", "error"]


class ModelResponseMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ModelToolCall] | None = Field(default=None, alias="toolCalls")
    refusal: str | None = None


class ModelGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    requested_model: str = Field(alias="requestedModel", min_length=1)
    returned_model: str | None = Field(default=None, alias="returnedModel")
    finish_reason: FinishReason = Field(alias="finishReason")
    message: ModelResponseMessage
    usage: ModelUsage
    provider_request_id: str | None = Field(default=None, alias="providerRequestId")


class ModelEmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    tenant_id: UUID = Field(alias="tenantId")
    workspace_id: UUID = Field(alias="workspaceId")
    model_config_id: UUID | None = Field(default=None, alias="modelConfigId")
    model_id: str = Field(alias="modelId", min_length=1)
    inputs: list[str] = Field(min_length=1, max_length=32)
    purpose: Literal["query", "passage"]

    @field_validator("inputs")
    @classmethod
    def validate_non_empty_strings(cls, v: list[str]) -> list[str]:
        for idx, item in enumerate(v):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"Input at index {idx} must be a non-empty string")
            if len(item.encode("utf-8")) > 32_768:
                raise ValueError(f"Input at index {idx} exceeds max text bytes (32768)")
        return v


class ModelEmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    model_id: str = Field(alias="modelId", min_length=1)
    dimensions: int = Field(ge=1)
    count: int = Field(ge=1, le=32)
    embeddings: list[list[float]] = Field(min_length=1, max_length=32)
    usage: ModelUsage

    @model_validator(mode="after")
    def validate_embeddings(self) -> ModelEmbeddingResponse:
        if len(self.embeddings) != self.count:
            raise ValueError(
                f"Embeddings count ({len(self.embeddings)}) does not match count field ({self.count})"
            )
        for idx, vec in enumerate(self.embeddings):
            if len(vec) != self.dimensions:
                raise ValueError(
                    f"Vector at index {idx} has dimension {len(vec)}, expected {self.dimensions}"
                )
            for v_idx, val in enumerate(vec):
                if not math.isfinite(val):
                    raise ValueError(
                        f"Non-finite value {val} at embedding index {idx}, dimension {v_idx}"
                    )
        return self


StreamingDeltaType = Literal["content", "tool_call", "finish", "error"]


class ToolCallDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    index: int = Field(ge=0)
    id: str | None = None
    name: str | None = None
    arguments_chunk: str | None = Field(default=None, alias="argumentsChunk")


class ModelStreamingDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    sequence: int = Field(ge=0)
    delta_type: StreamingDeltaType = Field(alias="deltaType")
    content: str | None = None
    tool_call_delta: ToolCallDelta | None = Field(default=None, alias="toolCallDelta")
    finish_reason: FinishReason | None = Field(default=None, alias="finishReason")
    usage: ModelUsage | None = None
    error_message: str | None = Field(default=None, alias="errorMessage")
