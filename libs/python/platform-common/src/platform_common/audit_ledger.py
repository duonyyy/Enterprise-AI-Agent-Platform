"""
Tamper-Evident Audit Ledger (Track 2 Day 24, SOC2 / EU AI Act Art. 12 Compliance).
Implements cryptographic SHA-256 hash-chaining over immutable audit logs,
guaranteeing mathematical detection of any unauthorized modification or deletion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple, Union

from platform_common.errors import PlatformError


class AuditIntegrityError(PlatformError):
    """Raised when an audit ledger verification fails due to data tampering."""
    pass


GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditRecord:
    tenant_id: str
    workspace_id: str
    actor_subject: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str  # 'allowed' | 'denied' | 'succeeded' | 'failed'
    occurred_at: str
    prev_hash: str
    record_hash: str
    id: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    reason_code: Optional[str] = None
    details_redacted: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "actor_subject": self.actor_subject,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "reason_code": self.reason_code,
            "occurred_at": self.occurred_at,
            "prev_hash": self.prev_hash,
            "record_hash": self.record_hash,
            "details_redacted": self.details_redacted,
        }


class TamperEvidentLedger:
    """
    Cryptographic auditor verifying immutability and provenance of governance.audit_events.
    """

    @staticmethod
    def compute_hash(
        prev_hash: str,
        occurred_at: str,
        tenant_id: str,
        workspace_id: str,
        actor_subject: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        details_redacted: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Computes SHA-256 over canonical string representation.
        """
        canonical_details = json.dumps(details_redacted or {}, sort_keys=True, separators=(",", ":"))
        raw = (
            f"{prev_hash}|{occurred_at}|{tenant_id}|{workspace_id}|"
            f"{actor_subject}|{action}|{resource_type}|{resource_id}|{outcome}|{canonical_details}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def create_record(
        cls,
        tenant_id: str,
        workspace_id: str,
        actor_subject: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        prev_hash: str = GENESIS_HASH,
        details_redacted: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        reason_code: Optional[str] = None,
        occurred_at: Optional[str] = None,
        id: Optional[str] = None,
    ) -> AuditRecord:
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc).isoformat()

        cleaned_details = dict(details_redacted or {})
        # Remove any existing hash keys in payload to avoid recursive hashing
        cleaned_details.pop("ledger_prev_hash", None)
        cleaned_details.pop("ledger_record_hash", None)

        record_hash = cls.compute_hash(
            prev_hash=prev_hash,
            occurred_at=occurred_at,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_subject=actor_subject,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details_redacted=cleaned_details,
        )

        # Store cryptographic hashes in details_redacted for DB compatibility
        db_details = dict(cleaned_details)
        db_details["ledger_prev_hash"] = prev_hash
        db_details["ledger_record_hash"] = record_hash

        return AuditRecord(
            id=id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_subject=actor_subject,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            request_id=request_id,
            trace_id=trace_id,
            reason_code=reason_code,
            occurred_at=occurred_at,
            prev_hash=prev_hash,
            record_hash=record_hash,
            details_redacted=db_details,
        )

    @classmethod
    def verify_chain(
        cls,
        records: List[Union[AuditRecord, Dict[str, Any]]],
        expected_genesis_hash: str = GENESIS_HASH,
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Verifies mathematical integrity of an ordered sequence of audit records.
        Returns:
            (is_valid, tampered_index, error_message)
        """
        if not records:
            return True, None, None

        current_expected_prev = expected_genesis_hash

        for idx, item in enumerate(records):
            if isinstance(item, AuditRecord):
                rec = item
                details = dict(rec.details_redacted)
            else:
                details = dict(item.get("details_redacted", {}))
                prev_h = details.get("ledger_prev_hash", item.get("prev_hash", ""))
                rec_h = details.get("ledger_record_hash", item.get("record_hash", ""))
                rec = AuditRecord(
                    id=item.get("id"),
                    tenant_id=item["tenant_id"],
                    workspace_id=item["workspace_id"],
                    actor_subject=item["actor_subject"],
                    action=item["action"],
                    resource_type=item["resource_type"],
                    resource_id=item["resource_id"],
                    outcome=item["outcome"],
                    occurred_at=item["occurred_at"],
                    prev_hash=prev_h,
                    record_hash=rec_h,
                    request_id=item.get("request_id"),
                    trace_id=item.get("trace_id"),
                    reason_code=item.get("reason_code"),
                    details_redacted=details,
                )

            # Check 1: prev_hash must link to preceding record_hash
            if rec.prev_hash != current_expected_prev:
                return (
                    False,
                    idx,
                    f"Chain break at record #{idx}: prev_hash '{rec.prev_hash}' does not match "
                    f"expected '{current_expected_prev}'",
                )

            # Check 2: recalculate hash over payload
            cleaned_details = dict(rec.details_redacted)
            cleaned_details.pop("ledger_prev_hash", None)
            cleaned_details.pop("ledger_record_hash", None)

            calculated = cls.compute_hash(
                prev_hash=rec.prev_hash,
                occurred_at=rec.occurred_at,
                tenant_id=rec.tenant_id,
                workspace_id=rec.workspace_id,
                actor_subject=rec.actor_subject,
                action=rec.action,
                resource_type=rec.resource_type,
                resource_id=rec.resource_id,
                outcome=rec.outcome,
                details_redacted=cleaned_details,
            )

            if calculated != rec.record_hash:
                return (
                    False,
                    idx,
                    f"Tampered record at #{idx}: record_hash '{rec.record_hash}' contradicts "
                    f"recalculated SHA-256 '{calculated}'",
                )

            current_expected_prev = rec.record_hash

        return True, None, None

