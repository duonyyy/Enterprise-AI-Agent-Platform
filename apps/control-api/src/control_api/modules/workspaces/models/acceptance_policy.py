"""Pure domain policy evaluator for invitation preview and atomic acceptance (AR-007, CT-012)."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from uuid import UUID

from control_api.modules.workspaces.models.enums import (
    AcceptOutcomeState,
    InvitationStatus,
    MembershipStatus,
    PreviewAcceptanceState,
)
from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationGoneError,
    InvitationNotFoundError,
)
from control_api.modules.workspaces.models.rbac import can_inviter_still_grant_role


def _evaluate_already_accepted(
    *,
    inv_row: Mapping[str, object],
    recipient_key: str,
    recipient_membership: Mapping[str, object] | None,
) -> tuple[str, str]:
    accepted_by = (
        str(inv_row["accepted_by_user_id"]) if inv_row["accepted_by_user_id"] is not None else ""
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
    member_status = str(recipient_membership["status"])
    if member_status == MembershipStatus.REVOKED:
        raise InvitationConflictError(
            "membership_revoked",
            "Workspace membership has been revoked and cannot be restored",
        )
    if member_status != MembershipStatus.ACTIVE:
        raise InvitationConflictError(
            "membership_conflict",
            "Workspace membership is not active",
        )
    return PreviewAcceptanceState.ALREADY_ACCEPTED, str(recipient_membership["role"])


def _evaluate_pending_acceptance(
    *,
    inv_row: Mapping[str, object],
    recipient_membership: Mapping[str, object] | None,
    memberships_by_user_id: Mapping[str, Mapping[str, object]],
) -> tuple[str, str]:
    inv_role = str(inv_row["role"])
    if not can_inviter_still_grant_role("owner", inv_role):
        raise InvitationForbiddenError(
            "owner_invite_forbidden",
            "Workspace owner role cannot be assigned via invitations",
        )

    if recipient_membership is not None:
        member_status = str(recipient_membership["status"])
        if member_status == MembershipStatus.REVOKED:
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
    if (
        authorizer_membership is None
        or str(authorizer_membership["status"]) != MembershipStatus.ACTIVE
    ):
        raise InvitationConflictError(
            "inviter_authority_invalid",
            "Inviter no longer holds active permission in this workspace",
        )
    auth_role = str(authorizer_membership["role"])
    if not can_inviter_still_grant_role(auth_role, inv_role):
        raise InvitationConflictError(
            "inviter_authority_invalid",
            "Inviter no longer has permission to grant the invited role",
        )
    return PreviewAcceptanceState.READY, inv_role


def evaluate_invitation_acceptance(
    *,
    inv_row: Mapping[str, object] | None,
    expected_token_hash: str,
    recipient_user_id: UUID,
    recipient_issuer: str,
    recipient_email_normalized: str,
    tenant_status: str,
    workspace_status: str,
    memberships_by_user_id: Mapping[str, Mapping[str, object]],
) -> tuple[str, str]:
    """Evaluate privacy-preserving proof, recipient, and lifecycle checks.

    Returns `(acceptance_state, role)` where `acceptance_state` is `'ready'` or
    `'already_accepted'`. Raises domain errors otherwise without leaking data
    to wrong proof holders or wrong recipients.
    """
    if inv_row is None or not hmac.compare_digest(str(inv_row["token_hash"]), expected_token_hash):
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
    if inv_status == InvitationStatus.REVOKED:
        raise InvitationGoneError(
            "invitation_revoked",
            "This invitation has been revoked",
        )
    if inv_status == InvitationStatus.EXPIRED or (
        inv_status == InvitationStatus.PENDING and is_expired
    ):
        raise InvitationGoneError(
            "invitation_expired",
            "This invitation has expired",
        )

    recipient_key = str(recipient_user_id)
    recipient_membership = memberships_by_user_id.get(recipient_key)

    if inv_status == InvitationStatus.ACCEPTED:
        return _evaluate_already_accepted(
            inv_row=inv_row,
            recipient_key=recipient_key,
            recipient_membership=recipient_membership,
        )

    if inv_status != InvitationStatus.PENDING:
        raise InvitationConflictError(
            "invitation_not_pending",
            "Invitation is not available for acceptance",
        )

    return _evaluate_pending_acceptance(
        inv_row=inv_row,
        recipient_membership=recipient_membership,
        memberships_by_user_id=memberships_by_user_id,
    )


__all__ = ["AcceptOutcomeState", "evaluate_invitation_acceptance"]
