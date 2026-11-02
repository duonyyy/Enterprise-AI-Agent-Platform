"""
Unit and Contract Test for Ingestion Data Contract & Quarantine DLQ (Track 2 Day 17 & Day 27).
Verifies that batch ingestion never crashes on invalid documents, properly isolates
corrupt/violating records to Quarantine DLQ, and sanitizes zero-width steganography.
"""

import pytest
from agent_runtime.knowledge.ingestion_quarantine import (
    IngestionDataContractGate,
    QuarantineCode,
)


def test_ingestion_quarantine_batch_handling():
    records = [
        # 1. Valid record
        {
            "id": "doc-01",
            "tenant_id": "t-001",
            "workspace_id": "ws-001",
            "content": "Đây là tài liệu quy trình vận hành Agent hợp lệ và đầy đủ nội dung.",
            "content_type": "text/markdown",
            "metadata": {"author": "VinAI Ops"},
        },
        # 2. Corrupt encoding / non-string content
        {
            "id": "doc-02",
            "tenant_id": "t-001",
            "workspace_id": "ws-001",
            "content": 1234567,  # Bad content type
            "content_type": "text/plain",
        },
        # 3. Missing tenant scope (Security boundary violation)
        {
            "id": "doc-03",
            "content": "Tài liệu này không có tenant scope nên phải bị cách ly.",
            "content_type": "text/plain",
        },
        # 4. Content too short (< 10 chars)
        {
            "id": "doc-04",
            "tenant_id": "t-001",
            "workspace_id": "ws-001",
            "content": "Ngắn",
            "content_type": "text/plain",
        },
        # 5. Valid record with zero-width characters (Steganography injection attack)
        {
            "id": "doc-05",
            "tenant_id": "t-001",
            "workspace_id": "ws-001",
            "content": "Nội dung chuẩn\u200b\u200c có chứa ký tự tàng hình zero-width.",
            "content_type": "text/plain",
        },
    ]

    result = IngestionDataContractGate.validate_and_route(records)

    assert result.total_processed == 5
    assert len(result.valid_documents) == 2  # doc-01 and doc-05
    assert len(result.quarantined) == 3      # doc-02, doc-03, doc-04
    assert result.success_rate == 0.4

    # Verify zero-width sanitization on doc-05
    sanitized_doc = next(d for d in result.valid_documents if d.document_id == "doc-05")
    assert "\u200b" not in sanitized_doc.content
    assert "\u200c" not in sanitized_doc.content
    assert sanitized_doc.metadata.get("sanitized_zero_width") is True

    # Verify quarantine codes
    q_map = {q.document_id: q for q in result.quarantined}
    assert q_map["doc-02"].quarantine_code == QuarantineCode.CORRUPT_ENCODING
    assert q_map["doc-03"].quarantine_code == QuarantineCode.MISSING_TENANT_SCOPE
    assert q_map["doc-04"].quarantine_code == QuarantineCode.CONTENT_TOO_SHORT
