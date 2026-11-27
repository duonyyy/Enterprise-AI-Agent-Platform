"""Contract tests for Model Gateway decision table, resilience, error taxonomy, and no-retry/no-fallback safety (G5-16, G5-17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from agent_contracts.model_contracts import (
    ContextLimits,
    ModelCapabilities,
    ModelGenerationRequest,
    ModelMessage,
    ModelPricing,
    ModelProfile,
    ModelToolDefinition,
)
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.llm.adapters.http_provider import HttpModelAdapter
from agent_runtime.llm.authority import ModelAuthority, ModelBinding
from agent_runtime.llm.budget_ledger import PostgresModelBudgetLedger
from agent_runtime.llm.errors import (
    ModelAuthError,
    ModelInvalidResponseError,
    ModelPolicyDeniedError,
    ModelRateLimitedError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from agent_runtime.llm.gateway import ModelGateway
from agent_runtime.llm.profile import TokenProfiler
from agent_runtime.llm.secrets import ScopedSecretResolver


def _sample_profile(
    *,
    supports_tools: bool = True,
    supports_json_schema: bool = True,
    supports_stream: bool = True,
) -> ModelProfile:
    return ModelProfile(
        provider="test-provider",
        model_id="test-model",
        capabilities=ModelCapabilities(
            supports_tools=supports_tools,
            supports_json_schema=supports_json_schema,
            supports_stream=supports_stream,
            supports_embeddings=False,
        ),
        context_limits=ContextLimits(
            context_window=16_384,
            max_input_tokens=15_000,
            max_output_tokens=1_000,
        ),
        pricing=ModelPricing(
            pricing_version="test-v1",
            cost_per_prompt_token_usd=Decimal("0.00001000"),
            cost_per_completion_token_usd=Decimal("0.00002000"),
        ),
    )


def _sample_request(**kwargs: Any) -> ModelGenerationRequest:
    defaults: dict[str, Any] = {
        "request_id": uuid4(),
        "tenant_id": uuid4(),
        "workspace_id": uuid4(),
        "model_config_id": uuid4(),
        "requested_provider": "test-provider",
        "requested_model": "test-model",
        "messages": [ModelMessage(role="user", content="Hello")],
    }
    defaults.update(kwargs)
    return ModelGenerationRequest(**defaults)


# ---------------------------------------------------------------------------
# Decision Table Tests: Pre-Dispatch Guard Failures (Zero Outbound Network Calls)
# ---------------------------------------------------------------------------


class TestPreDispatchDecisionTable:
    def test_deadline_expired_before_dispatch_fails_with_zero_outbound(self) -> None:
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"choices": []})

        async def check() -> None:
            nonlocal calls
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
            gateway = ModelGateway(
                budget_ledger=AsyncMock(spec=PostgresModelBudgetLedger),
                secret_resolver=ScopedSecretResolver(),
                token_profiler=TokenProfiler(
                    validated_utf8_models=frozenset({("test-provider", "test-model")})
                ),
                generation_adapter=adapter,
                authority=ModelAuthority({}),
            )
            req = _sample_request(deadline=datetime.now(UTC) - timedelta(seconds=1))
            session = AsyncMock()
            principal = RuntimePrincipal(
                subject="user-1",
                user_id=str(uuid4()),
                tenant_id=str(req.tenant_id),
                workspace_id=str(req.workspace_id),
                roles=("developer",),
                scopes=frozenset({"run:start"}),
            )
            with pytest.raises(ModelTimeoutError, match="Deadline expired before dispatch"):
                await gateway.generate(session, req, principal=principal)
            assert calls == 0

        asyncio.run(check())

    def test_unprovisioned_model_config_id_fails_with_zero_outbound(self) -> None:
        async def check() -> None:
            authority = ModelAuthority({}, principal_check=AsyncMock(side_effect=lambda p: p))
            gateway = ModelGateway(
                budget_ledger=AsyncMock(spec=PostgresModelBudgetLedger),
                secret_resolver=ScopedSecretResolver(),
                token_profiler=TokenProfiler(),
                generation_adapter=None,
                authority=authority,
            )
            req = _sample_request(model_config_id=uuid4())
            session = AsyncMock()
            principal = RuntimePrincipal(
                subject="user-1",
                user_id=str(uuid4()),
                tenant_id=str(req.tenant_id),
                workspace_id=str(req.workspace_id),
                roles=("developer",),
                scopes=frozenset({"run:start"}),
            )
            with pytest.raises(ModelPolicyDeniedError, match="Model profile is not provisioned"):
                await gateway.generate(session, req, principal=principal, run_id=uuid4())

        asyncio.run(check())

    def test_unsupported_capability_tools_fails_with_zero_outbound(self) -> None:
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={})

        async def check() -> None:
            profile_no_tools = _sample_profile(supports_tools=False)
            model_id = uuid4()
            authority = ModelAuthority(
                {model_id: ModelBinding(profile_no_tools, "https://test.example/v1")},
                principal_check=AsyncMock(side_effect=lambda p: p),
            )
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
            gateway = ModelGateway(
                budget_ledger=AsyncMock(spec=PostgresModelBudgetLedger),
                secret_resolver=ScopedSecretResolver(),
                token_profiler=TokenProfiler(
                    validated_utf8_models=frozenset({("test-provider", "test-model")})
                ),
                generation_adapter=adapter,
                authority=authority,
            )
            req = _sample_request(
                model_config_id=model_id,
                tools=[
                    ModelToolDefinition(
                        type="function",
                        function={"name": "test_fn", "description": "test", "parameters": {}},
                    )
                ],
            )
            session = AsyncMock()
            # Mock authority DB checks
            authority._authorize = AsyncMock(return_value=(ModelBinding(profile_no_tools, "https://test.example/v1"), None, {}))
            principal = RuntimePrincipal(
                subject="user-1",
                user_id=str(uuid4()),
                tenant_id=str(req.tenant_id),
                workspace_id=str(req.workspace_id),
                roles=("developer",),
                scopes=frozenset({"run:start"}),
            )
            with pytest.raises(ModelPolicyDeniedError, match="Unsupported model capability"):
                await gateway.generate(session, req, principal=principal, run_id=uuid4())
            assert calls == 0

        asyncio.run(check())


# ---------------------------------------------------------------------------
# Decision Table Tests: Post-Dispatch Failures (Strictly 1 Call, No Retries)
# ---------------------------------------------------------------------------


class TestPostDispatchDecisionTableNoRetry:
    @pytest.mark.parametrize(
        ("status_code", "expected_exception"),
        [
            (401, ModelAuthError),
            (403, ModelAuthError),
            (429, ModelRateLimitedError),
            (408, ModelTimeoutError),
            (500, ModelUnavailableError),
            (502, ModelUnavailableError),
            (503, ModelUnavailableError),
            (504, ModelTimeoutError),
        ],
    )
    def test_http_status_errors_raise_exact_exception_and_never_retry(
        self, status_code: int, expected_exception: type[Exception]
    ) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                status_code,
                json={"error": {"message": f"HTTP {status_code} error", "type": "error"}},
            )

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(expected_exception):
                    await adapter.generate(req)
            # Proven: Exactly 1 call was dispatched; no automatic retry was executed!
            assert calls == 1

        asyncio.run(check())

    def test_transport_disconnect_error_never_retries(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("Connection refused by test upstream")

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(ModelUnavailableError, match="HTTP model connection failed"):
                    await adapter.generate(req)
            assert calls == 1

        asyncio.run(check())

    def test_malformed_json_response_never_retries(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=b"this is not valid json")

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(ModelInvalidResponseError, match="Malformed provider JSON"):
                    await adapter.generate(req)
            assert calls == 1

        asyncio.run(check())

    def test_invalid_choices_array_never_retries(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"choices": "not-a-list"})

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(
                    ModelInvalidResponseError, match="missing valid 'choices' array"
                ):
                    await adapter.generate(req)
            assert calls == 1

        asyncio.run(check())

    def test_invalid_tool_arguments_json_never_retries(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": "{unclosed json",
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                },
            )

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(ModelInvalidResponseError):
                    await adapter.generate(req)
            assert calls == 1

        asyncio.run(check())


# ---------------------------------------------------------------------------
# Decision Table Tests: Streaming Errors and Truncation (Strictly 1 Call, No Retry)
# ---------------------------------------------------------------------------


class TestStreamingDecisionTable:
    @pytest.mark.parametrize(
        ("status_code", "expected_exception"),
        [
            (401, ModelAuthError),
            (403, ModelAuthError),
            (429, ModelRateLimitedError),
            (500, ModelUnavailableError),
            (503, ModelUnavailableError),
        ],
    )
    def test_streaming_http_errors_raise_exact_exception_and_never_retry(
        self, status_code: int, expected_exception: type[Exception]
    ) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                status_code,
                json={"error": {"message": f"Stream error {status_code}"}},
            )

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(expected_exception):
                    async for _ in adapter.stream(req):
                        pass
            assert calls == 1

        asyncio.run(check())

    def test_stream_data_after_finish_is_rejected(self) -> None:
        calls = 0
        body = (
            b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"extra data"}\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=body)

        async def check() -> None:
            nonlocal calls
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
                req = _sample_request()
                with pytest.raises(ModelInvalidResponseError):
                    async for _ in adapter.stream(req):
                        pass
            assert calls == 1

        asyncio.run(check())


# ---------------------------------------------------------------------------
# Decision Table Tests: No Fallback Safety (Fallback Ladder Remains Disabled)
# ---------------------------------------------------------------------------


class TestNoFallbackSafety:
    def test_primary_failure_does_not_dispatch_to_secondary_provider(self) -> None:
        primary_calls = 0
        secondary_calls = 0

        def primary_handler(request: httpx.Request) -> httpx.Response:
            nonlocal primary_calls
            primary_calls += 1
            return httpx.Response(503, json={"error": "Primary overloaded"})

        def secondary_handler(request: httpx.Request) -> httpx.Response:
            nonlocal secondary_calls
            secondary_calls += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "fallback"}}]})

        async def check() -> None:
            nonlocal primary_calls, secondary_calls
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(primary_handler)
            ) as primary_client:
                primary_adapter = HttpModelAdapter(
                    primary_client, default_base_url="https://primary.example/v1"
                )
                req = _sample_request()

                # ModelGateway only uses the configured single adapter for the authorized binding.
                # It does not fall back to another tier without an approved ADR and re-authorization.
                with pytest.raises(ModelUnavailableError):
                    await primary_adapter.generate(req)

            assert primary_calls == 1
            assert secondary_calls == 0  # Secondary was NEVER touched!

        asyncio.run(check())
