"""Canonical finite-state string enums for Workspaces domain, RBAC, and DTOs (CT-025)."""

from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    """Canonical workspace membership roles defined in iam.memberships."""

    OWNER = "owner"
    ADMIN = "admin"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    VIEWER = "viewer"


class InvitableRole(StrEnum):
    """Workspace roles assignable through invitations (excludes owner)."""

    ADMIN = "admin"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    VIEWER = "viewer"


class AdminManageableRole(StrEnum):
    """Subordinate workspace roles manageable by an Admin."""

    DEVELOPER = "developer"
    OPERATOR = "operator"
    VIEWER = "viewer"


class MembershipStatus(StrEnum):
    """Finite lifecycle statuses defined on iam.memberships."""

    ACTIVE = "active"
    INVITED = "invited"
    REVOKED = "revoked"


class ExpectedActiveStatus(StrEnum):
    """Strict optimistic precondition requiring active membership status."""

    ACTIVE = "active"


class InvitationStatus(StrEnum):
    """Finite lifecycle statuses defined on iam.workspace_invitations."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvitationDeliveryState(StrEnum):
    """Finite asynchronous email delivery states on iam.workspace_invitations."""

    QUEUED = "queued"
    SENDING = "sending"
    PROVIDER_ACCEPTED = "provider_accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class InvitationPreviewStatus(StrEnum):
    """Allowed non-sensitive statuses exposed by verified invitation previews."""

    PENDING = "pending"
    ACCEPTED = "accepted"


class PreviewAcceptanceState(StrEnum):
    """Actionability state returned by verified invitation preview."""

    READY = "ready"
    ALREADY_ACCEPTED = "already_accepted"


class AcceptOutcomeState(StrEnum):
    """Idempotent acceptance outcome returned after commit or replay."""

    ACCEPTED = "accepted"
    ALREADY_ACCEPTED = "already_accepted"


class MembershipRoleUpdateOutcome(StrEnum):
    """Outcome state for workspace membership role transitions."""

    UPDATED = "updated"
    NO_OP = "no_op"


class MembershipRevokeOutcome(StrEnum):
    """Outcome state for workspace membership revocation."""

    REVOKED = "revoked"
    ALREADY_REVOKED = "already_revoked"


class InvitationHandoffEntryKind(StrEnum):
    """Discriminator for encrypted browser-bound handoff cache envelopes."""

    HANDOFF = "handoff"
    CONSUMED_RECEIPT = "consumed_receipt"


__all__ = [
    "AcceptOutcomeState",
    "AdminManageableRole",
    "ExpectedActiveStatus",
    "InvitableRole",
    "InvitationDeliveryState",
    "InvitationHandoffEntryKind",
    "InvitationPreviewStatus",
    "InvitationStatus",
    "MembershipRevokeOutcome",
    "MembershipRoleUpdateOutcome",
    "MembershipStatus",
    "PreviewAcceptanceState",
    "WorkspaceRole",
]
