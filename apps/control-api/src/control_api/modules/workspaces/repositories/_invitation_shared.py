"""Shared PostgreSQL helpers for invitation locking, outbox dispatch, audit logs, and records."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationRateLimitError,
    InvitationValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.models.rbac import can_manage_invitations
from control_api.modules.workspaces.models.records import InvitationRecord
from control_api.modules.workspaces.repositories._db_helpers import (
    db_unavailable,
    decode_keyset_cursor,
    encode_keyset_cursor,
    format_timestamp,
    is_active_workspace_in_tenant,
)
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import (
    INVITATION_AGGREGATE_TYPE,
    INVITATION_DELIVERY_EVENT_TYPE,
    INVITATION_OUTBOX_TOPIC,
    InvitationDeliveryRequestedV1,
)
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


def invitation_db_unavailable(exc: Exception) -> WorkspaceDatabaseUnavailableError:
    """Translate SQLAlchemy persistence failures into structured domain unavailable errors."""
    return db_unavailable(
        exc,
        operation="invitation_persistence",
        message="Workspace invitation persistence service temporarily unavailable",
    )


def encode_invitation_cursor(created_at: datetime, invitation_id: str) -> str:
    """Encode deterministic `(created_at, id)` pagination cursor for invitations."""
    return encode_keyset_cursor("created_at", created_at, invitation_id)


def decode_invitation_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode and validate invitation keyset pagination cursor."""
    return decode_keyset_cursor(
        cursor,
        ts_field="created_at",
        error_factory=InvitationValidationError,
    )


def build_invitation_record(
    row: Mapping[Any, Any] | RowMapping,
    *,
    audit_event_id: str | None = None,
    outbox_event_id: str | None = None,
) -> InvitationRecord:
    """Build immutable `InvitationRecord` dataclass from DB mapping row."""
    return InvitationRecord(
        invitation_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        workspace_id=str(row["workspace_id"]),
        recipient_email_normalized=str(row["recipient_email_normalized"]),
        recipient_issuer=str(row["recipient_issuer"]),
        role=str(row["role"]),
        status=str(row.get("effective_status", row["status"])),
        generation=int(row["generation"]),
        delivery_state=str(row["delivery_state"]),
        delivery_error_code=(
            str(row["delivery_error_code"]) if row["delivery_error_code"] is not None else None
        ),
        created_by_user_id=str(row["created_by_user_id"]),
        delivery_requested_by_user_id=str(row["delivery_requested_by_user_id"]),
        revoked_by_user_id=(
            str(row["revoked_by_user_id"]) if row["revoked_by_user_id"] is not None else None
        ),
        accepted_by_user_id=(
            str(row["accepted_by_user_id"]) if row["accepted_by_user_id"] is not None else None
        ),
        expires_at=format_timestamp(row["expires_at"]) or "",
        created_at=format_timestamp(row["created_at"]) or "",
        updated_at=format_timestamp(row["updated_at"]) or "",
        provider_accepted_at=format_timestamp(row["provider_accepted_at"]),
        revoked_at=format_timestamp(row["revoked_at"]),
        accepted_at=format_timestamp(row["accepted_at"]),
        audit_event_id=audit_event_id,
        outbox_event_id=outbox_event_id,
    )


