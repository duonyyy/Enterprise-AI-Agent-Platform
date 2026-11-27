"""Opt-in paid provider test: real Gateway factory, isolated PG/Redis, synthetic data only.

RUN_LIVE_MODEL_TESTS=1 is required. Never enabled by normal CI. See private 05-C handoff.
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from agent_contracts.model_contracts import ModelGenerationRequest, ModelMessage
from agent_runtime.llm.composition import ModelRoute, model_gateway_resources
from agent_runtime.settings import RuntimeSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_model_gateway_pipeline import (
    _async_url,
    _authority,
    _principal,
    _seed_scope,
    gateway_redis,  # noqa: F401 -- pytest fixture registration
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_MODEL_TESTS") != "1",
        reason="NOT_RUN: real generation requires an approved profile, credentials and spending limit",
    ),
]


def test_approved_live_provider(migrated_database, tmp_path, monkeypatch):
    profile_path = os.environ.get("MODEL_LIVE_PROFILE_PATH", "")
    raw_key = os.environ.get("MODEL_LIVE_API_KEY", "")
    approved_max = Decimal(os.environ.get("MODEL_LIVE_MAX_USD", "0"))
    mode = os.environ.get("MODEL_LIVE_MODE", "generate")
    if (
        not profile_path
        or not raw_key
        or not approved_max.is_finite()
        or not 0 < approved_max <= 10
    ):
        raise RuntimeError(
            "Provision a profile, a local API key and an approved positive test limit <=10 USD"
        )
    if mode not in {"generate", "stream", "both"}:
        raise RuntimeError("MODEL_LIVE_MODE must be generate, stream or both")
    route = ModelRoute.model_validate(
        {**json.loads(Path(profile_path).read_text(encoding="utf-8")), "model_config_id": uuid4()}
    )
    if (
        route.adapter != "openai-chat"
        or route.endpoint_url is None
        or not route.utf8_bound_verified
    ):
        raise RuntimeError("Live test requires a verified OpenAI-compatible chat profile")
    if route.profile.pricing is None or route.profile.context_limits.max_output_tokens > 64:
        raise RuntimeError("Provision pricing and cap test outputs at <=64 tokens")
    if mode in {"stream", "both"} and not route.profile.capabilities.supports_stream:
        raise RuntimeError("The approved model profile does not support streaming")

    async def check():
        scope = _seed_scope(
            migrated_database["owner_url"],
            approved_max,
            profile=route.profile,
            effective_settings={
                "max_output_tokens": route.profile.context_limits.max_output_tokens
            },
        )
        route.model_config_id = scope["model_id"]
        with psycopg.connect(migrated_database["owner_url"]) as conn:
            conn.execute(
                "UPDATE catalog.model_configs SET secret_ref='live-test-key' WHERE id=%s",
                (scope["model_id"],),
            )
        # Only the test maps an explicitly provisioned credential into this newly
        # owned fixture scope. Production resolver has no global-key fallback.
        key_name = f"MODEL_GATEWAY_SECRET_MODEL_GATEWAY_{str(scope['tenant_id']).replace('-', '_').upper()}_{str(scope['workspace_id']).replace('-', '_').upper()}_LIVE_TEST_KEY"
        monkeypatch.setenv(key_name, raw_key)
        config = tmp_path / "live-gateway.json"
        config.write_text(json.dumps({"routes": [route.model_dump(mode="json")]}), encoding="utf-8")
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        count = 0
        try:
            async with model_gateway_resources(
                RuntimeSettings(environment="test", model_gateway_config_path=str(config)),
                principal_check=_authority(scope, factory)._principal_check,
            ) as gateway:
                for operation in ["generate", "stream"] if mode == "both" else [mode]:
                    request = ModelGenerationRequest(
                        requestId=uuid4(),
                        tenantId=scope["tenant_id"],
                        workspaceId=scope["workspace_id"],
                        modelConfigId=scope["model_id"],
                        requestedProvider=route.profile.provider,
                        requestedModel=route.profile.model_id,
                        messages=[
                            ModelMessage(
                                role="user",
                                content="Say hello briefly. This is a synthetic integration check.",
                            )
                        ],
                    )
                    async with factory() as session:
                        if operation == "generate":
                            response = await gateway.generate(
                                session,
                                request,
                                principal=_principal(scope),
                                run_id=scope["run_id"],
                            )
                            assert response.request_id == request.request_id
                            assert response.usage.usage_source == "provider"
                        else:
                            deltas = [
                                d
                                async for d in gateway.stream(
                                    session,
                                    request,
                                    principal=_principal(scope),
                                    run_id=scope["run_id"],
                                )
                            ]
                            assert (
                                deltas[-1].delta_type == "finish"
                                and deltas[-1].usage.usage_source == "provider"
                            )
                    count += 1
            with psycopg.connect(migrated_database["owner_url"]) as conn:
                rows = conn.execute(
                    "SELECT status FROM ops.model_reservations WHERE workspace_id=%s",
                    (scope["workspace_id"],),
                ).fetchall()
                assert len(rows) == count and all(row[0] == "settled" for row in rows)
                usage = conn.execute(
                    "SELECT count(*),sum(cost_usd) FROM ops.model_usage WHERE workspace_id=%s",
                    (scope["workspace_id"],),
                ).fetchone()
                assert usage[0] == count and 0 <= usage[1] <= approved_max
        finally:
            await engine.dispose()

    asyncio.run(check())
