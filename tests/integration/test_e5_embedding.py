"""Real offline E5 inference; live Qdrant is an explicit additional test environment."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from agent_runtime.knowledge.embeddings import E5EmbeddingModel
from agent_runtime.knowledge.hybrid_retriever import HybridRetriever
from agent_runtime.knowledge.qdrant_index import QdrantVectorIndex
from agent_runtime.knowledge.retrieval_types import KnowledgeChunk, RetrievalScope

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_model():
    snapshot = os.environ.get("E5_MODEL_SNAPSHOT")
    if not snapshot:
        pytest.skip("E5_MODEL_SNAPSHOT required for real offline inference")
    # Test-process CPU bounds; production workers own their own thread/budget settings.
    import torch

    torch.set_num_threads(2)
    return E5EmbeddingModel(Path(snapshot))


def test_real_e5_shape_normalization_prefix_and_semantic_order(real_model):
    query = real_model.embed_query("Làm sao xin nghỉ phép?")
    documents = real_model.embed_passages(
        [
            "Nhân viên gửi đơn xin nghỉ phép cho quản lý phê duyệt.",
            "Hướng dẫn thay pin bàn phím không dây.",
        ]
    )
    assert len(query) == 384 and len(documents) == 2
    for vector in (query, *documents):
        assert len(vector) == 384
        assert all(math.isfinite(value) for value in vector)
        assert sum(value * value for value in vector) == pytest.approx(1.0, abs=1e-4)
    similarities = [sum(a * b for a, b in zip(query, row, strict=True)) for row in documents]
    assert similarities[0] > similarities[1]
    assert real_model.embed_query("nghỉ phép") != real_model.embed_passages(["nghỉ phép"])[0]


def test_real_e5_rejects_oversize_empty_and_batch_overflow(real_model):
    with pytest.raises(ValueError, match="token_limit_exceeded"):
        real_model.embed_passages(["hello " * 600])
    with pytest.raises(ValueError, match="empty"):
        real_model.embed_query(" ")
    with pytest.raises(ValueError, match="batch_size"):
        real_model.embed_passages(["hello"] * 33)
    with pytest.raises(ValueError, match="batch_size"):
        real_model.embed_passages("not a batch")


class SyntheticSourceAuthorization:
    """Synthetic fixture only; this does not test IAM/PostgreSQL/RLS."""

    def require_index(self, chunk):
        return None

    def recheck(self, scope, chunks):
        return chunks


def test_real_qdrant_index_reopen_and_scope_isolation(real_model):
    url = os.environ.get("E5_TEST_QDRANT_URL")
    if not url:
        pytest.skip("E5_TEST_QDRANT_URL required; live Qdrant server not verified")
    name = "eap_e5_test_" + uuid4().hex
    scope = RetrievalScope(UUID(int=1), UUID(int=2))
    source = KnowledgeChunk(
        scope,
        UUID(int=3),
        UUID(int=4),
        UUID(int=5),
        UUID(int=6),
        "Nhân viên gửi đơn xin nghỉ phép cho quản lý phê duyệt.",
    )
    other = KnowledgeChunk(
        RetrievalScope(UUID(int=90), scope.workspace_id),
        source.base_id,
        source.document_id,
        source.document_version_id,
        source.chunk_id,
        "Nhân viên gửi đơn xin nghỉ phép cho quản lý phê duyệt.",
    )
    probe_path = os.environ.get("E5_TEST_PERSISTENCE_PROBE")
    kept_for_restart_probe = False
    try:
        with QdrantVectorIndex(url=url, collection=name) as index:
            index.ensure_collection()
            retriever = HybridRetriever(
                embeddings=real_model, index=index, authorization=SyntheticSourceAuthorization()
            )
            retriever.index_document(source)
            retriever.index_document(other)
        # New adapter/client reads persisted vectors; no old Python corpus survives.
        with QdrantVectorIndex(url=url, collection=name) as reopened:
            retriever = HybridRetriever(
                embeddings=real_model, index=reopened, authorization=SyntheticSourceAuthorization()
            )
            hits = retriever.search("xin nghỉ phép", scope=scope, authorized_chunks=[source])
            assert len(hits) == 1 and hits[0].chunk == source
            assert hits[0].dense_score is not None
            if probe_path:
                # Optional test-harness handoff for a real container restart check.
                # Contains synthetic test content/vectors only; caller cleans its collection.
                Path(probe_path).write_text(
                    json.dumps(
                        {
                            "url": url,
                            "collection": name,
                            "point_id": source.point_id,
                            "query_vector": real_model.embed_query("xin nghỉ phép"),
                            "source": {
                                "tenant_id": str(scope.tenant_id),
                                "workspace_id": str(scope.workspace_id),
                                "base_id": str(source.base_id),
                                "document_id": str(source.document_id),
                                "document_version_id": str(source.document_version_id),
                                "chunk_id": str(source.chunk_id),
                                "text": source.text,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                kept_for_restart_probe = True
    finally:
        # Only the uniquely named collection created by this test may be removed.
        if not kept_for_restart_probe:
            response = httpx.delete(
                f"{url.rstrip('/')}/collections/{name}", timeout=10, trust_env=False
            )
            response.raise_for_status()
