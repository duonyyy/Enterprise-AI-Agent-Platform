"""Response DTOs for Workspace endpoints (SPEC-WORKSPACE-001 / Step 03-A)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceResponseDTO(BaseModel):
    """Canonical representation of a provisioned or retrieved workspace."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_slug: str
    workspace_name: str
    status: str
    role: str
    initial_owner_user_id: str | None = None
    audit_event_id: str | None = None
    created_at: str


class WorkspaceSummaryDTO(BaseModel):
    """Summary projection of an allowed active workspace for a user."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_slug: str
    workspace_name: str
    status: str = "active"
    role: str


class WorkspaceListResponseDTO(BaseModel):
    """Response container for GET /v1/workspaces."""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceSummaryDTO] = Field(default_factory=list)

