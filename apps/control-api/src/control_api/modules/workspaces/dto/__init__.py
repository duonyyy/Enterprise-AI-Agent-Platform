"""Request and Response DTOs for the Workspaces module (SPEC-WORKSPACE-001)."""

from control_api.modules.workspaces.dto.requests import CreateWorkspaceRequestDTO
from control_api.modules.workspaces.dto.responses import (
    WorkspaceListResponseDTO,
    WorkspaceResponseDTO,
    WorkspaceSummaryDTO,
)

__all__ = [
    "CreateWorkspaceRequestDTO",
    "WorkspaceListResponseDTO",
    "WorkspaceResponseDTO",
    "WorkspaceSummaryDTO",
]

