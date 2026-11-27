"""Integration tests for ModelGateway pipeline against real PostgreSQL (G5-09, G5-11, G5-12, G5-15, G5-18, G5-21)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import redis
from agent_contracts.model_contracts import (
    ContextLimits,
    ModelCapabilities,
    ModelGenerationRequest,
    ModelMessage,
    ModelPricing,
    ModelProfile,
    ModelStreamingDelta,
)
from agent_runtime.auth import (
    RedisParentSessionVerifier,
    RuntimePrincipal,
    revalidate_runtime_authorization,
)
from agent_runtime.llm.adapters.http_provider import HttpModelAdapter
from agent_runtime.llm.authority import ModelAuthority, ModelBinding
from agent_runtime.llm.budget_ledger import PostgresModelBudgetLedger
from agent_runtime.llm.errors import (
    ModelBudgetExceededError,
    ModelContextTooLargeError,
    ModelTimeoutError,
    SecretNotFoundError,
)
from agent_runtime.llm.gateway import ModelGateway
from agent_runtime.llm.profile import TokenProfiler
from agent_runtime.llm.secrets import ScopedSecretResolver
from agent_runtime.settings import RuntimeSettings
from platform_common.rls import set_rls_context
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

_REDIS_URL = ""


@pytest.fixture(scope="module")
def gateway_redis():
    global _REDIS_URL
    name = f"gw05c-redis-{uuid4().hex}"
    result = subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", name, "-p", "127.0.0.1::6379", "redis:7-alpine"],
        capture_output=True,
        text=True,
        check=True,
    )
    container_id = result.stdout.strip()
    try:
        port = (
            subprocess.run(
                ["docker", "port", container_id, "6379"], capture_output=True, text=True, check=True
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        _REDIS_URL = f"redis://127.0.0.1:{port}/0"
        client = redis.Redis.from_url(_REDIS_URL)
        for _ in range(100):
            try:
                if client.ping():
                    break
            except redis.RedisError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Owned Redis fixture did not start")
        yield client
        client.close()
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=True)
        _REDIS_URL = ""


@pytest.fixture(autouse=True)
def real_parent_sessions(gateway_redis, monkeypatch):
    verifier = RedisParentSessionVerifier(RuntimeSettings(redis_url=_REDIS_URL))
    monkeypatch.setattr("agent_runtime.auth.get_parent_session_verifier", lambda *_: verifier)
    yield
    verifier.close()


def _principal(scope):
    return RuntimePrincipal(
        subject=f"caller-{scope['caller_id'].hex[:12]}",
        user_id=str(scope["caller_id"]),
        tenant_id=str(scope["tenant_id"]),
        workspace_id=str(scope["workspace_id"]),
        roles=("developer",),
        scopes=frozenset({"run:start"}),
        token_scopes=frozenset({"run:start"}),
        session_id=f"gw-{scope['caller_id'].hex}",
    )


def _authority(scope, factory, profile=None):
    @asynccontextmanager
    async def scoped_pg(tenant, workspace, user):
        async with factory() as session, session.begin():
            await set_rls_context(session, tenant_id=tenant, workspace_id=workspace, user_id=user)
            yield session

    async def check(principal):
        return await revalidate_runtime_authorization(
            principal,
            required_scope="run:start",
            session_factory=scoped_pg,
            settings=RuntimeSettings(redis_url=_REDIS_URL),
        )

    return ModelAuthority(
        {scope["model_id"]: ModelBinding(profile or _profile(), "https://api.testprovider.com/v1")},
        principal_check=check,
    )


@asynccontextmanager
async def _pipeline(database, handler, *, ledger=None, timeout=60):
    scope = _seed_scope(database["owner_url"])
    engine = create_async_engine(_async_url(database["app_url"]), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
        gateway = ModelGateway(
            budget_ledger=ledger or PostgresModelBudgetLedger(),
            secret_resolver=ScopedSecretResolver(),
            token_profiler=TokenProfiler(
                validated_utf8_models=frozenset({("test-provider", "test-model")})
            ),
            generation_adapter=adapter,
            streaming_adapter=adapter,
            authority=_authority(scope, factory),
            timeout_seconds=timeout,
        )
        try:
            yield scope, gateway, factory
        finally:
            await engine.dispose()


@asynccontextmanager
async def _raises(expected):
    with pytest.raises(expected):
        yield


def _response():
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"content": "test answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )


def _state(database, scope):
    with psycopg.connect(database["owner_url"]) as conn:
        return conn.execute(
            """SELECT r.status, b.reserved_usd, b.spent_usd,
            (SELECT count(*) FROM ops.model_usage u WHERE u.reservation_id=r.id)
            FROM ops.model_reservations r JOIN ops.workspace_budgets b ON b.id=r.budget_id
            WHERE r.tenant_id=%s AND r.workspace_id=%s""",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchall()


def _async_url(url: str) -> str:
    parts = psycopg.conninfo.conninfo_to_dict(url)
    return (
        f"postgresql+asyncpg://{parts['user']}:{parts.get('password', '')}"
        f"@{parts['host']}:{parts['port']}/{parts['dbname']}"
    )


def _profile(
    *,
    context_window: int = 16_384,
    max_output: int = 1_000,
    cost_prompt: Decimal = Decimal("0.00001000"),
    cost_comp: Decimal = Decimal("0.00002000"),
    supports_stream: bool = True,
    supports_tools: bool = True,
) -> ModelProfile:
    return ModelProfile(
        provider="test-provider",
        model_id="test-model",
        capabilities=ModelCapabilities(
            supports_tools=supports_tools,
            supports_json_schema=True,
            supports_stream=supports_stream,
            supports_embeddings=False,
        ),
        context_limits=ContextLimits(
            context_window=context_window,
            max_input_tokens=context_window - max_output,
            max_output_tokens=max_output,
        ),
        pricing=ModelPricing(
            pricing_version="test-v1",
            cost_per_prompt_token_usd=cost_prompt,
            cost_per_completion_token_usd=cost_comp,
        ),
    )


def _seed_scope(
    owner_url: str,
    budget_limit_usd: Decimal = Decimal("1.00000000"),
    profile: ModelProfile | None = None,
    effective_settings: dict | None = None,
) -> dict[str, UUID]:
    tenant_id, workspace_id, model_id, agent_id, version_id, run_id, caller_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    from agent_contracts.manifest import build_agent_manifest, compute_bundle_sha256

    selected = profile or _profile()
    spec = {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "gw-agent"},
        "mode": "react",
        "modelRef": selected.model_id,
        "persona": {"instructions": "Synthetic Gateway integration test"},
        "limits": {"maxSteps": 1},
    }
    manifest = build_agent_manifest(
        agent_id=agent_id,
        slug="gw-agent",
        spec=spec,
        model={
            "modelConfigId": str(model_id),
            "provider": selected.provider,
            "providerModelId": selected.model_id,
            "effectiveSettings": effective_settings or {},
        },
    )
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, 'Gateway Tenant')",
            (tenant_id, f"gw-{tenant_id.hex[:12]}"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, 'Gateway Workspace')",
            (tenant_id, workspace_id, f"gw-{workspace_id.hex[:12]}"),
        )
        conn.execute(
            "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) VALUES (%s, %s, %s, %s, %s)",
            (
                caller_id,
                "https://gw.test",
                f"caller-{caller_id.hex[:12]}",
                "GW Caller",
                f"{caller_id.hex}@example.test",
            ),
        )
        conn.execute(
            "INSERT INTO catalog.model_configs (tenant_id, workspace_id, id, name, provider, provider_model_id) VALUES (%s, %s, %s, 'gw-model', %s, %s)",
            (tenant_id, workspace_id, model_id, selected.provider, selected.model_id),
        )
        conn.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, 'gw-agent', 'GW Agent')",
            (tenant_id, workspace_id, agent_id),
        )
        conn.execute(
            """INSERT INTO catalog.agent_versions
            (tenant_id,workspace_id,id,agent_id,version,schema_version,frozen_spec,bundle_sha256,bundle_manifest,model_config_id)
            VALUES (%s,%s,%s,%s,1,'1.0',%s::jsonb,%s,%s::jsonb,%s)""",
            (
                tenant_id,
                workspace_id,
                version_id,
                agent_id,
                json.dumps(spec),
                compute_bundle_sha256(manifest),
                json.dumps(manifest),
                model_id,
            ),
        )
        conn.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (version_id,),
        )
        conn.execute(
            "INSERT INTO runtime.runs (tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key) VALUES (%s, %s, %s, %s, %s, %s)",
            (tenant_id, workspace_id, run_id, version_id, caller_id, f"gw-{run_id.hex}"),
        )
        conn.execute(
            "INSERT INTO ops.workspace_budgets (tenant_id, workspace_id, budget_limit_usd, period_start, period_end) VALUES (%s, %s, %s, now() - interval '1 minute', now() + interval '1 day')",
            (tenant_id, workspace_id, budget_limit_usd),
        )
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO iam.memberships (tenant_id,workspace_id,user_id,role) VALUES (%s,%s,%s,'developer')",
            (tenant_id, workspace_id, caller_id),
        )
    client = redis.Redis.from_url(_REDIS_URL)
    now = int(time.time())
    sid = f"gw-{caller_id.hex}"
    client.set(
        f"auth:session:{sid}",
        json.dumps(
            {
                "session_id": sid,
                "user_id": str(caller_id),
                "rotation_version": 1,
                "created_at": now,
                "idle_expires_at": now + 3600,
                "absolute_expires_at": now + 3600,
            }
        ),
        ex=3600,
    )
    client.close()
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "model_id": model_id,
        "run_id": run_id,
        "caller_id": caller_id,
    }


def _make_req(scope: dict[str, UUID], text_prompt: str = "Hello, agent!") -> ModelGenerationRequest:
    return ModelGenerationRequest(
        request_id=uuid4(),
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        model_config_id=scope["model_id"],
        requested_provider="test-provider",
        requested_model="test-model",
        messages=[ModelMessage(role="user", content=text_prompt)],
    )


def test_model_gateway_successful_generation_flow(migrated_database) -> None:
    scope = _seed_scope(migrated_database["owner_url"])
    engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    secret_resolver = ScopedSecretResolver(
        static_registry={
            (
                "model-gateway",
                scope["tenant_id"],
                scope["workspace_id"],
                "api-key",
            ): "sk-test-secret-123"
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-test-secret-123"
        # Simulate a price change after reservation but before provider response.
        assert prof.pricing is not None
        prof.pricing.cost_per_prompt_token_usd = Decimal("0.00000100")
        prof.pricing.cost_per_completion_token_usd = Decimal("0.00000100")
        res_body = {
            "id": "chatcmpl-mock-1",
            "model": "test-model-v1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "I am ready to assist."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }
        return httpx.Response(200, json=res_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
    gateway = ModelGateway(
        budget_ledger=PostgresModelBudgetLedger(),
        secret_resolver=secret_resolver,
        token_profiler=TokenProfiler(
            validated_utf8_models=frozenset({("test-provider", "test-model")})
        ),
        authority=_authority(scope, session_factory),
        generation_adapter=adapter,
    )

    with psycopg.connect(migrated_database["owner_url"]) as conn:
        conn.execute(
            "UPDATE catalog.model_configs SET secret_ref='api-key' WHERE id=%s",
            (scope["model_id"],),
        )
    req = _make_req(scope)
    prof = _profile()

    async def _run():
        async with session_factory() as session:
            await set_rls_context(
                session, tenant_id=str(scope["tenant_id"]), workspace_id=str(scope["workspace_id"])
            )
            return await gateway.generate(
                session,
                req,
                principal=_principal(scope),
                run_id=scope["run_id"],
            )

    res = asyncio.run(_run())

    assert res.request_id == req.request_id
    assert res.returned_model == "test-model-v1"
    assert res.message.content == "I am ready to assist."
    assert res.finish_reason == "stop"
    assert res.usage.input_tokens == 50
    assert res.usage.output_tokens == 20

    # Verify PostgreSQL durable state
    with psycopg.connect(migrated_database["owner_url"]) as conn:
        usage_row = conn.execute(
            """
            SELECT run_id, provider, model_id, input_tokens, output_tokens, cost_usd, usage_source
            FROM ops.model_usage
            WHERE tenant_id = %s AND workspace_id = %s AND run_id = %s
            """,
            (scope["tenant_id"], scope["workspace_id"], scope["run_id"]),
        ).fetchone()
        assert usage_row is not None
        assert usage_row[1] == "test-provider"
        assert usage_row[2] == "test-model"
        assert usage_row[3] == 50
        assert usage_row[4] == 20
        # 50 * 0.00001000 + 20 * 0.00002000 = 0.0005 + 0.0004 = 0.00090000
        assert usage_row[5] == Decimal("0.00090000")
        assert usage_row[6] == "provider"

        res_row = conn.execute(
            "SELECT status, reserved_cost_usd FROM ops.model_reservations WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert res_row[0] == "settled"

        budget_row = conn.execute(
            "SELECT spent_usd, reserved_usd FROM ops.workspace_budgets WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert budget_row[0] == Decimal("0.00090000")
        assert budget_row[1] == Decimal(0)


def test_model_gateway_context_admission_denied(migrated_database) -> None:
    scope = _seed_scope(migrated_database["owner_url"])
    engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
    gateway = ModelGateway(
        budget_ledger=PostgresModelBudgetLedger(),
        secret_resolver=ScopedSecretResolver(),
        token_profiler=TokenProfiler(
            validated_utf8_models=frozenset({("test-provider", "test-model")})
        ),
        authority=_authority(scope, session_factory, _profile(context_window=100, max_output=50)),
        generation_adapter=adapter,
    )

    req = _make_req(scope, text_prompt="word " * 500)

    async def _run():
        async with session_factory() as session:
            await set_rls_context(
                session, tenant_id=str(scope["tenant_id"]), workspace_id=str(scope["workspace_id"])
            )
            await gateway.generate(
                session, req, principal=_principal(scope), run_id=scope["run_id"]
            )

    with pytest.raises(ModelContextTooLargeError):
        asyncio.run(_run())

    # Ensure no reservation row in PostgreSQL
    with psycopg.connect(migrated_database["owner_url"]) as conn:
        count = conn.execute(
            "SELECT count(*) FROM ops.model_reservations WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()[0]
        assert count == 0


def test_model_gateway_budget_exceeded_denied(migrated_database) -> None:
    # Seed tiny budget of 0.00000001 USD
    scope = _seed_scope(migrated_database["owner_url"], budget_limit_usd=Decimal("0.00000001"))
    engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
    gateway = ModelGateway(
        budget_ledger=PostgresModelBudgetLedger(),
        secret_resolver=ScopedSecretResolver(),
        token_profiler=TokenProfiler(
            validated_utf8_models=frozenset({("test-provider", "test-model")})
        ),
        authority=_authority(scope, session_factory),
        generation_adapter=adapter,
    )

    req = _make_req(scope)

    async def _run():
        async with session_factory() as session:
            await set_rls_context(
                session, tenant_id=str(scope["tenant_id"]), workspace_id=str(scope["workspace_id"])
            )
            await gateway.generate(
                session, req, principal=_principal(scope), run_id=scope["run_id"]
            )

    with pytest.raises(ModelBudgetExceededError):
        asyncio.run(_run())


def test_model_gateway_secret_failure_releases_reservation(migrated_database) -> None:
    # The configured reference is authoritative; requests do not supply secrets.
    scope = _seed_scope(migrated_database["owner_url"])
    engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
    gateway = ModelGateway(
        budget_ledger=PostgresModelBudgetLedger(),
        secret_resolver=ScopedSecretResolver(),  # Empty resolver -> SecretNotFoundError
        token_profiler=TokenProfiler(
            validated_utf8_models=frozenset({("test-provider", "test-model")})
        ),
        authority=_authority(scope, session_factory),
        generation_adapter=adapter,
    )

    with psycopg.connect(migrated_database["owner_url"]) as conn:
        conn.execute(
            "UPDATE catalog.model_configs SET secret_ref='nonexistent_key' WHERE id=%s",
            (scope["model_id"],),
        )
    req = _make_req(scope)

    async def _run():
        async with session_factory() as session:
            await set_rls_context(
                session, tenant_id=str(scope["tenant_id"]), workspace_id=str(scope["workspace_id"])
            )
            await gateway.generate(
                session,
                req,
                principal=_principal(scope),
                run_id=scope["run_id"],
            )

    with pytest.raises(SecretNotFoundError):
        asyncio.run(_run())

    # Verify reservation released and budget reserved_usd restored to 0
    with psycopg.connect(migrated_database["owner_url"]) as conn:
        res_row = conn.execute(
            "SELECT status FROM ops.model_reservations WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert res_row[0] == "released"

        budget_row = conn.execute(
            "SELECT reserved_usd FROM ops.workspace_budgets WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert budget_row[0] == Decimal(0)


def test_model_gateway_timeout_marks_attempt_unknown(migrated_database) -> None:
    scope = _seed_scope(migrated_database["owner_url"])
    engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def timeout_handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Provider connection timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
    gateway = ModelGateway(
        budget_ledger=PostgresModelBudgetLedger(),
        secret_resolver=ScopedSecretResolver(),
        token_profiler=TokenProfiler(
            validated_utf8_models=frozenset({("test-provider", "test-model")})
        ),
        authority=_authority(scope, session_factory),
        generation_adapter=adapter,
    )

    req = _make_req(scope)

    async def _run():
        async with session_factory() as session:
            await set_rls_context(
                session, tenant_id=str(scope["tenant_id"]), workspace_id=str(scope["workspace_id"])
            )
            await gateway.generate(
                session, req, principal=_principal(scope), run_id=scope["run_id"]
            )

    with pytest.raises(ModelTimeoutError):
        asyncio.run(_run())

    # Verify attempt and reservation marked unknown, budget held
    with psycopg.connect(migrated_database["owner_url"]) as conn:
        attempt_row = conn.execute(
            "SELECT status FROM ops.model_attempts WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert attempt_row[0] == "unknown"

        res_row = conn.execute(
            "SELECT status, reserved_cost_usd FROM ops.model_reservations WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert res_row[0] == "unknown"

        budget_row = conn.execute(
            "SELECT spent_usd, reserved_usd FROM ops.workspace_budgets WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert budget_row[0] == Decimal(0)
        assert budget_row[1] > Decimal(0)  # Budget held for reconciler!


def test_model_gateway_streaming_flow_and_settlement(migrated_database) -> None:
    scope = _seed_scope(migrated_database["owner_url"])
    engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    sse_lines = [
        'data: {"choices": [{"delta": {"role": "assistant", "content": "Streamed "}}]}\n\n',
        'data: {"choices": [{"delta": {"content": "response."}}]}\n\n',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 15, "completion_tokens": 8}}\n\n',
        "data: [DONE]\n\n",
    ]

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="".join(sse_lines).encode("utf-8"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpModelAdapter(client, default_base_url="https://api.testprovider.com/v1")
    gateway = ModelGateway(
        budget_ledger=PostgresModelBudgetLedger(),
        secret_resolver=ScopedSecretResolver(),
        token_profiler=TokenProfiler(
            validated_utf8_models=frozenset({("test-provider", "test-model")})
        ),
        authority=_authority(scope, session_factory),
        generation_adapter=adapter,
        streaming_adapter=adapter,
    )

    req = _make_req(scope)

    async def _run() -> list[ModelStreamingDelta]:
        async with session_factory() as session:
            await set_rls_context(
                session, tenant_id=str(scope["tenant_id"]), workspace_id=str(scope["workspace_id"])
            )
            deltas = []
            async for delta in gateway.stream(
                session, req, principal=_principal(scope), run_id=scope["run_id"]
            ):
                deltas.append(delta)
            return deltas

    deltas = asyncio.run(_run())

    assert len(deltas) >= 3
    finish_deltas = [d for d in deltas if d.delta_type == "finish"]
    assert len(finish_deltas) == 1
    assert finish_deltas[0].finish_reason == "stop"

    with psycopg.connect(migrated_database["owner_url"]) as conn:
        usage_row = conn.execute(
            "SELECT input_tokens, output_tokens, usage_source, cost_usd FROM ops.model_usage WHERE tenant_id = %s AND workspace_id = %s",
            (scope["tenant_id"], scope["workspace_id"]),
        ).fetchone()
        assert usage_row is not None
        assert usage_row[0] == 15
        assert usage_row[1] == 8
        assert usage_row[2] == "provider"
        # 15 * 0.00001000 + 8 * 0.00002000 = 0.00015000 + 0.00016000 = 0.00031000
        assert usage_row[3] == Decimal("0.00031000")


@pytest.mark.parametrize(
    "denial", ["disabled", "identity", "scope", "revoked", "session", "role", "run", "settings"]
)
def test_authority_denies_before_provider(migrated_database, gateway_redis, denial):
    from agent_runtime.llm.errors import ModelPolicyDeniedError

    async def check():
        def handler(_):
            pytest.fail("Unauthorized provider call")

        async with _pipeline(migrated_database, handler) as (scope, gateway, factory):
            req = _make_req(scope)
            if denial == "scope":
                req.workspace_id = uuid4()
            elif denial == "identity":
                req.requested_model = "another-model"
            elif denial == "settings":
                req.settings = {"temperature": 0.9}
            elif denial == "session":
                gateway_redis.delete(f"auth:session:gw-{scope['caller_id'].hex}")
            else:
                statements = {
                    "disabled": (
                        "UPDATE catalog.model_configs SET status='disabled' WHERE id=%s",
                        scope["model_id"],
                    ),
                    "revoked": (
                        "UPDATE iam.memberships SET status='revoked' WHERE user_id=%s",
                        scope["caller_id"],
                    ),
                    "role": (
                        "UPDATE iam.memberships SET role='viewer' WHERE user_id=%s",
                        scope["caller_id"],
                    ),
                    "run": (
                        "UPDATE runtime.runs SET initiated_by=NULL WHERE id=%s",
                        scope["run_id"],
                    ),
                }
                sql, key = statements[denial]
                with psycopg.connect(migrated_database["owner_url"]) as conn:
                    conn.execute(sql, (key,))
            async with factory() as session, _raises(ModelPolicyDeniedError):
                await gateway.generate(
                    session, req, principal=_principal(scope), run_id=scope["run_id"]
                )
            assert _state(migrated_database, scope) == []

    asyncio.run(check())


@pytest.mark.parametrize(
    "failure", ["missing_usage", "type_error", "settlement", "malformed", "over_budget_usage"]
)
def test_postdispatch_failures_retain_budget_without_fake_usage(migrated_database, failure):
    from agent_runtime.llm.errors import ModelGatewayError
    from sqlalchemy.exc import SQLAlchemyError

    class FailingLedger(PostgresModelBudgetLedger):
        async def settle(self, *args, **kwargs):
            await super().settle(*args, **kwargs)
            raise SQLAlchemyError("Injected post-write settlement failure")

    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        if failure == "type_error":
            raise TypeError("Failure after request dispatch")
        if failure == "malformed":
            return httpx.Response(200, json={"choices": [None]})
        response = json.loads(_response().content)
        if failure == "missing_usage":
            response.pop("usage")
        if failure == "over_budget_usage":
            response["usage"]["completion_tokens"] = 100000
        return httpx.Response(200, json=response)

    async def check():
        async with _pipeline(
            migrated_database, handler, ledger=FailingLedger() if failure == "settlement" else None
        ) as (scope, gateway, factory):
            async with (
                factory() as session,
                _raises((ModelGatewayError, SQLAlchemyError, TypeError)),
            ):
                await gateway.generate(
                    session, _make_req(scope), principal=_principal(scope), run_id=scope["run_id"]
                )
            state = _state(migrated_database, scope)
            assert len(state) == 1 and state[0][0] == "unknown"
            assert state[0][1] > 0 and state[0][2] == 0 and state[0][3] == 0
        assert calls == 1

    asyncio.run(check())


@pytest.mark.parametrize("change", ["disabled", "revoked"])
def test_inflight_revocation_blocks_result_but_retains_actual_cost(migrated_database, change):
    from agent_runtime.llm.errors import ModelPolicyDeniedError

    async def check():
        scope = {}

        def handler(_):
            with psycopg.connect(migrated_database["owner_url"]) as conn:
                if change == "disabled":
                    conn.execute(
                        "UPDATE catalog.model_configs SET status='disabled' WHERE id=%s",
                        (scope["model_id"],),
                    )
                else:
                    conn.execute(
                        "UPDATE iam.memberships SET status='revoked' WHERE user_id=%s",
                        (scope["caller_id"],),
                    )
            return _response()

        async with _pipeline(migrated_database, handler) as (seed, gateway, factory):
            scope.update(seed)
            async with factory() as session, _raises(ModelPolicyDeniedError):
                await gateway.generate(
                    session, _make_req(scope), principal=_principal(scope), run_id=scope["run_id"]
                )
            state = _state(migrated_database, scope)
            assert (
                state[0][0] == "settled"
                and state[0][1] == 0
                and state[0][2] > 0
                and state[0][3] == 1
            )

    asyncio.run(check())


def test_idempotent_replay_never_dispatches_again(migrated_database):
    from agent_runtime.llm.errors import IdempotencyConflictError

    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return _response()

    async def check():
        async with _pipeline(migrated_database, handler) as (scope, gateway, factory):
            request = _make_req(scope)
            async with factory() as session:
                await gateway.generate(
                    session, request, principal=_principal(scope), run_id=scope["run_id"]
                )
            async with factory() as session, _raises(IdempotencyConflictError):
                await gateway.generate(
                    session, request, principal=_principal(scope), run_id=scope["run_id"]
                )
            assert _state(migrated_database, scope)[0][3] == 1
        assert calls == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "body",
    [
        b'data: {"choices":[{"delta":{"content":"secret partial"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
        b"data: not-json\n\n",
    ],
)
def test_bad_stream_releases_no_partial_output_and_preserves_unknown(migrated_database, body):
    from agent_runtime.llm.errors import ModelGatewayError

    async def check():
        async with _pipeline(migrated_database, lambda _: httpx.Response(200, content=body)) as (
            scope,
            gateway,
            factory,
        ):
            received = []
            async with factory() as session, _raises(ModelGatewayError):
                async for delta in gateway.stream(
                    session, _make_req(scope), principal=_principal(scope), run_id=scope["run_id"]
                ):
                    received.append(delta)
            assert received == []
            state = _state(migrated_database, scope)
            assert state[0][0] == "unknown" and state[0][1] > 0 and state[0][3] == 0

    asyncio.run(check())


@pytest.mark.parametrize("mode", ["generate", "stream", "deadline"])
def test_cancel_or_deadline_after_dispatch_is_unknown(migrated_database, mode):
    async def check():
        started = asyncio.Event()

        class WaitingBody(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                self.closed = True

        body = WaitingBody()
        async with _pipeline(
            migrated_database,
            lambda _: httpx.Response(200, stream=body),
            timeout=3.0 if mode == "deadline" else 10,
        ) as (scope, gateway, factory):

            async def call():
                async with factory() as session:
                    if mode == "stream":
                        return [
                            delta
                            async for delta in gateway.stream(
                                session,
                                _make_req(scope),
                                principal=_principal(scope),
                                run_id=scope["run_id"],
                            )
                        ]
                    return await gateway.generate(
                        session,
                        _make_req(scope),
                        principal=_principal(scope),
                        run_id=scope["run_id"],
                    )

            task = asyncio.create_task(call())
            await asyncio.wait_for(started.wait(), timeout=5)
            if mode != "deadline":
                task.cancel()
            with pytest.raises(ModelTimeoutError if mode == "deadline" else asyncio.CancelledError):
                await task
            assert body.closed
            state = _state(migrated_database, scope)
            assert state[0][0] == "unknown" and state[0][1] > 0 and state[0][3] == 0

    asyncio.run(check())


def test_e5_gateway_startup_composition_records_real_usage(migrated_database, tmp_path):
    """Actual weights + actual PG/RLS + Redis auth, via the production startup factory."""
    import math

    from agent_contracts.model_contracts import ModelEmbeddingRequest
    from agent_runtime.knowledge.embeddings import EMBEDDING_MODEL, EMBEDDING_REVISION
    from agent_runtime.llm.composition import model_gateway_resources

    from tests.integration.e5_snapshot import provisioned_e5_snapshot
    snapshot=provisioned_e5_snapshot()
    if snapshot is None or not (snapshot / "model.safetensors").is_file():
        pytest.skip("NOT_RUN: provision E5_MODEL_SNAPSHOT offline")
    import torch

    torch.set_num_threads(2)

    async def check():
        scope = _seed_scope(migrated_database["owner_url"])
        model_id, job_id = uuid4(), uuid4()
        with psycopg.connect(migrated_database["owner_url"]) as conn:
            conn.execute(
                "INSERT INTO catalog.model_configs (tenant_id,workspace_id,id,name,provider,provider_model_id) VALUES (%s,%s,%s,'e5','local',%s)",
                (scope["tenant_id"], scope["workspace_id"], model_id, EMBEDDING_MODEL),
            )
            conn.execute(
                "INSERT INTO runtime.jobs (tenant_id,workspace_id,id,run_id,job_type) VALUES (%s,%s,%s,%s,'run')",
                (scope["tenant_id"], scope["workspace_id"], job_id, scope["run_id"]),
            )
        profile = _profile(cost_prompt=Decimal(0), cost_comp=Decimal(0))
        profile.provider, profile.model_id = "local", EMBEDDING_MODEL
        profile.capabilities.supports_embeddings = True
        profile.pricing.pricing_version = f"local-no-provider-bill:{EMBEDDING_REVISION}"
        config_path = tmp_path / "gateway.json"
        config_path.write_text(
            json.dumps(
                {
                    "routes": [
                        {
                            "model_config_id": str(model_id),
                            "profile": profile.model_dump(mode="json"),
                            "adapter": "e5",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        # Use the very same live Redis/PG revalidator as the other Gateway tests.
        check_principal = _authority(scope, factory)._principal_check
        try:
            async with model_gateway_resources(
                RuntimeSettings(
                    environment="test",
                    model_gateway_config_path=str(config_path),
                    e5_model_snapshot=str(snapshot),
                ),
                principal_check=check_principal,
            ) as gateway:
                req = ModelEmbeddingRequest(
                    request_id=uuid4(),
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    model_config_id=model_id,
                    model_id=EMBEDDING_MODEL,
                    inputs=["Thủ tục xin nghỉ phép", "Hướng dẫn thay pin"],
                    purpose="passage",
                )
                async with factory() as session:
                    result = await gateway.embed(
                        session,
                        req,
                        principal=_principal(scope),
                        run_id=scope["run_id"],
                        job_id=job_id,
                    )
                assert result.count == 2 and result.dimensions == 384
                assert result.usage.input_tokens > 0 and result.usage.output_tokens == 0
                assert result.usage.usage_source == "estimated"
                assert all(
                    math.isclose(sum(v * v for v in row), 1, abs_tol=1e-3)
                    for row in result.embeddings
                )
            with psycopg.connect(migrated_database["owner_url"]) as conn:
                row = conn.execute(
                    "SELECT provider,model_id,input_tokens,output_tokens,cost_usd,pricing_version,job_id FROM ops.model_usage WHERE workspace_id=%s",
                    (scope["workspace_id"],),
                ).fetchone()
                assert row[:5] == (
                    "local",
                    EMBEDDING_MODEL,
                    result.usage.input_tokens,
                    0,
                    Decimal(0),
                )
                assert row[5] == profile.pricing.pricing_version and row[6] == job_id
                assert _state(migrated_database, scope)[0][0] == "settled"
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_buffered_stream_stops_on_live_revocation_during_wait(migrated_database):
    from agent_runtime.llm.errors import ModelPolicyDeniedError

    async def check():
        started = asyncio.Event()

        class WaitingBody(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                self.closed = True

        body = WaitingBody()
        async with _pipeline(migrated_database, lambda _: httpx.Response(200, stream=body)) as (
            scope,
            gateway,
            factory,
        ):
            received = []

            async def call():
                async with factory() as session:
                    async for delta in gateway.stream(
                        session,
                        _make_req(scope),
                        principal=_principal(scope),
                        run_id=scope["run_id"],
                    ):
                        received.append(delta)

            task = asyncio.create_task(call())
            await asyncio.wait_for(started.wait(), 5)
            with psycopg.connect(migrated_database["owner_url"]) as conn:
                conn.execute(
                    "UPDATE iam.memberships SET status='revoked' WHERE user_id=%s",
                    (scope["caller_id"],),
                )
            with pytest.raises(ModelPolicyDeniedError):
                await asyncio.wait_for(task, 5)
            assert received == [] and body.closed
            assert _state(migrated_database, scope)[0][0] == "unknown"

    asyncio.run(check())


def test_http_startup_composition_uses_real_tcp_and_scoped_secret(
    migrated_database, tmp_path, monkeypatch
):
    """Scripted provider on a real socket, not LLM quality/provider evidence."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from agent_runtime.llm.composition import model_gateway_resources

    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            observed.append((self.path, self.headers.get("Authorization"), payload))
            if payload["stream"]:
                body = (
                    b'data: {"choices":[{"delta":{"content":"test answer"}}]}\n\n'
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
                    b"data: [DONE]\n\n"
                )
            else:
                body = _response().content
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def check():
        scope = _seed_scope(migrated_database["owner_url"])
        with psycopg.connect(migrated_database["owner_url"]) as conn:
            conn.execute(
                "UPDATE catalog.model_configs SET secret_ref='tcp-test-key' WHERE id=%s",
                (scope["model_id"],),
            )
        env = f"MODEL_GATEWAY_SECRET_MODEL_GATEWAY_{str(scope['tenant_id']).replace('-', '_').upper()}_{str(scope['workspace_id']).replace('-', '_').upper()}_TCP_TEST_KEY"
        monkeypatch.setenv(env, "scoped-test-value")
        config = tmp_path / "gateway.json"
        config.write_text(
            json.dumps(
                {
                    "routes": [
                        {
                            "model_config_id": str(scope["model_id"]),
                            "adapter": "openai-chat",
                            "profile": _profile().model_dump(mode="json"),
                            "endpoint_url": f"http://127.0.0.1:{server.server_port}/v1",
                            "utf8_bound_verified": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with model_gateway_resources(
                RuntimeSettings(
                    environment="test",
                    model_gateway_config_path=str(config),
                    model_gateway_allow_local_endpoints=True,
                ),
                principal_check=_authority(scope, factory)._principal_check,
            ) as gateway:
                async with factory() as session:
                    response = await gateway.generate(
                        session,
                        _make_req(scope),
                        principal=_principal(scope),
                        run_id=scope["run_id"],
                    )
                    assert response.message.content == "test answer"
                async with factory() as session:
                    stream = gateway.stream(
                        session,
                        _make_req(scope),
                        principal=_principal(scope),
                        run_id=scope["run_id"],
                    )
                    # Closing a consumer after the first buffered item preserves
                    # the settled receipt: the provider has already completed.
                    first = await anext(stream)
                    assert first.delta_type == "content"
                    await stream.aclose()
            states = _state(migrated_database, scope)
            assert len(states) == 2 and all(row[0] == "settled" and row[3] == 1 for row in states)
            assert len(observed) == 2 and all(
                row[0] == "/v1/chat/completions" and row[1] == "Bearer scoped-test-value"
                for row in observed
            )
            assert all(row[2]["max_tokens"] == 1000 for row in observed)
            assert observed[1][2]["stream_options"] == {"include_usage": True}
        finally:
            await engine.dispose()

    try:
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
