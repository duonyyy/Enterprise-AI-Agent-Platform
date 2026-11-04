"""Real BM25 + E5/Qdrant with RRF, scoped to authorized source snapshots.

No in-process corpus, simulated semantic scores or lexical-only outage fallback.
This service-owned component is not exposed as a public route/Tool.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .retrieval_types import (
    EmbeddingPort,
    KnowledgeAuthorization,
    KnowledgeChunk,
    RetrievalHit,
    RetrievalScope,
    RetrievalUnavailable,
    VectorIndex,
)

MAX_RETRIEVAL_CHUNKS = 256
MAX_TOP_K = 20
MAX_QUERY_BYTES = 8_192


class HybridRetriever:
    def __init__(
        self,
        *,
        embeddings: EmbeddingPort,
        index: VectorIndex,
        authorization: KnowledgeAuthorization,
        rrf_k: int = 60,
    ) -> None:
        if type(rrf_k) is not int or rrf_k < 1:
            raise ValueError("rrf_k_must_be_positive_integer")
        self._embeddings = embeddings
        self._index = index
        self._authorization = authorization
        self._rrf_k = rrf_k

    def index_document(self, chunk: KnowledgeChunk) -> str:
        self._authorization.require_index(chunk)
        vectors = self._embeddings.embed_passages((chunk.text,))
        self._authorization.require_index(chunk)
        return self._index.upsert(chunk, vectors[0])

    def search(
        self,
        query: str,
        *,
        scope: RetrievalScope,
        authorized_chunks: Sequence[KnowledgeChunk],
        top_k: int = 5,
    ) -> tuple[RetrievalHit, ...]:
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise ValueError("retrieval_top_k_out_of_range")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("retrieval_query_empty")
        if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
            raise ValueError("retrieval_query_too_large")
        if len(authorized_chunks) > MAX_RETRIEVAL_CHUNKS:
            raise ValueError("retrieval_candidate_limit_exceeded")
        if any(chunk.scope != scope for chunk in authorized_chunks):
            raise PermissionError("retrieval_scope_mismatch")
        if len({chunk.chunk_id for chunk in authorized_chunks}) != len(authorized_chunks):
            raise ValueError("retrieval_duplicate_chunk")
        chunks = self._recheck(scope, authorized_chunks)
        if not chunks:
            return ()
        dense = self._index.search(
            scope, chunks, self._embeddings.embed_query(query), limit=min(len(chunks), MAX_TOP_K)
        )
        lexical = self._lexical(query, chunks)
        by_point = {chunk.point_id: chunk for chunk in chunks}
        scores: dict[str, float] = {}
        dense_scores: dict[str, float] = {}
        for rank, hit in enumerate(dense, 1):
            if hit.point_id not in by_point or hit.point_id in dense_scores:
                raise RetrievalUnavailable("retrieval_index_returned_unexpected_point")
            dense_scores[hit.point_id] = hit.score
            scores[hit.point_id] = 1.0 / (self._rrf_k + rank)
        lexical_scores = dict(lexical)
        for rank, (point_id, _) in enumerate(lexical, 1):
            scores[point_id] = scores.get(point_id, 0.0) + 1.0 / (self._rrf_k + rank)
        ranked = sorted(scores, key=lambda point_id: (-scores[point_id], point_id))
        still_allowed = {chunk.point_id for chunk in self._recheck(scope, chunks)}
        return tuple(
            RetrievalHit(
                by_point[point_id],
                scores[point_id],
                dense_scores.get(point_id),
                lexical_scores.get(point_id),
            )
            for point_id in ranked
            if point_id in still_allowed
        )[:top_k]

    def _recheck(
        self, scope: RetrievalScope, chunks: Sequence[KnowledgeChunk]
    ) -> tuple[KnowledgeChunk, ...]:
        checked = tuple(self._authorization.recheck(scope, chunks))
        original = {chunk.point_id: chunk for chunk in chunks}
        if any(chunk.scope != scope or original.get(chunk.point_id) != chunk for chunk in checked):
            raise PermissionError("retrieval_authorization_snapshot_changed")
        if len({chunk.point_id for chunk in checked}) != len(checked):
            raise PermissionError("retrieval_authorization_duplicate_chunk")
        return checked

    @staticmethod
    def _lexical(query: str, chunks: Sequence[KnowledgeChunk]) -> list[tuple[str, float]]:
        try:
            from rank_bm25 import BM25Plus
        except ImportError as exc:
            raise RetrievalUnavailable("retrieval_bm25_dependency_missing") from exc
        terms = re.findall(r"\w+", query.casefold())
        corpus = [re.findall(r"\w+", chunk.text.casefold()) for chunk in chunks]
        if not terms or not any(corpus):
            return []
        values = BM25Plus(corpus).get_scores(terms)
        scores = [
            (chunk.point_id, float(value))
            for chunk, tokens, value in zip(chunks, corpus, values, strict=True)
            if set(tokens).intersection(terms)
        ]
        return sorted(scores, key=lambda pair: (-pair[1], pair[0]))[:MAX_TOP_K]
