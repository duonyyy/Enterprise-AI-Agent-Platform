"""Unit/REST-contract fixtures only; these tests do not prove live Qdrant or RLS."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from agent_runtime.knowledge.embeddings import EMBEDDING_REVISION, E5EmbeddingModel
from agent_runtime.knowledge.hybrid_retriever import HybridRetriever
from agent_runtime.knowledge.qdrant_index import QdrantVectorIndex, VectorPayload, vector_payload
from agent_runtime.knowledge.retrieval_types import (
    KnowledgeChunk,
    RetrievalScope,
    RetrievalUnavailable,
    VectorHit,
)
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

SCOPE = RetrievalScope(UUID(int=1), UUID(int=2))
VECTOR = (1.0,) + (0.0,) * 383  # Labeled port fixture; never configured in Runtime.


def chunk(number: int = 10, text: str = "quy trình nghỉ phép") -> KnowledgeChunk:
    return KnowledgeChunk(SCOPE, UUID(int=3), UUID(int=4), UUID(int=5), UUID(int=number), text)


class AuthorizationFixture:
    def __init__(self, *, revoke_on_second: bool = False) -> None:
        self.calls = 0
        self.revoke_on_second = revoke_on_second

    def require_index(self, source: KnowledgeChunk) -> None:
        assert source.scope == SCOPE

    def recheck(self, scope: RetrievalScope, chunks: Sequence[KnowledgeChunk]):
        self.calls += 1
        return () if self.revoke_on_second and self.calls >= 2 else chunks


class EmbeddingFixture:
    def embed_query(self, text: str):
        return VECTOR

    def embed_passages(self, texts: Sequence[str]):
        return tuple(VECTOR for _ in texts)


class IndexFixture:
    def __init__(self, sources: Sequence[KnowledgeChunk], *, fail: bool = False) -> None:
        self.sources = sources
        self.fail = fail

    def search(self, scope, chunks, vector, *, limit):
        if self.fail:
            raise RetrievalUnavailable("qdrant_unavailable")
        return tuple(VectorHit(source.point_id, 0.9) for source in self.sources)


class QdrantRestFixture:
    """HTTP seam fixture, not a Qdrant server or durability proof."""

    def __init__(self) -> None:
        self.points = {}
        self.calls = []
        self.corrupt_payload = False
        self.acknowledged_only = False
        self.dimension = 384
        self.score = 0.9

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        if request.method == "GET":
            result = {
                "config": {
                    "params": {
                        "vectors": {
                            "size": self.dimension,
                            "distance": "Cosine",
                        }
                    }
                }
            }
        elif request.method == "PUT":
            self.points.update({point["id"]: point for point in body["points"]})
            result = {"status": "acknowledged" if self.acknowledged_only else "completed"}
        elif request.url.path.endswith("/search"):
            conditions = body["filter"]["must"]
            assert {"key": "tenant_id", "match": {"value": str(SCOPE.tenant_id)}} in conditions
            assert {
                "key": "workspace_id",
                "match": {"value": str(SCOPE.workspace_id)},
            } in conditions
            ids = next(condition["has_id"] for condition in conditions if "has_id" in condition)
            result = [
                dict(self.points[point_id], score=self.score) for point_id in ids[: body["limit"]]
            ]
        else:
            result = [
                dict(self.points[point_id]) for point_id in body["ids"] if point_id in self.points
            ]
            if self.corrupt_payload and result:
                result[0]["payload"] = {**result[0]["payload"], "workspace_id": str(UUID(int=900))}
        # Raw JSON lets the contract test exercise non-finite vendor scores too.
        return httpx.Response(
            200,
            content=json.dumps({"status": "ok", "result": result}),
            headers={"content-type": "application/json"},
        )


@pytest.fixture
def rest_index():
    fixture = QdrantRestFixture()
    with (
        httpx.Client(transport=httpx.MockTransport(fixture)) as client,
        QdrantVectorIndex(
            url="http://qdrant.invalid", collection="test_e5", client=client
        ) as index,
    ):
        yield fixture, index


def test_vector_payload_matches_schema_and_has_no_text_or_grants():
    payload = vector_payload(chunk()).model_dump()
    schema = json.loads(Path("contracts/jsonschema/knowledge-vector.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
    assert "text" not in payload and "grants" not in payload
    with pytest.raises(ValidationError):
        VectorPayload.model_validate({**payload, "role": "admin"})
    with pytest.raises(ValidationError):
        VectorPayload.model_validate({**payload, "tenant_id": "spoofed"})


def test_point_identity_changes_with_content_revision_or_scope():
    original = chunk()
    assert chunk().point_id == original.point_id
    assert replace(original, text="new version").point_id != original.point_id
    assert replace(original, document_version_id=UUID(int=500)).point_id != original.point_id
    assert (
        replace(original, scope=RetrievalScope(UUID(int=500), SCOPE.workspace_id)).point_id
        != original.point_id
    )


def test_qdrant_contract_uses_scoped_filter_and_immutable_identity(rest_index):
    fixture, index = rest_index
    source = chunk()
    assert index.upsert(source, VECTOR) == source.point_id
    index.upsert(source, VECTOR)
    assert len(fixture.points) == 1
    hits = index.search(SCOPE, [source], VECTOR, limit=1)
    assert hits == (VectorHit(source.point_id, 0.9),)


def test_missing_vector_is_not_empty_or_bm25_fallback(rest_index):
    _, index = rest_index
    with pytest.raises(RetrievalUnavailable, match="missing_or_stale"):
        index.search(SCOPE, [chunk()], VECTOR, limit=1)


@pytest.mark.parametrize("score", [True, None, float("inf"), 1.5])
def test_invalid_dense_score_is_rejected(rest_index, score):
    fixture, index = rest_index
    source = chunk()
    index.upsert(source, VECTOR)
    fixture.score = score
    with pytest.raises(RetrievalUnavailable, match="score_invalid"):
        index.search(SCOPE, [source], VECTOR, limit=1)


def test_corrupt_or_cross_scope_payload_is_rejected(rest_index):
    fixture, index = rest_index
    index.upsert(chunk(), VECTOR)
    fixture.corrupt_payload = True
    with pytest.raises(RetrievalUnavailable, match="payload_mismatch"):
        index.search(SCOPE, [chunk()], VECTOR, limit=1)


def test_collection_mismatch_does_not_delete_or_recreate(rest_index):
    fixture, index = rest_index
    fixture.dimension = 768
    with pytest.raises(RetrievalUnavailable, match="model_mismatch"):
        index.ensure_collection()
    assert all(method == "GET" for method, _, _ in fixture.calls)


def test_unconfirmed_write_does_not_report_success_or_retry(rest_index):
    fixture, index = rest_index
    fixture.acknowledged_only = True
    with pytest.raises(RetrievalUnavailable, match="write_not_confirmed"):
        index.upsert(chunk(), VECTOR)
    assert sum(method == "PUT" for method, _, _ in fixture.calls) == 1


def test_qdrant_network_failure_is_not_empty_success():
    def fail(request):
        raise httpx.ConnectError("fixture outage", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(fail)) as client,
        QdrantVectorIndex(url="http://qdrant.invalid", collection="test", client=client) as index,
        pytest.raises(RetrievalUnavailable, match="unconfirmed"),
    ):
        index.search(SCOPE, [chunk()], VECTOR, limit=1)


def test_hybrid_uses_actual_bm25_and_rrf_over_source_text():
    first, second = chunk(), chunk(11, "thay pin bàn phím không dây")
    retriever = HybridRetriever(
        embeddings=EmbeddingFixture(),
        index=IndexFixture([first, second]),
        authorization=AuthorizationFixture(),
    )
    hits = retriever.search("nghỉ phép", scope=SCOPE, authorized_chunks=[first, second])
    assert hits[0].chunk == first
    assert hits[0].bm25_score > 0
    assert hits[1].bm25_score is None
    assert hits[0].score == pytest.approx(2 / 61)


def test_revocation_during_search_releases_no_source_text():
    source = chunk()
    retriever = HybridRetriever(
        embeddings=EmbeddingFixture(),
        index=IndexFixture([source]),
        authorization=AuthorizationFixture(revoke_on_second=True),
    )
    assert retriever.search("nghỉ phép", scope=SCOPE, authorized_chunks=[source]) == ()


def test_wrong_scope_is_denied_before_model_and_no_scope_fallback():
    retriever = HybridRetriever(
        embeddings=EmbeddingFixture(), index=IndexFixture([]), authorization=AuthorizationFixture()
    )
    with pytest.raises(PermissionError, match="scope_mismatch"):
        retriever.search(
            "hello",
            scope=RetrievalScope(UUID(int=900), SCOPE.workspace_id),
            authorized_chunks=[chunk()],
        )


def test_vector_outage_is_not_hidden_by_lexical_matching():
    retriever = HybridRetriever(
        embeddings=EmbeddingFixture(),
        index=IndexFixture([], fail=True),
        authorization=AuthorizationFixture(),
    )
    with pytest.raises(RetrievalUnavailable, match="unavailable"):
        retriever.search("nghỉ phép", scope=SCOPE, authorized_chunks=[chunk()])


@pytest.mark.parametrize("top_k", [0, 21, True])
def test_query_limits(top_k):
    retriever = HybridRetriever(
        embeddings=EmbeddingFixture(), index=IndexFixture([]), authorization=AuthorizationFixture()
    )
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("query", scope=SCOPE, authorized_chunks=[], top_k=top_k)


def test_missing_snapshot_fails_without_network_download(tmp_path):
    with pytest.raises(ValueError, match="revision_mismatch"):
        E5EmbeddingModel(tmp_path)
    with pytest.raises(RuntimeError, match="not_provisioned"):
        E5EmbeddingModel(tmp_path / EMBEDDING_REVISION)
