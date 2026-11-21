"""Offline E5 embedding adapter conforming to EmbeddingPort."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from agent_contracts.model_contracts import (
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelUsage,
)
from agent_runtime.llm.errors import ModelInvalidResponseError, ModelUnavailableError
from agent_runtime.llm.ports import EmbeddingPort

if TYPE_CHECKING:
    from agent_runtime.knowledge.embeddings import E5EmbeddingModel

EMBEDDING_DIMENSIONS = 384


class E5EmbeddingAdapter(EmbeddingPort):
    """Adapter that wraps E5EmbeddingModel for dense vector generation."""

    def __init__(self, model: E5EmbeddingModel) -> None:
        self._model = model

    async def embed(self, request: ModelEmbeddingRequest) -> ModelEmbeddingResponse:
        """Generate 384-dimensional dense vectors for queries or passages."""
        if not request.inputs:
            raise ModelInvalidResponseError("Embedding request inputs list cannot be empty")

        try:
            if request.purpose == "query":
                vectors: list[list[float]] = [
                    list(self._model.embed_query(inp)) for inp in request.inputs
                ]
            else:
                raw_tuple = self._model.embed_passages(request.inputs)
                vectors = [list(v) for v in raw_tuple]
        except Exception as exc:
            raise ModelUnavailableError(f"E5 embedding inference failed: {exc}") from exc

        count = len(vectors)
        if count != len(request.inputs):
            raise ModelInvalidResponseError(
                f"Expected {len(request.inputs)} vectors, got {count}"
            )

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

        return ModelEmbeddingResponse(
            request_id=request.request_id,
            model_id=request.model_id,
            dimensions=EMBEDDING_DIMENSIONS,
            count=count,
            embeddings=vectors,
            usage=ModelUsage(input_tokens=None, usage_source="unknown"),
        )
