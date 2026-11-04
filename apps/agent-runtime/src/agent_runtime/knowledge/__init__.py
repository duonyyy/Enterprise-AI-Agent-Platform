"""
Knowledge utilities and scoped E5/Qdrant retrieval adapters.
"""

from .citation_verifier import CitationVerifier, InvalidCitationError
from .delta_audit_ledger import DeltaAuditLedger
from .embeddings import E5EmbeddingModel, EmbeddingUnavailable
from .feast_store import FeastDualStore
from .hybrid_retriever import HybridRetriever
from .ingestion_quarantine import (
    IngestionBatchResult,
    IngestionDataContractGate,
    QuarantineCode,
    QuarantineRecord,
    ValidatedDocument,
)
from .qdrant_index import QdrantVectorIndex
from .retrieval_types import KnowledgeChunk, RetrievalHit, RetrievalScope, RetrievalUnavailable

__all__ = [
    "CitationVerifier",
    "DeltaAuditLedger",
    "E5EmbeddingModel",
    "EmbeddingUnavailable",
    "FeastDualStore",
    "HybridRetriever",
    "IngestionBatchResult",
    "IngestionDataContractGate",
    "InvalidCitationError",
    "KnowledgeChunk",
    "QdrantVectorIndex",
    "QuarantineCode",
    "QuarantineRecord",
    "RetrievalHit",
    "RetrievalScope",
    "RetrievalUnavailable",
    "ValidatedDocument",
]
