"""PostgreSQL invitation preview and atomic acceptance operations (ADR-020 / R01 lock ordering)."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from control_api.core.database import set_rls_context
from control_api.modules.workspaces.models.acceptance_policy import evaluate_invitation_acceptance
from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationGoneError,
    InvitationNotFoundError,
)
from control_api.modules.workspaces.models.records import (
    InvitationAcceptRecord,
    InvitationPreviewRecord,
)
from control_api.modules.workspaces.repositories._db_helpers import (
    format_timestamp,
    is_unique_violation,
)
from control_api.modules.workspaces.repositories._invitation_shared import (
    dead_letter_outbox_for_invitations,
    insert_invitation_audit_event,
    invitation_db_unavailable,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


async def _resolve_accept_scope(
    session: AsyncSession,
    *,
    invitation_id: UUID,
    expected_token_hash: str,
    recipient_user_id: UUID,
    recipient_issuer: str,
    recipient_subject: str,
    recipient_email_normalized: str,
) -> tuple[UUID, UUID]:
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
    return UUID(str(row["tenant_id"])), UUID(str(row["workspace_id"]))


async def _fetch_workspace_status_row(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    for_share: bool,
) -> dict[str, Any]:
    lock_clause = " FOR SHARE OF w" if for_share else ""
    ws_stmt = text(
        "SELECT w.status AS workspace_status, w.slug AS workspace_slug, w.name AS workspace_name, "
        "       t.status AS tenant_status, t.slug AS tenant_slug, t.name AS tenant_name "
        "FROM iam.workspaces AS w "
        "JOIN iam.tenants AS t ON t.id = w.tenant_id "
        f"WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id{lock_clause}"
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
    return dict(ws_row)


async def _fetch_preview_memberships(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    recipient_user_id: UUID,
    inv_row: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if inv_row is None:
        return {}
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
    return {str(r["user_id"]): dict(r) for r in m_rows}


async def preview_invitation_op(
    session: AsyncSession,
    *,
    invitation_id: UUID,
    expected_token_hash: str,
    recipient_user_id: UUID,
    recipient_issuer: str,
    recipient_subject: str,
    recipient_email_normalized: str,
) -> InvitationPreviewRecord:
    """Preview invitation details strictly within RLS after verifying proof & recipient."""
    try:
        tenant_id, workspace_id = await _resolve_accept_scope(
            session,
            invitation_id=invitation_id,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_subject=recipient_subject,
            recipient_email_normalized=recipient_email_normalized,
        )
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(recipient_user_id),
        )
        ws_row = await _fetch_workspace_status_row(
            session, tenant_id=tenant_id, workspace_id=workspace_id, for_share=False
        )
        inv_stmt = text(
            "SELECT id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, "
            "       role, status, generation, token_hash, delivery_requested_by_user_id, "
            "       accepted_by_user_id, expires_at, (expires_at <= clock_timestamp()) AS is_expired "
            "FROM iam.workspace_invitations "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id"
        )
        inv_raw = (
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
        inv_dict = dict(inv_raw) if inv_raw is not None else None
        if inv_dict is None:
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or link is invalid",
            )
        memberships_by_user = await _fetch_preview_memberships(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            inv_row=inv_dict,
        )
        acceptance_state, role = evaluate_invitation_acceptance(
            inv_row=inv_dict,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email_normalized,
            tenant_status=str(ws_row["tenant_status"]),
            workspace_status=str(ws_row["workspace_status"]),
            memberships_by_user_id=memberships_by_user,
        )
        return InvitationPreviewRecord(
            invitation_id=str(inv_dict["id"]),
            tenant_id=str(tenant_id),
            tenant_slug=str(ws_row["tenant_slug"]),
            tenant_name=str(ws_row["tenant_name"]),
            workspace_id=str(workspace_id),
            workspace_slug=str(ws_row["workspace_slug"]),
            workspace_name=str(ws_row["workspace_name"]),
            role=role,
            status="accepted" if acceptance_state == "already_accepted" else "pending",
            expires_at=format_timestamp(inv_dict["expires_at"]) or "",
            can_accept=acceptance_state == "ready",
            acceptance_state=acceptance_state,
        )
    except SQLAlchemyError as exc:
        raise invitation_db_unavailable(exc) from exc


async def _lock_accept_memberships(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    recipient_user_id: UUID,
) -> dict[str, dict[str, Any]]:
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
    return {str(r["user_id"]): dict(r) for r in m_rows}


async def _lock_invitation_for_accept(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
) -> dict[str, Any] | None:
    inv_lock_stmt = text(
        "SELECT id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, "
        "       role, status, generation, token_hash, delivery_state, "
        "       delivery_requested_by_user_id, accepted_by_user_id, "
        "       expires_at "
        "FROM iam.workspace_invitations "
        "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :invitation_id "
        "FOR UPDATE"
    )
    inv_raw = (
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
    if inv_raw is None:
        return None
    inv_dict = dict(inv_raw)
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
    return inv_dict


async def _refresh_concurrent_recipient_membership(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    recipient_user_id: UUID,
    inv_dict: dict[str, Any] | None,
    memberships_by_user: dict[str, dict[str, Any]],
) -> None:
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


def _enforce_post_lock_deadlines(
    *,
    enforce_wall_clock_deadlines: bool,
    identity_freshness_deadline: int | None,
    handoff_expires_at: int | None,
) -> None:
    if not enforce_wall_clock_deadlines:
        return
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


async def _activate_membership_and_consume_invitation(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    invitation_id: UUID,
    recipient_user_id: UUID,
    role: str,
) -> str:
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
        if is_unique_violation(exc):
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
        raise InvitationGoneError("invitation_expired", "This invitation has expired")
    return str(membership_row["id"])


async def accept_invitation_op(
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
) -> InvitationAcceptRecord:
    """Atomically activate workspace membership, consume invitation, dead-letter outbox, and audit.

    Maintains R01 canonical lock order across concurrent workers and management mutations:
      iam.workspaces -> iam.memberships (sorted by user_id ASC) -> iam.workspace_invitations -> integration.outbox_events
    """
    try:
        tenant_id, workspace_id = await _resolve_accept_scope(
            session,
            invitation_id=invitation_id,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_subject=recipient_subject,
            recipient_email_normalized=recipient_email_normalized,
        )
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(recipient_user_id),
        )
        # Canonical lock step 1: iam.workspaces FOR SHARE
        ws_row = await _fetch_workspace_status_row(
            session, tenant_id=tenant_id, workspace_id=workspace_id, for_share=True
        )
        # Canonical lock step 2: iam.memberships ORDER BY user_id ASC FOR SHARE
        memberships_by_user = await _lock_accept_memberships(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            recipient_user_id=recipient_user_id,
        )
        # Canonical lock step 3: iam.workspace_invitations FOR UPDATE
        inv_dict = await _lock_invitation_for_accept(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
        if inv_dict is None:
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or link is invalid",
            )
        await _refresh_concurrent_recipient_membership(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            recipient_user_id=recipient_user_id,
            inv_dict=inv_dict,
            memberships_by_user=memberships_by_user,
        )

        acceptance_state, role = evaluate_invitation_acceptance(
            inv_row=inv_dict,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_email_normalized=recipient_email_normalized,
            tenant_status=str(ws_row["tenant_status"]),
            workspace_status=str(ws_row["workspace_status"]),
            memberships_by_user_id=memberships_by_user,
        )

        if acceptance_state == "already_accepted":
            return InvitationAcceptRecord(
                invitation_id=str(inv_dict["id"]),
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                workspace_slug=str(ws_row["workspace_slug"]),
                workspace_name=str(ws_row["workspace_name"]),
                role=role,
                acceptance_state="already_accepted",
            )

        _enforce_post_lock_deadlines(
            enforce_wall_clock_deadlines=enforce_wall_clock_deadlines,
            identity_freshness_deadline=identity_freshness_deadline,
            handoff_expires_at=handoff_expires_at,
        )

        membership_id = await _activate_membership_and_consume_invitation(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            recipient_user_id=recipient_user_id,
            role=role,
        )

        # Canonical lock step 4: integration.outbox_events
        await dead_letter_outbox_for_invitations(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_ids=[invitation_id],
            states=("pending", "delivering"),
        )
        await insert_invitation_audit_event(
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
                "membership_id": membership_id,
            },
        )
        return InvitationAcceptRecord(
            invitation_id=str(inv_dict["id"]),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            workspace_slug=str(ws_row["workspace_slug"]),
            workspace_name=str(ws_row["workspace_name"]),
            role=role,
            acceptance_state="accepted",
        )
    except SQLAlchemyError as exc:
        raise invitation_db_unavailable(exc) from exc
