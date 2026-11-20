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
    """Published immutable version receipt."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_version_id: str
    version: int
    bundle_sha256: str
    is_immutable: bool = True
    created_at: str
    spec_hash: str | None = None

    def model_post_init(self, __context: Any, /) -> None:
        if self.spec_hash is None:
            object.__setattr__(self, "spec_hash", self.bundle_sha256)


# Backward compatibility alias
PublishAgentResponseDTO = PublishResponseDTO


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


class AgentVersionMetadataDTO(BaseModel):
    """Metadata for an immutable published agent version."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_version_id: str
    version: int
    bundle_sha256: str
    is_immutable: bool
    created_at: str
    published_by: str | None = None
    model_name: str | None = None


class AgentVersionPageDTO(BaseModel):
    """Paginated list of immutable agent versions."""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentVersionMetadataDTO]
    next_cursor: str | None = None


class AgentPublicationDTO(BaseModel):
    """Current publication pointer for an environment."""

    model_config = ConfigDict(extra="forbid")

    publication_id: str
    agent_id: str
    agent_version_id: str
    version: int
    environment: str
    revision: int
    bundle_sha256: str
    published_at: str


class AgentPublicationListDTO(BaseModel):
    """List of active environment publications."""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentPublicationDTO]


class PromoteAgentVersionResponseDTO(BaseModel):
    """Outcome of promoting an agent version to an environment (Step 04-D)."""

    model_config = ConfigDict(extra="forbid")

    publication_id: str
    agent_id: str
    agent_version_id: str
    version: int
    environment: str
    revision: int
    bundle_sha256: str
    published_at: str
    is_noop: bool = False


__all__ = [
    "AgentDiagnosticDTO",
    "AgentDraftDTO",
    "AgentDraftMetadataDTO",
    "AgentDraftPageDTO",
    "AgentErrorDetailDTO",
    "AgentErrorResponseDTO",
    "AgentPublicationDTO",
    "AgentPublicationListDTO",
    "AgentResponseDTO",
    "AgentValidationResultDTO",
    "AgentVersionMetadataDTO",
    "AgentVersionPageDTO",
    "PromoteAgentVersionResponseDTO",
    "PublishAgentResponseDTO",
    "PublishResponseDTO",
]
