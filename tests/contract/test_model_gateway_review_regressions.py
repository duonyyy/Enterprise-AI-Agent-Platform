"""Regression checks for 05-C review findings."""

import asyncio
import threading
from uuid import uuid4

import httpx
import pytest
from agent_contracts.model_contracts import (
    ModelEmbeddingRequest,
    ModelGenerationRequest,
    ModelMessage,
)
from agent_runtime.llm.adapters.egress import PinnedEgressTransport, validate_endpoint
from agent_runtime.llm.adapters.http_provider import HttpModelAdapter
from agent_runtime.llm.embedding_adapter import E5EmbeddingAdapter
from agent_runtime.llm.errors import (
    ModelInvalidResponseError,
    ModelPolicyDeniedError,
    ModelTimeoutError,
)


def test_truncated_stream_is_not_success() -> None:
    async def check() -> None:
        req = ModelGenerationRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_config_id=uuid4(),
            requested_provider="test",
            requested_model="test",
            messages=[ModelMessage(role="user", content="test")],
        )
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
            received = []
            with pytest.raises(ModelInvalidResponseError):
                async for delta in adapter.stream(req):
                    received.append(delta)
            assert [d.delta_type for d in received] == ["content"]

    asyncio.run(check())


def test_stream_usage_after_finish_is_retained() -> None:
    async def check() -> None:
        req = ModelGenerationRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_config_id=uuid4(),
            requested_provider="test",
            requested_model="test",
            messages=[ModelMessage(role="user", content="test")],
        )
        body = (
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
            b"data: [DONE]\n\n"
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        ) as client:
            adapter = HttpModelAdapter(client, default_base_url="https://test.example/v1")
            deltas = [delta async for delta in adapter.stream(req)]
            assert len([delta for delta in deltas if delta.delta_type == "finish"]) == 1
            assert deltas[-1].usage is not None
            assert deltas[-1].usage.output_tokens == 2

    asyncio.run(check())


def test_cancelled_embedding_retains_permit_until_thread_finishes() -> None:
    class BlockingModel:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self.release = threading.Event()
            self.started = threading.Event()

        def count_tokens(self, _texts, *, purpose):
            return 1

        def embed_query(self, _text: str) -> list[float]:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started.set()
            self.release.wait(3)
            self.active -= 1
            return [1.0] + [0.0] * 383

    async def check() -> None:
        model = BlockingModel()
        adapter = E5EmbeddingAdapter(model, max_concurrency=1, max_queue=1)
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=["test"],
            purpose="query",
        )
        first = asyncio.create_task(adapter.embed(req))
        assert await asyncio.to_thread(model.started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(adapter.embed(req))
        await asyncio.sleep(0.1)
        assert model.peak == 1
        from agent_runtime.llm.errors import ModelUnavailableError

        with pytest.raises(ModelUnavailableError, match="queue capacity"):
            await adapter.embed(req)
        model.release.set()
        await second
        assert model.peak == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/v1#fragment",
        "https://[::ffff:127.0.0.1]/v1",
        "https://[::]/v1",
        "https://224.0.0.1/v1",
        "https://100.64.0.1/v1",
    ],
)
def test_egress_rejects_unsafe_url_forms(url) -> None:
    with pytest.raises(ModelPolicyDeniedError):
        validate_endpoint(url)


def test_transport_pins_dns_and_preserves_host_and_tls_identity() -> None:
    async def check():
        lookups = 0
        calls = 0

        async def resolve(host, port):
            nonlocal lookups
            lookups += 1
            assert (host, port) == ("approved.example", 443)
            return ["1.1.1.1"] if lookups == 1 else ["127.0.0.1"]

        def network(request):
            nonlocal calls
            calls += 1
            assert request.url.host == "1.1.1.1"
            assert request.headers["Host"] == "approved.example"
            assert request.extensions["sni_hostname"] == "approved.example"
            return httpx.Response(200)

        transport = PinnedEgressTransport(
            ["https://approved.example/v1"],
            resolver=resolve,
            transport=httpx.MockTransport(network),
        )
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            await client.post("https://approved.example/v1/chat/completions")
            with pytest.raises(ModelPolicyDeniedError):
                await client.post("https://approved.example/v1/chat/completions")
            with pytest.raises(ModelPolicyDeniedError):
                await client.post("https://attacker.example/v1/chat/completions")
        assert calls == 1 and lookups == 2

    asyncio.run(check())


