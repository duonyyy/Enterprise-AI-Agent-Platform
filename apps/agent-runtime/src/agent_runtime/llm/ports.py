"""Internal typed Protocol ports for Model Gateway orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

from agent_contracts.model_contracts import (
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelGenerationRequest,
    ModelGenerationResponse,
    ModelProfile,
    ModelStreamingDelta,
    TokenMeasurement,
)


class GenerationPort(Protocol):
    """Port for executing text/structured model generation."""

    async def generate(self, request: ModelGenerationRequest) -> ModelGenerationResponse:
        """Execute model generation under admission guard and return normalized response."""
        ...


class StreamingPort(Protocol):
    """Port for streaming generation tokens and tool deltas."""

    def stream(self, request: ModelGenerationRequest) -> AsyncIterator[ModelStreamingDelta]:
        """Stream chunks from the model provider."""
        ...


class EmbeddingPort(Protocol):
    """Port for generating dense vector embeddings."""

    async def embed(self, request: ModelEmbeddingRequest) -> ModelEmbeddingResponse:
        """Generate normalized vector embeddings with finite float validation."""
        ...


class TokenProfilePort(Protocol):
    """Port for measuring tokens and checking context allowance against verified profile."""

    def measure(
        self,
        profile: ModelProfile,
        text: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> TokenMeasurement:
        """Measure token count with explicit accuracy basis."""
        ...


class SecretResolverPort(Protocol):
    """Port for resolving scoped credentials from trusted server-side state."""

    def resolve(
        self,
        service: str,
        tenant_id: UUID,
        workspace_id: UUID,
        secret_ref: str,
    ) -> str:
        """Resolve a secret reference within verified tenant and workspace boundaries."""
        ...
