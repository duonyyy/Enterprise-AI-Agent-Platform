"""Domain models and exceptions for the Workspaces module (SPEC-WORKSPACE-001)."""

from control_api.modules.workspaces.models.errors import (
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
    WorkspaceError,
)

__all__ = [
    "WorkspaceAccessDeniedError",
    "WorkspaceConflictError",
    "WorkspaceDatabaseUnavailableError",
    "WorkspaceError",
]

