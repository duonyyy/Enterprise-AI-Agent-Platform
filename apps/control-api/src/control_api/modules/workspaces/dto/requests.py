"""Request DTOs for Workspace endpoints (SPEC-WORKSPACE-001 / Step 03-A)."""

from __future__ import annotations

import re
from typing import Final, Literal
from uuid import UUID

from platform_common.invitations import normalize_invitation_email
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORKSPACE_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
TENANT_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class CreateWorkspaceRequestDTO(BaseModel):
    """Strict input contract for POST /v1/workspaces (WS-D01, WS-D03, WS-D06).

    Forbids mass-assignment of role, status, permissions, or workspace_id via extra='forbid'.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=2, max_length=63)
    name: str = Field(..., min_length=1, max_length=255)
    tenant_id: str | None = Field(default=None)
    tenant_slug: str | None = Field(default=None)
    tenant_name: str | None = Field(default=None)
    initial_owner_user_id: str | None = Field(default=None)

    @field_validator("slug")
    @classmethod
    def validate_workspace_slug(cls, value: str) -> str:
        cleaned = value.strip()
        if not WORKSPACE_SLUG_RE.match(cleaned):
            raise ValueError(
                "Invalid workspace slug: must match ^[a-z0-9][a-z0-9-]{1,62}$ (2-63 chars)"
            )
        return cleaned

    @field_validator("name")
    @classmethod
    def validate_workspace_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 255:
            raise ValueError("Workspace name must be a non-empty string up to 255 characters")
        return cleaned

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(str(value).strip()))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("tenant_id must be a valid UUID") from exc

    @field_validator("tenant_slug")
    @classmethod
    def validate_tenant_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not TENANT_SLUG_RE.match(cleaned):
            raise ValueError(
                "Invalid tenant_slug: must match ^[a-z0-9][a-z0-9-]{2,62}$ (3-63 chars)"
            )
        return cleaned

    @field_validator("tenant_name")
    @classmethod
    def validate_tenant_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 255:
            raise ValueError("tenant_name must be a non-empty string up to 255 characters")
        return cleaned

    @field_validator("initial_owner_user_id")
    @classmethod
    def validate_initial_owner_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(str(value).strip()))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("initial_owner_user_id must be a valid UUID") from exc

    @model_validator(mode="after")
    def validate_tenant_target(self) -> CreateWorkspaceRequestDTO:
        has_tenant_slug = self.tenant_slug is not None
        has_tenant_name = self.tenant_name is not None
        if has_tenant_slug != has_tenant_name:
            raise ValueError(
                "Both tenant_slug and tenant_name must be provided together when bootstrapping a tenant"
            )
        if not has_tenant_slug and self.tenant_id is None:
            raise ValueError(
                "Either tenant_id (for an existing tenant) or both tenant_slug and tenant_name "
                "(for operator tenant bootstrap) must be provided"
            )
        return self


class CreateInvitationRequestDTO(BaseModel):
    """Strict input contract for POST /v1/workspaces/{workspace_id}/invitations (ADR-019)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)
    role: Literal["admin", "developer", "operator", "viewer"]

    @field_validator("email")
    @classmethod
    def validate_and_normalize_email(cls, value: str) -> str:
        return normalize_invitation_email(value)


class InvitationHandoffRequestDTO(BaseModel):
    """Strict input contract for POST /v1/invitations/{invitation_id}/handoff (ADR-020)."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=8, max_length=512)

    @field_validator("token")
    @classmethod
    def validate_token_shape(cls, value: str) -> str:
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("Invitation token must be a compact non-whitespace opaque token")
        if len(value) < 8 or len(value) > 512:
            raise ValueError("Invitation token length is out of bounds")
        return value

