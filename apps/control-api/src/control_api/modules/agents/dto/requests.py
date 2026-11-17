"""Request DTOs for Agent draft endpoints matching candidate OpenAPI specification."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateAgentDraftDTO(BaseModel):
    """Payload for creating a new mutable agent draft."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(..., min_length=1, max_length=256)
    spec: dict[str, Any]


class ReplaceAgentDraftSpecDTO(BaseModel):
    """Payload for replacing the full agent draft specification."""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]


class ValidateAgentDraftDTO(BaseModel):
    """Payload for validating an agent draft. If spec is omitted, persisted draft is validated."""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] = Field(default_factory=dict)


class PublishAgentRequestDTO(BaseModel):
    """Payload for atomically publishing an immutable agent version."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1, description="Expected current draft revision")


# Backward compatibility alias
UpdateAgentDraftDTO = ReplaceAgentDraftSpecDTO

__all__ = [
    "CreateAgentDraftDTO",
    "PublishAgentRequestDTO",
    "ReplaceAgentDraftSpecDTO",
    "UpdateAgentDraftDTO",
    "ValidateAgentDraftDTO",
]
