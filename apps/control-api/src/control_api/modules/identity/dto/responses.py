"""Response DTOs for Identity endpoints (SPEC-AUTH-001)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginInitiateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str
    state: str


class CallbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    issuer: str
    subject: str
    display_name: str | None = None
    email_normalized: str | None = None
    redirect_url: str


class WorkspaceMembershipDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    role: str


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    issuer: str
    subject: str
    display_name: str | None = None
    email_normalized: str | None = None
    workspaces: list[WorkspaceMembershipDTO] = Field(default_factory=list)


class TokenResponseDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 900
