"""Response DTOs for Workspace endpoints (SPEC-WORKSPACE-001 / Step 03-A)."""

from __future__ import annotations

from typing import Literal

from control_api.modules.workspaces.models.rbac import MembershipStatus, WorkspaceRole
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class InvitationHandoffResponseDTO(BaseModel):
    """Generic acknowledgement for POST /v1/invitations/{invitation_id}/handoff (ADR-020)."""

    model_config = ConfigDict(extra="forbid")

    expires_at: str


class InvitationPreviewResponseDTO(BaseModel):
    """Safe verified invitation preview metadata returned by POST /v1/invitations/{invitation_id}/preview."""

    model_config = ConfigDict(extra="forbid")

    invitation_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    role: WorkspaceRole
    status: Literal["pending", "accepted"]
    expires_at: str
    can_accept: bool
    acceptance_state: Literal["ready", "already_accepted"]

    @model_validator(mode="after")
    def validate_owner_already_accepted_only(self) -> InvitationPreviewResponseDTO:
        """Allow owner role strictly in already_accepted receipts after post-join promotion."""
        if self.role == "owner" and (
            self.acceptance_state != "already_accepted"
            or self.status != "accepted"
            or self.can_accept
        ):
            raise ValueError(
                "owner role is only valid in already_accepted invitation preview receipts"
            )
        return self


class InvitationAcceptResponseDTO(BaseModel):
    """Authoritative response after invitation acceptance commit or same-user replay."""

    model_config = ConfigDict(extra="forbid")

    invitation_id: str
    tenant_id: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    role: WorkspaceRole
    acceptance_state: Literal["accepted", "already_accepted"]
    redirect_url: str

    @model_validator(mode="after")
    def validate_owner_already_accepted_only(self) -> InvitationAcceptResponseDTO:
        """Allow owner role strictly in already_accepted receipts after post-join promotion."""
        if self.role == "owner" and self.acceptance_state != "already_accepted":
            raise ValueError(
                "owner role is only valid in already_accepted invitation acceptance receipts"
            )
        return self


class WorkspaceMembershipRecordDTO(BaseModel):
    """Minimal scoped membership projection returned by member management endpoints (ADR-021)."""

    model_config = ConfigDict(extra="forbid")

    membership_id: str
    user_id: str
    display_name: str | None = None
    role: WorkspaceRole
    status: MembershipStatus
    joined_at: str


class WorkspaceMembershipListResponseDTO(BaseModel):
    """Paginated response for GET /v1/workspaces/{workspace_id}/memberships."""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceMembershipRecordDTO] = Field(default_factory=list)
    next_cursor: str | None = None


class WorkspaceMembershipRoleUpdateResponseDTO(WorkspaceMembershipRecordDTO):
    """Committed response for PATCH /v1/workspaces/{workspace_id}/memberships/{membership_id}/role."""

    outcome: Literal["updated", "no_op"]
    audit_event_id: str | None = None


class WorkspaceMembershipRevokeResponseDTO(WorkspaceMembershipRecordDTO):
    """Committed response for POST /v1/workspaces/{workspace_id}/memberships/{membership_id}/revoke."""

    outcome: Literal["revoked", "already_revoked"]
    audit_event_id: str | None = None
