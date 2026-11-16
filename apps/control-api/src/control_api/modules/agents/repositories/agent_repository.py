"""PostgreSQL repository for Agent draft CRUD, CAS revision control, and transactional audit trails.

Authority:
- SPEC-AGENT-001: catalog.agents persistence and CAS state machine.
- ADR-022 (D04-01): Strong ETag integer revision optimistic concurrency.
- SEC-003, DB-004: Server-side trust, RLS, live role recheck, and atomic audit logging.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from control_api.core.audit import AuditEventWrite, write_audit_event
from control_api.modules.agents.models.errors import (
    AgentAccessDeniedError,
    AgentArchivedError,
    AgentDatabaseUnavailableError,
    AgentNotFoundError,
    AgentSlugConflictError,
    AgentStaleRevisionError,
    AgentValidationError,
)
from control_api.modules.agents.models.records import (
    AgentDraftEntity,
    AgentDraftMetadataEntity,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_PERMITTED_WRITE_ROLES = frozenset({"owner", "admin", "developer"})


def _encode_cursor(created_at: datetime, agent_id: UUID) -> str:
    payload = f"{created_at.isoformat()}|{agent_id}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor_str: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        parts = raw.split("|", 1)
        if len(parts) != 2:
            raise ValueError("Invalid cursor format")
        dt = datetime.fromisoformat(parts[0])
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("Cursor timestamp must include timezone")
        uid = UUID(parts[1])
        return dt, uid
    except Exception as exc:
        raise AgentValidationError("Invalid pagination cursor", code="invalid_cursor") from exc


class AgentRepository:
    """Atomic transactional repository for Agent drafts under strict RLS and CAS concurrency."""

    async def list_drafts(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[AgentDraftMetadataEntity], str | None]:
        """List Agent drafts ordered newest-first with keyset cursor pagination."""
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "limit_plus_one": limit + 1,
        }

        cursor_filter = ""
        if cursor:
            cursor_dt, cursor_id = _decode_cursor(cursor)
            cursor_filter = (
                " AND (created_at < :cursor_dt OR (created_at = :cursor_dt AND id < :cursor_id))"
            )
            params["cursor_dt"] = cursor_dt
            params["cursor_id"] = cursor_id

        stmt = text(
            "SELECT id, slug, display_name, revision, archived_at, created_at "
            "FROM catalog.agents "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id"
            f"{cursor_filter} "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT :limit_plus_one"
        )

        try:
            result = await session.execute(stmt, params)
            rows = result.mappings().all()
        except SQLAlchemyError as exc:
            logger.warning("Failed to list agent drafts (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        has_more = len(rows) > limit
        selected_rows = rows[:limit]

        items = [
            AgentDraftMetadataEntity(
                agent_id=row["id"],
                slug=row["slug"],
                display_name=row["display_name"],
                revision=row["revision"],
                archived_at=row["archived_at"],
                created_at=row["created_at"],
            )
            for row in selected_rows
        ]

        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.created_at, last.agent_id)

        return items, next_cursor

    async def get_draft(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
    ) -> AgentDraftEntity | None:
        """Fetch committed Agent draft by ID within authorized workspace."""
        stmt = text(
            "SELECT id, slug, display_name, draft_spec, revision, archived_at, created_at "
            "FROM catalog.agents "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :agent_id"
        )
        try:
            result = await session.execute(
                stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
            )
            row = result.mappings().one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("Failed to fetch agent draft (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if row is None:
            return None

        return AgentDraftEntity(
            agent_id=row["id"],
            slug=row["slug"],
            display_name=row["display_name"],
            revision=row["revision"],
            archived_at=row["archived_at"],
            created_at=row["created_at"],
            spec=dict(row["draft_spec"]),
        )

    async def create_draft(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        slug: str,
        display_name: str,
        spec: dict[str, Any],
        actor_user_id: UUID,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentDraftEntity:
        """Create a new Agent draft with revision 1 and append atomic audit event."""
        # 1. Canonical workspace-first lock step: verify active workspace and parent tenant
        await self._verify_active_workspace(session, tenant_id=tenant_id, workspace_id=workspace_id)

        # 2. Recheck live membership and write authority before mutating
        await self._recheck_live_authority(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="create",
        )

        # 3. Insert draft
        new_id = uuid4()
        spec_json = json.dumps(spec)
        stmt = text(
            "INSERT INTO catalog.agents ("
            "    tenant_id, workspace_id, id, slug, display_name, draft_spec, revision, archived_at, created_at"
            ") VALUES ("
            "    :tenant_id, :workspace_id, :id, :slug, :display_name, CAST(:draft_spec AS jsonb), 1, NULL, now()"
            ") RETURNING id, slug, display_name, draft_spec, revision, archived_at, created_at"
        )
        try:
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": new_id,
                    "slug": slug,
                    "display_name": display_name,
                    "draft_spec": spec_json,
                },
            )
            row = result.mappings().one()
        except IntegrityError as exc:
            orig_msg = str(getattr(exc, "orig", exc)).lower()
            if "slug" in orig_msg or "unique" in orig_msg:
                raise AgentSlugConflictError(f"Agent with slug '{slug}' already exists in workspace") from exc
            raise AgentDatabaseUnavailableError("Database integrity constraint violated") from exc
        except SQLAlchemyError as exc:
            logger.warning("Failed to insert agent draft (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # 4. Atomic sanitized audit event
        await write_audit_event(
            session,
            AuditEventWrite(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_subject=str(actor_user_id),
                action="agent.create",
                resource_type="agent",
                resource_id=str(row["id"]),
                outcome="succeeded",
                request_id=request_id,
                trace_id=trace_id,
                details_redacted={
                    "slug": slug,
                    "display_name": display_name,
                    "revision": 1,
                },
            ),
        )

        return AgentDraftEntity(
            agent_id=row["id"],
            slug=row["slug"],
            display_name=row["display_name"],
            revision=row["revision"],
            archived_at=row["archived_at"],
            created_at=row["created_at"],
            spec=dict(row["draft_spec"]),
        )

    async def replace_draft_spec(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        spec: dict[str, Any],
        expected_revision: int,
        actor_user_id: UUID,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentDraftEntity:
        """Atomically replace the full draft specification using optimistic CAS revision control."""
        # 1. Canonical workspace-first lock step
        await self._verify_active_workspace(session, tenant_id=tenant_id, workspace_id=workspace_id)

        # 2. Recheck live membership and write authority before mutating
        await self._recheck_live_authority(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="edit",
        )

        # 3. Conditional CAS update
        spec_json = json.dumps(spec)
        update_stmt = text(
            "UPDATE catalog.agents "
            "SET draft_spec = CAST(:draft_spec AS jsonb), "
            "    revision = revision + 1 "
            "WHERE tenant_id = :tenant_id "
            "  AND workspace_id = :workspace_id "
            "  AND id = :agent_id "
            "  AND revision = :expected_revision "
            "  AND archived_at IS NULL "
            "RETURNING id, slug, display_name, draft_spec, revision, archived_at, created_at"
        )
        try:
            result = await session.execute(
                update_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "agent_id": agent_id,
                    "expected_revision": expected_revision,
                    "draft_spec": spec_json,
                },
            )
            row = result.mappings().one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("Failed to update agent draft CAS (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # 4. If row was not updated, diagnose failure reason
        if row is None:
            check_stmt = text(
                "SELECT revision, archived_at "
                "FROM catalog.agents "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :agent_id"
            )
            try:
                check_res = await session.execute(
                    check_stmt,
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "agent_id": agent_id,
                    },
                )
                curr = check_res.mappings().one_or_none()
            except SQLAlchemyError as exc:
                raise AgentDatabaseUnavailableError() from exc

            if curr is None:
                raise AgentNotFoundError(f"Agent '{agent_id}' not found in workspace")
            if curr["archived_at"] is not None:
                raise AgentArchivedError(f"Agent '{agent_id}' is archived and cannot be modified")
            curr_rev = int(curr["revision"])
            if curr_rev != expected_revision:
                raise AgentStaleRevisionError(
                    f"Stale revision: expected {expected_revision}, committed is {curr_rev}"
                )
            raise AgentStaleRevisionError("Concurrent update conflict")

        # 5. Atomic sanitized audit event
        await write_audit_event(
            session,
            AuditEventWrite(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_subject=str(actor_user_id),
                action="agent.update",
                resource_type="agent",
                resource_id=str(row["id"]),
                outcome="succeeded",
                request_id=request_id,
                trace_id=trace_id,
                details_redacted={
                    "slug": row["slug"],
                    "old_revision": expected_revision,
                    "new_revision": row["revision"],
                },
            ),
        )

        return AgentDraftEntity(
            agent_id=row["id"],
            slug=row["slug"],
            display_name=row["display_name"],
            revision=row["revision"],
            archived_at=row["archived_at"],
            created_at=row["created_at"],
            spec=dict(row["draft_spec"]),
        )

    async def _verify_active_workspace(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
    ) -> None:
        """Verify target workspace and parent tenant are active with FOR SHARE lock."""
        ws_stmt = text(
            "SELECT w.id, w.status AS workspace_status, t.status AS tenant_status "
            "FROM iam.workspaces AS w "
            "JOIN iam.tenants AS t ON t.id = w.tenant_id "
            "WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id "
            "FOR SHARE OF w"
        )
        try:
            res = (
                await session.execute(
                    ws_stmt,
                    {"tenant_id": tenant_id, "workspace_id": workspace_id},
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("Workspace verification failed (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if (
            res is None
            or str(res["workspace_status"]) != "active"
            or str(res["tenant_status"]) != "active"
        ):
            raise AgentAccessDeniedError("Target workspace is inactive or does not exist")

    async def _recheck_live_authority(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        action: str,
    ) -> None:
        """Re-verify actor's active membership and write authority under the transaction."""
        m_stmt = text(
            "SELECT role, status "
            "FROM iam.memberships "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND user_id = :user_id "
            "FOR SHARE"
        )
        try:
            res = (
                await session.execute(
                    m_stmt,
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "user_id": actor_user_id,
                    },
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("Live membership check failed (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if res is None or str(res["status"]) != "active":
            raise AgentAccessDeniedError("Active workspace membership required for write operations")

        role = str(res["role"])
        if role not in _PERMITTED_WRITE_ROLES:
            raise AgentAccessDeniedError(
                f"Role '{role}' is not permitted to {action} agent drafts in this workspace"
            )


agent_repository = AgentRepository()

__all__ = ["AgentRepository", "agent_repository"]
