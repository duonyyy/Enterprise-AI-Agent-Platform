"""
Ingestion Data Contract & Quarantine DLQ Pipeline (Track 2 Day 17 & Day 27).
Validates incoming knowledge documents against enterprise data contracts.
Corrupt or non-compliant records are diverted into a Dead Letter Queue (Quarantine)
without terminating the batch ingestion job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import enum
import re
from typing import Any, Dict, List, Optional, Tuple


class QuarantineCode(str, enum.Enum):
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    MISSING_TENANT_SCOPE = "MISSING_TENANT_SCOPE"
    CORRUPT_ENCODING = "CORRUPT_ENCODING"
    CONTENT_TOO_SHORT = "CONTENT_TOO_SHORT"
    ZERO_WIDTH_STEGANOGRAPHY_DETECTED = "ZERO_WIDTH_STEGANOGRAPHY_DETECTED"


@dataclass
class ValidatedDocument:
    document_id: str
    tenant_id: str
    workspace_id: str
    content: str
    content_type: str
    metadata: Dict[str, Any]
    validated_at: str


@dataclass
class QuarantineRecord:
    document_id: Optional[str]
    tenant_id: Optional[str]
    workspace_id: Optional[str]
    quarantine_code: QuarantineCode
    reason: str
    raw_preview: str
    quarantined_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionBatchResult:
    valid_documents: List[ValidatedDocument]
    quarantined: List[QuarantineRecord]
    total_processed: int

    @property
    def success_rate(self) -> float:
        if self.total_processed == 0:
            return 1.0
        return len(self.valid_documents) / self.total_processed


class IngestionDataContractGate:
    """
    Enforces Document Data Contract and Quarantine routing.
    """

    ZERO_WIDTH_REGEX = re.compile(r"[\u200B-\u200D\uFEFF]")
    MIN_CONTENT_LENGTH = 10

    @classmethod
    def validate_and_route(
        cls, raw_records: List[Dict[str, Any]]
    ) -> IngestionBatchResult:
        valid_docs: List[ValidatedDocument] = []
        quarantined: List[QuarantineRecord] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for item in raw_records:
            doc_id = item.get("document_id") or item.get("id")
            tenant_id = item.get("tenant_id")
            workspace_id = item.get("workspace_id")
            content = item.get("content")
            content_type = item.get("content_type", "text/plain")
            metadata = item.get("metadata", {})

            # Rule 1: Tenant and Workspace isolation scope
            if not tenant_id or not workspace_id:
                quarantined.append(
                    QuarantineRecord(
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        quarantine_code=QuarantineCode.MISSING_TENANT_SCOPE,
                        reason="Document missing required tenant_id or workspace_id scope",
                        raw_preview=str(content)[:100] if content else "",
                        quarantined_at=now_iso,
                        metadata=metadata,
                    )
                )
                continue

            # Rule 2: Document ID must exist
            if not doc_id:
                quarantined.append(
                    QuarantineRecord(
                        document_id=None,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        quarantine_code=QuarantineCode.SCHEMA_VIOLATION,
                        reason="Document missing required document_id or id identifier",
                        raw_preview=str(content)[:100] if content else "",
                        quarantined_at=now_iso,
                        metadata=metadata,
                    )
                )
                continue

            # Rule 3: Content string validity and length
            if not isinstance(content, str):
                quarantined.append(
                    QuarantineRecord(
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        quarantine_code=QuarantineCode.CORRUPT_ENCODING,
                        reason=f"Document content must be string, received {type(content).__name__}",
                        raw_preview=str(content)[:100],
                        quarantined_at=now_iso,
                        metadata=metadata,
                    )
                )
                continue

            cleaned_content = content.strip()
            if len(cleaned_content) < cls.MIN_CONTENT_LENGTH:
                quarantined.append(
                    QuarantineRecord(
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        quarantine_code=QuarantineCode.CONTENT_TOO_SHORT,
                        reason=f"Content length {len(cleaned_content)} is below minimum {cls.MIN_CONTENT_LENGTH}",
                        raw_preview=cleaned_content[:100],
                        quarantined_at=now_iso,
                        metadata=metadata,
                    )
                )
                continue

            # Rule 4: Zero-width steganography detection
            if cls.ZERO_WIDTH_REGEX.search(cleaned_content):
                # Clean zero-width chars or quarantine if configured
                cleaned_content = cls.ZERO_WIDTH_REGEX.sub("", cleaned_content)
                metadata["sanitized_zero_width"] = True

            valid_docs.append(
                ValidatedDocument(
                    document_id=str(doc_id),
                    tenant_id=str(tenant_id),
                    workspace_id=str(workspace_id),
                    content=cleaned_content,
                    content_type=content_type,
                    metadata=metadata,
                    validated_at=now_iso,
                )
            )

        return IngestionBatchResult(
            valid_documents=valid_docs,
            quarantined=quarantined,
            total_processed=len(raw_records),
        )

