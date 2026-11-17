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

from agent_contracts.manifest import (
    build_agent_manifest,
    compute_bundle_sha256,
)
from agent_contracts.validation import AgentSpecValidationError, validate_agent_spec_raw
from control_api.core.audit import AuditEventWrite, write_audit_event
from control_api.modules.agents.models.errors import (
    AgentAccessDeniedError,
    AgentArchivedError,
    AgentDatabaseUnavailableError,
    AgentIdempotencyConflictError,
    AgentNotFoundError,
    AgentSlugConflictError,
    AgentStaleRevisionError,
    AgentValidationError,
)
from control_api.modules.agents.models.ports import (
    ResolvedKnowledgeBase,
    ResolvedModelConfig,
    ResolvedSkillVersion,
    ResolvedToolVersion,
)
from control_api.modules.agents.models.records import (
    AgentDraftEntity,
    AgentDraftMetadataEntity,
    AgentPublishReceiptEntity,
    PublishResultEntity,
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
                raise AgentSlugConflictError(
                    f"Agent with slug '{slug}' already exists in workspace"
                ) from exc
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
        for_update: bool = False,
    ) -> None:
        """Verify target workspace and parent tenant are active with FOR SHARE or FOR UPDATE lock."""
        lock_mode = "FOR UPDATE OF w" if for_update else "FOR SHARE OF w"
        ws_stmt = text(
            "SELECT w.id, w.status AS workspace_status, t.status AS tenant_status "
            "FROM iam.workspaces AS w "
            "JOIN iam.tenants AS t ON t.id = w.tenant_id "
            "WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id "
            f"{lock_mode}"
        )
        try:
            res = (
                (
                    await session.execute(
                        ws_stmt,
                        {"tenant_id": tenant_id, "workspace_id": workspace_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
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
    ) -> str:
        """Re-verify actor's active membership and write authority under the transaction; return role."""
        m_stmt = text(
            "SELECT role, status "
            "FROM iam.memberships "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND user_id = :user_id "
            "FOR SHARE"
        )
        try:
            res = (
                (
                    await session.execute(
                        m_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "user_id": actor_user_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            logger.warning("Live membership check failed (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if res is None or str(res["status"]) != "active":
            raise AgentAccessDeniedError(
                "Active workspace membership required for write operations"
            )

        role = str(res["role"])
        if role not in _PERMITTED_WRITE_ROLES:
            raise AgentAccessDeniedError(
                f"Role '{role}' is not permitted to {action} agent drafts in this workspace"
            )
        return role

    async def resolve_model_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        model_id: UUID,
        for_share: bool = False,
    ) -> ResolvedModelConfig | None:
        """Resolve model configuration within workspace scope under RLS."""
        lock_clause = " FOR SHARE" if for_share else ""
        stmt = text(
            "SELECT id, tenant_id, workspace_id, name, provider, provider_model_id, status, settings "
            "FROM catalog.model_configs "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :model_id"
            f"{lock_clause}"
        )
        try:
            result = await session.execute(
                stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "model_id": model_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return ResolvedModelConfig(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                name=row["name"],
                provider=row["provider"],
                provider_model_id=row["provider_model_id"],
                status=str(row["status"]),
                settings=dict(row["settings"]) if row["settings"] else {},
            )
        except SQLAlchemyError as exc:
            logger.warning("resolve_model_config database error (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

    async def resolve_tool_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        tool_version_id: UUID,
        caller_role: str,
        for_share: bool = False,
    ) -> ResolvedToolVersion | None:
        """Resolve scoped tool identity and invoke grant; MCP readiness is disabled in the service."""
        lock_clause = " FOR SHARE OF tv" if for_share else ""
        stmt = text(
            "SELECT tv.id, tv.tool_id, tv.tenant_id, tv.workspace_id, t.tool_key, t.kind, "
            "       tv.version, tv.risk, tv.sha256, "
            "       EXISTS ( "
            "           SELECT 1 FROM catalog.tool_grants tg "
            "           WHERE tg.tenant_id = tv.tenant_id AND tg.workspace_id = tv.workspace_id "
            "             AND tg.tool_id = tv.tool_id "
            "             AND tg.principal_kind = 'role' AND tg.principal_key = :caller_role "
            "             AND tg.allowed_operation = 'invoke' "
            "       ) AS has_invoke_grant "
            "FROM catalog.tool_versions tv "
            "JOIN catalog.tools t ON t.id = tv.tool_id AND t.tenant_id = tv.tenant_id AND t.workspace_id = tv.workspace_id "
            "WHERE tv.tenant_id = :tenant_id AND tv.workspace_id = :workspace_id AND tv.id = :tool_version_id"
            f"{lock_clause}"
        )
        try:
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "tool_version_id": tool_version_id,
                    "caller_role": caller_role,
                },
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return ResolvedToolVersion(
                id=row["id"],
                tool_id=row["tool_id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                tool_key=row["tool_key"],
                kind=str(row["kind"]),
                version=row["version"],
                risk=str(row["risk"]),
                sha256=row["sha256"],
                mcp_status=None,
                has_invoke_grant=bool(row["has_invoke_grant"]),
            )
        except SQLAlchemyError as exc:
            logger.warning("resolve_tool_version database error (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

    async def resolve_skill_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        skill_id: UUID,
        skill_version_id: UUID,
        for_share: bool = False,
    ) -> ResolvedSkillVersion | None:
        """Resolve skill version and parent skill status within workspace scope."""
        lock_clause = " FOR SHARE OF sv" if for_share else ""
        stmt = text(
            "SELECT sv.id AS skill_version_id, sv.skill_id, sv.tenant_id, sv.workspace_id, "
            "       s.slug AS skill_slug, s.status AS skill_status, "
            "       sv.version, sv.bundle_sha256 "
            "FROM catalog.skill_versions sv "
            "JOIN catalog.skills s ON s.id = sv.skill_id AND s.tenant_id = sv.tenant_id AND s.workspace_id = sv.workspace_id "
            "WHERE sv.tenant_id = :tenant_id AND sv.workspace_id = :workspace_id AND sv.id = :skill_version_id"
            f"{lock_clause}"
        )
        try:
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "skill_version_id": skill_version_id,
                },
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return ResolvedSkillVersion(
                skill_id=row["skill_id"],
                skill_version_id=row["skill_version_id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                skill_slug=row["skill_slug"],
                skill_status=str(row["skill_status"]),
                version=row["version"],
                bundle_sha256=row["bundle_sha256"],
            )
        except SQLAlchemyError as exc:
            logger.warning("resolve_skill_version database error (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

    async def resolve_knowledge_base(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        base_id: UUID,
        user_id: UUID,
        caller_role: str,
        for_share: bool = False,
    ) -> ResolvedKnowledgeBase | None:
        """Resolve knowledge base and verify author's binding permission."""
        lock_clause = " FOR SHARE OF kb" if for_share else ""
        stmt = text(
            "SELECT kb.id, kb.tenant_id, kb.workspace_id, kb.slug, kb.name, kb.visibility, "
            "       ( "
            "           kb.visibility = 'workspace' OR "
            "           EXISTS ( "
            "               SELECT 1 FROM knowledge.base_grants bg "
            "               WHERE bg.tenant_id = kb.tenant_id AND bg.workspace_id = kb.workspace_id "
            "                 AND bg.base_id = kb.id "
            "                 AND bg.permission IN ('read', 'manage') "
            "                 AND ( "
            "                     (bg.principal_kind = 'user' AND bg.principal_key = :user_id_str) OR "
            "                     (bg.principal_kind = 'role' AND bg.principal_key = :caller_role) "
            "                 ) "
            "           ) "
            "       ) AS has_access "
            "FROM knowledge.bases kb "
            "WHERE kb.tenant_id = :tenant_id AND kb.workspace_id = :workspace_id AND kb.id = :base_id"
            f"{lock_clause}"
        )
        try:
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "base_id": base_id,
                    "user_id_str": str(user_id),
                    "caller_role": caller_role,
                },
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return ResolvedKnowledgeBase(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                slug=row["slug"],
                name=row["name"],
                visibility=str(row["visibility"]),
                has_access=bool(row["has_access"]),
            )
        except SQLAlchemyError as exc:
            logger.warning("resolve_knowledge_base database error (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

    async def get_publish_receipt(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        operation_key: str,
    ) -> AgentPublishReceiptEntity | None:
        """Fetch committed publish receipt by scoped operation key."""
        stmt = text(
            "SELECT id, tenant_id, workspace_id, agent_id, operation_key, request_sha256, "
            "       actor_user_id, agent_version_id, response_status, response_body, created_at "
            "FROM catalog.agent_publish_receipts "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
            "  AND agent_id = :agent_id AND operation_key = :operation_key"
        )
        try:
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "agent_id": agent_id,
                    "operation_key": operation_key,
                },
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            return AgentPublishReceiptEntity(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                agent_id=row["agent_id"],
                operation_key=row["operation_key"],
                request_sha256=row["request_sha256"],
                actor_user_id=row["actor_user_id"],
                agent_version_id=row["agent_version_id"],
                response_status=row["response_status"],
                response_body=dict(row["response_body"]),
                created_at=row["created_at"],
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to fetch agent publish receipt (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

    async def publish_version(
        self,
        session: AsyncSession,
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
    ) -> PublishResultEntity:
        """Execute atomic 16-step publication transaction under strict lock ordering.

        Authority:
        - ADR-022, SPEC-AGENT-001, AG-IMPL-001 Section 5
        - Lock order: workspace (FOR UPDATE) -> actor membership (FOR SHARE) -> agent draft (FOR UPDATE)
        - Receipt replay check precedes current draft revision check
        - All dependency resolutions acquire FOR SHARE locks
        - Enforces bindings sealing protocol and deferred constraint triggers
        - Outbox event and tamper-evident audit event recorded within the same transaction
        """
        # Step 1: Canonical workspace-first lock
        await self._verify_active_workspace(
            session, tenant_id=tenant_id, workspace_id=workspace_id, for_update=False
        )

        # Step 2: Live role authority check
        caller_role = await self._recheck_live_authority(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="publish",
        )

        # Step 2b: Advisory transaction lock to serialize concurrent publishes for this agent
        try:
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtext('agent_publish:' || CAST(:agent_id AS text)))"
                ),
                {"agent_id": str(agent_id)},
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to acquire publish advisory lock (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 3: Lock agent draft row FOR UPDATE
        agent_stmt = text(
            "SELECT id, slug, display_name, draft_spec, revision, archived_at, created_at "
            "FROM catalog.agents "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :agent_id "
            "FOR UPDATE"
        )
        try:
            agent_res = await session.execute(
                agent_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
            )
            agent_row = agent_res.mappings().one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("Failed to lock agent draft for publish (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if agent_row is None:
            raise AgentNotFoundError(f"Agent '{agent_id}' not found in workspace")

        # Step 4: Idempotency receipt lookup BEFORE stale revision or archived check
        receipt = await self.get_publish_receipt(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            operation_key=operation_key,
        )
        if receipt is not None:
            if receipt.request_sha256 != request_sha256 or receipt.actor_user_id != actor_user_id:
                raise AgentIdempotencyConflictError(
                    f"Operation key '{operation_key}' has already been used with a different request or actor"
                )
            resp = receipt.response_body
            return PublishResultEntity(
                agent_id=receipt.agent_id,
                agent_version_id=receipt.agent_version_id,
                version=int(resp["version"]),
                bundle_sha256=str(resp["bundle_sha256"]),
                is_immutable=bool(resp.get("is_immutable", True)),
                created_at=receipt.created_at,
                is_replay=True,
            )

        # Step 4b: Disallow new publication on archived draft
        if agent_row["archived_at"] is not None:
            raise AgentArchivedError(f"Agent '{agent_id}' is archived and cannot be published")

        # Step 5: Check expected draft revision
        current_revision = int(agent_row["revision"])
        if current_revision != expected_revision:
            raise AgentStaleRevisionError(
                f"Stale revision: expected {expected_revision}, committed is {current_revision}"
            )

        # Step 6: Validate spec & resolve dependencies under FOR SHARE locks
        spec = dict(agent_row["draft_spec"])
        if not isinstance(spec, dict):
            raise AgentValidationError("Draft spec must be a JSON object")

        try:
            validate_agent_spec_raw(spec)
        except AgentSpecValidationError as exc:
            raise AgentValidationError(
                f"Invalid agent specification: {', '.join(exc.errors)}"
            ) from exc

        # Slug match invariant
        spec_name = (
            spec.get("metadata", {}).get("name") if isinstance(spec.get("metadata"), dict) else None
        )
        if spec_name != agent_row["slug"]:
            raise AgentValidationError(
                f"spec.metadata.name must match agent slug '{agent_row['slug']}'"
            )

        # Execution mode check
        mode = spec.get("mode")
        if mode != "fixed_plan":
            raise AgentValidationError(
                f"Agent execution mode '{mode}' is not supported; only 'fixed_plan' is available"
            )
        if spec.get("workflowRef") or spec.get("approvalPolicyRef"):
            raise AgentValidationError("Unsupported workflowRef or approvalPolicyRef")

        # Resolve model configuration (FOR SHARE)
        model_ref = spec.get("modelRef")
        if not model_ref:
            raise AgentValidationError("Missing modelRef in agent spec")
        try:
            model_uuid = UUID(str(model_ref))
        except (ValueError, TypeError) as exc:
            raise AgentValidationError(f"Invalid modelRef UUID: {model_ref}") from exc

        model_cfg = await self.resolve_model_config(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            model_id=model_uuid,
            for_share=True,
        )
        if model_cfg is None:
            raise AgentValidationError(f"Model configuration '{model_uuid}' not found in workspace")
        if model_cfg.status != "active":
            raise AgentValidationError(f"Model configuration is {model_cfg.status}")

        # Resolve tool versions (FOR SHARE)
        tool_refs = spec.get("toolRefs") or []
        resolved_tools: list[ResolvedToolVersion] = []
        for tool_ref in tool_refs:
            try:
                t_uuid = UUID(str(tool_ref))
            except (ValueError, TypeError) as exc:
                raise AgentValidationError(f"Invalid toolRef UUID: {tool_ref}") from exc
            t_res = await self.resolve_tool_version(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                tool_version_id=t_uuid,
                caller_role=caller_role,
                for_share=True,
            )
            if t_res is None:
                raise AgentValidationError(f"Tool version '{t_uuid}' not found in workspace")
            if t_res.kind == "mcp":
                raise AgentValidationError("MCP tools are not supported")
            if not t_res.has_invoke_grant:
                raise AgentValidationError(
                    f"Caller role lacks invoke grant for tool '{t_res.tool_key}'"
                )
            resolved_tools.append(t_res)

        # Resolve skills (FOR SHARE)
        skill_refs = spec.get("skillRefs") or []
        resolved_skills: list[ResolvedSkillVersion] = []
        for s_ref in skill_refs:
            if not isinstance(s_ref, dict):
                raise AgentValidationError("Skill reference must be an object")
            try:
                s_id = UUID(str(s_ref.get("skillId")))
                s_ver_id = UUID(str(s_ref.get("skillVersionId")))
            except (ValueError, TypeError) as exc:
                raise AgentValidationError("Invalid skillId or skillVersionId UUID") from exc
            s_res = await self.resolve_skill_version(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                skill_id=s_id,
                skill_version_id=s_ver_id,
                for_share=True,
            )
            if s_res is None or s_res.skill_id != s_id:
                raise AgentValidationError(
                    f"Skill version '{s_ver_id}' not found for skill '{s_id}' in workspace"
                )
            if s_res.skill_status != "active":
                raise AgentValidationError(f"Skill '{s_res.skill_slug}' is {s_res.skill_status}")
            resolved_skills.append(s_res)

        # Resolve knowledge bases (FOR SHARE)
        knowledge_refs = spec.get("knowledgeRefs") or []
        resolved_kbs: list[ResolvedKnowledgeBase] = []
        for kb_ref in knowledge_refs:
            try:
                kb_id = UUID(str(kb_ref))
            except (ValueError, TypeError) as exc:
                raise AgentValidationError(f"Invalid knowledgeRef UUID: {kb_ref}") from exc
            kb_res = await self.resolve_knowledge_base(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                base_id=kb_id,
                user_id=actor_user_id,
                caller_role=caller_role,
                for_share=True,
            )
            if kb_res is None or not kb_res.has_access:
                raise AgentValidationError(
                    f"Knowledge base '{kb_id}' not found or access denied in workspace"
                )
            resolved_kbs.append(kb_res)

        # Step 7: Allocate next sequential version number
        ver_stmt = text(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
            "FROM catalog.agent_versions "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND agent_id = :agent_id"
        )
        try:
            ver_res = await session.execute(
                ver_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
            )
            next_version = int(ver_res.scalar_one())
        except SQLAlchemyError as exc:
            logger.warning("Failed to allocate next agent version (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 8: Build canonical manifest and compute bundle SHA-256
        model_dict = {
            "id": str(model_cfg.id),
            "name": model_cfg.name,
            "provider": model_cfg.provider,
            "providerModelId": model_cfg.provider_model_id,
        }
        sorted_tools = sorted(
            [
                {
                    "toolId": str(t.tool_id),
                    "toolVersionId": str(t.id),
                    "toolKey": t.tool_key,
                    "version": t.version,
                    "sha256": t.sha256,
                }
                for t in resolved_tools
            ],
            key=lambda x: (x["toolKey"], x["version"]),
        )
        sorted_skills = sorted(
            [
                {
                    "skillId": str(s.skill_id),
                    "skillVersionId": str(s.skill_version_id),
                    "slug": s.skill_slug,
                    "version": s.version,
                    "bundleSha256": s.bundle_sha256,
                }
                for s in resolved_skills
            ],
            key=lambda x: (x["slug"], x["version"]),
        )
        sorted_kbs = sorted(
            [
                {
                    "knowledgeBaseId": str(kb.id),
                    "slug": kb.slug,
                }
                for kb in resolved_kbs
            ],
            key=lambda x: x["slug"],
        )

        manifest = build_agent_manifest(
            agent_id=agent_id,
            slug=str(agent_row["slug"]),
            spec=spec,
            model=model_dict,
            tools=sorted_tools,
            skills=sorted_skills,
            knowledge_bases=sorted_kbs,
        )
        bundle_sha256 = compute_bundle_sha256(manifest)

        # Step 9: Insert version in unsealed state (bindings_sealed_at IS NULL)
        version_id = uuid4()
        insert_version_stmt = text(
            "INSERT INTO catalog.agent_versions ("
            "    tenant_id, workspace_id, id, agent_id, version, schema_version, "
            "    frozen_spec, bundle_sha256, bundle_manifest, workflow_version_id, "
            "    model_config_id, published_by, bindings_sealed_at, created_at"
            ") VALUES ("
            "    :tenant_id, :workspace_id, :id, :agent_id, :version, '1.0', "
            "    CAST(:frozen_spec AS jsonb), :bundle_sha256, CAST(:bundle_manifest AS jsonb), NULL, "
            "    :model_config_id, :published_by, NULL, now()"
            ") RETURNING created_at"
        )
        try:
            v_res = await session.execute(
                insert_version_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": version_id,
                    "agent_id": agent_id,
                    "version": next_version,
                    "frozen_spec": json.dumps(spec),
                    "bundle_sha256": bundle_sha256,
                    "bundle_manifest": json.dumps(manifest),
                    "model_config_id": model_cfg.id,
                    "published_by": actor_user_id,
                },
            )
            v_row = v_res.mappings().one()
            version_created_at = v_row["created_at"]
        except SQLAlchemyError as exc:
            logger.warning("Failed to insert agent version (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 10: Insert bindings before sealing
        try:
            for t in resolved_tools:
                await session.execute(
                    text(
                        "INSERT INTO catalog.agent_tool_bindings ("
                        "    tenant_id, workspace_id, agent_version_id, tool_version_id, usage_mode"
                        ") VALUES (:tenant_id, :workspace_id, :version_id, :tool_version_id, 'allowed')"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "version_id": version_id,
                        "tool_version_id": t.id,
                    },
                )

            for kb in resolved_kbs:
                await session.execute(
                    text(
                        "INSERT INTO catalog.agent_knowledge_bindings ("
                        "    tenant_id, workspace_id, agent_version_id, base_id"
                        ") VALUES (:tenant_id, :workspace_id, :version_id, :base_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "version_id": version_id,
                        "base_id": kb.id,
                    },
                )

            for s in resolved_skills:
                await session.execute(
                    text(
                        "INSERT INTO catalog.agent_skill_bindings ("
                        "    tenant_id, workspace_id, agent_version_id, skill_id, skill_version_id"
                        ") VALUES (:tenant_id, :workspace_id, :version_id, :skill_id, :skill_version_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "version_id": version_id,
                        "skill_id": s.skill_id,
                        "skill_version_id": s.skill_version_id,
                    },
                )
        except SQLAlchemyError as exc:
            logger.warning("Failed to insert agent bindings (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 11: Seal bindings (transition bindings_sealed_at from NULL to now())
        seal_stmt = text(
            "UPDATE catalog.agent_versions "
            "SET bindings_sealed_at = now() "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :version_id"
        )
        try:
            await session.execute(
                seal_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "version_id": version_id},
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to seal agent version bindings (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 12: Prepare durable receipt response body
        response_body = {
            "agent_id": str(agent_id),
            "agent_version_id": str(version_id),
            "version": next_version,
            "bundle_sha256": bundle_sha256,
            "is_immutable": True,
            "created_at": version_created_at.isoformat(),
            "spec_hash": bundle_sha256,
        }

        # Step 13: Insert durable publish receipt
        receipt_id = uuid4()
        receipt_stmt = text(
            "INSERT INTO catalog.agent_publish_receipts ("
            "    tenant_id, workspace_id, id, agent_id, operation_key, request_sha256, "
            "    actor_user_id, agent_version_id, response_status, response_body, created_at"
            ") VALUES ("
            "    :tenant_id, :workspace_id, :id, :agent_id, :operation_key, :request_sha256, "
            "    :actor_user_id, :agent_version_id, 200, CAST(:response_body AS jsonb), now()"
            ")"
        )
        try:
            await session.execute(
                receipt_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": receipt_id,
                    "agent_id": agent_id,
                    "operation_key": operation_key,
                    "request_sha256": request_sha256,
                    "actor_user_id": actor_user_id,
                    "agent_version_id": version_id,
                    "response_body": json.dumps(response_body),
                },
            )
        except IntegrityError as exc:
            orig_msg = str(getattr(exc, "orig", exc)).lower()
            if "unique" in orig_msg or "operation_key" in orig_msg:
                raise AgentIdempotencyConflictError(
                    f"Operation key '{operation_key}' was concurrently executed"
                ) from exc
            raise AgentDatabaseUnavailableError("Failed to store publish receipt") from exc
        except SQLAlchemyError as exc:
            logger.warning("Failed to insert publish receipt (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 14: Atomic audit event
        await write_audit_event(
            session,
            AuditEventWrite(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_subject=str(actor_user_id),
                action="agent.publish",
                resource_type="agent_version",
                resource_id=str(version_id),
                outcome="succeeded",
                request_id=request_id,
                trace_id=trace_id,
                details_redacted={
                    "agent_id": str(agent_id),
                    "version": next_version,
                    "bundle_sha256": bundle_sha256,
                    "operation_key": operation_key,
                },
            ),
        )

        # Step 15: Outbox event into integration.outbox_events
        outbox_id = uuid4()
        outbox_payload = {
            "id": str(outbox_id),
            "type": "agent.published",
            "aggregate_id": str(agent_id),
            "version": next_version,
            "agent_version_id": str(version_id),
            "bundle_sha256": bundle_sha256,
        }
        outbox_envelope = {
            "id": str(outbox_id),
            "topic": "catalog.agent.events",
            "partition_key": f"{tenant_id}:{workspace_id}",
            "headers": {},
            "data": outbox_payload,
        }
        try:
            await session.execute(
                text(
                    "INSERT INTO integration.outbox_events ("
                    "    tenant_id, workspace_id, id, aggregate_type, aggregate_id, event_type, payload, state"
                    ") VALUES ("
                    "    :tenant_id, :workspace_id, :id, 'agent', :aggregate_id, 'agent.published', "
                    "    CAST(:payload AS jsonb), 'pending'"
                    ")"
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": outbox_id,
                    "aggregate_id": agent_id,
                    "payload": json.dumps(outbox_envelope),
                },
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to insert outbox event (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Step 16: Return immutable publish outcome
        return PublishResultEntity(
            agent_id=agent_id,
            agent_version_id=version_id,
            version=next_version,
            bundle_sha256=bundle_sha256,
            is_immutable=True,
            created_at=version_created_at,
            is_replay=False,
        )


agent_repository = AgentRepository()

__all__ = ["AgentRepository", "agent_repository"]