@pytest.mark.parametrize("addresses", [["1.1.1.1", "10.0.0.1"], ["::ffff:169.254.169.254"], []])
def test_transport_denies_mixed_or_private_dns_before_network(addresses):
    async def check():
        async def resolve(*_):
            return addresses

        def network(_):
            pytest.fail("Forbidden network dispatch")

        async with httpx.AsyncClient(
            transport=PinnedEgressTransport(
                ["https://approved.example/v1"],
                resolver=resolve,
                transport=httpx.MockTransport(network),
            )
        ) as client:
            with pytest.raises(ModelPolicyDeniedError):
                await client.post("https://approved.example/v1/chat/completions")

    asyncio.run(check())


def test_adapter_total_deadline_closes_slow_stream():
    from datetime import UTC, datetime, timedelta

    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.02)
                yield b": keepalive\n\n"

        async def aclose(self):
            self.closed = True

    async def check():
        body = SlowBody()
        request = ModelGenerationRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_config_id=uuid4(),
            requested_provider="test",
            requested_model="test",
            messages=[ModelMessage(role="user", content="hi")],
            deadline=datetime.now(UTC) + timedelta(seconds=0.08),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))
        ) as client:
            with pytest.raises(ModelTimeoutError):
                async for _ in HttpModelAdapter(
                    client, default_base_url="https://test.example/v1"
                ).stream(request):
                    pass
        assert body.closed

    asyncio.run(check())


@pytest.mark.parametrize("raw", [True, "5", -1, 1.5])
def test_provider_usage_does_not_coerce_invalid_counts(raw):
    adapter = HttpModelAdapter(httpx.AsyncClient())
    assert adapter._usage_count(raw) is None


def test_production_env_alias_cannot_enable_local_egress(monkeypatch):
    from agent_runtime.llm.composition import model_gateway_resources
    from agent_runtime.settings import RuntimeSettings

    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    settings = RuntimeSettings(_env_file=None, model_gateway_allow_local_endpoints=True)
    assert settings.environment == "production"

    async def check():
        with pytest.raises(ValueError, match="Local model endpoints"):
            async with model_gateway_resources(settings):
                pytest.fail("Production local egress accepted")

    asyncio.run(check())


def test_runtime_lifespan_composes_and_releases_gateway(monkeypatch):
    from agent_runtime.llm.gateway import ModelGateway
    from agent_runtime.main import app, lifespan
    from agent_runtime.settings import RuntimeSettings

    monkeypatch.setattr("agent_runtime.main.get_settings", lambda: RuntimeSettings(_env_file=None))

    async def check():
        async with lifespan(app):
            assert isinstance(app.state.model_gateway, ModelGateway)
        assert app.state.model_gateway is None

    asyncio.run(check())


def test_stream_deadline_does_not_cancel_consumer_between_next_calls():
    from datetime import UTC, datetime, timedelta

    async def check():
        request = ModelGenerationRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_config_id=uuid4(),
            requested_provider="test",
            requested_model="test",
            messages=[ModelMessage(role="user", content="test")],
            deadline=datetime.now(UTC) + timedelta(seconds=0.15),
        )
        body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        ) as client:
            stream = HttpModelAdapter(client, default_base_url="https://test.example/v1").stream(
                request
            )
            first = await anext(stream)
            assert first.delta_type == "content"
            await asyncio.sleep(0.25)  # Must not receive cancellation from a suspended generator.
            with pytest.raises(ModelTimeoutError):
                await anext(stream)
            await stream.aclose()

    asyncio.run(check())
