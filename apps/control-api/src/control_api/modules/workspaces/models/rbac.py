"""Canonical workspace role, invitation, and member management policy matrix (ADR-019 / ADR-021)."""

from __future__ import annotations

from typing import Final

from control_api.modules.workspaces.models.enums import (
    AdminManageableRole,
    InvitableRole,
    MembershipStatus,
    WorkspaceRole,
)

WORKSPACE_ROLES: Final[tuple[WorkspaceRole, ...]] = tuple(WorkspaceRole)
INVITABLE_ROLES: Final[tuple[InvitableRole, ...]] = tuple(InvitableRole)
ADMIN_MANAGEABLE_ROLES: Final[frozenset[str]] = frozenset(AdminManageableRole)
MEMBER_MANAGEMENT_ACTOR_ROLES: Final[frozenset[str]] = frozenset(
    {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}
)


def can_list_workspace_members(actor_role: str) -> bool:
    """Return True iff active actor role may inspect scoped workspace memberships."""
    return actor_role in MEMBER_MANAGEMENT_ACTOR_ROLES


def can_manage_invitations(actor_role: str) -> bool:
    """Return True iff active actor role may create, list, resend, or revoke invitations."""
    return actor_role in MEMBER_MANAGEMENT_ACTOR_ROLES


def can_invite_target_role(actor_role: str, target_role: str) -> tuple[bool, str, str]:
    """Evaluate invitation role-grant matrix and return `(allowed, error_code, message)`.

    Single authoritative source of truth shared by `InvitationService` pre-checks
    and post-lock DB validation (`AR-001`).
    """
    if target_role == WorkspaceRole.OWNER or target_role not in INVITABLE_ROLES:
        return (
            False,
            "owner_invite_forbidden",
            "Workspace owner role cannot be assigned via invitations",
        )
    if actor_role == WorkspaceRole.OWNER:
        return True, "", ""
    if actor_role == WorkspaceRole.ADMIN:
        if target_role in ADMIN_MANAGEABLE_ROLES:
            return True, "", ""
        return (
            False,
            "admin_cannot_invite_admin",
            "Workspace admins can invite developer, operator, or viewer roles only",
        )
    return (
        False,
        "invitation_forbidden",
        "Only workspace owners and admins can manage invitations",
    )


def can_inviter_still_grant_role(authorizer_role: str, target_role: str) -> bool:
    """Return True iff the stored inviter role still authorizes granting `target_role`."""
    allowed, _code, _message = can_invite_target_role(authorizer_role, target_role)
    return allowed


def can_manage_invitation_target(
    actor_role: str,
    target_role: str,
    *,
    action: str,
) -> tuple[bool, str, str]:
    """Evaluate resend/revoke authority against target invitation role (ADR-019)."""
    if actor_role == WorkspaceRole.OWNER:
        return True, "", ""
    if actor_role == WorkspaceRole.ADMIN:
        if target_role in ADMIN_MANAGEABLE_ROLES:
            return True, "", ""
        return (
            False,
            "admin_cannot_manage_admin",
            f"Workspace admins cannot {action} admin invitations",
        )
    return (
        False,
        "invitation_forbidden",
        "Only workspace owners and admins can manage invitations",
    )


def can_manage_target_role(actor_role: str, target_current_role: str) -> bool:
    """Return True iff actor role has authority over target's current role."""
    if actor_role == WorkspaceRole.OWNER:
        return target_current_role in WORKSPACE_ROLES
    if actor_role == WorkspaceRole.ADMIN:
        return target_current_role in ADMIN_MANAGEABLE_ROLES
    return False


def can_assign_target_role(
    actor_role: str,
    target_current_role: str,
    requested_role: str,
) -> bool:
    """Return True iff actor may transition target from current role to requested role.

    - Owner manages all 5 roles and can promote active members to Owner.
    - Admin manages only developer/operator/viewer and cannot self-promote or touch Owner/Admin.
    """
    if not can_manage_target_role(actor_role, target_current_role):
        return False
    if actor_role == WorkspaceRole.OWNER:
        return requested_role in WORKSPACE_ROLES
    if actor_role == WorkspaceRole.ADMIN:
        return requested_role in ADMIN_MANAGEABLE_ROLES
    return False


def can_revoke_target_role(actor_role: str, target_current_role: str) -> bool:
    """Return True iff actor may revoke a member with target_current_role (subject to last-owner check)."""
    return can_manage_target_role(actor_role, target_current_role)


__all__ = [
    "ADMIN_MANAGEABLE_ROLES",
    "INVITABLE_ROLES",
    "MEMBER_MANAGEMENT_ACTOR_ROLES",
    "WORKSPACE_ROLES",
    "AdminManageableRole",
    "InvitableRole",
    "MembershipStatus",
    "WorkspaceRole",
    "can_assign_target_role",
    "can_invite_target_role",
    "can_inviter_still_grant_role",
    "can_list_workspace_members",
    "can_manage_invitation_target",
    "can_manage_invitations",
    "can_manage_target_role",
    "can_revoke_target_role",
]
