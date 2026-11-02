"""
Data Substrate Package: Delta Lake ACID Audit Store, Feast Feature Store, and Qdrant Hybrid RRF.
"""

from .delta_audit_ledger import DeltaAuditLedger
from .feast_store import FeastDualStore
from .hybrid_retriever import HybridRetriever
from .citation_verifier import CitationVerifier, InvalidCitationError
from .ingestion_quarantine import (
    IngestionDataContractGate,
    ValidatedDocument,
    QuarantineRecord,
    QuarantineCode,
    IngestionBatchResult,
)

__all__ = [
    "DeltaAuditLedger",
    "FeastDualStore",
    "HybridRetriever",
    "CitationVerifier",
    "InvalidCitationError",
    "IngestionDataContractGate",
    "ValidatedDocument",
    "QuarantineRecord",
    "QuarantineCode",
    "IngestionBatchResult",
]

