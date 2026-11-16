"""Repository contract independent of a concrete persistence adapter."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar
from uuid import UUID

from control_api.modules.agents.models.records import AgentDraftEntity, AgentDraftMetadataEntity

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
