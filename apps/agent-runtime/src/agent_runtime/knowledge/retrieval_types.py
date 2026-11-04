"""Internal ports. A scope/vector payload never constitutes an ACL grant."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .embeddings import EMBEDDING_MODEL, EMBEDDING_REVISION, MAX_TEXT_BYTES


class RetrievalUnavailable(RuntimeError):
    """Missing, stale or unavailable index; no lexical-only outage fallback."""


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    tenant_id: UUID
    workspace_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.workspace_id, UUID):
            raise TypeError("retrieval_scope_requires_uuid")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """Already-authorized source snapshot supplied by a trusted server caller."""

    scope: RetrievalScope
    base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RetrievalScope) or not all(
            isinstance(value, UUID)
            for value in (self.base_id, self.document_id, self.document_version_id, self.chunk_id)
        ):
            raise TypeError("knowledge_chunk_requires_uuid")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("knowledge_chunk_text_empty")
        if len(self.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("knowledge_chunk_text_too_large")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def point_id(self) -> str:
        identity = ":".join(
            str(value)
            for value in (
                self.scope.tenant_id,
                self.scope.workspace_id,
                self.base_id,
                self.document_id,
                self.document_version_id,
                self.chunk_id,
                self.content_hash,
                EMBEDDING_MODEL,
                EMBEDDING_REVISION,
            )
        )
        return str(uuid5(NAMESPACE_URL, identity))


@dataclass(frozen=True, slots=True)
class VectorHit:
    point_id: str
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    chunk: KnowledgeChunk
    score: float
    dense_score: float | None
    bm25_score: float | None


class KnowledgeAuthorization(Protocol):
    """Trusted IAM/published-source checks; no allow-all default adapter is supplied."""

    def require_index(self, chunk: KnowledgeChunk) -> None: ...

    def recheck(
        self, scope: RetrievalScope, chunks: Sequence[KnowledgeChunk]
    ) -> Sequence[KnowledgeChunk]: ...


class EmbeddingPort(Protocol):
    def embed_query(self, text: str) -> tuple[float, ...]: ...

    def embed_passages(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class VectorIndex(Protocol):
    def upsert(self, chunk: KnowledgeChunk, vector: Sequence[float]) -> str: ...

    def search(
        self,
        scope: RetrievalScope,
        chunks: Sequence[KnowledgeChunk],
        vector: Sequence[float],
        *,
        limit: int,
    ) -> Sequence[VectorHit]: ...
