"""PostgreSQL repository for workspace invitations (ADR-019 / Step 03-B)."""

from __future__ import annotations

import base64
import hmac
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from control_api.core.database import AsyncSessionLocal, set_rls_context
from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationGoneError,
    InvitationNotFoundError,
    InvitationRateLimitError,
    InvitationValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceDatabaseUnavailableError,
)
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import (
    INVITATION_AGGREGATE_TYPE,
    INVITATION_DELIVERY_EVENT_TYPE,
    INVITATION_OUTBOX_TOPIC,
    InvitationDeliveryRequestedV1,
    InvitationProofBinding,
    InvitationProofProtector,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _is_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "23505":
        return True
    msg = str(orig or exc).lower()
    return "unique" in msg or "duplicate key" in msg


def _db_unavailable(exc: Exception) -> WorkspaceDatabaseUnavailableError:
    logger.warning(
        "Workspace invitation database operation failed (%s)",
        type(exc).__name__,
    )
    return WorkspaceDatabaseUnavailableError(
        "Workspace invitation persistence service temporarily unavailable"
    )


def _format_ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def encode_invitation_cursor(created_at: datetime, invitation_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.astimezone(UTC).isoformat(), "id": invitation_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_invitation_cursor(cursor: str) -> tuple[datetime, UUID]:
    if not cursor or len(cursor) > 256:
        raise InvitationValidationError("invalid_cursor", "Invalid pagination cursor")
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Cursor payload must be a JSON object")
        created_at = datetime.fromisoformat(str(data["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        invitation_id = UUID(str(data["id"]))
        return created_at, invitation_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise InvitationValidationError("invalid_cursor", "Invalid pagination cursor") from exc


@dataclass(frozen=True, slots=True)
class InvitationPolicyConfig:
    """Bounded operational limits for invitation creation and resend."""

    ttl_hours: int
    resend_cooldown_seconds: int
    recipient_hourly_limit: int
    workspace_hourly_limit: int


class InvitationRepository:
    """Atomic transactional repository for workspace invitations under strict RLS and lock ordering."""

    def __init__(self, outbox_bus: PostgresOutboxEventBus | None = None) -> None:
        self._outbox_bus = outbox_bus or PostgresOutboxEventBus(AsyncSessionLocal)

    async def _lock_workspace_and_actor(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        lock_workspace: bool,
    ) -> str:
        """Lock workspace (if mutating) then actor's active membership; return actor role."""
        ws_lock_clause = "FOR UPDATE OF w" if lock_workspace else "FOR SHARE OF w"
        ws_stmt = text(
            "SELECT w.id, w.status AS workspace_status, t.status AS tenant_status "
            "FROM iam.workspaces AS w "
            "JOIN iam.tenants AS t ON t.id = w.tenant_id "
            "WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id "
            f"{ws_lock_clause}"
        )
        ws_row = (
            (
                await session.execute(
                    ws_stmt,
                    {"tenant_id": tenant_id, "workspace_id": workspace_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            ws_row is None
            or str(ws_row["workspace_status"]) != "active"
            or str(ws_row["tenant_status"]) != "active"
        ):
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
        if actor_role not in {"owner", "admin"}:
            raise InvitationForbiddenError(
                "invitation_forbidden",
                "Only workspace owners and admins can manage invitations",
            )
        return actor_role

    async def _assert_no_recipient_membership(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        recipient_issuer: str,
        recipient_email_normalized: str,
    ) -> None:
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

    async def _dead_letter_outbox_for_invitations(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_ids: list[UUID],
        states: tuple[str, ...],
    ) -> None:
        if not invitation_ids:
            return
        for inv_id in invitation_ids:
            await session.execute(
                text(
                    "UPDATE integration.outbox_events "
                    "SET state = 'dead' "
                    "WHERE tenant_id = :tenant_id "
                    "  AND workspace_id = :workspace_id "
                    "  AND aggregate_type = :aggregate_type "
                    "  AND aggregate_id = :aggregate_id "
                    "  AND state = ANY(:states)"
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "aggregate_type": INVITATION_AGGREGATE_TYPE,
                    "aggregate_id": inv_id,
                    "states": list(states),
                },
            )

    async def _enforce_rate_limits(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        recipient_issuer: str,
        recipient_email_normalized: str,
        policy: InvitationPolicyConfig,
    ) -> None:
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
        if int(ws_row["cnt"] or 0) >= policy.workspace_hourly_limit:
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
        if int(rec_row["cnt"] or 0) >= policy.recipient_hourly_limit:
            retry_after = max(1, math.ceil(float(rec_row["retry_after"] or 60.0)))
            raise InvitationRateLimitError(
                "recipient_rate_limit_exceeded",
                "Recipient hourly invitation limit exceeded",
                retry_after_seconds=retry_after,
            )

    async def _insert_audit_event(
        self,
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

    async def _publish_delivery_outbox(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        generation: int,
        actor_user_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> str:
        event = InvitationDeliveryRequestedV1(
            invitation_id=invitation_id,
            generation=generation,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            delivery_requested_by_user_id=actor_user_id,
            requested_at=datetime.now(UTC),
        )
        return await self._outbox_bus.publish_in_transaction(
            session,
            topic=INVITATION_OUTBOX_TOPIC,
            key=f"{tenant_id}:{workspace_id}",
            payload=event.model_dump(mode="json"),
            headers={"request_id": request_id, "trace_id": trace_id},
            aggregate_type=INVITATION_AGGREGATE_TYPE,
            aggregate_id=invitation_id,
            set_rls=False,
        )

    @staticmethod
    def _row_to_record(
        row: dict[str, Any],
        *,
        audit_event_id: str | None = None,
        outbox_event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "invitation_id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "workspace_id": str(row["workspace_id"]),
            "recipient_email_normalized": str(row["recipient_email_normalized"]),
            "recipient_issuer": str(row["recipient_issuer"]),
            "role": str(row["role"]),
            "status": str(row.get("effective_status", row["status"])),
            "generation": int(row["generation"]),
            "delivery_state": str(row["delivery_state"]),
            "delivery_error_code": (
                str(row["delivery_error_code"]) if row["delivery_error_code"] is not None else None
            ),
            "created_by_user_id": str(row["created_by_user_id"]),
            "delivery_requested_by_user_id": str(row["delivery_requested_by_user_id"]),
            "revoked_by_user_id": (
                str(row["revoked_by_user_id"]) if row["revoked_by_user_id"] is not None else None
            ),
            "accepted_by_user_id": (
                str(row["accepted_by_user_id"]) if row["accepted_by_user_id"] is not None else None
            ),
            "expires_at": _format_ts(row["expires_at"]) or "",
            "created_at": _format_ts(row["created_at"]) or "",
            "updated_at": _format_ts(row["updated_at"]) or "",
            "provider_accepted_at": _format_ts(row["provider_accepted_at"]),
            "revoked_at": _format_ts(row["revoked_at"]),
            "accepted_at": _format_ts(row["accepted_at"]),
            "audit_event_id": audit_event_id,
            "outbox_event_id": outbox_event_id,
        }

    async def create_invitation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        recipient_email_normalized: str,
        recipient_issuer: str,
        role: str,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            actor_role = await self._lock_workspace_and_actor(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                lock_workspace=True,
            )
            if role == "owner":
                raise InvitationForbiddenError(
                    "owner_invite_forbidden",
                    "Workspace owner role cannot be assigned via invitations",
                )
            if actor_role == "admin" and role == "admin":
                raise InvitationForbiddenError(
                    "admin_cannot_invite_admin",
                    "Workspace admins can invite developer, operator, or viewer roles only",
                )

            await self._assert_no_recipient_membership(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email_normalized,
            )

            # Materialize any expired pending invitation for this recipient before duplicate check.
            expire_stmt = text(
                "UPDATE iam.workspace_invitations "
                "SET status = 'expired', "
                "    proof_ciphertext = NULL, "
                "    proof_key_id = NULL, "
                "    lease_token = NULL, "
                "    leased_until = NULL, "
                "    delivery_state = CASE "
                "        WHEN delivery_state IN ('queued', 'sending') THEN 'cancelled' "
                "        ELSE delivery_state "
                "    END, "
                "    updated_at = now() "
                "WHERE tenant_id = :tenant_id "
                "  AND workspace_id = :workspace_id "
                "  AND recipient_issuer = :recipient_issuer "
                "  AND recipient_email_normalized = :recipient_email "
                "  AND status = 'pending' "
                "  AND expires_at <= now() "
                "RETURNING id"
            )
            expired_rows = (
                (
                    await session.execute(
                        expire_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "recipient_issuer": recipient_issuer,
                            "recipient_email": recipient_email_normalized,
                        },
                    )
                )
                .mappings()
                .all()
            )

            dup_stmt = text(
                "SELECT id FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id "
                "  AND workspace_id = :workspace_id "
                "  AND recipient_issuer = :recipient_issuer "
                "  AND recipient_email_normalized = :recipient_email "
                "  AND status = 'pending' "
                "FOR UPDATE"
            )
            if (
                await session.execute(
                    dup_stmt,
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "recipient_issuer": recipient_issuer,
                        "recipient_email": recipient_email_normalized,
                    },
                )
            ).mappings().one_or_none() is not None:
                raise InvitationConflictError(
                    "pending_invitation_conflict",
                    "An active pending invitation already exists for this recipient",
                )

            await self._dead_letter_outbox_for_invitations(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_ids=[UUID(str(r["id"])) for r in expired_rows],
                states=("pending",),
            )

            await self._enforce_rate_limits(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email_normalized,
                policy=policy,
            )

            invitation_id = uuid4()
            proof, token_hash = protector.mint_proof()
            binding = InvitationProofBinding(
                invitation_id=str(invitation_id),
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                generation=1,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email_normalized,
                role=role,
            )
            proof_ciphertext, proof_key_id = protector.encrypt_proof(proof, binding)

            insert_stmt = text(
                "INSERT INTO iam.workspace_invitations ("
                "    tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "    role, created_by_user_id, status, generation, token_hash, "
                "    proof_ciphertext, proof_key_id, delivery_state, delivery_requested_by_user_id, "
                "    expires_at"
                ") VALUES ("
                "    :tenant_id, :workspace_id, :id, :recipient_email, :recipient_issuer, "
                "    :role, :created_by_user_id, 'pending', 1, :token_hash, "
                "    :proof_ciphertext, :proof_key_id, 'queued', :delivery_requested_by_user_id, "
                "    now() + make_interval(hours => :ttl_hours)"
                ") RETURNING tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "    role, status, generation, delivery_state, delivery_error_code, "
                "    created_by_user_id, delivery_requested_by_user_id, revoked_by_user_id, "
                "    accepted_by_user_id, expires_at, created_at, updated_at, "
                "    provider_accepted_at, revoked_at, accepted_at"
            )
            inv_row = (
                (
                    await session.execute(
                        insert_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "id": invitation_id,
                            "recipient_email": recipient_email_normalized,
                            "recipient_issuer": recipient_issuer,
                            "role": role,
                            "created_by_user_id": actor_user_id,
                            "token_hash": token_hash,
                            "proof_ciphertext": proof_ciphertext,
                            "proof_key_id": proof_key_id,
                            "delivery_requested_by_user_id": actor_user_id,
                            "ttl_hours": policy.ttl_hours,
                        },
                    )
                )
                .mappings()
                .one()
            )

            outbox_event_id = await self._publish_delivery_outbox(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_id=invitation_id,
                generation=1,
                actor_user_id=actor_user_id,
                request_id=request_id,
                trace_id=trace_id,
            )
            audit_event_id = await self._insert_audit_event(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action="workspace.invitation.create",
                invitation_id=invitation_id,
                request_id=request_id,
                trace_id=trace_id,
                details={
                    "invitation_id": str(invitation_id),
                    "role": role,
                    "generation": 1,
                    "status": "pending",
                    "delivery_state": "queued",
                    "outbox_event_id": outbox_event_id,
                },
            )
            return self._row_to_record(
                dict(inv_row),
                audit_event_id=audit_event_id,
                outbox_event_id=outbox_event_id,
            )
        except (
            InvitationConflictError,
            InvitationForbiddenError,
            InvitationRateLimitError,
            WorkspaceAccessDeniedError,
        ):
            raise
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise InvitationConflictError(
                    "pending_invitation_conflict",
                    "An active pending invitation already exists for this recipient",
                ) from exc
            raise _db_unavailable(exc) from exc
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    async def list_invitations(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        decoded_cursor = decode_invitation_cursor(cursor) if cursor is not None else None
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            await self._lock_workspace_and_actor(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                lock_workspace=False,
            )

            params: dict[str, Any] = {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "fetch_limit": limit + 1,
            }
            cursor_clause = ""
            if decoded_cursor is not None:
                cursor_created_at, cursor_id = decoded_cursor
                params["cursor_created_at"] = cursor_created_at
                params["cursor_id"] = cursor_id
                cursor_clause = "AND (created_at, id) < (:cursor_created_at, :cursor_id) "

            stmt = text(
                "SELECT tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "       role, status, "
                "       CASE WHEN status = 'pending' AND expires_at <= now() THEN 'expired' ELSE status END AS effective_status, "
                "       generation, delivery_state, delivery_error_code, "
                "       created_by_user_id, delivery_requested_by_user_id, revoked_by_user_id, "
                "       accepted_by_user_id, expires_at, created_at, updated_at, "
                "       provider_accepted_at, revoked_at, accepted_at "
                "FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                f"{cursor_clause}"
                "ORDER BY created_at DESC, id DESC "
                "LIMIT :fetch_limit"
            )
            rows = (await session.execute(stmt, params)).mappings().all()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = [self._row_to_record(dict(r)) for r in page_rows]
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                last_created = last["created_at"]
                if isinstance(last_created, datetime):
                    next_cursor = encode_invitation_cursor(last_created, str(last["id"]))
            return items, next_cursor
        except (InvitationForbiddenError, InvitationValidationError, WorkspaceAccessDeniedError):
            raise
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    async def resend_invitation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            actor_role = await self._lock_workspace_and_actor(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                lock_workspace=True,
            )

            lock_stmt = text(
                "SELECT tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "       role, status, (expires_at <= now()) AS is_expired, generation "
                "FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id "
                "FOR UPDATE"
            )
            inv = (
                (
                    await session.execute(
                        lock_stmt,
                        {"tenant_id": tenant_id, "workspace_id": workspace_id, "id": invitation_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if inv is None:
                raise InvitationNotFoundError(
                    "invitation_not_found", "Workspace invitation not found"
                )

            target_role = str(inv["role"])
            if actor_role == "admin" and target_role == "admin":
                raise InvitationForbiddenError(
                    "admin_cannot_manage_admin",
                    "Workspace admins cannot resend admin invitations",
                )

            current_status = str(inv["status"])
            if current_status != "pending" or bool(inv["is_expired"]):
                raise InvitationConflictError(
                    "invitation_not_pending",
                    "Only unexpired pending invitations can be resent",
                )

            cooldown_stmt = text(
                "SELECT EXTRACT(EPOCH FROM (MAX(created_at) + make_interval(secs => :cooldown) - now())) AS remaining "
                "FROM integration.outbox_events "
                "WHERE tenant_id = :tenant_id "
                "  AND workspace_id = :workspace_id "
                "  AND aggregate_type = :aggregate_type "
                "  AND aggregate_id = :invitation_id "
                "  AND event_type = :event_type "
                "  AND created_at > now() - make_interval(secs => :cooldown)"
            )
            if policy.resend_cooldown_seconds > 0:
                cd_row = (
                    (
                        await session.execute(
                            cooldown_stmt,
                            {
                                "tenant_id": tenant_id,
                                "workspace_id": workspace_id,
                                "aggregate_type": INVITATION_AGGREGATE_TYPE,
                                "invitation_id": invitation_id,
                                "event_type": INVITATION_DELIVERY_EVENT_TYPE,
                                "cooldown": policy.resend_cooldown_seconds,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                if cd_row["remaining"] is not None and float(cd_row["remaining"]) > 0:
                    raise InvitationRateLimitError(
                        "resend_cooldown_active",
                        "Please wait before resending this invitation",
                        retry_after_seconds=max(1, math.ceil(float(cd_row["remaining"]))),
                    )

            recipient_issuer = str(inv["recipient_issuer"])
            recipient_email = str(inv["recipient_email_normalized"])

            await self._enforce_rate_limits(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email,
                policy=policy,
            )
            await self._assert_no_recipient_membership(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email,
            )

            next_generation = int(inv["generation"]) + 1
            proof, token_hash = protector.mint_proof()
            binding = InvitationProofBinding(
                invitation_id=str(invitation_id),
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                generation=next_generation,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email,
                role=target_role,
            )
            proof_ciphertext, proof_key_id = protector.encrypt_proof(proof, binding)

            update_stmt = text(
                "UPDATE iam.workspace_invitations "
                "SET generation = :generation, "
                "    token_hash = :token_hash, "
                "    proof_ciphertext = :proof_ciphertext, "
                "    proof_key_id = :proof_key_id, "
                "    delivery_state = 'queued', "
                "    delivery_requested_by_user_id = :actor_user_id, "
                "    delivery_error_code = NULL, "
                "    provider_accepted_at = NULL, "
                "    lease_token = NULL, "
                "    leased_until = NULL, "
                "    expires_at = now() + make_interval(hours => :ttl_hours), "
                "    updated_at = now() "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id "
                "RETURNING tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "    role, status, generation, delivery_state, delivery_error_code, "
                "    created_by_user_id, delivery_requested_by_user_id, revoked_by_user_id, "
                "    accepted_by_user_id, expires_at, created_at, updated_at, "
                "    provider_accepted_at, revoked_at, accepted_at"
            )
            updated_row = (
                (
                    await session.execute(
                        update_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "id": invitation_id,
                            "generation": next_generation,
                            "token_hash": token_hash,
                            "proof_ciphertext": proof_ciphertext,
                            "proof_key_id": proof_key_id,
                            "actor_user_id": actor_user_id,
                            "ttl_hours": policy.ttl_hours,
                        },
                    )
                )
                .mappings()
                .one()
            )

            await self._dead_letter_outbox_for_invitations(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_ids=[invitation_id],
                states=("pending",),
            )

            outbox_event_id = await self._publish_delivery_outbox(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_id=invitation_id,
                generation=next_generation,
                actor_user_id=actor_user_id,
                request_id=request_id,
                trace_id=trace_id,
            )
            audit_event_id = await self._insert_audit_event(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action="workspace.invitation.resend",
                invitation_id=invitation_id,
                request_id=request_id,
                trace_id=trace_id,
                details={
                    "invitation_id": str(invitation_id),
                    "role": target_role,
                    "generation": next_generation,
                    "status": "pending",
                    "delivery_state": "queued",
                    "outbox_event_id": outbox_event_id,
                },
            )
            return self._row_to_record(
                dict(updated_row),
                audit_event_id=audit_event_id,
                outbox_event_id=outbox_event_id,
            )
        except (
            InvitationConflictError,
            InvitationForbiddenError,
            InvitationNotFoundError,
            InvitationRateLimitError,
            WorkspaceAccessDeniedError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    async def revoke_invitation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            actor_role = await self._lock_workspace_and_actor(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                lock_workspace=True,
            )

            lock_stmt = text(
                "SELECT tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "       role, status, (expires_at <= now()) AS is_expired, generation, "
                "       delivery_state, delivery_error_code, created_by_user_id, "
                "       delivery_requested_by_user_id, revoked_by_user_id, accepted_by_user_id, "
                "       expires_at, created_at, updated_at, provider_accepted_at, revoked_at, accepted_at "
                "FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id "
                "FOR UPDATE"
            )
            inv = (
                (
                    await session.execute(
                        lock_stmt,
                        {"tenant_id": tenant_id, "workspace_id": workspace_id, "id": invitation_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if inv is None:
                raise InvitationNotFoundError(
                    "invitation_not_found", "Workspace invitation not found"
                )

            target_role = str(inv["role"])
            if actor_role == "admin" and target_role == "admin":
                raise InvitationForbiddenError(
                    "admin_cannot_manage_admin",
                    "Workspace admins cannot revoke admin invitations",
                )

            current_status = str(inv["status"])
            if current_status == "accepted":
                raise InvitationConflictError(
                    "invitation_already_accepted",
                    "Accepted invitations cannot be revoked",
                )
            if current_status == "revoked":
                return self._row_to_record(dict(inv))
            if current_status == "expired":
                return self._row_to_record(dict(inv))
            if bool(inv["is_expired"]):
                expire_stmt = text(
                    "UPDATE iam.workspace_invitations "
                    "SET status = 'expired', "
                    "    proof_ciphertext = NULL, "
                    "    proof_key_id = NULL, "
                    "    lease_token = NULL, "
                    "    leased_until = NULL, "
                    "    delivery_state = CASE "
                    "        WHEN delivery_state IN ('queued', 'sending') THEN 'cancelled' "
                    "        ELSE delivery_state "
                    "    END, "
                    "    updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id "
                    "RETURNING tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                    "    role, status, generation, delivery_state, delivery_error_code, "
                    "    created_by_user_id, delivery_requested_by_user_id, revoked_by_user_id, "
                    "    accepted_by_user_id, expires_at, created_at, updated_at, "
                    "    provider_accepted_at, revoked_at, accepted_at"
                )
                expired_row = (
                    (
                        await session.execute(
                            expire_stmt,
                            {
                                "tenant_id": tenant_id,
                                "workspace_id": workspace_id,
                                "id": invitation_id,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                await self._dead_letter_outbox_for_invitations(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    invitation_ids=[invitation_id],
                    states=("pending",),
                )
                return self._row_to_record(dict(expired_row))

            revoke_stmt = text(
                "UPDATE iam.workspace_invitations "
                "SET status = 'revoked', "
                "    revoked_at = now(), "
                "    revoked_by_user_id = :actor_user_id, "
                "    proof_ciphertext = NULL, "
                "    proof_key_id = NULL, "
                "    lease_token = NULL, "
                "    leased_until = NULL, "
                "    delivery_state = CASE "
                "        WHEN delivery_state IN ('queued', 'sending') THEN 'cancelled' "
                "        ELSE delivery_state "
                "    END, "
                "    updated_at = now() "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id "
                "RETURNING tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
                "    role, status, generation, delivery_state, delivery_error_code, "
                "    created_by_user_id, delivery_requested_by_user_id, revoked_by_user_id, "
                "    accepted_by_user_id, expires_at, created_at, updated_at, "
                "    provider_accepted_at, revoked_at, accepted_at"
            )
            revoked_row = (
                (
                    await session.execute(
                        revoke_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "id": invitation_id,
                            "actor_user_id": actor_user_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            await self._dead_letter_outbox_for_invitations(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_ids=[invitation_id],
                states=("pending",),
            )
            audit_event_id = await self._insert_audit_event(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action="workspace.invitation.revoke",
                invitation_id=invitation_id,
                request_id=request_id,
                trace_id=trace_id,
                details={
                    "invitation_id": str(invitation_id),
                    "role": target_role,
                    "generation": int(revoked_row["generation"]),
                    "status": "revoked",
                    "delivery_state": str(revoked_row["delivery_state"]),
                },
            )
            return self._row_to_record(dict(revoked_row), audit_event_id=audit_event_id)
        except (
            InvitationConflictError,
            InvitationForbiddenError,
            InvitationNotFoundError,
            WorkspaceAccessDeniedError,
        ):
            raise
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    async def _resolve_accept_scope(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
    ) -> dict[str, Any]:
        stmt = text(
            "SELECT invitation_id, tenant_id, workspace_id "
            "FROM iam.resolve_invitation_accept_scope("
            "    :invitation_id, :token_hash, :user_id, :issuer, :subject, :email_normalized"
            ")"
        )
        row = (
            (
                await session.execute(
                    stmt,
                    {
                        "invitation_id": invitation_id,
                        "token_hash": expected_token_hash,
                        "user_id": recipient_user_id,
                        "issuer": recipient_issuer,
                        "subject": recipient_subject,
                        "email_normalized": recipient_email_normalized,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or link is invalid",
            )
        return dict(row)

    @staticmethod
    def _evaluate_invitation_acceptance(
        *,
        inv_row: dict[str, Any] | None,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_email_normalized: str,
        tenant_status: str,
        workspace_status: str,
        memberships_by_user_id: dict[str, dict[str, Any]],
    ) -> tuple[str, str]:
        """Evaluate privacy-preserving proof, recipient, and lifecycle checks.

        Returns `(acceptance_state, role)` where `acceptance_state` is `'ready'` or
        `'already_accepted'`. Raises domain errors otherwise without leaking data
        to wrong proof holders or wrong recipients.
        """
        if inv_row is None:
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or link is invalid",
            )

        stored_hash = str(inv_row["token_hash"])
        if not hmac.compare_digest(stored_hash, expected_token_hash):
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or link is invalid",
            )

        if (
            str(inv_row["recipient_issuer"]) != recipient_issuer
            or str(inv_row["recipient_email_normalized"]) != recipient_email_normalized
        ):
            raise InvitationForbiddenError(
                "recipient_mismatch",
                "Authenticated account does not match the invitation recipient",
            )

        if tenant_status != "active" or workspace_status != "active":
            raise InvitationConflictError(
                "workspace_inactive",
                "Target tenant or workspace is not active",
            )

        inv_status = str(inv_row["status"])
        is_expired = bool(inv_row.get("is_expired"))
        if inv_status == "revoked":
            raise InvitationGoneError(
                "invitation_revoked",
                "This invitation has been revoked",
            )
        if inv_status == "expired" or (inv_status == "pending" and is_expired):
            raise InvitationGoneError(
                "invitation_expired",
                "This invitation has expired",
            )

        recipient_key = str(recipient_user_id)
        recipient_membership = memberships_by_user_id.get(recipient_key)

        if inv_status == "accepted":
            accepted_by = (
                str(inv_row["accepted_by_user_id"])
                if inv_row["accepted_by_user_id"] is not None
                else ""
            )
            if accepted_by != recipient_key:
                raise InvitationConflictError(
                    "invitation_already_accepted",
                    "This invitation has already been accepted",
                )
            if recipient_membership is None:
                raise InvitationConflictError(
                    "membership_conflict",
                    "Workspace membership is no longer present",
                )
            m_status = str(recipient_membership["status"])
            if m_status == "revoked":
                raise InvitationConflictError(
                    "membership_revoked",
                    "Workspace membership has been revoked and cannot be restored",
                )
            if m_status != "active":
                raise InvitationConflictError(
                    "membership_conflict",
                    "Workspace membership is not active",
                )
            return "already_accepted", str(recipient_membership["role"])

        if inv_status != "pending":
            raise InvitationConflictError(
                "invitation_not_pending",
                "Invitation is not available for acceptance",
            )

        inv_role = str(inv_row["role"])
        if inv_role not in {"admin", "developer", "operator", "viewer"}:
            raise InvitationForbiddenError(
                "owner_invite_forbidden",
                "Workspace owner role cannot be assigned via invitations",
            )

        if recipient_membership is not None:
            m_status = str(recipient_membership["status"])
            if m_status == "revoked":
                raise InvitationConflictError(
                    "membership_revoked",
                    "Revoked workspace membership cannot be reactivated via invitation",
                )
            raise InvitationConflictError(
                "membership_conflict",
                "Account already holds a membership record in this workspace",
            )

        authorizer_id = str(inv_row["delivery_requested_by_user_id"])
        authorizer_membership = memberships_by_user_id.get(authorizer_id)
        if authorizer_membership is None or str(authorizer_membership["status"]) != "active":
            raise InvitationConflictError(
                "inviter_authority_invalid",
                "Inviter no longer holds active permission in this workspace",
            )
        auth_role = str(authorizer_membership["role"])
        can_grant = auth_role == "owner" or (
            auth_role == "admin" and inv_role in {"developer", "operator", "viewer"}
        )
        if not can_grant:
            raise InvitationConflictError(
                "inviter_authority_invalid",
                "Inviter no longer has permission to grant the invited role",
            )

        return "ready", inv_role

    async def preview_invitation(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
    ) -> dict[str, Any]:
        """Preview invitation details strictly within RLS after verifying proof & recipient."""
        try:
            scope = await self._resolve_accept_scope(
                session,
                invitation_id=invitation_id,
                expected_token_hash=expected_token_hash,
                recipient_user_id=recipient_user_id,
                recipient_issuer=recipient_issuer,
                recipient_subject=recipient_subject,
                recipient_email_normalized=recipient_email_normalized,
            )
            tenant_id = UUID(str(scope["tenant_id"]))
            workspace_id = UUID(str(scope["workspace_id"]))

            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(recipient_user_id),
            )

            ws_stmt = text(
                "SELECT w.status AS workspace_status, w.slug AS workspace_slug, w.name AS workspace_name, "
                "       t.status AS tenant_status, t.slug AS tenant_slug, t.name AS tenant_name "
                "FROM iam.workspaces AS w "
                "JOIN iam.tenants AS t ON t.id = w.tenant_id "
                "WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id"
            )
            ws_row = (
                (await session.execute(ws_stmt, {"tenant_id": tenant_id, "workspace_id": workspace_id}))
                .mappings()
                .one_or_none()
            )
            if ws_row is None:
                raise InvitationNotFoundError(
                    "invitation_not_found",
                    "Invitation not found or link is invalid",
                )

            inv_stmt = text(
                "SELECT id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, "
                "       role, status, generation, token_hash, delivery_requested_by_user_id, "
                "       accepted_by_user_id, expires_at, (expires_at <= clock_timestamp()) AS is_expired "
                "FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id"
            )
            inv_row = (
                (
                    await session.execute(
                        inv_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "invitation_id": invitation_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )

            memberships_by_user: dict[str, dict[str, Any]] = {}
            if inv_row is not None:
                authorizer_id = UUID(str(inv_row["delivery_requested_by_user_id"]))
                m_stmt = text(
                    "SELECT id, user_id, role, status "
                    "FROM iam.memberships "
                    "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                    "  AND user_id IN (:recipient_user_id, :authorizer_user_id)"
                )
                m_rows = (
                    (
                        await session.execute(
                            m_stmt,
                            {
                                "tenant_id": tenant_id,
                                "workspace_id": workspace_id,
                                "recipient_user_id": recipient_user_id,
                                "authorizer_user_id": authorizer_id,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                memberships_by_user = {str(r["user_id"]): dict(r) for r in m_rows}

            inv_dict = dict(inv_row) if inv_row is not None else None
            acceptance_state, role = self._evaluate_invitation_acceptance(
                inv_row=inv_dict,
                expected_token_hash=expected_token_hash,
                recipient_user_id=recipient_user_id,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email_normalized,
                tenant_status=str(ws_row["tenant_status"]),
                workspace_status=str(ws_row["workspace_status"]),
                memberships_by_user_id=memberships_by_user,
            )
            assert inv_dict is not None
            return {
                "invitation_id": str(inv_dict["id"]),
                "tenant_id": str(tenant_id),
                "tenant_slug": str(ws_row["tenant_slug"]),
                "tenant_name": str(ws_row["tenant_name"]),
                "workspace_id": str(workspace_id),
                "workspace_slug": str(ws_row["workspace_slug"]),
                "workspace_name": str(ws_row["workspace_name"]),
                "role": role,
                "status": "accepted" if acceptance_state == "already_accepted" else "pending",
                "expires_at": _format_ts(inv_dict["expires_at"]) or "",
                "can_accept": acceptance_state == "ready",
                "acceptance_state": acceptance_state,
            }
        except (
            InvitationConflictError,
            InvitationForbiddenError,
            InvitationGoneError,
            InvitationNotFoundError,
        ):
            await session.rollback()
            raise
        except SQLAlchemyError as exc:
            await session.rollback()
            raise _db_unavailable(exc) from exc

    async def accept_invitation(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
        request_id: str,
        trace_id: str,
        identity_freshness_deadline: int | None = None,
        handoff_expires_at: int | None = None,
        enforce_wall_clock_deadlines: bool = True,
    ) -> dict[str, Any]:
        """Atomically activate workspace membership, consume invitation, dead-letter outbox, and audit.

        Maintains R01 canonical lock order across concurrent workers and management mutations:
          iam.workspaces -> iam.memberships (sorted by user_id ASC) -> iam.workspace_invitations -> integration.outbox_events
        Commits explicitly before returning so Redis post-commit cleanup never precedes DB durability.
        """
        try:
            scope = await self._resolve_accept_scope(
                session,
                invitation_id=invitation_id,
                expected_token_hash=expected_token_hash,
                recipient_user_id=recipient_user_id,
                recipient_issuer=recipient_issuer,
                recipient_subject=recipient_subject,
                recipient_email_normalized=recipient_email_normalized,
            )
            tenant_id = UUID(str(scope["tenant_id"]))
            workspace_id = UUID(str(scope["workspace_id"]))

            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(recipient_user_id),
            )

            # Canonical lock step 1: iam.workspaces FOR SHARE
            ws_stmt = text(
                "SELECT w.status AS workspace_status, w.slug AS workspace_slug, w.name AS workspace_name, "
                "       t.status AS tenant_status, t.slug AS tenant_slug, t.name AS tenant_name "
                "FROM iam.workspaces AS w "
                "JOIN iam.tenants AS t ON t.id = w.tenant_id "
                "WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id "
                "FOR SHARE OF w"
            )
            ws_row = (
                (await session.execute(ws_stmt, {"tenant_id": tenant_id, "workspace_id": workspace_id}))
                .mappings()
                .one_or_none()
            )
            if ws_row is None:
                raise InvitationNotFoundError(
                    "invitation_not_found",
                    "Invitation not found or link is invalid",
                )

            # Snapshot peek of delivery_requested_by_user_id prior to membership locks
            peek_stmt = text(
                "SELECT delivery_requested_by_user_id "
                "FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id"
            )
            peek_row = (
                (
                    await session.execute(
                        peek_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "invitation_id": invitation_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            peeked_authorizer_id = (
                UUID(str(peek_row["delivery_requested_by_user_id"]))
                if peek_row is not None
                else recipient_user_id
            )

            # Canonical lock step 2: relevant iam.memberships locked deterministically before invitation lock
            m_lock_stmt = text(
                "SELECT id, user_id, role, status "
                "FROM iam.memberships "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                "  AND (user_id = :recipient_user_id OR user_id = :peeked_authorizer_id OR role IN ('owner', 'admin')) "
                "ORDER BY user_id ASC "
                "FOR SHARE"
            )
            m_rows = (
                (
                    await session.execute(
                        m_lock_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "recipient_user_id": recipient_user_id,
                            "peeked_authorizer_id": peeked_authorizer_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
            memberships_by_user = {str(r["user_id"]): dict(r) for r in m_rows}

            # Canonical lock step 3: iam.workspace_invitations FOR UPDATE
            inv_lock_stmt = text(
                "SELECT id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, "
                "       role, status, generation, token_hash, delivery_state, "
                "       delivery_requested_by_user_id, accepted_by_user_id, "
                "       expires_at "
                "FROM iam.workspace_invitations "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id "
                "FOR UPDATE"
            )
            inv_row = (
                (
                    await session.execute(
                        inv_lock_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "invitation_id": invitation_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            inv_dict = dict(inv_row) if inv_row is not None else None
            if inv_dict is not None:
                # Re-evaluate expiry against database wall-clock strictly AFTER lock acquisition completes.
                wall_expiry_stmt = text(
                    "SELECT (expires_at <= clock_timestamp()) AS is_expired "
                    "FROM iam.workspace_invitations "
                    "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id"
                )
                wall_expiry_row = (
                    (
                        await session.execute(
                            wall_expiry_stmt,
                            {
                                "tenant_id": tenant_id,
                                "workspace_id": workspace_id,
                                "invitation_id": invitation_id,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                inv_dict["is_expired"] = bool(wall_expiry_row["is_expired"])

            # If a concurrent worker accepted and committed between our step-2
            # membership snapshot and our step-3 invitation lock, refresh only the
            # recipient's committed membership state lock-free so idempotent retry
            # sees the committed row cleanly without altering canonical lock order.
            recipient_key = str(recipient_user_id)
            if (
                inv_dict is not None
                and str(inv_dict["status"]) == "accepted"
                and str(inv_dict.get("accepted_by_user_id") or "") == recipient_key
                and recipient_key not in memberships_by_user
            ):
                fresh_m_stmt = text(
                    "SELECT id, user_id, role, status "
                    "FROM iam.memberships "
                    "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                    "  AND user_id = :recipient_user_id"
                )
                fresh_m_row = (
                    (
                        await session.execute(
                            fresh_m_stmt,
                            {
                                "tenant_id": tenant_id,
                                "workspace_id": workspace_id,
                                "recipient_user_id": recipient_user_id,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if fresh_m_row is not None:
                    memberships_by_user[recipient_key] = dict(fresh_m_row)

            acceptance_state, role = self._evaluate_invitation_acceptance(
                inv_row=inv_dict,
                expected_token_hash=expected_token_hash,
                recipient_user_id=recipient_user_id,
                recipient_issuer=recipient_issuer,
                recipient_email_normalized=recipient_email_normalized,
                tenant_status=str(ws_row["tenant_status"]),
                workspace_status=str(ws_row["workspace_status"]),
                memberships_by_user_id=memberships_by_user,
            )
            assert inv_dict is not None

            if acceptance_state == "already_accepted":
                return {
                    "invitation_id": str(inv_dict["id"]),
                    "tenant_id": str(tenant_id),
                    "workspace_id": str(workspace_id),
                    "workspace_slug": str(ws_row["workspace_slug"]),
                    "workspace_name": str(ws_row["workspace_name"]),
                    "role": role,
                    "acceptance_state": "already_accepted",
                }

            if enforce_wall_clock_deadlines:
                post_lock_now = int(time.time())
                if identity_freshness_deadline is not None and post_lock_now >= identity_freshness_deadline:
                    raise InvitationForbiddenError(
                        "reauthentication_required",
                        "Identity verification window has expired; please sign in again",
                    )
                if handoff_expires_at is not None and post_lock_now >= handoff_expires_at:
                    raise InvitationNotFoundError(
                        "invitation_not_found",
                        "Invitation not found or handoff session has expired",
                    )

            # Atomic activation inside held locks
            try:
                async with session.begin_nested():
                    ins_membership_stmt = text(
                        "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                        "VALUES (:tenant_id, :workspace_id, :user_id, :role, 'active') "
                        "RETURNING id"
                    )
                    membership_row = (
                        (
                            await session.execute(
                                ins_membership_stmt,
                                {
                                    "tenant_id": tenant_id,
                                    "workspace_id": workspace_id,
                                    "user_id": recipient_user_id,
                                    "role": role,
                                },
                            )
                        )
                        .mappings()
                        .one()
                    )
            except IntegrityError as exc:
                if _is_unique_violation(exc):
                    raise InvitationConflictError(
                        "membership_conflict",
                        "Account already holds a membership record in this workspace",
                    ) from exc
                raise

            upd_inv_stmt = text(
                "UPDATE iam.workspace_invitations "
                "SET status = 'accepted', "
                "    accepted_at = now(), "
                "    accepted_by_user_id = :user_id, "
                "    proof_ciphertext = NULL, "
                "    proof_key_id = NULL, "
                "    lease_token = NULL, "
                "    leased_until = NULL, "
                "    delivery_state = CASE "
                "        WHEN delivery_state IN ('queued', 'sending') THEN 'cancelled' "
                "        ELSE delivery_state "
                "    END, "
                "    updated_at = now() "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id "
                "  AND status = 'pending' AND expires_at > clock_timestamp() "
                "RETURNING id"
            )
            upd_row = (
                await session.execute(
                    upd_inv_stmt,
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "invitation_id": invitation_id,
                        "user_id": recipient_user_id,
                    },
                )
            ).scalar_one_or_none()
            if upd_row is None:
                raise InvitationGoneError(
                    "invitation_expired",
                    "This invitation has expired",
                )

            # Canonical lock step 4: integration.outbox_events
            await self._dead_letter_outbox_for_invitations(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_ids=[invitation_id],
                states=("pending", "delivering"),
            )

            await self._insert_audit_event(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=recipient_user_id,
                action="workspace.invitation.accept",
                invitation_id=invitation_id,
                request_id=request_id,
                trace_id=trace_id,
                details={
                    "invitation_id": str(invitation_id),
                    "role": role,
                    "generation": int(inv_dict["generation"]),
                    "status": "accepted",
                    "membership_id": str(membership_row["id"]),
                },
            )

            await session.commit()
            return {
                "invitation_id": str(inv_dict["id"]),
                "tenant_id": str(tenant_id),
                "workspace_id": str(workspace_id),
                "workspace_slug": str(ws_row["workspace_slug"]),
                "workspace_name": str(ws_row["workspace_name"]),
                "role": role,
                "acceptance_state": "accepted",
            }
        except (
            InvitationConflictError,
            InvitationForbiddenError,
            InvitationGoneError,
            InvitationNotFoundError,
        ):
            await session.rollback()
            raise
        except SQLAlchemyError as exc:
            await session.rollback()
            raise _db_unavailable(exc) from exc
