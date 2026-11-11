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


__all__ = [
    "WorkspaceAccessDeniedError",
    "WorkspaceConflictError",
    "WorkspaceDatabaseUnavailableError",
    "WorkspaceError",
]

