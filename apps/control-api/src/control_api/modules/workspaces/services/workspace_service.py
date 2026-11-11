"""Application service for Tenant/Workspace provisioning and Initial Owner bootstrap (SPEC-WORKSPACE-001 / Step 03-A).

Enforces approved design decisions:
- WS-D01: Tenant onboarding by Bootstrap Operator; Workspace creation by Bootstrap Operator or
  existing active Owner in the same active Tenant. Initial Owner defaults to caller; only
  Bootstrap Operators may designate a different provisioned user.
- WS-D03: Single PostgreSQL transaction for Workspace + Initial active Owner + Audit event;
  deterministic 409 Conflict on duplicate slug.
- WS-D06: Standardized DTOs and sanitized HTTP error translation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from control_api.core.settings import settings
from control_api.modules.workspaces.dto import (
    CreateWorkspaceRequestDTO,
    WorkspaceListResponseDTO,
    WorkspaceResponseDTO,
    WorkspaceSummaryDTO,
)
from control_api.modules.workspaces.models.errors import (
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.repositories.workspace_repository import (
    WorkspaceRepository,
    workspace_repository,
)

if TYPE_CHECKING:
    from control_api.core.auth import AuthenticatedUserContext
    from platform_common.principal import AuthenticatedPrincipal
    from sqlalchemy.ext.asyncio import AsyncSession


class WorkspaceService:
    """Domain and application service for workspace bootstrap, creation, and scoped discovery."""

    def __init__(self, repo: WorkspaceRepository | None = None) -> None:
        self.repo = repo or workspace_repository

    async def create_workspace(
        self,
        session: AsyncSession,
        auth_user: AuthenticatedUserContext,
        dto: CreateWorkspaceRequestDTO,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> WorkspaceResponseDTO:
        """Authorize and atomically create a workspace and its initial active Owner membership."""
        if auth_user.session_data is None:
            raise WorkspaceAccessDeniedError(
                "Active application session required; workspace-scoped tokens cannot create workspaces"
            )

        try:
            actor_uid = UUID(auth_user.user_id)
        except ValueError as exc:
            raise WorkspaceAccessDeniedError("Invalid authenticated user identifier") from exc

        is_operator = settings.is_bootstrap_operator(auth_user.issuer, auth_user.subject)

        # 1. Resolve and authorize initial Owner (WS-D01)
        if dto.initial_owner_user_id is not None:
            owner_uid = UUID(dto.initial_owner_user_id)
            if owner_uid != actor_uid and not is_operator:
                raise WorkspaceAccessDeniedError(
                    "Only authorized bootstrap operators may designate a different initial owner"
                )
        else:
            owner_uid = actor_uid

        is_tenant_bootstrap = dto.tenant_slug is not None and dto.tenant_name is not None

        # 2. Enforce tenant provisioning vs existing-tenant workspace creation authority (WS-D01)
        if is_tenant_bootstrap:
            if not is_operator:
                raise WorkspaceAccessDeniedError(
                    "Tenant provisioning requires bootstrap operator authority"
                )
            if not await self.repo.user_exists(session, owner_uid):
                raise WorkspaceAccessDeniedError(
                    "Designated initial owner user does not exist"
                )
            target_tenant_id = UUID(dto.tenant_id) if dto.tenant_id else uuid4()
            reason_code = "BOOTSTRAP_OPERATOR"
        else:
            if dto.tenant_id is None:
                raise WorkspaceAccessDeniedError("Target tenant_id is required")
            target_tenant_id = UUID(dto.tenant_id)

            if is_operator:
                if not await self.repo.user_exists(session, owner_uid):
                    raise WorkspaceAccessDeniedError(
                        "Designated initial owner user does not exist"
                    )
                reason_code = "BOOTSTRAP_OPERATOR"
            else:
                # Actor must hold an active 'owner' membership in at least one active workspace
                # of the target active tenant, verified server-side and locked FOR SHARE.
                user_workspaces = await self.repo.get_user_workspaces(session, actor_uid)
                authorizing_rows = [
                    r
                    for r in user_workspaces
                    if UUID(str(r["tenant_id"])) == target_tenant_id
                    and str(r["role"]) == "owner"
                    and str(r.get("membership_status", "active")) == "active"
                ]
                if not authorizing_rows:
                    raise WorkspaceAccessDeniedError(
                        "Workspace creation requires active tenant owner or bootstrap operator authority"
                    )

                authorizing_workspace_id = UUID(str(authorizing_rows[0]["workspace_id"]))
                locked = await self.repo.lock_authorizing_owner_membership(
                    session,
                    tenant_id=target_tenant_id,
                    authorizing_workspace_id=authorizing_workspace_id,
                    user_id=actor_uid,
                )
                if not locked:
                    raise WorkspaceAccessDeniedError(
                        "Workspace creation requires active tenant owner authority"
                    )
                reason_code = "TENANT_OWNER"

        # 3. Execute atomic creation of (optional tenant +) workspace + initial owner + audit event (WS-D03)
        new_workspace_id = uuid4()
        created = await self.repo.create_workspace_atomic(
            session,
            actor_user_id=actor_uid,
            owner_user_id=owner_uid,
            tenant_id=target_tenant_id,
            workspace_id=new_workspace_id,
            workspace_slug=dto.slug,
            workspace_name=dto.name,
            reason_code=reason_code,
            tenant_slug=dto.tenant_slug,
            tenant_name=dto.tenant_name,
            request_id=request_id,
            trace_id=trace_id,
        )

        return WorkspaceResponseDTO(**created)

    async def list_user_workspaces(
        self,
        session: AsyncSession,
        auth_user: AuthenticatedUserContext,
    ) -> WorkspaceListResponseDTO:
        """List all active workspaces where the authenticated user holds an active membership."""
        if auth_user.session_data is None:
            raise WorkspaceAccessDeniedError(
                "Active application session required; workspace-scoped tokens cannot list all workspaces"
            )

        try:
            actor_uid = UUID(auth_user.user_id)
        except ValueError:
            return WorkspaceListResponseDTO(items=[])

        rows = await self.repo.get_user_workspaces(session, actor_uid)
        items = [
            WorkspaceSummaryDTO(
                workspace_id=str(r["workspace_id"]),
                tenant_id=str(r["tenant_id"]),
                tenant_slug=str(r["tenant_slug"]),
                tenant_name=str(r["tenant_name"]),
                workspace_slug=str(r["workspace_slug"]),
                workspace_name=str(r["workspace_name"]),
                status="active",
                role=str(r["role"]),
            )
            for r in rows
            if str(r.get("membership_status", "active")) == "active"
        ]
        return WorkspaceListResponseDTO(items=items)

    async def get_workspace(
        self,
        session: AsyncSession,
        principal: AuthenticatedPrincipal,
    ) -> WorkspaceResponseDTO:
        """Retrieve workspace details within the caller's verified RLS scope."""
        try:
            tenant_uid = UUID(principal.tenant_id)
            workspace_uid = UUID(principal.workspace_id)
            user_uid = UUID(principal.user_id)
        except ValueError as exc:
            raise WorkspaceAccessDeniedError("Active workspace membership required") from exc

        row = await self.repo.get_workspace_in_scope(
            session,
            tenant_id=tenant_uid,
            workspace_id=workspace_uid,
            user_id=user_uid,
        )
        if row is None:
            raise WorkspaceAccessDeniedError("Active workspace membership required")

        return WorkspaceResponseDTO(**row)


workspace_service = WorkspaceService()

__all__ = [
    "WorkspaceAccessDeniedError",
    "WorkspaceConflictError",
    "WorkspaceDatabaseUnavailableError",
    "WorkspaceService",
    "workspace_service",
]

