"""Application service orchestrating Agent draft use cases, schema validation, and invariants."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from agent_contracts.validation import AgentSpecValidationError, validate_agent_spec_raw
from control_api.modules.agents.models.errors import (
    AgentNotFoundError,
    AgentValidationError,
)
from control_api.modules.agents.models.ports import AgentRepositoryPort
from control_api.modules.agents.models.records import (
    AgentDraftEntity,
    AgentDraftMetadataEntity,
)
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class AgentDraftService:
    """Coordinates validation against canonical JSON Schema, domain rules, and persistence."""

    def __init__(self, repository: AgentRepositoryPort[AsyncSession]) -> None:
        self._repository = repository

    async def list_drafts(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[AgentDraftMetadataEntity], str | None]:
        """List drafts for the authorized workspace."""
        tenant_id = UUID(principal.tenant_id)
        workspace_id = UUID(principal.workspace_id)
        return await self._repository.list_drafts(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=limit,
            cursor=cursor,
        )

    async def get_draft(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
    ) -> AgentDraftEntity:
        """Fetch committed draft by ID or raise AgentNotFoundError."""
        tenant_id = UUID(principal.tenant_id)
        workspace_id = UUID(principal.workspace_id)
        draft = await self._repository.get_draft(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        if draft is None:
            raise AgentNotFoundError(f"Agent '{agent_id}' not found in workspace")
        return draft

    async def create_draft(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        slug: str,
        display_name: str,
        spec: dict[str, Any],
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentDraftEntity:
        """Validate raw AgentSpec, verify slug consistency, and persist draft atomically."""
        # 1. Canonical JSON Schema validation on raw dict before any transformation
        try:
            validate_agent_spec_raw(spec)
        except AgentSpecValidationError as exc:
            msg = "; ".join(exc.errors) if exc.errors else str(exc)
            raise AgentValidationError(msg, code="invalid_spec") from exc

        # 2. Local semantic invariants (A04: slug and spec.metadata.name must match)
        spec_name = spec.get("metadata", {}).get("name")
        if spec_name != slug:
            raise AgentValidationError(
                f"Payload slug '{slug}' does not match spec.metadata.name '{spec_name}'",
                code="invalid_slug",
            )

        tenant_id = UUID(principal.tenant_id)
        workspace_id = UUID(principal.workspace_id)
        actor_user_id = UUID(principal.user_id)

        return await self._repository.create_draft(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            slug=slug,
            display_name=display_name,
            spec=spec,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def replace_draft_spec(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
        spec: dict[str, Any],
        expected_revision: int,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentDraftEntity:
        """Validate raw AgentSpec, verify slug immutability, and update draft spec via CAS."""
        # 1. Canonical JSON Schema validation on raw dict
        try:
            validate_agent_spec_raw(spec)
        except AgentSpecValidationError as exc:
            msg = "; ".join(exc.errors) if exc.errors else str(exc)
            raise AgentValidationError(msg, code="invalid_spec") from exc

        # 2. Verify draft exists and immutable slug matches spec.metadata.name
        existing = await self.get_draft(session, principal=principal, agent_id=agent_id)
        spec_name = spec.get("metadata", {}).get("name")
        if spec_name != existing.slug:
            raise AgentValidationError(
                f"spec.metadata.name '{spec_name}' cannot be changed and must match agent slug '{existing.slug}'",
                code="invalid_spec",
            )

        tenant_id = UUID(principal.tenant_id)
        workspace_id = UUID(principal.workspace_id)
        actor_user_id = UUID(principal.user_id)

        return await self._repository.replace_draft_spec(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            spec=spec,
            expected_revision=expected_revision,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )


__all__ = ["AgentDraftService"]
