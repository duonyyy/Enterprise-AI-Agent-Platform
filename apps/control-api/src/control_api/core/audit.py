"""Shared transactional audit writer for Control API governance events.

Authority:
- ADR-005: Trusted server-side audit trails bound to PostgreSQL transaction boundaries.
- CODING_STANDARDS_AND_ARCHITECTURE_RULES: SEC-003, DB-004.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

AuditOutcome = Literal["allowed", "denied", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class AuditEventWrite:
    """Strongly-typed DTO for appending an immutable record to governance.audit_events.

    Notice: This DTO represents the transactional DB-56 ``governance.audit_events`` row,
    distinct from the cryptographic tamper-evident hash-chain AuditRecord in platform_common.
    """

    tenant_id: UUID
    workspace_id: UUID
    actor_subject: str
    action: str
    resource_type: str
    resource_id: str
    outcome: AuditOutcome = "succeeded"
    request_id: str | None = None
    trace_id: str | None = None
    reason_code: str | None = None
    details_redacted: dict[str, Any] | None = None


async def write_audit_event(
    session: AsyncSession,
    event: AuditEventWrite,
) -> str:
    """Insert an immutable audit event within the caller's active transaction.

    Returns the generated audit event UUID string.
    Does NOT commit, rollback, or alter the session's RLS context.
    Errors propagate directly to the caller to preserve transactional atomicity.
    """
    details_json = json.dumps(event.details_redacted or {}, sort_keys=True)
    stmt = text(
        "INSERT INTO governance.audit_events ("
        "    tenant_id, workspace_id, actor_subject, action, "
        "    resource_type, resource_id, outcome, request_id, "
        "    trace_id, reason_code, details_redacted"
        ") VALUES ("
        "    :tenant_id, :workspace_id, :actor_subject, :action, "
        "    :resource_type, :resource_id, :outcome, :request_id, "
        "    :trace_id, :reason_code, CAST(:details_redacted AS jsonb)"
        ") RETURNING id"
    )
    result = await session.execute(
        stmt,
        {
            "tenant_id": event.tenant_id,
            "workspace_id": event.workspace_id,
            "actor_subject": event.actor_subject,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "outcome": event.outcome,
            "request_id": event.request_id,
            "trace_id": event.trace_id,
            "reason_code": event.reason_code,
            "details_redacted": details_json,
        },
    )
    row = result.mappings().one()
    return str(row["id"])


__all__ = ["AuditEventWrite", "AuditOutcome", "write_audit_event"]
