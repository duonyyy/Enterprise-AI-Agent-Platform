"""
Contract and Security Test for Tamper-Evident Audit Ledger (Track 2 Day 24 Control 4).
Verifies cryptographic SHA-256 hash chaining, detection of unauthorized modification,
and resistance to ledger truncation / record insertion.
"""

import pytest
from platform_common.audit_ledger import (
    AuditRecord,
    TamperEvidentLedger,
    GENESIS_HASH,
)


def test_tamper_evident_chain_generation_and_valid_verification():
    ledger = []
    prev_h = GENESIS_HASH

    # 1. First event
    rec1 = TamperEvidentLedger.create_record(
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000002",
        actor_subject="user:alice@corp.vinai.vn",
        action="agent:publish",
        resource_type="catalog.agents",
        resource_id="agent-v1",
        outcome="allowed",
        prev_hash=prev_h,
        details_redacted={"version": "1.0.0", "digest": "sha256-abc"},
    )
    ledger.append(rec1)
    prev_h = rec1.record_hash

    # 2. Second event
    rec2 = TamperEvidentLedger.create_record(
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000002",
        actor_subject="user:bob@corp.vinai.vn",
        action="tool:execute",
        resource_type="catalog.tools",
        resource_id="tool-web-search",
        outcome="succeeded",
        prev_hash=prev_h,
        details_redacted={"latency_ms": 120},
    )
    ledger.append(rec2)
    prev_h = rec2.record_hash

    # 3. Third event
    rec3 = TamperEvidentLedger.create_record(
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000002",
        actor_subject="user:eve@corp.vinai.vn",
        action="tool:execute",
        resource_type="catalog.tools",
        resource_id="tool-db-delete",
        outcome="denied",
        prev_hash=prev_h,
        reason_code="PEP_RESTRICTED_EGRESS_DENIED",
        details_redacted={"policy_rule": "R-EGRESS-01"},
    )
    ledger.append(rec3)

    is_valid, bad_idx, err = TamperEvidentLedger.verify_chain(ledger)
    assert is_valid is True
    assert bad_idx is None
    assert err is None


def test_tamper_detection_on_modified_actor():
    ledger = []
    prev_h = GENESIS_HASH

    rec1 = TamperEvidentLedger.create_record(
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000002",
        actor_subject="user:alice@corp.vinai.vn",
        action="agent:publish",
        resource_type="catalog.agents",
        resource_id="agent-v1",
        outcome="allowed",
        prev_hash=prev_h,
    )
    ledger.append(rec1)

    rec2 = TamperEvidentLedger.create_record(
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000002",
        actor_subject="user:mallory@adversary.com",
        action="secret:read",
        resource_type="runtime.secrets",
        resource_id="sec-jwt",
        outcome="denied",
        prev_hash=rec1.record_hash,
    )
    ledger.append(rec2)

    # Mallory attempts to tamper with the audit record in DB, changing actor to 'user:admin'
    tampered_rec2 = AuditRecord(
        id=rec2.id,
        tenant_id=rec2.tenant_id,
        workspace_id=rec2.workspace_id,
        actor_subject="user:admin@corp.vinai.vn",  # Tampered!
        action=rec2.action,
        resource_type=rec2.resource_type,
        resource_id=rec2.resource_id,
        outcome=rec2.outcome,
        occurred_at=rec2.occurred_at,
        prev_hash=rec2.prev_hash,
        record_hash=rec2.record_hash,
        details_redacted=rec2.details_redacted,
    )
    tampered_ledger = [rec1, tampered_rec2]

    is_valid, bad_idx, err = TamperEvidentLedger.verify_chain(tampered_ledger)
    assert is_valid is False
    assert bad_idx == 1
    assert "Tampered record at #1" in err


def test_tamper_detection_on_deleted_record_gap():
    ledger = []
    prev_h = GENESIS_HASH

    for i in range(3):
        rec = TamperEvidentLedger.create_record(
            tenant_id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000002",
            actor_subject=f"user:user{i}@corp.vinai.vn",
            action="item:read",
            resource_type="knowledge.bases",
            resource_id=f"kb-{i}",
            outcome="allowed",
            prev_hash=prev_h,
        )
        ledger.append(rec)
        prev_h = rec.record_hash

    # Attacker drops middle record #1
    tampered_ledger = [ledger[0], ledger[2]]

    is_valid, bad_idx, err = TamperEvidentLedger.verify_chain(tampered_ledger)
    assert is_valid is False
    assert bad_idx == 1
    assert "Chain break at record #1" in err

