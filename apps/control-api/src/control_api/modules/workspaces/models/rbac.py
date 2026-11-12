"""Canonical workspace role and member management policy matrix (ADR-021 / Step 03-D)."""

from __future__ import annotations

from typing import Final, Literal

WorkspaceRole = Literal["owner", "admin", "developer", "operator", "viewer"]
InvitableRole = Literal["admin", "developer", "operator", "viewer"]
AdminManageableRole = Literal["developer", "operator", "viewer"]
MembershipStatus = Literal["active", "invited", "revoked"]

WORKSPACE_ROLES: Final[tuple[WorkspaceRole, ...]] = (
    "owner",
    "admin",
    "developer",
    "operator",
    "viewer",
)
INVITABLE_ROLES: Final[tuple[InvitableRole, ...]] = (
    "admin",
    "developer",
    "operator",
    "viewer",
)
ADMIN_MANAGEABLE_ROLES: Final[frozenset[str]] = frozenset(
    {"developer", "operator", "viewer"}
)
MEMBER_MANAGEMENT_ACTOR_ROLES: Final[frozenset[str]] = frozenset({"owner", "admin"})


def can_list_workspace_members(actor_role: str) -> bool:
    """Return True iff active actor role may inspect scoped workspace memberships."""
    return actor_role in MEMBER_MANAGEMENT_ACTOR_ROLES


def can_manage_target_role(actor_role: str, target_current_role: str) -> bool:
    """Return True iff actor role has authority over target's current role."""
    if actor_role == "owner":
        return target_current_role in WORKSPACE_ROLES
    if actor_role == "admin":
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
    if actor_role == "owner":
        return requested_role in WORKSPACE_ROLES
    if actor_role == "admin":
        return requested_role in ADMIN_MANAGEABLE_ROLES
    return False


def can_revoke_target_role(actor_role: str, target_current_role: str) -> bool:
    """Return True iff actor may revoke a member with target_current_role (subject to last-owner check)."""
    return can_manage_target_role(actor_role, target_current_role)

