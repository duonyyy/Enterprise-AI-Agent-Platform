"""Domain service for Agent version listing, publications, and environment promotion (Step 04-D, ADR-022, SPEC-AGENT-001)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from control_api.core.authorization import require_permission
from control_api.modules.agents.models.errors import (
    AgentAccessDeniedError,
    AgentValidationError,
)
from control_api.modules.agents.models.ports import AgentRepositoryPort
from control_api.modules.agents.models.records import (
    AgentPublicationEntity,
    AgentVersionMetadataEntity,
    PromoteResultEntity,
)

if TYPE_CHECKING:
    from platform_common.principal import AuthenticatedPrincipal
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ALLOWED_ENVIRONMENTS = ("development", "staging", "production")
PROMOTION_ALLOWED_ROLES = {"owner", "admin"}


class AgentPromotionService:
    """Manages version history, environment publications, and CAS-protected promotion/rollback."""

    def __init__(self, repository: AgentRepositoryPort[AsyncSession]) -> None:
        self._repository = repository

    async def list_versions(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[AgentVersionMetadataEntity], str | None]:
        """List published immutable versions for an agent with keyset pagination."""
        require_permission(principal, "agent:read")
        return await self._repository.list_versions(
            session,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            agent_id=agent_id,
            limit=limit,
            cursor=cursor,
        )

    async def list_publications(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
    ) -> list[AgentPublicationEntity]:
        """List active environment publication pointers for an agent."""
        require_permission(principal, "agent:read")
        return await self._repository.list_publications(
            session,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            agent_id=agent_id,
        )

    async def promote_version(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
        agent_version_id: UUID,
        environment: str,
        expected_publication_revision: int,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> PromoteResultEntity:
        """Promote or rollback an agent version to an environment using CAS."""
        require_permission(principal, "agent:promote")

        # R04-ENV: Proposed Owner/Admin-only authority check
        caller_roles = [r.lower() for r in principal.roles]
        if not any(r in PROMOTION_ALLOWED_ROLES for r in caller_roles):
            logger.warning(
                "Access denied for promotion: caller %s with roles %s lacks owner/admin authority",
                principal.user_id,
                principal.roles,
            )
            raise AgentAccessDeniedError(
                "Only workspace Owner or Admin can promote agent versions",
                code="access_denied",
            )

        if environment not in ALLOWED_ENVIRONMENTS:
            raise AgentValidationError(
                f"Invalid environment '{environment}'. Must be one of: {', '.join(ALLOWED_ENVIRONMENTS)}",
                code="invalid_environment",
            )

        if expected_publication_revision < 0:
            raise AgentValidationError(
                "expected_publication_revision must be >= 0",
                code="invalid_revision",
            )

        return await self._repository.promote_version(
            session,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            environment=environment,
            expected_publication_revision=expected_publication_revision,
            actor_user_id=UUID(principal.user_id),
            caller_roles=caller_roles,
            request_id=request_id,
            trace_id=trace_id,
        )
