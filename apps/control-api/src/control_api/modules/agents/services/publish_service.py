"""Agent publishing application service executing atomic immutable publication (ADR-022, SPEC-AGENT-001, STEP-04C)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from agent_contracts.manifest import CanonicalizationError, compute_request_sha256
from control_api.modules.agents.models.errors import AgentValidationError
from control_api.modules.agents.models.ports import AgentRepositoryPort
from control_api.modules.agents.models.records import PublishResultEntity
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class AgentPublishService:
    """Orchestrates publish request canonicalization, authority recheck, and atomic transaction execution."""

    def __init__(self, repository: AgentRepositoryPort[AsyncSession]) -> None:
        self._repository = repository

    async def publish_agent_revision(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
        expected_revision: int,
        operation_key: str,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> PublishResultEntity:
        """Publish agent revision atomically under idempotent replay protection."""
        tenant_id = UUID(principal.tenant_id)
        workspace_id = UUID(principal.workspace_id)
        actor_user_id = UUID(principal.user_id)

        # Compute deterministic RFC 8785 request digest (RF04-05: fail closed as domain 422 on non-canonicalizable input)
        try:
            request_sha256 = compute_request_sha256(
                operation="agent:publish:v1",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actor_user_id=actor_user_id,
                expected_revision=expected_revision,
                body=body,
            )
        except CanonicalizationError as exc:
            raise AgentValidationError(
                "Publish request contains values that cannot be canonically serialized under RFC 8785",
                code="invalid_spec",
            ) from exc

        # Execute publish transaction under strict lock ordering
        return await self._repository.publish_version(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            expected_revision=expected_revision,
            operation_key=operation_key,
            request_sha256=request_sha256,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )


__all__ = ["AgentPublishService"]
