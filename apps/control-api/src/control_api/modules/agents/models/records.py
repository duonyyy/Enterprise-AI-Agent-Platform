"""Domain entities for Agent draft lifecycle (SPEC-AGENT-001, ADR-022, D04-01)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentDraftMetadataEntity:
    """Read projection for an Agent draft within a scoped workspace."""

    agent_id: UUID
    slug: str
    display_name: str
    revision: int
    archived_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentDraftEntity:
    """Full committed state of a mutable Agent draft and its current spec."""

    agent_id: UUID
    slug: str
    display_name: str
    revision: int
    archived_at: datetime | None
    created_at: datetime
    spec: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentDiagnosticEntity:
    """Safe diagnostic for an individual spec field or dependency."""

    field: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AgentValidationResultEntity:
    """Readiness and semantic dependency validation result for an Agent draft."""

    agent_id: UUID
    revision: int
    ready: bool
    diagnostics: list[AgentDiagnosticEntity]


@dataclass(frozen=True, slots=True)
class AgentPublishReceiptEntity:
    """Committed durable publish receipt."""

    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    agent_id: UUID
    operation_key: str
    request_sha256: str
    actor_user_id: UUID
    agent_version_id: UUID
    response_status: int
    response_body: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PublishResultEntity:
    """Published version outcome returned by service/repository."""

    agent_id: UUID
    agent_version_id: UUID
    version: int
    bundle_sha256: str
    is_immutable: bool
    created_at: datetime
    is_replay: bool = False


@dataclass(frozen=True, slots=True)
class AgentVersionMetadataEntity:
    """Read projection for an immutable published agent version."""

    agent_id: UUID
    agent_version_id: UUID
    version: int
    bundle_sha256: str
    is_immutable: bool
    created_at: datetime
    published_by: UUID | None = None
    model_name: str | None = None


@dataclass(frozen=True, slots=True)
class AgentPublicationEntity:
    """Active publication pointer for an environment."""

    publication_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    version: int
    environment: str
    revision: int
    bundle_sha256: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class PromoteResultEntity:
    """Outcome of promoting an agent version to an environment."""

    publication_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    version: int
    environment: str
    revision: int
    bundle_sha256: str
    published_at: datetime
    is_noop: bool = False
