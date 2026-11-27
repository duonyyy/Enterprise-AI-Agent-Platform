"""Offline E5 embedding adapter conforming to EmbeddingPort with bounded scheduling."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from agent_contracts.model_contracts import (
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelUsage,
)

from agent_runtime.knowledge.embeddings import EMBEDDING_MODEL
from agent_runtime.llm.errors import (
    ModelContextTooLargeError,
    ModelInvalidResponseError,
    ModelUnavailableError,
)
from agent_runtime.llm.ports import EmbeddingPort

if TYPE_CHECKING:
    from agent_runtime.knowledge.embeddings import E5EmbeddingModel

EMBEDDING_DIMENSIONS = 384
MAX_EMBEDDING_TOKENS = 512
MAX_EMBEDDING_BATCH = 32
MAX_TEXT_BYTES = 32_768
DEFAULT_CONCURRENCY = 4
T = TypeVar("T")


class E5EmbeddingAdapter(EmbeddingPort):
    """Adapter that wraps E5EmbeddingModel with bounded concurrency and non-blocking scheduling."""

    def __init__(
        self,
        model: E5EmbeddingModel,
        *,
        max_concurrency: int = DEFAULT_CONCURRENCY,
        validate_normalization: bool = False,
        max_queue: int = 32,
    ) -> None:
        if max_concurrency < 1 or max_queue < 0:
            raise ValueError("Invalid E5 concurrency/queue limit")
        self._model = model
        self._capacity = max_concurrency + max_queue
        self._pending = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._validate_normalization = validate_normalization

    async def _schedule(self, work: Callable[[], T]) -> T:
        if self._pending >= self._capacity:
            raise ModelUnavailableError("E5 queue capacity exceeded")
        self._pending += 1
        acquired = False
        try:
            await self._semaphore.acquire()
            acquired = True
            task = asyncio.create_task(asyncio.to_thread(work))
            self._tasks.add(task)

            def completed(done: asyncio.Task[T]) -> None:
                self._semaphore.release()
                self._pending -= 1
                self._tasks.discard(done)
                if not done.cancelled():
                    done.exception()  # Consume errors even when the caller was cancelled.

            task.add_done_callback(completed)
            return await asyncio.shield(task)
        finally:
            if not acquired:
                self._pending -= 1

    async def count_tokens(self, request: ModelEmbeddingRequest) -> int:
        if request.model_id != EMBEDDING_MODEL:
            raise ModelInvalidResponseError("Embedding model identity mismatch")

        def count() -> int:
            try:
                result = self._model.count_tokens(request.inputs, purpose=request.purpose)
            except ValueError as exc:
                raise ModelContextTooLargeError("Invalid or oversized E5 token input") from exc
            if type(result) is not int or result < 1:
                raise ModelUnavailableError("E5 tokenizer usage is unavailable")
            return result

        return await self._schedule(count)

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _sync_inference(self, request: ModelEmbeddingRequest) -> tuple[list[list[float]], int]:
        """Run CPU-intensive model inference synchronously on worker thread."""
        try:
            tokens = self._model.count_tokens(request.inputs, purpose=request.purpose)
            if type(tokens) is not int or tokens < 1:
                raise ModelUnavailableError("E5 tokenizer usage unavailable")
            if request.purpose == "query":
                vectors: list[list[float]] = [
                    list(self._model.embed_query(inp)) for inp in request.inputs
                ]
            else:
                raw_tuple = self._model.embed_passages(request.inputs)
                vectors = [list(v) for v in raw_tuple]
            return vectors, tokens
        except ValueError as exc:
            msg = str(exc)
            if "token_limit_exceeded" in msg:
                raise ModelContextTooLargeError(
                    f"E5 embedding token limit exceeded (max {MAX_EMBEDDING_TOKENS}): {msg}"
                ) from exc
            if "batch_size" in msg:
                raise ModelInvalidResponseError(
                    f"E5 batch size out of range (max {MAX_EMBEDDING_BATCH}): {msg}"
                ) from exc
            if "empty" in msg or "too_large" in msg:
                raise ModelInvalidResponseError(f"Invalid embedding input: {msg}") from exc
            raise ModelInvalidResponseError(f"Embedding validation error: {msg}") from exc
        except Exception as exc:
            raise ModelUnavailableError(f"E5 embedding inference failed: {exc}") from exc

    async def embed(self, request: ModelEmbeddingRequest) -> ModelEmbeddingResponse:
        """Generate 384-dimensional dense vectors with bounded concurrency."""
        if request.model_id != EMBEDDING_MODEL:
            raise ModelInvalidResponseError("Embedding model identity mismatch")
        if not request.inputs:
            raise ModelInvalidResponseError("Embedding request inputs list cannot be empty")
        if len(request.inputs) > MAX_EMBEDDING_BATCH:
            raise ModelInvalidResponseError(
                f"Embedding request batch size ({len(request.inputs)}) exceeds max {MAX_EMBEDDING_BATCH}"
            )
        for idx, text in enumerate(request.inputs):
            if not isinstance(text, str) or not text.strip():
                raise ModelInvalidResponseError(f"Embedding input at index {idx} cannot be empty")
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ModelInvalidResponseError(
                    f"Embedding input at index {idx} exceeds {MAX_TEXT_BYTES} bytes"
                )

        vectors, tokens = await self._schedule(lambda: self._sync_inference(request))
        usage = ModelUsage(inputTokens=tokens, outputTokens=0, usageSource="estimated")

        count = len(vectors)
        if count != len(request.inputs):
            raise ModelInvalidResponseError(f"Expected {len(request.inputs)} vectors, got {count}")

        for idx, vec in enumerate(vectors):
            if len(vec) != EMBEDDING_DIMENSIONS:
                raise ModelInvalidResponseError(
                    f"Vector at index {idx} has dimension {len(vec)}, expected {EMBEDDING_DIMENSIONS}"
                )
            for v_idx, val in enumerate(vec):
                if not math.isfinite(val):
                    raise ModelInvalidResponseError(
                        f"Non-finite float value {val} at vector index {idx}, dimension {v_idx}"
                    )
            # Verify unit length normalization if requested (real E5EmbeddingModel enforces this internally)
            if self._validate_normalization:
                norm = math.sqrt(sum(v * v for v in vec))
                if not math.isclose(norm, 1.0, abs_tol=1e-3):
                    raise ModelInvalidResponseError(
                        f"Vector at index {idx} not normalized: norm = {norm:.6f}"
                    )

        return ModelEmbeddingResponse(
            requestId=request.request_id,
            modelId=request.model_id,
            dimensions=EMBEDDING_DIMENSIONS,
            count=count,
            embeddings=vectors,
            usage=usage,
        )
