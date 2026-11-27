"""Contract and unit tests for HttpModelAdapter and E5EmbeddingAdapter (G5-01, G5-02, G5-12, G5-14, G5-15, G5-20)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from agent_contracts.model_contracts import (
    ModelGenerationRequest,
    ModelMessage,
    ModelStreamingDelta,
    ModelToolDefinition,
)
from agent_runtime.llm.adapters.http_provider import HttpModelAdapter, validate_egress_endpoint
from agent_runtime.llm.errors import (
    ModelAuthError,
    ModelInvalidResponseError,
    ModelPolicyDeniedError,
    ModelRateLimitedError,
    ModelTimeoutError,
    ModelUnavailableError,
)


def _make_sample_request(**kwargs) -> ModelGenerationRequest:
    defaults = {
        "request_id": uuid4(),
        "tenant_id": uuid4(),
        "workspace_id": uuid4(),
        "model_config_id": uuid4(),
        "requested_provider": "openai",
        "requested_model": "gpt-4o",
        "messages": [ModelMessage(role="user", content="Hello, world!")],
    }
    defaults.update(kwargs)
    return ModelGenerationRequest(**defaults)


class TestEgressEndpointValidation:
    def test_blocks_localhost_and_private_networks(self) -> None:
        disallowed_urls = [
            "http://127.0.0.1:8000/v1",
            "http://localhost:8080/v1",
            "https://10.0.0.1/v1",
            "https://172.16.0.1/v1",
            "https://192.168.1.1/v1",
            "https://169.254.169.254/latest/meta-data",
            "http://[::1]:8080",
        ]
        for url in disallowed_urls:
            with pytest.raises(ModelPolicyDeniedError):
                validate_egress_endpoint(url, allow_local=False)

    def test_blocks_insecure_http_in_production(self) -> None:
        with pytest.raises(ModelPolicyDeniedError, match="Insecure HTTP scheme"):
            validate_egress_endpoint("http://api.openai.com/v1", allow_local=False)

    def test_allows_public_https_endpoints(self) -> None:
        allowed_urls = [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://gateway.enterprise.vinai.vn/v1",
        ]
        for url in allowed_urls:
            validate_egress_endpoint(url, allow_local=False)

    def test_allows_local_when_flag_enabled(self) -> None:
        validate_egress_endpoint("http://127.0.0.1:8000/v1", allow_local=True)
        validate_egress_endpoint("http://localhost:8080/v1", allow_local=True)


class TestHttpModelAdapterGeneration:
    def test_successful_chat_generation(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-request-id"] is not None
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.read())
            assert body["model"] == "gpt-4o"
            assert body["messages"][0]["content"] == "Hello, world!"

            res_body = {
                "id": "chatcmpl-123",
                "model": "gpt-4o-2024-08-06",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello there! How can I help?"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }
            return httpx.Response(200, json=res_body, headers={"x-request-id": "req-999"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
        req = _make_sample_request()

        res = asyncio.run(adapter.generate(req, auth_header="Bearer test-key"))

        assert res.request_id == req.request_id
        assert res.requested_model == "gpt-4o"
        assert res.returned_model == "gpt-4o-2024-08-06"
        assert res.finish_reason == "stop"
        assert res.message.content == "Hello there! How can I help?"
        assert res.usage.input_tokens == 12
        assert res.usage.output_tokens == 8
        assert res.usage.usage_source == "provider"
        assert res.provider_request_id == "req-999"

    def test_tool_calling_intent_normalization(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            res_body = {
                "id": "chatcmpl-tool-1",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc123",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup_user",
                                        "arguments": '{"user_id": "usr_456"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            }
            return httpx.Response(200, json=res_body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
        req = _make_sample_request(
            tools=[
                ModelToolDefinition(
                    type="function",
                    function={
                        "name": "lookup_user",
                        "description": "Lookup user profile",
                        "parameters": {
                            "type": "object",
                            "properties": {"user_id": {"type": "string"}},
                        },
                    },
                )
            ]
        )

        res = asyncio.run(adapter.generate(req))

        assert res.finish_reason == "tool_calls"
        assert res.message.tool_calls is not None
        assert len(res.message.tool_calls) == 1
        tc = res.message.tool_calls[0]
        assert tc.id == "call_abc123"
        assert tc.function.name == "lookup_user"
        assert json.loads(tc.function.arguments) == {"user_id": "usr_456"}

    def test_structured_output_schema_validation(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            res_body = {
                "id": "chatcmpl-invalid-json",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "not valid json!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
            return httpx.Response(200, json=res_body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
        req = _make_sample_request(response_format={"type": "json_object"})

        with pytest.raises(
            ModelInvalidResponseError, match="Structured output schema validation failed"
        ):
            asyncio.run(adapter.generate(req))

    def test_error_status_mapping(self) -> None:
        status_map = [
            (401, ModelAuthError),
            (403, ModelAuthError),
            (429, ModelRateLimitedError),
            (408, ModelTimeoutError),
            (504, ModelTimeoutError),
            (500, ModelUnavailableError),
            (503, ModelUnavailableError),
        ]
        for code, exc_type in status_map:

            def handler(_req: httpx.Request, c=code) -> httpx.Response:
                return httpx.Response(c, text=f"Error {c}")

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
            req = _make_sample_request()

            with pytest.raises(exc_type):
                asyncio.run(adapter.generate(req))

    def test_expired_deadline_fails_before_dispatch(self) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
        req = _make_sample_request(deadline=datetime.now(UTC) - timedelta(seconds=1))
        with pytest.raises(ModelTimeoutError, match="expired before dispatch"):
            asyncio.run(adapter.generate(req))


class TestHttpModelAdapterStreaming:
    def test_streaming_emits_ordered_deltas_and_final_finish(self) -> None:
        sse_lines = [
            'data: {"choices": [{"delta": {"role": "assistant", "content": "Hello"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}\n\n',
            "data: [DONE]\n\n",
        ]

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content="".join(sse_lines).encode("utf-8"))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
        req = _make_sample_request()

        async def _run_stream() -> list[ModelStreamingDelta]:
            deltas: list[ModelStreamingDelta] = []
            async for delta in adapter.stream(req):
                deltas.append(delta)
            return deltas

        deltas = asyncio.run(_run_stream())

        assert len(deltas) >= 3
        # Check sequence order
        for idx, d in enumerate(deltas):
            assert d.sequence == idx

        content_deltas = [d for d in deltas if d.delta_type == "content"]
        assert "".join(d.content for d in content_deltas if d.content) == "Hello world"

        finish_deltas = [d for d in deltas if d.delta_type == "finish"]
        assert len(finish_deltas) == 1
        assert finish_deltas[0].finish_reason == "stop"
        assert finish_deltas[0].usage is not None
        assert finish_deltas[0].usage.input_tokens == 10
        assert finish_deltas[0].usage.output_tokens == 2

    def test_streaming_tool_call_deltas(self) -> None:
        sse_lines = [
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "query_db", "arguments": "{\\"id\\": "}}]}}]}\n\n',
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "123}"}}]}}]}\n\n',
            'data: {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}\n\n',
            "data: [DONE]\n\n",
        ]

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content="".join(sse_lines).encode("utf-8"))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = HttpModelAdapter(client, default_base_url="https://api.openai.com/v1")
        req = _make_sample_request()

        async def _run_stream() -> list[ModelStreamingDelta]:
            deltas: list[ModelStreamingDelta] = []
            async for delta in adapter.stream(req):
                deltas.append(delta)
            return deltas

        deltas = asyncio.run(_run_stream())

        tc_deltas = [d for d in deltas if d.delta_type == "tool_call"]
        assert len(tc_deltas) == 2
        assert tc_deltas[0].tool_call_delta is not None
        assert tc_deltas[0].tool_call_delta.name == "query_db"
        assert tc_deltas[0].tool_call_delta.arguments_chunk == '{"id": '
        assert tc_deltas[1].tool_call_delta.arguments_chunk == "123}"

        finish_delta = deltas[-1]
        assert finish_delta.delta_type == "finish"
        assert finish_delta.finish_reason == "tool_calls"
