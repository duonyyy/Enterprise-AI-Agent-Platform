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


__all__ = [
    "InvitationConfigurationError",
    "InvitationConflictError",
    "InvitationError",
    "InvitationForbiddenError",
    "InvitationNotFoundError",
    "InvitationRateLimitError",
    "InvitationValidationError",
    "WorkspaceAccessDeniedError",
    "WorkspaceConflictError",
    "WorkspaceDatabaseUnavailableError",
    "WorkspaceError",
]

