"""Startup composition from a maintainer-provisioned file, never request-supplied URLs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from agent_contracts.model_contracts import ModelProfile
from pydantic import BaseModel, ConfigDict

from agent_runtime.knowledge.embeddings import EMBEDDING_MODEL, E5EmbeddingModel
from agent_runtime.llm.adapters.egress import PinnedEgressTransport
from agent_runtime.llm.adapters.http_provider import HttpModelAdapter
from agent_runtime.llm.authority import (
    ModelAuthority,
    ModelBinding,
    PrincipalCheck,
    check_runtime_principal,
)
from agent_runtime.llm.budget_ledger import PostgresModelBudgetLedger
from agent_runtime.llm.embedding_adapter import E5EmbeddingAdapter
from agent_runtime.llm.gateway import ModelGateway
from agent_runtime.llm.profile import TokenProfiler
from agent_runtime.llm.secrets import ScopedSecretResolver
from agent_runtime.settings import RuntimeSettings


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_config_id: UUID
    profile: ModelProfile
    adapter: Literal["openai-chat", "e5"]
    endpoint_url: str | None = None
    utf8_bound_verified: bool = False


class GatewayConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[ModelRoute]


@asynccontextmanager
async def model_gateway_resources(
    settings: RuntimeSettings,
    *,
    principal_check: PrincipalCheck = check_runtime_principal,
) -> AsyncIterator[ModelGateway]:
    """Absent config is an empty fail-closed gateway; invalid config fails startup."""
    config = GatewayConfiguration(routes=[])
    if settings.model_gateway_config_path:
        config = GatewayConfiguration.model_validate_json(
            Path(settings.model_gateway_config_path).read_bytes()
        )
    if len({r.model_config_id for r in config.routes}) != len(config.routes):
        raise ValueError("Duplicate model route")
    allow_local = settings.model_gateway_allow_local_endpoints
    if allow_local and settings.environment not in {"development", "test"}:
        raise ValueError("Local model endpoints require development/test environment")
    endpoints: list[str] = []
    for route in config.routes:
        if route.adapter == "openai-chat":
            if route.endpoint_url is None:
                raise ValueError("HTTP model route requires an endpoint")
            if route.profile.capabilities.supports_embeddings:
                raise ValueError("HTTP embedding adapter is not configured")
            endpoints.append(route.endpoint_url)
        elif (
            route.endpoint_url is not None
            or route.profile.model_id != EMBEDDING_MODEL
            or route.profile.provider != "local"
        ):
            raise ValueError("E5 route must use the pinned local model")
    client = httpx.AsyncClient(
        transport=PinnedEgressTransport(endpoints, allow_local=allow_local),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(settings.model_gateway_timeout_seconds, connect=10),
    )
    secrets = ScopedSecretResolver()
    embedding: E5EmbeddingAdapter | None = None
    try:
        if any(r.adapter == "e5" for r in config.routes):
            if not settings.e5_model_snapshot:
                raise ValueError("E5_MODEL_SNAPSHOT must be provisioned offline")
            model = await asyncio.to_thread(E5EmbeddingModel, Path(settings.e5_model_snapshot))
            embedding = E5EmbeddingAdapter(model, validate_normalization=True)
        http_adapter = HttpModelAdapter(client, allow_local_endpoints=allow_local)
        ready: set[str] = set()
        for route in config.routes:
            pricing = route.profile.pricing
            if pricing is None:
                continue
            if route.adapter == "openai-chat" and route.utf8_bound_verified:
                ready.add("generation")
                if route.profile.capabilities.supports_stream:
                    ready.add("streaming")
            elif (route.adapter == "e5" and embedding is not None
                  and route.profile.capabilities.supports_embeddings
                  and pricing.cost_per_prompt_token_usd == 0
                  and pricing.cost_per_completion_token_usd == 0):
                ready.add("embeddings")
        yield ModelGateway(
            budget_ledger=PostgresModelBudgetLedger(),
            secret_resolver=secrets,
            token_profiler=TokenProfiler(
                validated_utf8_models=frozenset(
                    (r.profile.provider, r.profile.model_id)
                    for r in config.routes
                    if r.utf8_bound_verified
                )
            ),
            generation_adapter=http_adapter if endpoints else None,
            streaming_adapter=http_adapter if endpoints else None,
            embedding_adapter=embedding,
            authority=ModelAuthority(
                {r.model_config_id: ModelBinding(r.profile, r.endpoint_url) for r in config.routes},
                principal_check=principal_check,
            ),
            timeout_seconds=settings.model_gateway_timeout_seconds,
            ready_capabilities=frozenset(ready),
        )
    finally:
        try:
            if embedding is not None:
                await embedding.aclose()
        finally:
            await client.aclose()
            secrets.close()
