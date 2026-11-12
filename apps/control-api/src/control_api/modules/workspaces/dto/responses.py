"""Response DTOs for Workspace endpoints (SPEC-WORKSPACE-001 / Step 03-A)."""

from __future__ import annotations

from typing import Literal

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


class WorkspaceInvitationResponseDTO(BaseModel):
    """Canonical safe projection of iam.workspace_invitations (never exposes proof, link, or hashes)."""

    model_config = ConfigDict(extra="forbid")

    invitation_id: str
    tenant_id: str
    workspace_id: str
    recipient_email_normalized: str
    recipient_issuer: str
    role: Literal["admin", "developer", "operator", "viewer"]
    status: Literal["pending", "accepted", "revoked", "expired"]
    generation: int
    delivery_state: Literal[
        "queued",
        "sending",
        "provider_accepted",
        "failed",
        "unknown",
        "cancelled",
    ]
    delivery_error_code: str | None = None
    created_by_user_id: str
    delivery_requested_by_user_id: str
    revoked_by_user_id: str | None = None
    accepted_by_user_id: str | None = None
    expires_at: str
    created_at: str
    updated_at: str
    provider_accepted_at: str | None = None
    revoked_at: str | None = None
    accepted_at: str | None = None
    audit_event_id: str | None = None
    outbox_event_id: str | None = None


class WorkspaceInvitationListResponseDTO(BaseModel):
    """Cursor-paginated response container for GET /v1/workspaces/{workspace_id}/invitations."""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceInvitationResponseDTO] = Field(default_factory=list)
    next_cursor: str | None = None

