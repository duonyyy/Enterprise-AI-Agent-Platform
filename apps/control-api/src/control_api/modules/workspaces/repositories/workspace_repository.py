"""PostgreSQL data access repository for workspaces and initial owner bootstrap (SPEC-WORKSPACE-001 / Step 03-A).

Encapsulates atomic workspace creation, initial owner membership binding, governance audit logging,
and RLS-scoped workspace reads under the restricted application role (`platform_app`).
Zero Web/HTTP framework dependencies (AGENT_CODING_RULES.md §2).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from control_api.core.database import set_rls_context
from control_api.modules.workspaces.models.errors import (
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.models.ports import WorkspaceRepositoryPort
from control_api.modules.workspaces.models.records import (
    WorkspaceRecord,
    WorkspaceSummaryRecord,
)
from control_api.modules.workspaces.repositories._db_helpers import (
    db_unavailable,
    is_unique_violation,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _db_unavailable(exc: Exception) -> WorkspaceDatabaseUnavailableError:
    return db_unavailable(
        exc,
        operation="workspace_persistence",
        message="Workspace persistence service temporarily unavailable",
    )


class WorkspaceRepository:
    """PostgreSQL repository for tenant/workspace bootstrap and scoped workspace reads."""

    async def user_exists(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> bool:
        """Verify that the target user UUID exists in global table iam.users."""
        stmt = text("SELECT id FROM iam.users WHERE id = :id")
        try:
            row = (await session.execute(stmt, {"id": user_id})).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc
        return row is not None

    async def get_user_workspaces(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> list[WorkspaceSummaryRecord]:
        """Fetch active workspaces for user via narrow projection iam.get_user_workspaces (ADR-018)."""
        stmt = text("SELECT * FROM iam.get_user_workspaces(:user_id)")
        try:
            rows = (await session.execute(stmt, {"user_id": user_id})).mappings().all()
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc
        return [
            WorkspaceSummaryRecord(
                workspace_id=str(r["workspace_id"]),
                tenant_id=str(r["tenant_id"]),
                tenant_slug=str(r["tenant_slug"]),
                tenant_name=str(r["tenant_name"]),
                workspace_slug=str(r["workspace_slug"]),
                workspace_name=str(r["workspace_name"]),
                role=str(r["role"]),
                membership_status=str(r.get("membership_status", "active")),
            )
            for r in rows
        ]

    async def lock_authorizing_owner_membership(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        authorizing_workspace_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Acquire a FOR SHARE lock on the actor's active owner membership, workspace, and tenant.

        Prevents race conditions where the actor's owner membership is revoked or the tenant
        is suspended concurrently while provisioning a new workspace in the same tenant.
        """
        stmt = text(
            "SELECT m.id "
            "FROM iam.memberships AS m "
            "JOIN iam.workspaces AS w "
            "  ON w.tenant_id = m.tenant_id AND w.id = m.workspace_id "
            "JOIN iam.tenants AS t "
            "  ON t.id = m.tenant_id "
            "WHERE m.tenant_id = :tenant_id "
            "  AND m.workspace_id = :workspace_id "
            "  AND m.user_id = :user_id "
            "  AND m.role = 'owner' "
            "  AND m.status = 'active' "
            "  AND w.status = 'active' "
            "  AND t.status = 'active' "
            "FOR SHARE"
        )
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(authorizing_workspace_id),
                user_id=str(user_id),
            )
            row = (
                (
                    await session.execute(
                        stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": authorizing_workspace_id,
                            "user_id": user_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc
        return row is not None

    async def create_workspace_atomic(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        owner_user_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        workspace_slug: str,
        workspace_name: str,
        reason_code: str,
        tenant_slug: str | None = None,
        tenant_name: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> WorkspaceRecord:
        """Atomically persist (optional tenant +) workspace + initial active owner + audit event.

        Executes within the caller's active database transaction under transaction-local RLS context.
        Any failure raises a typed domain exception and causes full transaction rollback.
        """
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )

            if tenant_slug is not None and tenant_name is not None:
                stmt_tenant = text(
                    "INSERT INTO iam.tenants (id, slug, name, status) "
                    "VALUES (:tenant_id, :tenant_slug, :tenant_name, 'active') "
                    "RETURNING id, slug, name, status"
                )
                tenant_row = (
                    (
                        await session.execute(
                            stmt_tenant,
                            {
                                "tenant_id": tenant_id,
                                "tenant_slug": tenant_slug,
                                "tenant_name": tenant_name,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                audit_action = "workspace.bootstrap"
                tenant_bootstrapped = True
            else:
                stmt_check_tenant = text(
                    "SELECT id, slug, name, status FROM iam.tenants WHERE id = :tenant_id FOR SHARE"
                )
                existing_tenant_row = (
                    (
                        await session.execute(
                            stmt_check_tenant,
                            {"tenant_id": tenant_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_tenant_row is None or str(existing_tenant_row["status"]) != "active":
                    raise WorkspaceAccessDeniedError("Target tenant is not active or accessible")
                tenant_row = existing_tenant_row
                audit_action = "workspace.create"
                tenant_bootstrapped = False

            stmt_workspace = text(
                "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) "
                "VALUES (:tenant_id, :workspace_id, :slug, :name, 'active') "
                "RETURNING tenant_id, id, slug, name, status, created_at"
            )
            ws_row = (
                (
                    await session.execute(
                        stmt_workspace,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "slug": workspace_slug,
                            "name": workspace_name,
                        },
                    )
                )
                .mappings()
                .one()
            )

            stmt_membership = text(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                "VALUES (:tenant_id, :workspace_id, :owner_user_id, 'owner', 'active') "
                "RETURNING id, user_id, role, status, joined_at"
            )
            membership_row = (
                (
                    await session.execute(
                        stmt_membership,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "owner_user_id": owner_user_id,
                        },
                    )
                )
                .mappings()
                .one()
            )

            details_payload = json.dumps(
                {
                    "tenant_id": str(tenant_id),
                    "tenant_slug": str(tenant_row["slug"]),
                    "workspace_slug": str(ws_row["slug"]),
                    "initial_owner_user_id": str(owner_user_id),
                    "membership_id": str(membership_row["id"]),
                    "tenant_bootstrapped": tenant_bootstrapped,
                },
                sort_keys=True,
            )

            stmt_audit = text(
                "INSERT INTO governance.audit_events ("
                "    tenant_id, workspace_id, actor_subject, action, "
                "    resource_type, resource_id, outcome, request_id, "
                "    trace_id, reason_code, details_redacted"
                ") VALUES ("
                "    :tenant_id, :workspace_id, :actor_subject, :action, "
                "    'workspace', :resource_id, 'succeeded', :request_id, "
                "    :trace_id, :reason_code, CAST(:details_redacted AS jsonb)"
                ") RETURNING id, occurred_at"
            )
            audit_row = (
                (
                    await session.execute(
                        stmt_audit,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "actor_subject": str(actor_user_id),
                            "action": audit_action,
                            "resource_id": str(workspace_id),
                            "request_id": request_id,
                            "trace_id": trace_id,
                            "reason_code": reason_code,
                            "details_redacted": details_payload,
                        },
                    )
                )
                .mappings()
                .one()
            )

        except WorkspaceAccessDeniedError:
            raise
        except IntegrityError as exc:
            if is_unique_violation(exc):
                raise WorkspaceConflictError(
                    "Workspace slug or tenant slug already exists"
                ) from exc
            raise _db_unavailable(exc) from exc
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

        raw_created_at = ws_row["created_at"]
        created_at_iso = (
            raw_created_at.isoformat()
            if hasattr(raw_created_at, "isoformat")
            else datetime.now(UTC).isoformat()
        )

        return WorkspaceRecord(
            workspace_id=str(ws_row["id"]),
            tenant_id=str(ws_row["tenant_id"]),
            tenant_slug=str(tenant_row["slug"]),
            tenant_name=str(tenant_row["name"]),
            workspace_slug=str(ws_row["slug"]),
            workspace_name=str(ws_row["name"]),
            status=str(ws_row["status"]),
            role=str(membership_row["role"]),
            initial_owner_user_id=str(membership_row["user_id"]),
            audit_event_id=str(audit_row["id"]),
            created_at=created_at_iso,
        )

    async def get_workspace_in_scope(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceRecord | None:
        """Read a workspace within the caller's verified RLS transaction context."""
        stmt = text(
            "SELECT "
            "    w.id AS workspace_id, "
            "    w.tenant_id AS tenant_id, "
            "    t.slug AS tenant_slug, "
            "    t.name AS tenant_name, "
            "    w.slug AS workspace_slug, "
            "    w.name AS workspace_name, "
            "    w.status AS status, "
            "    m.role AS role, "
            "    w.created_at AS created_at "
            "FROM iam.workspaces AS w "
            "JOIN iam.tenants AS t "
            "  ON t.id = w.tenant_id "
            "JOIN iam.memberships AS m "
            "  ON m.tenant_id = w.tenant_id "
            " AND m.workspace_id = w.id "
            " AND m.user_id = :user_id "
            "WHERE w.tenant_id = :tenant_id "
            "  AND w.id = :workspace_id "
            "  AND w.status = 'active' "
            "  AND t.status = 'active' "
            "  AND m.status = 'active'"
        )
        try:
            row = (
                (
                    await session.execute(
                        stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "user_id": user_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

        if row is None:
            return None

        raw_created_at = row["created_at"]
        created_at_iso = (
            raw_created_at.isoformat()
            if hasattr(raw_created_at, "isoformat")
            else str(raw_created_at)
        )
        return WorkspaceRecord(
            workspace_id=str(row["workspace_id"]),
            tenant_id=str(row["tenant_id"]),
            tenant_slug=str(row["tenant_slug"]),
            tenant_name=str(row["tenant_name"]),
            workspace_slug=str(row["workspace_slug"]),
            workspace_name=str(row["workspace_name"]),
            status=str(row["status"]),
            role=str(row["role"]),
            initial_owner_user_id=None,
            audit_event_id=None,
            created_at=created_at_iso,
        )

    def bind(self, session: AsyncSession) -> WorkspaceRepositoryPort:
        """Bind an AsyncSession into a session-free WorkspaceRepositoryPort adapter."""
        return SessionBoundWorkspaceRepository(repository=self, session=session)


class SessionBoundWorkspaceRepository:
    """Session-scoped adapter implementing WorkspaceRepositoryPort for pure services."""

    def __init__(self, repository: WorkspaceRepository, session: AsyncSession) -> None:
        self._repository = repository
        self._session = session

    async def user_exists(self, user_id: UUID) -> bool:
        return await self._repository.user_exists(self._session, user_id)

    async def get_user_workspaces(self, user_id: UUID) -> list[WorkspaceSummaryRecord]:
        return await self._repository.get_user_workspaces(self._session, user_id)

    async def lock_authorizing_owner_membership(
        self,
        *,
        tenant_id: UUID,
        authorizing_workspace_id: UUID,
        user_id: UUID,
    ) -> bool:
        return await self._repository.lock_authorizing_owner_membership(
            self._session,
            tenant_id=tenant_id,
            authorizing_workspace_id=authorizing_workspace_id,
            user_id=user_id,
        )

    async def create_workspace_atomic(
        self,
        *,
        actor_user_id: UUID,
        owner_user_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        workspace_slug: str,
        workspace_name: str,
        reason_code: str,
        tenant_slug: str | None = None,
        tenant_name: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> WorkspaceRecord:
        return await self._repository.create_workspace_atomic(
            self._session,
            actor_user_id=actor_user_id,
            owner_user_id=owner_user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            workspace_name=workspace_name,
            reason_code=reason_code,
            tenant_slug=tenant_slug,
            tenant_name=tenant_name,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def get_workspace_in_scope(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceRecord | None:
        return await self._repository.get_workspace_in_scope(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )


workspace_repository = WorkspaceRepository()
