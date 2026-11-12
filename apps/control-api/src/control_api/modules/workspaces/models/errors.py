"""Domain and repository exceptions for the Workspaces module (SPEC-WORKSPACE-001 / Step 03-A).

Pure Python exception hierarchy free of Web/HTTP framework dependencies (AGENT_CODING_RULES.md §2).
"""

from __future__ import annotations

from control_api.modules.identity.models.errors import RepositoryUnavailableError


class WorkspaceError(Exception):
    """Base domain exception for workspace operations."""


class WorkspaceAccessDeniedError(WorkspaceError):
    """Raised when an actor lacks authority to provision, access, or manage a workspace or tenant."""


class WorkspaceConflictError(WorkspaceError):
    """Raised when a workspace or tenant slug collides with an existing record."""


class WorkspaceDatabaseUnavailableError(RepositoryUnavailableError, WorkspaceError):
    """Raised when PostgreSQL persistence or authorization lookup fails during workspace operations."""


class InvitationError(WorkspaceError):
    """Base domain error for workspace invitation lifecycle operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InvitationNotFoundError(InvitationError):
    """Raised when an invitation does not exist inside the target workspace."""


class InvitationForbiddenError(InvitationError):
    """Raised when the actor lacks permission for the target invitation role or action."""


class InvitationConflictError(InvitationError):
    """Raised on membership, duplicate pending, or state transition conflicts."""


class InvitationRateLimitError(InvitationError):
    """Raised when resend cooldown or hourly invitation enqueue quota is exceeded."""

    def __init__(self, code: str, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(code, message)
        self.retry_after_seconds = max(1, retry_after_seconds)


class InvitationValidationError(InvitationError):
    """Raised on invalid cursor, email normalization, or role inputs."""


class InvitationConfigurationError(InvitationError):
    """Raised when invitations feature flags or proof encryption config are missing or invalid."""


class InvitationGoneError(InvitationError):
    """Raised when a verified recipient presents a valid proof for an expired or revoked invitation."""


class MembershipError(WorkspaceError):
    """Base domain error for workspace member management operations (ADR-021)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MembershipNotFoundError(MembershipError):
    """Raised when a target membership does not exist in the scoped workspace."""


class MembershipForbiddenError(MembershipError):
    """Raised when the actor lacks role authority for a member management operation."""


class MembershipConflictError(MembershipError):
    """Raised on stale preconditions, last-owner protection, or state transition conflicts."""


class MembershipValidationError(MembershipError):
    """Raised on invalid cursor, status filter, or malformed identifier inputs."""


__all__ = [
    "InvitationConfigurationError",
    "InvitationConflictError",
    "InvitationError",
    "InvitationForbiddenError",
    "InvitationGoneError",
    "InvitationNotFoundError",
    "InvitationRateLimitError",
    "InvitationValidationError",
    "MembershipConflictError",
    "MembershipError",
    "MembershipForbiddenError",
    "MembershipNotFoundError",
    "MembershipValidationError",
    "WorkspaceAccessDeniedError",
    "WorkspaceConflictError",
    "WorkspaceDatabaseUnavailableError",
    "WorkspaceError",
]

