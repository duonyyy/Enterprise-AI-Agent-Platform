"""Tests for E5EmbeddingAdapter conforming to EmbeddingPort (G5-13, G5-14)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from agent_contracts.model_contracts import ModelEmbeddingRequest
from agent_runtime.llm.embedding_adapter import E5EmbeddingAdapter
from agent_runtime.llm.errors import ModelInvalidResponseError


class TestE5EmbeddingAdapter:
    def test_query_embedding_returns_384_dimensions(self) -> None:
        mock_e5 = MagicMock()
        mock_e5.count_tokens.return_value = 4
        mock_e5.embed_query.return_value = tuple([0.05] * 384)

        adapter = E5EmbeddingAdapter(mock_e5)
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=["Where is Hanoi?"],
            purpose="query",
        )

        import asyncio

        res = asyncio.run(adapter.embed(req))

        assert res.dimensions == 384
        assert res.count == 1
        assert len(res.embeddings[0]) == 384
        assert mock_e5.embed_query.called

    def test_passage_embedding_batch(self) -> None:
        mock_e5 = MagicMock()
        mock_e5.count_tokens.return_value = 4
        mock_e5.embed_passages.return_value = (
            tuple([0.01] * 384),
            tuple([0.02] * 384),
        )

        adapter = E5EmbeddingAdapter(mock_e5)
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=["Passage 1 content", "Passage 2 content"],
            purpose="passage",
        )

        import asyncio

        res = asyncio.run(adapter.embed(req))

        assert res.count == 2
        assert len(res.embeddings) == 2
        assert len(res.embeddings[0]) == 384
        assert len(res.embeddings[1]) == 384
        assert mock_e5.embed_passages.called

    def test_mismatched_dimensions_raises_error(self) -> None:
        mock_e5 = MagicMock()
        mock_e5.count_tokens.return_value = 4
        # Returns 512 dimensions instead of 384
        mock_e5.embed_query.return_value = tuple([0.1] * 512)

        adapter = E5EmbeddingAdapter(mock_e5)
        req = ModelEmbeddingRequest(
            request_id=uuid4(),
            tenant_id=uuid4(),
            workspace_id=uuid4(),
            model_id="intfloat/multilingual-e5-small",
            inputs=["Test"],
            purpose="query",
        )

        import asyncio

        with pytest.raises(ModelInvalidResponseError) as exc_info:
            asyncio.run(adapter.embed(req))
        assert "expected 384" in str(exc_info.value)
