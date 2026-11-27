"""HTTP provider adapter conforming to GenerationPort and StreamingPort (SPEC-LLM-001)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import jsonschema
from agent_contracts.model_contracts import (
    FinishReason,
    ModelGenerationRequest,
    ModelGenerationResponse,
    ModelResponseMessage,
    ModelStreamingDelta,
    ModelToolCall,
    ModelToolFunction,
    ModelUsage,
    ToolCallDelta,
)

from agent_runtime.llm.adapters.egress import validate_endpoint
from agent_runtime.llm.errors import (
    ModelAuthError,
    ModelInvalidResponseError,
    ModelPolicyDeniedError,
    ModelRateLimitedError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from agent_runtime.llm.ports import GenerationPort, StreamingPort


def validate_egress_endpoint(url: str, *, allow_local: bool = False) -> None:
    """Syntax/literal-address checks. DNS enforcement belongs to PinnedEgressTransport."""
    validate_endpoint(url, allow_local=allow_local)


class HttpModelAdapter(GenerationPort, StreamingPort):
    """Adapter executing chat generation and streaming via HTTP provider APIs."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        default_base_url: str | None = None,
        allow_local_endpoints: bool = False,
    ) -> None:
        self._client = client
        self._default_base_url = default_base_url
        self._allow_local_endpoints = allow_local_endpoints

    def _resolve_url(self, endpoint_url: str | None) -> str:
        url = endpoint_url or self._default_base_url
        if not url:
            raise ModelUnavailableError("No endpoint URL configured for HTTP model adapter")
        validate_egress_endpoint(url, allow_local=self._allow_local_endpoints)
        return url.rstrip("/")

    def _build_payload(
        self, request: ModelGenerationRequest, *, stream: bool = False
    ) -> dict[str, Any]:
        """Convert ModelGenerationRequest into canonical chat completion payload."""
        messages: list[dict[str, Any]] = []
        for msg in request.messages:
            m: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                m["content"] = msg.content
            if msg.name is not None:
                m["name"] = msg.name
            if msg.tool_call_id is not None:
                m["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(m)

        payload: dict[str, Any] = {
            "model": request.requested_model,
            "messages": messages,
            "stream": stream,
        }

        if request.tools:
            payload["tools"] = [
                {
                    "type": t.type,
                    "function": {
                        "name": t.function.get("name"),
                        "description": t.function.get("description"),
                        "parameters": t.function.get("parameters", {}),
                    },
                }
                for t in request.tools
            ]

        if stream:
            payload["stream_options"] = {"include_usage": True}

        if request.response_format:
            fmt_type = request.response_format.get("type")
            if fmt_type == "json_object":
                payload["response_format"] = {"type": "json_object"}
            elif fmt_type == "json_schema":
                schema = request.response_format.get("jsonSchema", {})
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": schema
                    if "schema" in schema
                    else {"name": "response", "schema": schema},
                }

        if request.settings:
            settings = request.settings
            if "temperature" in settings:
                payload["temperature"] = settings["temperature"]
            if "top_p" in settings:
                payload["top_p"] = settings["top_p"]
            if "max_output_tokens" in settings:
                payload["max_tokens"] = settings["max_output_tokens"]
            if "frequency_penalty" in settings:
                payload["frequency_penalty"] = settings["frequency_penalty"]
            if "presence_penalty" in settings:
                payload["presence_penalty"] = settings["presence_penalty"]
            if "stop_sequences" in settings:
                payload["stop"] = settings["stop_sequences"]
            if "seed" in settings:
                payload["seed"] = settings["seed"]

        return payload

    def _calculate_timeout(self, deadline: datetime | None) -> float:
        if deadline is None:
            return 60.0
        now = datetime.now(UTC)
        if deadline.tzinfo is None:
            raise ModelPolicyDeniedError("Deadline must include timezone")
        remaining = min(60.0, (deadline - now).total_seconds())
        if remaining <= 0:
            raise ModelTimeoutError("Request deadline has already expired before dispatch")
        return remaining

    async def generate(
        self,
        request: ModelGenerationRequest,
        *,
        auth_header: str | None = None,
        endpoint_url: str | None = None,
    ) -> ModelGenerationResponse:
        """Execute text/structured model generation under strict outcome normalization."""
        base_url = self._resolve_url(endpoint_url)
        url = (
            f"{base_url}/chat/completions"
            if not base_url.endswith("/chat/completions")
            else base_url
        )
        payload = self._build_payload(request, stream=False)

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": str(request.request_id),
        }
        if auth_header:
            headers["Authorization"] = auth_header

        timeout = self._calculate_timeout(request.deadline)

        try:
            async with (
                asyncio.timeout(timeout),
                self._client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                ) as response,
            ):
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 1_048_576:
                        raise ModelInvalidResponseError("Provider response exceeds size limit")
                    body.extend(chunk)
        except (httpx.TimeoutException, TimeoutError):
            raise ModelTimeoutError("HTTP model call timed out") from None
        except httpx.TransportError:
            raise ModelUnavailableError("HTTP model connection failed") from None

        if response.status_code == 401 or response.status_code == 403:
            raise ModelAuthError(f"Provider authentication failed (HTTP {response.status_code})")
        if response.status_code == 429:
            raise ModelRateLimitedError("Provider rate limit exceeded (HTTP 429)")
        if response.status_code in (408, 504):
            raise ModelTimeoutError(f"Provider timed out (HTTP {response.status_code})")
        if response.status_code >= 500:
            raise ModelUnavailableError(
                f"Provider service unavailable (HTTP {response.status_code})"
            )
        if response.status_code != 200:
            raise ModelInvalidResponseError(f"Unexpected HTTP {response.status_code} from provider")

        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise TypeError()
        except (TypeError, ValueError, UnicodeError):
            raise ModelInvalidResponseError("Malformed provider JSON") from None

        try:
            return self._normalize_response(request, data, response_headers=dict(response.headers))
        except (TypeError, ValueError, KeyError, AttributeError):
            raise ModelInvalidResponseError("Malformed provider response") from None

    def _normalize_response(
        self,
        request: ModelGenerationRequest,
        data: dict[str, Any],
        response_headers: dict[str, str] | None = None,
    ) -> ModelGenerationResponse:
        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            raise ModelInvalidResponseError("Provider response missing valid 'choices' array")

        choice = choices[0]
        choice_msg = choice.get("message") or {}
        raw_finish = choice.get("finish_reason")
        content = choice_msg.get("content")
        refusal = choice_msg.get("refusal")
        if raw_finish not in {"stop", "length", "tool_calls", "function_call", "content_filter"}:
            raise ModelInvalidResponseError("Missing or unsupported finish reason")

        tool_calls: list[ModelToolCall] | None = None
        raw_tool_calls = choice_msg.get("tool_calls")
        if raw_tool_calls and isinstance(raw_tool_calls, list):
            tool_calls = []
            for tc in raw_tool_calls:
                func = tc.get("function") or {}
                tool_calls.append(
                    ModelToolCall(
                        id=tc["id"],
                        type="function",
                        function=ModelToolFunction(
                            name=str(func.get("name", "")),
                            arguments=str(func.get("arguments", "")),
                        ),
                    )
                )
                if not isinstance(json.loads(func.get("arguments", "")), dict):
                    raise ModelInvalidResponseError("Invalid completed tool arguments")

        # Normalize finish_reason
        finish_reason: FinishReason
        if refusal or raw_finish == "content_filter":
            finish_reason = "refusal"
        elif raw_finish == "length":
            finish_reason = "length"
        elif raw_finish in ("tool_calls", "function_call") or tool_calls:
            finish_reason = "tool_calls"
        elif raw_finish == "stop":
            finish_reason = "stop"
        else:
            raise ModelInvalidResponseError("Missing or unsupported finish reason")

        # Validate structured output if schema requested
        if request.response_format and finish_reason == "stop":
            fmt_type = request.response_format.get("type")
            if fmt_type in ("json_object", "json_schema"):
                try:
                    if not isinstance(content, str):
                        raise TypeError()
                    parsed_json = json.loads(content)
                    if fmt_type == "json_schema":
                        schema = request.response_format.get("jsonSchema")
                        if schema:
                            # Accept contract form (jsonSchema = schema) and standard
                            # OpenAI named wrapper (jsonSchema.schema = schema).
                            jsonschema.validate(parsed_json, schema.get("schema", schema))
                except (TypeError, ValueError, jsonschema.ValidationError, jsonschema.SchemaError):
                    finish_reason = "error"
                    raise ModelInvalidResponseError(
                        "Structured output schema validation failed"
                    ) from None

        # Extract usage
        raw_usage = data.get("usage")
        if raw_usage and isinstance(raw_usage, dict):
            input_tokens = raw_usage.get("prompt_tokens")
            output_tokens = raw_usage.get("completion_tokens")
            usage = ModelUsage(
                inputTokens=input_tokens
                if type(input_tokens) is int and input_tokens >= 0
                else None,
                outputTokens=output_tokens
                if type(output_tokens) is int and output_tokens >= 0
                else None,
                usageSource="provider",
            )
        else:
            usage = ModelUsage(inputTokens=None, outputTokens=None, usageSource="unknown")

        returned_model = data.get("model")
        provider_request_id = (
            (response_headers or {}).get("x-request-id")
            or (response_headers or {}).get("request-id")
            or data.get("id")
        )

        return ModelGenerationResponse(
            requestId=request.request_id,
            requestedModel=request.requested_model,
            returnedModel=str(returned_model) if returned_model else None,
            finishReason=finish_reason,
            message=ModelResponseMessage(
                role="assistant",
                content=content,
                toolCalls=tool_calls,
                refusal=refusal,
            ),
            usage=usage,
            providerRequestId=str(provider_request_id) if provider_request_id else None,
        )

    async def stream(
        self,
        request: ModelGenerationRequest,
        *,
        auth_header: str | None = None,
        endpoint_url: str | None = None,
    ) -> AsyncGenerator[ModelStreamingDelta, None]:
        from contextlib import aclosing

        end = asyncio.get_running_loop().time() + self._calculate_timeout(request.deadline)
        try:
            async with aclosing(
                self._stream(request, auth_header=auth_header, endpoint_url=endpoint_url)
            ) as source:
                while True:
                    remaining = end - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise ModelTimeoutError("Model stream timed out")
                    try:
                        delta = await asyncio.wait_for(anext(source), remaining)
                    except StopAsyncIteration:
                        break
                    # No task-bound timeout remains active across a consumer yield.
                    yield delta
        except (httpx.TimeoutException, TimeoutError):
            raise ModelTimeoutError("Model stream timed out") from None
        except httpx.TransportError:
            raise ModelUnavailableError("Model stream connection failed") from None
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise ModelInvalidResponseError("Malformed model stream") from None

    async def _lines(self, response: httpx.Response) -> AsyncIterator[str]:
        pending = bytearray()
        size = 0
        async for chunk in response.aiter_bytes(chunk_size=4096):
            size += len(chunk)
            if size > 1_048_576:
                raise ModelInvalidResponseError("Stream response size exceeded")
            pending.extend(chunk)
            while b"\n" in pending:
                raw, _, rest = pending.partition(b"\n")
                if len(raw) > 65_536:
                    raise ModelInvalidResponseError("Stream event size exceeded")
                pending = bytearray(rest)
                yield raw.decode("utf-8").rstrip("\r")
            if len(pending) > 65_536:
                raise ModelInvalidResponseError("Stream event size exceeded")
        if pending:
            raise ModelInvalidResponseError("Truncated SSE event")

    async def _stream(
        self,
        request: ModelGenerationRequest,
        *,
        auth_header: str | None = None,
        endpoint_url: str | None = None,
    ) -> AsyncGenerator[ModelStreamingDelta, None]:
        """Stream chunks from the model provider conforming to ModelStreamingDelta contract."""
        base_url = self._resolve_url(endpoint_url)
        url = (
            f"{base_url}/chat/completions"
            if not base_url.endswith("/chat/completions")
            else base_url
        )
        payload = self._build_payload(request, stream=True)

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Request-ID": str(request.request_id),
        }
        if auth_header:
            headers["Authorization"] = auth_header

        timeout = self._calculate_timeout(request.deadline)
        sequence = 0
        final_emitted = False
        pending_finish: str | None = None
        pending_usage: ModelUsage | None = None
        collected_content: list[str] = []
        collected_tools: dict[int, dict[str, Any]] = {}

        try:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code == 401 or response.status_code == 403:
                    raise ModelAuthError(
                        f"Provider authentication failed (HTTP {response.status_code})"
                    )
                if response.status_code == 429:
                    raise ModelRateLimitedError("Provider rate limit exceeded (HTTP 429)")
                if response.status_code in (408, 504):
                    raise ModelTimeoutError(f"Provider timed out (HTTP {response.status_code})")
                if response.status_code >= 500:
                    raise ModelUnavailableError(
                        f"Provider unavailable (HTTP {response.status_code})"
                    )
                if response.status_code != 200:
                    raise ModelInvalidResponseError(
                        f"Unexpected HTTP {response.status_code} in stream"
                    )

                async for line in self._lines(response):
                    if not line:
                        continue
                    if line.startswith(":"):  # SSE comment
                        continue
                    if line.startswith("data:"):
                        raw_data = line[5:].strip()
                        if raw_data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw_data)
                        except json.JSONDecodeError:
                            raise ModelInvalidResponseError("Malformed streaming JSON") from None

                        choices = chunk.get("choices") or []
                        if not choices:
                            # Might be usage-only final chunk in OpenAI stream_options
                            raw_usage = chunk.get("usage")
                            if raw_usage and isinstance(raw_usage, dict):
                                pending_usage = ModelUsage(
                                    inputTokens=self._usage_count(raw_usage.get("prompt_tokens")),
                                    outputTokens=self._usage_count(
                                        raw_usage.get("completion_tokens")
                                    ),
                                    usageSource="provider",
                                )
                            continue

                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        finish_reason_raw = choice.get("finish_reason")

                        if pending_finish is not None:
                            raise ModelInvalidResponseError("Stream data arrived after finish")

                        # Content delta
                        content_piece = delta.get("content")
                        if content_piece:
                            collected_content.append(content_piece)
                            yield ModelStreamingDelta(
                                requestId=request.request_id,
                                sequence=sequence,
                                deltaType="content",
                                content=content_piece,
                            )
                            sequence += 1

                        # Tool call delta
                        tool_calls = delta.get("tool_calls")
                        if tool_calls and isinstance(tool_calls, list):
                            for tc in tool_calls:
                                func = tc.get("function") or {}
                                index = tc.get("index", 0)
                                if type(index) is not int or index < 0 or index > 63:
                                    raise ModelInvalidResponseError("Invalid tool call index")
                                accumulated = collected_tools.setdefault(
                                    index,
                                    {
                                        "id": None,
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if tc.get("id"):
                                    accumulated["id"] = tc["id"]
                                accumulated["function"]["name"] += func.get("name") or ""
                                accumulated["function"]["arguments"] += func.get("arguments") or ""
                                yield ModelStreamingDelta(
                                    requestId=request.request_id,
                                    sequence=sequence,
                                    deltaType="tool_call",
                                    toolCallDelta=ToolCallDelta(
                                        index=int(tc.get("index", 0)),
                                        id=tc.get("id"),
                                        name=func.get("name"),
                                        argumentsChunk=func.get("arguments"),
                                    ),
                                )
                                sequence += 1

                        # Terminal finish in chunk
                        if finish_reason_raw:
                            if pending_finish is not None:
                                raise ModelInvalidResponseError("Duplicate stream finish")
                            raw_usage = chunk.get("usage")
                            if raw_usage and isinstance(raw_usage, dict):
                                pending_usage = ModelUsage(
                                    inputTokens=self._usage_count(raw_usage.get("prompt_tokens")),
                                    outputTokens=self._usage_count(
                                        raw_usage.get("completion_tokens")
                                    ),
                                    usageSource="provider",
                                )
                            if finish_reason_raw not in (
                                "stop",
                                "length",
                                "tool_calls",
                                "refusal",
                                "error",
                            ):
                                raise ModelInvalidResponseError("Unsupported stream finish reason")
                            pending_finish = finish_reason_raw

                if pending_finish is not None:
                    # Validate final structured content and complete tool intent
                    # before the Gateway releases its buffered deltas.
                    if collected_tools:
                        for tool in collected_tools.values():
                            if not isinstance(json.loads(tool["function"]["arguments"]), dict):
                                raise ModelInvalidResponseError("Invalid completed tool arguments")
                    self._normalize_response(
                        request,
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": "".join(collected_content),
                                        "tool_calls": list(collected_tools.values()) or None,
                                    },
                                    "finish_reason": pending_finish,
                                }
                            ]
                        },
                    )
                    yield ModelStreamingDelta(
                        requestId=request.request_id,
                        sequence=sequence,
                        deltaType="finish",
                        finishReason=pending_finish,
                        usage=pending_usage,
                    )
                    final_emitted = True

        except httpx.TransportError:
            raise ModelUnavailableError("Model stream connection failed") from None
        if not final_emitted:
            raise ModelInvalidResponseError("Stream ended without a terminal event")

    @staticmethod
    def _usage_count(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None
