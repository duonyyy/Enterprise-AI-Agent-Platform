"""PostgreSQL invitation administration operations (create, list, resend, revoke)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from control_api.core.database import set_rls_context
from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationNotFoundError,
    InvitationRateLimitError,
    InvitationValidationError,
    WorkspaceAccessDeniedError,
)
from control_api.modules.workspaces.models.rbac import (
    can_invite_target_role,
    can_manage_invitation_target,
)
from control_api.modules.workspaces.models.records import (
    InvitationPolicyConfig,
    InvitationRecord,
)
from control_api.modules.workspaces.repositories._db_helpers import is_unique_violation
from control_api.modules.workspaces.repositories._invitation_shared import (
    assert_no_recipient_membership,
    build_invitation_record,
    dead_letter_outbox_for_invitations,
    decode_invitation_cursor,
    encode_invitation_cursor,
    enforce_invitation_rate_limits,
    insert_invitation_audit_event,
    invitation_db_unavailable,
    lock_workspace_and_actor,
    publish_delivery_outbox,
)
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import (
    INVITATION_AGGREGATE_TYPE,
    INVITATION_DELIVERY_EVENT_TYPE,
    InvitationProofBinding,
    InvitationProofProtector,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

_INVITATION_RETURNING_COLS: Final[str] = (
    "tenant_id, workspace_id, id, recipient_email_normalized, recipient_issuer, "
    "role, status, generation, delivery_state, delivery_error_code, "
    "created_by_user_id, delivery_requested_by_user_id, revoked_by_user_id, "
    "accepted_by_user_id, expires_at, created_at, updated_at, "
    "provider_accepted_at, revoked_at, accepted_at"
)

_EXPIRE_INVITATION_SET_SQL: Final[str] = (
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
)


async def _expire_pending_for_recipient(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    recipient_issuer: str,
    recipient_email_normalized: str,
) -> list[UUID]:
    expire_stmt = text(
        "UPDATE iam.workspace_invitations "
        f"{_EXPIRE_INVITATION_SET_SQL}"
        "WHERE tenant_id = :tenant_id "
        "  AND workspace_id = :workspace_id "
        "  AND recipient_issuer = :recipient_issuer "
        "  AND recipient_email_normalized = :recipient_email "
        "  AND status = 'pending' "
        "  AND expires_at <= now() "
        "RETURNING id"
    )
    rows = (
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
    return [UUID(str(r["id"])) for r in rows]


async def _assert_no_pending_duplicate(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    recipient_issuer: str,
    recipient_email_normalized: str,
) -> None:
    dup_stmt = text(
        "SELECT id FROM iam.workspace_invitations "
        "WHERE tenant_id = :tenant_id "
        "  AND workspace_id = :workspace_id "
        "  AND recipient_issuer = :recipient_issuer "
        "  AND recipient_email_normalized = :recipient_email "
        "  AND status = 'pending' "
        "FOR UPDATE"
    )
    existing = (
        (
            await session.execute(
                dup_stmt,
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
            "pending_invitation_conflict",
            "An active pending invitation already exists for this recipient",
        )


async def _insert_invitation_row(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    recipient_email_normalized: str,
    recipient_issuer: str,
    role: str,
    actor_user_id: UUID,
    token_hash: str,
    proof_ciphertext: str,
    proof_key_id: str,
    ttl_hours: int,
) -> dict[str, Any]:
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
        f") RETURNING {_INVITATION_RETURNING_COLS}"
    )
    row = (
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
                    "ttl_hours": ttl_hours,
                },
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _check_resend_cooldown(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    cooldown_seconds: int,
) -> None:
    if cooldown_seconds <= 0:
        return
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
                    "cooldown": cooldown_seconds,
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


async def _lock_invitation_row(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
) -> dict[str, Any]:
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
        raise InvitationNotFoundError("invitation_not_found", "Workspace invitation not found")
    return dict(inv)


async def create_invitation_op(
    session: AsyncSession,
    *,
    outbox_bus: PostgresOutboxEventBus,
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
) -> InvitationRecord:
    """Create a pending workspace invitation and enqueue transactional outbox delivery."""
    try:
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(actor_user_id),
        )
        actor_role = await lock_workspace_and_actor(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            lock_workspace=True,
        )
        allowed, err_code, err_message = can_invite_target_role(actor_role, role)
        if not allowed:
            raise InvitationForbiddenError(err_code, err_message)

        await assert_no_recipient_membership(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email_normalized,
        )
        expired_ids = await _expire_pending_for_recipient(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email_normalized,
        )
        await _assert_no_pending_duplicate(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email_normalized,
        )
        await dead_letter_outbox_for_invitations(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_ids=expired_ids,
            states=("pending",),
        )
        await enforce_invitation_rate_limits(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email_normalized,
            workspace_hourly_limit=policy.workspace_hourly_limit,
            recipient_hourly_limit=policy.recipient_hourly_limit,
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
        inv_row = await _insert_invitation_row(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            recipient_email_normalized=recipient_email_normalized,
            recipient_issuer=recipient_issuer,
            role=role,
            actor_user_id=actor_user_id,
            token_hash=token_hash,
            proof_ciphertext=proof_ciphertext,
            proof_key_id=proof_key_id,
            ttl_hours=policy.ttl_hours,
        )
        outbox_event_id = await publish_delivery_outbox(
            session,
            outbox_bus=outbox_bus,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            generation=1,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        audit_event_id = await insert_invitation_audit_event(
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
        return build_invitation_record(
            inv_row,
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
        if is_unique_violation(exc):
            raise InvitationConflictError(
                "pending_invitation_conflict",
                "An active pending invitation already exists for this recipient",
            ) from exc
        raise invitation_db_unavailable(exc) from exc
    except SQLAlchemyError as exc:
        raise invitation_db_unavailable(exc) from exc


async def list_invitations_op(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    actor_user_id: UUID,
    limit: int,
    cursor: str | None,
) -> tuple[list[InvitationRecord], str | None]:
    """List workspace invitations deterministically with keyset pagination."""
    decoded_cursor = decode_invitation_cursor(cursor) if cursor is not None else None
    try:
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(actor_user_id),
        )
        await lock_workspace_and_actor(
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
        items = [build_invitation_record(r) for r in page_rows]
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
        raise invitation_db_unavailable(exc) from exc


async def resend_invitation_op(
    session: AsyncSession,
    *,
    outbox_bus: PostgresOutboxEventBus,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    actor_user_id: UUID,
    protector: InvitationProofProtector,
    policy: InvitationPolicyConfig,
    request_id: str,
    trace_id: str,
) -> InvitationRecord:
    """Rotate proof generation, extend expiry, dead-letter stale outbox items, and re-enqueue delivery."""
    try:
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(actor_user_id),
        )
        actor_role = await lock_workspace_and_actor(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            lock_workspace=True,
        )
        inv = await _lock_invitation_row(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
        target_role = str(inv["role"])
        allowed, err_code, err_message = can_manage_invitation_target(
            actor_role, target_role, action="resend"
        )
        if not allowed:
            raise InvitationForbiddenError(err_code, err_message)

        if str(inv["status"]) != "pending" or bool(inv["is_expired"]):
            raise InvitationConflictError(
                "invitation_not_pending",
                "Only unexpired pending invitations can be resent",
            )

        await _check_resend_cooldown(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            cooldown_seconds=policy.resend_cooldown_seconds,
        )
        recipient_issuer = str(inv["recipient_issuer"])
        recipient_email = str(inv["recipient_email_normalized"])
        await enforce_invitation_rate_limits(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email,
            workspace_hourly_limit=policy.workspace_hourly_limit,
            recipient_hourly_limit=policy.recipient_hourly_limit,
        )
        await assert_no_recipient_membership(
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
            f"RETURNING {_INVITATION_RETURNING_COLS}"
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

        await dead_letter_outbox_for_invitations(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_ids=[invitation_id],
            states=("pending",),
        )
        outbox_event_id = await publish_delivery_outbox(
            session,
            outbox_bus=outbox_bus,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            generation=next_generation,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        audit_event_id = await insert_invitation_audit_event(
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
        return build_invitation_record(
            updated_row,
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
        raise invitation_db_unavailable(exc) from exc


async def _materialize_expired_invitation(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
) -> InvitationRecord:
    expire_stmt = text(
        "UPDATE iam.workspace_invitations "
        f"{_EXPIRE_INVITATION_SET_SQL}"
        "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id "
        f"RETURNING {_INVITATION_RETURNING_COLS}"
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
    await dead_letter_outbox_for_invitations(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        invitation_ids=[invitation_id],
        states=("pending",),
    )
    return build_invitation_record(expired_row)


async def revoke_invitation_op(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    actor_user_id: UUID,
    request_id: str,
    trace_id: str,
) -> InvitationRecord:
    """Idempotently revoke a pending invitation and dead-letter queued delivery outbox events."""
    try:
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(actor_user_id),
        )
        actor_role = await lock_workspace_and_actor(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            lock_workspace=True,
        )
        inv = await _lock_invitation_row(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
        target_role = str(inv["role"])
        allowed, err_code, err_message = can_manage_invitation_target(
            actor_role, target_role, action="revoke"
        )
        if not allowed:
            raise InvitationForbiddenError(err_code, err_message)

        current_status = str(inv["status"])
        if current_status == "accepted":
            raise InvitationConflictError(
                "invitation_already_accepted",
                "Accepted invitations cannot be revoked",
            )
        if current_status in {"revoked", "expired"}:
            return build_invitation_record(inv)
        if bool(inv["is_expired"]):
            return await _materialize_expired_invitation(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                invitation_id=invitation_id,
            )

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
            f"RETURNING {_INVITATION_RETURNING_COLS}"
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
        await dead_letter_outbox_for_invitations(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_ids=[invitation_id],
            states=("pending",),
        )
        audit_event_id = await insert_invitation_audit_event(
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
        return build_invitation_record(revoked_row, audit_event_id=audit_event_id)
    except (
        InvitationConflictError,
        InvitationForbiddenError,
        InvitationNotFoundError,
        WorkspaceAccessDeniedError,
    ):
        raise
    except SQLAlchemyError as exc:
        raise invitation_db_unavailable(exc) from exc
