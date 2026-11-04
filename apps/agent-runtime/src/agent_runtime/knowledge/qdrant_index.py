"""Persistent Qdrant REST adapter (legacy search API compatible with server 1.9).

Only vector identity/provenance is stored. Text and authorization remain at the source.
No local/in-memory mode, automatic retries or collection recreation on mismatch.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, EMBEDDING_REVISION
from .retrieval_types import KnowledgeChunk, RetrievalScope, RetrievalUnavailable, VectorHit


class VectorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: Literal["1.0"] = "1.0"
    tenant_id: str
    workspace_id: str
    base_id: str
    document_id: str
    document_version_id: str
    chunk_id: str
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    embedding_model: Literal["intfloat/multilingual-e5-small"] = EMBEDDING_MODEL
    embedding_revision: Literal["614241f622f53c4eeff9890bdc4f31cfecc418b3"] = EMBEDDING_REVISION

    @field_validator(
        "tenant_id", "workspace_id", "base_id", "document_id", "document_version_id", "chunk_id"
    )
    @classmethod
    def require_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("vector_payload_requires_canonical_uuid")
        return value


def vector_payload(chunk: KnowledgeChunk) -> VectorPayload:
    return VectorPayload(
        tenant_id=str(chunk.scope.tenant_id),
        workspace_id=str(chunk.scope.workspace_id),
        base_id=str(chunk.base_id),
        document_id=str(chunk.document_id),
        document_version_id=str(chunk.document_version_id),
        chunk_id=str(chunk.chunk_id),
        content_hash=chunk.content_hash,
    )


class QdrantVectorIndex:
    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("qdrant_requires_trusted_server_url")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", collection):
            raise ValueError("qdrant_collection_invalid")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError("qdrant_timeout_invalid")
        self._collection = collection
        self._url = url.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = {"api-key": api_key} if api_key else {}
        self._owns_client = client is None
        self._client = client or httpx.Client(trust_env=False, follow_redirects=False)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ensure_collection(self) -> None:
        """Explicit provisioning operation; existing incompatible vectors are never erased."""
        try:
            response = self._client.get(
                f"{self._url}/collections/{self._collection}",
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise RetrievalUnavailable("qdrant_unavailable") from exc
        if response.status_code == 404:
            self._request(
                "PUT",
                "",
                {
                    "vectors": {
                        "size": EMBEDDING_DIMENSIONS,
                        "distance": "Cosine",
                    }
                },
            )
        self._require_collection()

    def _require_collection(self) -> None:
        result = self._request("GET", "")
        if not isinstance(result, dict):
            raise RetrievalUnavailable("qdrant_collection_response_invalid")
        try:
            vectors = result["config"]["params"]["vectors"]
        except (KeyError, TypeError) as exc:
            raise RetrievalUnavailable("qdrant_collection_response_invalid") from exc
        if (
            not isinstance(vectors, dict)
            or vectors.get("size") != EMBEDDING_DIMENSIONS
            or vectors.get("distance") != "Cosine"
        ):
            raise RetrievalUnavailable("qdrant_collection_model_mismatch")

    def upsert(self, chunk: KnowledgeChunk, vector: Sequence[float]) -> str:
        self._require_collection()
        self._require_vector(vector)
        result = self._request(
            "PUT",
            "/points?wait=true",
            {
                "points": [
                    {
                        "id": chunk.point_id,
                        "vector": list(vector),
                        "payload": vector_payload(chunk).model_dump(),
                    }
                ]
            },
        )
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise RetrievalUnavailable("qdrant_write_not_confirmed")
        return chunk.point_id

    def search(
        self,
        scope: RetrievalScope,
        chunks: Sequence[KnowledgeChunk],
        vector: Sequence[float],
        *,
        limit: int,
    ) -> tuple[VectorHit, ...]:
        if not chunks or len(chunks) > 256 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("qdrant_search_bounds_invalid")
        if any(chunk.scope != scope for chunk in chunks):
            raise PermissionError("qdrant_scope_mismatch")
        expected = {chunk.point_id: vector_payload(chunk) for chunk in chunks}
        if len(expected) != len(chunks):
            raise ValueError("qdrant_duplicate_point")
        self._require_vector(vector)
        self._require_collection()
        points = self._request(
            "POST",
            "/points",
            {
                "ids": list(expected),
                "with_payload": True,
                "with_vector": False,
            },
        )
        found = self._validate_points(points, expected)
        if set(found) != set(expected):
            raise RetrievalUnavailable("qdrant_index_missing_or_stale")
        matches = self._request(
            "POST",
            "/points/search",
            {
                "vector": list(vector),
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
                "filter": {
                    "must": [
                        {"key": "tenant_id", "match": {"value": str(scope.tenant_id)}},
                        {"key": "workspace_id", "match": {"value": str(scope.workspace_id)}},
                        {"key": "embedding_model", "match": {"value": EMBEDDING_MODEL}},
                        {"key": "embedding_revision", "match": {"value": EMBEDDING_REVISION}},
                        {"has_id": list(expected)},
                    ]
                },
            },
        )
        validated = self._validate_points(matches, expected)
        if not validated or len(validated) > limit:
            raise RetrievalUnavailable("qdrant_search_response_invalid")
        hits = []
        for point_id, point in validated.items():
            score = point.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not -1.0001 <= score <= 1.0001
            ):
                raise RetrievalUnavailable("qdrant_score_invalid")
            hits.append(VectorHit(point_id, float(score)))
        return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.point_id)))

    @staticmethod
    def _validate_points(
        points: object,
        expected: dict[str, VectorPayload],
    ) -> dict[str, dict[str, object]]:
        if not isinstance(points, list):
            raise RetrievalUnavailable("qdrant_points_response_invalid")
        found: dict[str, dict[str, object]] = {}
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("id"), str):
                raise RetrievalUnavailable("qdrant_point_invalid")
            point_id = point["id"]
            try:
                UUID(point_id)
                payload = VectorPayload.model_validate(point.get("payload"))
            except (ValueError, ValidationError) as exc:
                raise RetrievalUnavailable("qdrant_payload_invalid") from exc
            if point_id in found or expected.get(point_id) != payload:
                raise RetrievalUnavailable("qdrant_payload_mismatch")
            found[point_id] = point
        return found

    @staticmethod
    def _require_vector(vector: Sequence[float]) -> None:
        if len(vector) != EMBEDDING_DIMENSIONS or any(
            type(value) not in {int, float} or not math.isfinite(value) for value in vector
        ):
            raise ValueError("qdrant_embedding_invalid")
        if not math.isclose(sum(value * value for value in vector), 1.0, abs_tol=1e-4):
            raise ValueError("qdrant_embedding_not_normalized")

    def _request(
        self,
        method: str,
        suffix: str,
        body: dict[str, object] | None = None,
    ) -> object:
        try:
            response = self._client.request(
                method,
                f"{self._url}/collections/{self._collection}{suffix}",
                json=body,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            envelope = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # A failed write may already have committed remotely. No automatic retry here.
            raise RetrievalUnavailable("qdrant_request_unconfirmed") from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("status") != "ok"
            or "result" not in envelope
        ):
            raise RetrievalUnavailable("qdrant_response_invalid")
        return envelope["result"]
