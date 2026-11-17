"""Repository contract independent of a concrete persistence adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from uuid import UUID

from control_api.modules.agents.models.records import (
    AgentDraftEntity,
    AgentDraftMetadataEntity,
    AgentPublishReceiptEntity,
    PublishResultEntity,
)

SessionT_contra = TypeVar("SessionT_contra", contravariant=True)


class AgentRepositoryPort(Protocol[SessionT_contra]):
    """Use the caller-owned transaction; adapters preserve scope, CAS and atomic audit."""

    async def list_drafts(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[AgentDraftMetadataEntity], str | None]: ...

    async def get_draft(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
    ) -> AgentDraftEntity | None: ...

    async def create_draft(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        slug: str,
        display_name: str,
        spec: dict[str, Any],
        actor_user_id: UUID,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentDraftEntity: ...

    async def replace_draft_spec(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        spec: dict[str, Any],
        expected_revision: int,
        actor_user_id: UUID,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentDraftEntity: ...

    async def get_publish_receipt(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        operation_key: str,
    ) -> AgentPublishReceiptEntity | None: ...

    async def publish_version(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        expected_revision: int,
        operation_key: str,
        request_sha256: str,
        actor_user_id: UUID,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> PublishResultEntity: ...


@dataclass(frozen=True, slots=True)
class ResolvedModelConfig:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    provider: str
    provider_model_id: str
    status: str
    settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResolvedToolVersion:
    id: UUID
    tool_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    tool_key: str
    kind: str
    version: int
    risk: str
    sha256: str
    mcp_status: str | None
    has_invoke_grant: bool
    connection_status: str | None = None
    approved_schema_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedSkillVersion:
    skill_id: UUID
    skill_version_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    skill_slug: str
    skill_status: str
    version: int
    bundle_sha256: str


@dataclass(frozen=True, slots=True)
class ResolvedKnowledgeBase:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    slug: str
    name: str
    visibility: str
    has_access: bool


class AgentDependencyResolverPort(Protocol[SessionT_contra]):
    """Scoped dependency lookup across owner schemas under trusted RLS boundaries."""

    async def resolve_model_config(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
    ) -> ResolvedModelConfig | None: ...

    async def resolve_tool_version(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        tool_version_id: UUID,
        caller_role: str,
    ) -> ResolvedToolVersion | None: ...

    async def resolve_skill_version(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        skill_id: UUID,
        skill_version_id: UUID,
    ) -> ResolvedSkillVersion | None: ...

    async def resolve_knowledge_base(
        self,
        session: SessionT_contra,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        base_id: UUID,
        user_id: UUID,
        caller_role: str,
    ) -> ResolvedKnowledgeBase | None: ...
