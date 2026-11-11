"""Repositories for the Workspaces module (SPEC-WORKSPACE-001)."""

from control_api.modules.workspaces.repositories.workspace_repository import (
    WorkspaceRepository,
    workspace_repository,
)

__all__ = [
    "WorkspaceRepository",
    "workspace_repository",
]

