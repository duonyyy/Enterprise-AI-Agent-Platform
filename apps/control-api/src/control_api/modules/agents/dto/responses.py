"""Response DTOs for Agent draft endpoints matching candidate OpenAPI specification."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AgentDraftMetadataDTO(BaseModel):
    """Scoped draft metadata item for list views."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    slug: str
    display_name: str
    revision: int
    archived_at: str | None = None
    created_at: str


class AgentDraftDTO(BaseModel):
    """Full committed draft representation including spec and revision."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    slug: str
    display_name: str
    revision: int
    archived_at: str | None = None
    created_at: str
    spec: dict[str, Any]


class AgentDraftPageDTO(BaseModel):
    """Keyset-paginated draft list page."""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentDraftMetadataDTO]
    next_cursor: str | None = None


class AgentErrorDetailDTO(BaseModel):
    """Structured error payload for agent operations."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class AgentErrorResponseDTO(BaseModel):
    """Standard error envelope matching AgentError schema."""

    model_config = ConfigDict(extra="forbid")

    detail: AgentErrorDetailDTO


# Backward compatibility aliases
AgentResponseDTO = AgentDraftMetadataDTO


class PublishResponseDTO(BaseModel):
    """Legacy publish response placeholder."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    version: int
    spec_hash: str
    is_immutable: bool = True


class AgentDiagnosticDTO(BaseModel):
    """Diagnostic detail for a specific field or dependency."""

    model_config = ConfigDict(extra="forbid")

    field: str
    code: str
    message: str


class AgentValidationResultDTO(BaseModel):
    """Validation response indicating readiness and diagnostic findings."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    revision: int
    ready: bool
    diagnostics: list[AgentDiagnosticDTO]


__all__ = [
    "AgentDiagnosticDTO",
    "AgentDraftDTO",
    "AgentDraftMetadataDTO",
    "AgentDraftPageDTO",
    "AgentErrorDetailDTO",
    "AgentErrorResponseDTO",
    "AgentResponseDTO",
    "AgentValidationResultDTO",
    "PublishResponseDTO",
]