async def lock_workspace_and_actor(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    actor_user_id: UUID,
    lock_workspace: bool,
) -> str:
    """Lock workspace (if mutating) then actor's active membership; return actor role."""
    is_active = await is_active_workspace_in_tenant(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lock_mode="update" if lock_workspace else "share",
    )
    if not is_active:
        raise WorkspaceAccessDeniedError("Workspace is not active or accessible")

    actor_stmt = text(
        "SELECT role, status "
        "FROM iam.memberships "
        "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND user_id = :user_id "
        "FOR SHARE"
    )
    actor_row = (
        (
            await session.execute(
                actor_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "user_id": actor_user_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if actor_row is None or str(actor_row["status"]) != "active":
        raise InvitationForbiddenError(
            "active_membership_required",
            "Active workspace membership required",
        )
    actor_role = str(actor_row["role"])
    if not can_manage_invitations(actor_role):
        raise InvitationForbiddenError(
            "invitation_forbidden",
            "Only workspace owners and admins can manage invitations",
        )
    return actor_role


async def assert_no_recipient_membership(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    recipient_issuer: str,
    recipient_email_normalized: str,
) -> None:
    """Reject invitation creation/resend when the recipient already has a workspace membership."""
    stmt = text(
        "SELECT m.id "
        "FROM iam.memberships AS m "
        "JOIN iam.users AS u ON u.id = m.user_id "
        "WHERE m.tenant_id = :tenant_id "
        "  AND m.workspace_id = :workspace_id "
        "  AND u.issuer = :recipient_issuer "
        "  AND u.email_normalized = :recipient_email "
        "LIMIT 1"
    )
    existing = (
        (
            await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "recipient_issuer": recipient_issuer,
                    "recipient_email": recipient_email_normalized,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        raise InvitationConflictError(
            "membership_conflict",
            "Recipient already has a membership record in this workspace",
        )


async def dead_letter_outbox_for_invitations(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_ids: list[UUID],
    states: tuple[str, ...],
) -> None:
    """Mark pending/delivering outbox entries dead in a single batched UPDATE."""
    if not invitation_ids:
        return
    await session.execute(
        text(
            "UPDATE integration.outbox_events "
            "SET state = 'dead' "
            "WHERE tenant_id = :tenant_id "
            "  AND workspace_id = :workspace_id "
            "  AND aggregate_type = :aggregate_type "
            "  AND aggregate_id = ANY(:invitation_ids) "
            "  AND state = ANY(:states)"
        ),
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "aggregate_type": INVITATION_AGGREGATE_TYPE,
            "invitation_ids": invitation_ids,
            "states": list(states),
        },
    )


async def enforce_invitation_rate_limits(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    recipient_issuer: str,
    recipient_email_normalized: str,
    workspace_hourly_limit: int,
    recipient_hourly_limit: int,
) -> None:
    """Enforce workspace-level and recipient-level hourly invitation enqueue quotas."""
    ws_stmt = text(
        "SELECT count(*) AS cnt, "
        "       EXTRACT(EPOCH FROM (MIN(created_at) + interval '60 minutes' - now())) AS retry_after "
        "FROM integration.outbox_events "
        "WHERE tenant_id = :tenant_id "
        "  AND workspace_id = :workspace_id "
        "  AND event_type = :event_type "
        "  AND created_at > now() - interval '60 minutes'"
    )
    ws_row = (
        (
            await session.execute(
                ws_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "event_type": INVITATION_DELIVERY_EVENT_TYPE,
                },
            )
        )
        .mappings()
        .one()
    )
    if int(ws_row["cnt"] or 0) >= workspace_hourly_limit:
        retry_after = max(1, math.ceil(float(ws_row["retry_after"] or 60.0)))
        raise InvitationRateLimitError(
            "workspace_rate_limit_exceeded",
            "Workspace hourly invitation limit exceeded",
            retry_after_seconds=retry_after,
        )

    rec_stmt = text(
        "SELECT count(*) AS cnt, "
        "       EXTRACT(EPOCH FROM (MIN(o.created_at) + interval '60 minutes' - now())) AS retry_after "
        "FROM integration.outbox_events AS o "
        "JOIN iam.workspace_invitations AS i "
        "  ON i.tenant_id = o.tenant_id "
        " AND i.workspace_id = o.workspace_id "
        " AND i.id = o.aggregate_id "
        "WHERE o.tenant_id = :tenant_id "
        "  AND o.workspace_id = :workspace_id "
        "  AND o.event_type = :event_type "
        "  AND o.created_at > now() - interval '60 minutes' "
        "  AND i.recipient_issuer = :recipient_issuer "
        "  AND i.recipient_email_normalized = :recipient_email"
    )
    rec_row = (
        (
            await session.execute(
                rec_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "event_type": INVITATION_DELIVERY_EVENT_TYPE,
                    "recipient_issuer": recipient_issuer,
                    "recipient_email": recipient_email_normalized,
                },
            )
        )
        .mappings()
        .one()
    )
    if int(rec_row["cnt"] or 0) >= recipient_hourly_limit:
        retry_after = max(1, math.ceil(float(rec_row["retry_after"] or 60.0)))
        raise InvitationRateLimitError(
            "recipient_rate_limit_exceeded",
            "Recipient hourly invitation limit exceeded",
            retry_after_seconds=retry_after,
        )


async def insert_invitation_audit_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    actor_user_id: UUID,
    action: str,
    invitation_id: UUID,
    request_id: str,
    trace_id: str,
    details: dict[str, Any],
) -> str:
    """Append an immutable governance audit log record for an invitation lifecycle action."""
    stmt = text(
        "INSERT INTO governance.audit_events ("
        "    tenant_id, workspace_id, actor_subject, action, "
        "    resource_type, resource_id, outcome, request_id, "
        "    trace_id, details_redacted"
        ") VALUES ("
        "    :tenant_id, :workspace_id, :actor_subject, :action, "
        "    'workspace_invitation', :resource_id, 'succeeded', :request_id, "
        "    :trace_id, CAST(:details_redacted AS jsonb)"
        ") RETURNING id"
    )
    row = (
        (
            await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "actor_subject": str(actor_user_id),
                    "action": action,
                    "resource_id": str(invitation_id),
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "details_redacted": json.dumps(details, sort_keys=True),
                },
            )
        )
        .mappings()
        .one()
    )
    return str(row["id"])


async def publish_delivery_outbox(
    session: AsyncSession,
    *,
    outbox_bus: PostgresOutboxEventBus,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    generation: int,
    actor_user_id: UUID,
    request_id: str,
    trace_id: str,
) -> str:
    """Enqueue an outbox delivery event strictly inside the open DB transaction."""
    event = InvitationDeliveryRequestedV1(
        invitation_id=invitation_id,
        generation=generation,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        delivery_requested_by_user_id=actor_user_id,
        requested_at=datetime.now(UTC),
    )
    event_id: str = str(
        await outbox_bus.publish_in_transaction(
            session,
            topic=INVITATION_OUTBOX_TOPIC,
            key=f"{tenant_id}:{workspace_id}",
            payload=event.model_dump(mode="json"),
            headers={"request_id": request_id, "trace_id": trace_id},
            aggregate_type=INVITATION_AGGREGATE_TYPE,
            aggregate_id=invitation_id,
            set_rls=False,
        )
    )
    return event_id
