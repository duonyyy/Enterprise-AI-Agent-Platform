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
import math
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from agent_contracts.manifest import (
    CanonicalizationError,
    build_agent_manifest,
    canonicalize_json,
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
    AgentPublicationEntity,
    AgentPublishReceiptEntity,
    AgentVersionMetadataEntity,
    PromoteResultEntity,
    PublishResultEntity,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_PERMITTED_WRITE_ROLES = frozenset({"owner", "admin", "developer"})
_SUPPORTED_TOOL_KINDS = frozenset({"builtin", "http"})

# Approved non-secret model generation settings for manifest snapshot.
# Secret values (api_key, credentials, tokens) are NEVER included.
# This allowlist follows R04-SNAPSHOT; changes require manifest contract review.
_MODEL_SETTINGS_ALLOWLIST = frozenset({
    "temperature",
    "top_p",
    "top_k",
    "max_output_tokens",
    "frequency_penalty",
    "presence_penalty",
    "stop_sequences",
    "response_format",
    "seed",
})

_MODEL_SETTINGS_DENYLIST = frozenset({
    "api_key", "api_secret", "token", "credential", "password",
    "secret", "access_key", "private_key", "authorization", "x-api-key",
})

# JSON Schema map keywords whose immediate keys are output field/definition names,
# not credential-bearing provider configuration keys (RF04-06).
_JSON_SCHEMA_NAME_MAP_KEYS = frozenset({
    "properties",
    "patternProperties",
    "dependentRequired",
    "dependentSchemas",
    "$defs",
    "definitions",
})

# Patterns identifying actual embedded raw credential material in string values.
_EMBEDDED_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bbasic\s+[A-Za-z0-9+/=]{12,}", re.IGNORECASE),
    re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|api[_-]?secret|access[_-]?key|private[_-]?key|"
        r"client[_-]?secret|password|passwd|auth[_-]?token|access[_-]?token|"
        r"refresh[_-]?token|secret[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}",
        re.IGNORECASE,
    ),
)


def _contains_embedded_credential(text_value: str) -> bool:
    """Return True if a string value appears to contain raw credential material."""
    return any(pattern.search(text_value) for pattern in _EMBEDDED_CREDENTIAL_PATTERNS)


def _validate_response_format_node(
    node: Any,
    *,
    in_schema: bool,
    is_name_map: bool,
) -> None:
    """Recursively validate response_format while distinguishing schema field names from config keys."""
    if isinstance(node, str):
        if _contains_embedded_credential(node):
            raise AgentValidationError(
                "Setting 'response_format' contains forbidden embedded credential material",
                code="invalid_model_setting",
            )
        return
    if isinstance(node, list):
        for item in node:
            _validate_response_format_node(item, in_schema=in_schema, is_name_map=False)
        return
    if isinstance(node, dict):
        for k, v in node.items():
            k_str = str(k)
            k_lower = k_str.lower()
            if not is_name_map and any(deny in k_lower for deny in _MODEL_SETTINGS_DENYLIST):
                raise AgentValidationError(
                    f"Forbidden key in response_format: '{k_str}'",
                    code="invalid_model_setting",
                )
            if is_name_map:
                _validate_response_format_node(v, in_schema=True, is_name_map=False)
            else:
                child_in_schema = in_schema or (k_str == "schema")
                child_is_name_map = child_in_schema and (k_str in _JSON_SCHEMA_NAME_MAP_KEYS)
                _validate_response_format_node(
                    v,
                    in_schema=child_in_schema,
                    is_name_map=child_is_name_map,
                )


def _validate_snapshot_setting(key: str, value: Any) -> Any:
    """Validate snapshot setting type, bounds, canonicalizability, and ensure no secrets."""
    if key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AgentValidationError(f"Setting '{key}' must be a number", code="invalid_model_setting")
        if not math.isfinite(value):
            raise AgentValidationError(f"Setting '{key}' must be finite", code="invalid_model_setting")
        if key == "temperature" and not (0.0 <= value <= 2.0):
            raise AgentValidationError(f"Setting '{key}' out of range [0.0, 2.0]", code="invalid_model_setting")
        if key == "top_p" and not (0.0 <= value <= 1.0):
            raise AgentValidationError(f"Setting '{key}' out of range [0.0, 1.0]", code="invalid_model_setting")
        if key in ("frequency_penalty", "presence_penalty") and not (-2.0 <= value <= 2.0):
            raise AgentValidationError(f"Setting '{key}' out of range [-2.0, 2.0]", code="invalid_model_setting")
        return value
    if key in ("top_k", "max_output_tokens"):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise AgentValidationError(f"Setting '{key}' must be a positive integer", code="invalid_model_setting")
        return value
    if key == "seed":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AgentValidationError("Setting 'seed' must be an integer", code="invalid_model_setting")
        return value
    if key == "stop_sequences":
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise AgentValidationError("Setting 'stop_sequences' must be a list of strings", code="invalid_model_setting")
        for s in value:
            if _contains_embedded_credential(s):
                raise AgentValidationError(
                    "Setting 'stop_sequences' contains forbidden embedded credential material",
                    code="invalid_model_setting",
                )
        return value
    if key == "response_format":
        if isinstance(value, str):
            if _contains_embedded_credential(value):
                raise AgentValidationError(
                    "Setting 'response_format' contains forbidden embedded credential material",
                    code="invalid_model_setting",
                )
            return value
        if isinstance(value, dict):
            _validate_response_format_node(value, in_schema=False, is_name_map=False)
            return value
        raise AgentValidationError("Setting 'response_format' must be an object or string", code="invalid_model_setting")
    return value


def extract_effective_model_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Filter model settings through the approved non-secret allowlist and validate values."""
    effective_settings: dict[str, Any] = {}
    for key, value in settings.items():
        if key not in _MODEL_SETTINGS_ALLOWLIST:
            continue
        effective_settings[key] = _validate_snapshot_setting(key, value)
    try:
        canonicalize_json(effective_settings)
    except CanonicalizationError as exc:
        raise AgentValidationError(
            "Allowlisted model settings cannot be canonically serialized under RFC 8785",
            code="invalid_model_setting",
        ) from exc
    return effective_settings


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
        """Verify target workspace and parent tenant are active with row locks.

        Lock Hierarchy & Serialization Design:
        - iam.workspaces (w) is row-locked (FOR SHARE OF w for read/publish guards,
          FOR UPDATE OF w for promotion mutations) to serialize workspace operations.
        - iam.tenants (t) is row-locked FOR SHARE (FOR SHARE OF t) to eliminate TOCTOU
          races where an authorized writer suspends the tenant mid-transaction, while
          allowing concurrent non-conflicting operations across sibling workspaces.
        """
        lock_mode = "FOR UPDATE OF w FOR SHARE OF t" if for_update else "FOR SHARE OF w, t"
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
        lock_clause = " FOR SHARE OF tv, t" if for_share else ""
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
            # Lock the authority grant row that the publish decision relies on,
            # preventing it from being revoked mid-transaction (R04C-02).
            # Fail-closed: verify the grant row was actually locked.
            has_invoke_grant = False
            if for_share and row["has_invoke_grant"]:
                try:
                    lock_res = await session.execute(
                        text(
                            "SELECT 1 FROM catalog.tool_grants "
                            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                            "  AND tool_id = :tool_id "
                            "  AND principal_kind = 'role' AND principal_key = :caller_role "
                            "  AND allowed_operation = 'invoke' "
                            "FOR SHARE"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "tool_id": row["tool_id"],
                            "caller_role": caller_role,
                        },
                    )
                    has_invoke_grant = bool(lock_res.scalars().all())
                except SQLAlchemyError as exc:
                    logger.warning(
                        "resolve_tool_version grant lock error (%s)",
                        type(exc).__name__,
                    )
                    raise AgentDatabaseUnavailableError() from exc
            else:
                has_invoke_grant = bool(row["has_invoke_grant"]) if not for_share else False

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
                has_invoke_grant=has_invoke_grant,
            )
        except SQLAlchemyError as exc:
            logger.warning("resolve_tool_version database error (%s): %s", type(exc).__name__, exc)
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
        lock_clause = " FOR SHARE OF sv, s" if for_share else ""  # R04C-02: lock parent skill too
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
            # Lock the authority grant row for restricted bases, preventing
            # revocation mid-transaction (R04C-02).
            # Fail-closed: verify the grant row was actually locked.
            has_access = False
            if for_share and row["visibility"] != "workspace" and row["has_access"]:
                try:
                    lock_res = await session.execute(
                        text(
                            "SELECT 1 FROM knowledge.base_grants "
                            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                            "  AND base_id = :base_id "
                            "  AND permission IN ('read', 'manage') "
                            "  AND ( "
                            "      (principal_kind = 'user' AND principal_key = :user_id_str) OR "
                            "      (principal_kind = 'role' AND principal_key = :caller_role) "
                            "  ) "
                            "ORDER BY id FOR SHARE"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "base_id": base_id,
                            "user_id_str": str(user_id),
                            "caller_role": caller_role,
                        },
                    )
                    # Several grants can independently authorize the same actor
                    # (user + role, or read + manage). Consume and lock the full
                    # matching set instead of imposing a uniqueness invariant.
                    has_access = bool(lock_res.scalars().all())
                except SQLAlchemyError as exc:
                    logger.warning(
                        "resolve_knowledge_base grant lock error (%s)",
                        type(exc).__name__,
                    )
                    raise AgentDatabaseUnavailableError() from exc
            else:
                has_access = bool(row["has_access"]) if (not for_share or row["visibility"] == "workspace") else False

            return ResolvedKnowledgeBase(
                id=row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                slug=row["slug"],
                name=row["name"],
                visibility=str(row["visibility"]),
                has_access=has_access,
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
        seen_tool_version_ids: set[UUID] = set()
        for tool_ref in tool_refs:
            try:
                t_uuid = UUID(str(tool_ref))
            except (ValueError, TypeError) as exc:
                raise AgentValidationError(f"Invalid toolRef UUID: {tool_ref}") from exc
            if t_uuid in seen_tool_version_ids:
                raise AgentValidationError(
                    f"Duplicate tool version reference '{t_uuid}' in toolRefs",
                    code="invalid_spec",
                )
            seen_tool_version_ids.add(t_uuid)
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
            if t_res.kind not in _SUPPORTED_TOOL_KINDS:
                raise AgentValidationError(
                    "MCP tools are not supported"
                    if t_res.kind == "mcp"
                    else f"Tool kind '{t_res.kind}' is not supported",
                    code="unsupported_capability",
                )
            if not t_res.has_invoke_grant:
                raise AgentValidationError(
                    f"Caller role lacks invoke grant for tool '{t_res.tool_key}'"
                )
            resolved_tools.append(t_res)

        # Resolve skills (FOR SHARE)
        skill_refs = spec.get("skillRefs") or []
        resolved_skills: list[ResolvedSkillVersion] = []
        seen_skill_versions_by_skill: dict[UUID, UUID] = {}
        for s_ref in skill_refs:
            if not isinstance(s_ref, dict):
                raise AgentValidationError("Skill reference must be an object")
            try:
                s_id = UUID(str(s_ref.get("skillId")))
                s_ver_id = UUID(str(s_ref.get("skillVersionId")))
            except (ValueError, TypeError) as exc:
                raise AgentValidationError("Invalid skillId or skillVersionId UUID") from exc
            if s_id in seen_skill_versions_by_skill:
                if seen_skill_versions_by_skill[s_id] == s_ver_id:
                    raise AgentValidationError(
                        f"Duplicate skill reference '{s_id}' in skillRefs",
                        code="invalid_spec",
                    )
                raise AgentValidationError(
                    f"Conflicting skill references: multiple versions bound for skill '{s_id}'",
                    code="invalid_spec",
                )
            seen_skill_versions_by_skill[s_id] = s_ver_id
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
        seen_kb_ids: set[UUID] = set()
        for kb_ref in knowledge_refs:
            try:
                kb_id = UUID(str(kb_ref))
            except (ValueError, TypeError) as exc:
                raise AgentValidationError(f"Invalid knowledgeRef UUID: {kb_ref}") from exc
            if kb_id in seen_kb_ids:
                raise AgentValidationError(
                    f"Duplicate knowledge base reference '{kb_id}' in knowledgeRefs",
                    code="invalid_spec",
                )
            seen_kb_ids.add(kb_id)
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
        # Filter model settings through allowlist — never include secrets
        effective_settings = extract_effective_model_settings(model_cfg.settings)

        model_dict = {
            "id": str(model_cfg.id),
            "name": model_cfg.name,
            "provider": model_cfg.provider,
            "providerModelId": model_cfg.provider_model_id,
            "effectiveSettings": effective_settings,
            "modelConfigId": str(model_cfg.id),
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
        try:
            bundle_sha256 = compute_bundle_sha256(manifest)
        except CanonicalizationError as exc:
            raise AgentValidationError(
                "Agent specification or resolved dependency snapshot cannot be canonically serialized under RFC 8785",
                code="invalid_spec",
            ) from exc

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

    async def list_versions(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[AgentVersionMetadataEntity], str | None]:
        """List published immutable versions for an agent with keyset pagination."""
        # Verify active workspace
        await self._verify_active_workspace(session, tenant_id=tenant_id, workspace_id=workspace_id)

        # Verify agent exists in scope
        agent_exists_stmt = text(
            "SELECT 1 FROM catalog.agents "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :agent_id"
        )
        try:
            agent_exists_res = await session.execute(
                agent_exists_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
            )
            if agent_exists_res.scalar_one_or_none() is None:
                raise AgentNotFoundError("Agent not found in workspace")
        except SQLAlchemyError as exc:
            logger.warning("Failed to verify agent existence (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        cursor_version: int | None = None
        if cursor is not None and cursor.strip():
            try:
                raw_cursor = base64.urlsafe_b64decode(cursor.strip()).decode("ascii")
                cursor_version = int(raw_cursor)
            except Exception as exc:
                raise AgentValidationError("Invalid cursor format", code="invalid_cursor") from exc

        query_sql = (
            "SELECT av.id, av.agent_id, av.version, av.bundle_sha256, av.created_at, av.published_by, "
            "       mc.name AS model_name "
            "FROM catalog.agent_versions av "
            "LEFT JOIN catalog.model_configs mc ON av.model_config_id = mc.id "
            "     AND av.tenant_id = mc.tenant_id AND av.workspace_id = mc.workspace_id "
            "WHERE av.tenant_id = :tenant_id AND av.workspace_id = :workspace_id AND av.agent_id = :agent_id "
        )
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "limit_plus_one": limit + 1,
        }

        if cursor_version is not None:
            query_sql += "AND av.version < :cursor_version "
            params["cursor_version"] = cursor_version

        query_sql += "ORDER BY av.version DESC LIMIT :limit_plus_one"

        try:
            res = (await session.execute(text(query_sql), params)).mappings().all()
        except SQLAlchemyError as exc:
            logger.warning("Failed to list agent versions (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        items: list[AgentVersionMetadataEntity] = []
        has_more = len(res) > limit
        page_rows = res[:limit]

        for r in page_rows:
            items.append(
                AgentVersionMetadataEntity(
                    agent_id=r["agent_id"],
                    agent_version_id=r["id"],
                    version=int(r["version"]),
                    bundle_sha256=str(r["bundle_sha256"]),
                    is_immutable=True,
                    created_at=r["created_at"],
                    published_by=r["published_by"],
                    model_name=r["model_name"],
                )
            )

        next_cursor = None
        if has_more and page_rows:
            last_version = page_rows[-1]["version"]
            next_cursor = base64.urlsafe_b64encode(str(last_version).encode("ascii")).decode("ascii")

        return items, next_cursor

    async def list_publications(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
    ) -> list[AgentPublicationEntity]:
        """List active environment publications for an agent."""
        # Verify active workspace
        await self._verify_active_workspace(session, tenant_id=tenant_id, workspace_id=workspace_id)

        # Verify agent exists in scope
        agent_exists_stmt = text(
            "SELECT 1 FROM catalog.agents "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :agent_id"
        )
        try:
            agent_exists_res = await session.execute(
                agent_exists_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
            )
            if agent_exists_res.scalar_one_or_none() is None:
                raise AgentNotFoundError("Agent not found in workspace")
        except SQLAlchemyError as exc:
            logger.warning("Failed to verify agent existence (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        query_sql = (
            "SELECT ap.id, ap.agent_id, ap.agent_version_id, ap.environment, ap.revision, ap.published_at, "
            "       av.version, av.bundle_sha256 "
            "FROM catalog.agent_publications ap "
            "JOIN catalog.agent_versions av ON ap.agent_version_id = av.id "
            "     AND ap.tenant_id = av.tenant_id AND ap.workspace_id = av.workspace_id AND ap.agent_id = av.agent_id "
            "WHERE ap.tenant_id = :tenant_id AND ap.workspace_id = :workspace_id AND ap.agent_id = :agent_id "
            "ORDER BY CASE ap.environment "
            "           WHEN 'development' THEN 1 "
            "           WHEN 'staging' THEN 2 "
            "           WHEN 'production' THEN 3 "
            "           ELSE 4 END ASC"
        )

        try:
            res = (
                (
                    await session.execute(
                        text(query_sql),
                        {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to list agent publications (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        return [
            AgentPublicationEntity(
                publication_id=r["id"],
                agent_id=r["agent_id"],
                agent_version_id=r["agent_version_id"],
                version=int(r["version"]),
                environment=str(r["environment"]),
                revision=int(r["revision"]),
                bundle_sha256=str(r["bundle_sha256"]),
                published_at=r["published_at"],
            )
            for r in res
        ]

    async def promote_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        agent_id: UUID,
        agent_version_id: UUID,
        environment: str,
        expected_publication_revision: int,
        actor_user_id: UUID,
        caller_roles: list[str],
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> PromoteResultEntity:
        """Promote or rollback an agent version to an environment using CAS."""
        # Step 1: Workspace lock FOR UPDATE
        await self._verify_active_workspace(
            session, tenant_id=tenant_id, workspace_id=workspace_id, for_update=True
        )

        # Step 2: Recheck live authority (Owner/Admin required)
        role = await self._recheck_live_authority(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="promote",
        )
        if role not in ("owner", "admin"):
            raise AgentAccessDeniedError(
                f"Role '{role}' is not permitted to promote agent versions (Owner or Admin required)"
            )

        # Step 3: Agent row check FOR SHARE
        agent_stmt = text(
            "SELECT id, archived_at "
            "FROM catalog.agents "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :agent_id "
            "FOR SHARE"
        )
        try:
            agent_res = (
                (
                    await session.execute(
                        agent_stmt,
                        {"tenant_id": tenant_id, "workspace_id": workspace_id, "agent_id": agent_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to lock agent row (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if agent_res is None:
            raise AgentNotFoundError("Agent not found in workspace")
        if agent_res["archived_at"] is not None:
            raise AgentArchivedError("Archived agent cannot be promoted")

        # Step 4: Target version verification FOR SHARE
        ver_stmt = text(
            "SELECT id, version, bundle_sha256, model_config_id, bindings_sealed_at "
            "FROM catalog.agent_versions "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND agent_id = :agent_id AND id = :version_id "
            "FOR SHARE"
        )
        try:
            ver_res = (
                (
                    await session.execute(
                        ver_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "agent_id": agent_id,
                            "version_id": agent_version_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to lock agent version row (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        if ver_res is None:
            raise AgentNotFoundError("Target version not found for this agent")
        if ver_res["bindings_sealed_at"] is None:
            raise AgentValidationError(
                "Cannot promote an unsealed agent version",
                code="unsealed_version",
            )

        target_version_number = int(ver_res["version"])
        target_bundle_sha256 = str(ver_res["bundle_sha256"])
        model_config_id = ver_res["model_config_id"]

        # Step 5: Live dependency admissibility recheck under FOR SHARE locks
        # 5a. Model configuration
        if model_config_id is not None:
            mc_stmt = text(
                "SELECT status "
                "FROM catalog.model_configs "
                "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :model_config_id "
                "FOR SHARE"
            )
            try:
                mc_res = (
                    (
                        await session.execute(
                            mc_stmt,
                            {
                                "tenant_id": tenant_id,
                                "workspace_id": workspace_id,
                                "model_config_id": model_config_id,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            except SQLAlchemyError as exc:
                logger.warning("Failed to verify model config (%s)", type(exc).__name__)
                raise AgentDatabaseUnavailableError() from exc

            if mc_res is None or str(mc_res["status"]) != "active":
                raise AgentValidationError(
                    "Model configuration in target version is unavailable or inactive",
                    code="dependency_unavailable",
                )

        # 5b. Tool dependencies (RF04-03: verify current tool kind under parent FOR SHARE lock)
        tools_stmt = text(
            "SELECT tv.id AS tool_version_id, tv.tool_id, t.tool_key, t.kind, "
            "       EXISTS("
            "           SELECT 1 FROM catalog.tool_grants tg "
            "           WHERE tg.tool_id = t.id AND tg.tenant_id = t.tenant_id AND tg.workspace_id = t.workspace_id "
            "             AND tg.allowed_operation = 'invoke' AND tg.principal_kind = 'role' AND tg.principal_key = :caller_role"
            "       ) AS has_grant "
            "FROM catalog.agent_tool_bindings atb "
            "JOIN catalog.tool_versions tv ON atb.tool_version_id = tv.id "
            "     AND atb.tenant_id = tv.tenant_id AND atb.workspace_id = tv.workspace_id "
            "JOIN catalog.tools t ON tv.tool_id = t.id "
            "     AND tv.tenant_id = t.tenant_id AND tv.workspace_id = t.workspace_id "
            "WHERE atb.tenant_id = :tenant_id AND atb.workspace_id = :workspace_id AND atb.agent_version_id = :version_id "
            "ORDER BY tv.id "
            "FOR SHARE OF tv, t"
        )
        try:
            tools_res = (
                (
                    await session.execute(
                        tools_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "version_id": agent_version_id,
                            "caller_role": role,
                        },
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to verify tool bindings (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        for tr in tools_res:
            tool_kind = str(tr["kind"])
            if tool_kind not in _SUPPORTED_TOOL_KINDS:
                raise AgentValidationError(
                    "MCP tools are not supported"
                    if tool_kind == "mcp"
                    else f"Tool kind '{tool_kind}' is not supported",
                    code="unsupported_capability",
                )
            if not tr["has_grant"]:
                raise AgentValidationError(
                    f"Caller role lacks invoke grant for tool version '{tr['tool_version_id']}' in target version",
                    code="dependency_not_permitted",
                )
            # Lock and verify tool grant row under FOR SHARE to eliminate TOCTOU revoke race
            try:
                t_lock_res = await session.execute(
                    text(
                        "SELECT id FROM catalog.tool_grants "
                        "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                        "  AND tool_id = :tool_id "
                        "  AND principal_kind = 'role' AND principal_key = :caller_role "
                        "  AND allowed_operation = 'invoke' "
                        "FOR SHARE"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "tool_id": tr["tool_id"],
                        "caller_role": role,
                    },
                )
                if not t_lock_res.scalars().all():
                    raise AgentValidationError(
                        f"Caller role lacks invoke grant for tool version '{tr['tool_version_id']}' in target version",
                        code="dependency_not_permitted",
                    )
            except SQLAlchemyError as exc:
                if isinstance(exc, AgentValidationError):
                    raise
                logger.warning("Failed to lock tool grant for promote (%s)", type(exc).__name__)
                raise AgentDatabaseUnavailableError() from exc

        # 5c. Skill dependencies
        skills_stmt = text(
            "SELECT sv.id AS skill_version_id, s.status AS skill_status "
            "FROM catalog.agent_skill_bindings asb "
            "JOIN catalog.skill_versions sv ON asb.skill_version_id = sv.id "
            "     AND asb.tenant_id = sv.tenant_id AND asb.workspace_id = sv.workspace_id "
            "JOIN catalog.skills s ON sv.skill_id = s.id "
            "     AND sv.tenant_id = s.tenant_id AND sv.workspace_id = s.workspace_id "
            "WHERE asb.tenant_id = :tenant_id AND asb.workspace_id = :workspace_id AND asb.agent_version_id = :version_id "
            "FOR SHARE OF sv, s"
        )
        try:
            skills_res = (
                (
                    await session.execute(
                        skills_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "version_id": agent_version_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to verify skill bindings (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        for sr in skills_res:
            if str(sr["skill_status"]) != "active":
                raise AgentValidationError(
                    f"Skill version '{sr['skill_version_id']}' parent skill is not active",
                    code="dependency_unavailable",
                )

        # 5d. Knowledge base dependencies
        kb_stmt = text(
            "SELECT kb.id, kb.visibility, "
            "       ( "
            "           kb.visibility = 'workspace' OR "
            "           EXISTS("
            "               SELECT 1 FROM knowledge.base_grants bg "
            "               WHERE bg.base_id = kb.id AND bg.tenant_id = kb.tenant_id AND bg.workspace_id = kb.workspace_id "
            "                 AND bg.permission IN ('read', 'manage') "
            "                 AND ( "
            "                     (bg.principal_kind = 'user' AND bg.principal_key = :actor_user_id_text) OR "
            "                     (bg.principal_kind = 'role' AND bg.principal_key = :caller_role) "
            "                 )"
            "           ) "
            "       ) AS has_grant "
            "FROM catalog.agent_knowledge_bindings akb "
            "JOIN knowledge.bases kb ON akb.base_id = kb.id "
            "     AND akb.tenant_id = kb.tenant_id AND akb.workspace_id = kb.workspace_id "
            "WHERE akb.tenant_id = :tenant_id AND akb.workspace_id = :workspace_id AND akb.agent_version_id = :version_id "
            "FOR SHARE OF kb"
        )
        try:
            kb_res = (
                (
                    await session.execute(
                        kb_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "version_id": agent_version_id,
                            "actor_user_id_text": str(actor_user_id),
                            "caller_role": role,
                        },
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to verify knowledge bindings (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        for kr in kb_res:
            if str(kr["visibility"]) != "workspace":
                if not kr["has_grant"]:
                    raise AgentValidationError(
                        f"Knowledge base '{kr['id']}' access denied in target version",
                        code="dependency_not_permitted",
                    )
                # Lock and verify KB grant row under FOR SHARE to eliminate TOCTOU revoke race
                try:
                    kb_lock_res = await session.execute(
                        text(
                            "SELECT id FROM knowledge.base_grants "
                            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id "
                            "  AND base_id = :base_id "
                            "  AND permission IN ('read', 'manage') "
                            "  AND ( "
                            "      (principal_kind = 'user' AND principal_key = :actor_user_id_text) OR "
                            "      (principal_kind = 'role' AND principal_key = :caller_role) "
                            "  ) "
                            "ORDER BY id FOR SHARE"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "base_id": kr["id"],
                            "actor_user_id_text": str(actor_user_id),
                            "caller_role": role,
                        },
                    )
                    if not kb_lock_res.scalars().all():
                        raise AgentValidationError(
                            f"Knowledge base '{kr['id']}' access denied in target version",
                            code="dependency_not_permitted",
                        )
                except SQLAlchemyError as exc:
                    if isinstance(exc, AgentValidationError):
                        raise
                    logger.warning("Failed to lock KB grant for promote (%s)", type(exc).__name__)
                    raise AgentDatabaseUnavailableError() from exc

        # Step 6: Pointer row lock FOR UPDATE
        pointer_stmt = text(
            "SELECT ap.id, ap.revision, ap.agent_version_id, ap.published_at, av.version AS current_version "
            "FROM catalog.agent_publications ap "
            "JOIN catalog.agent_versions av ON ap.agent_version_id = av.id "
            "     AND ap.tenant_id = av.tenant_id AND ap.workspace_id = av.workspace_id AND ap.agent_id = av.agent_id "
            "WHERE ap.tenant_id = :tenant_id AND ap.workspace_id = :workspace_id AND ap.agent_id = :agent_id "
            "  AND ap.environment = :environment "
            "FOR UPDATE OF ap"
        )
        try:
            pointer_res = (
                (
                    await session.execute(
                        pointer_stmt,
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "agent_id": agent_id,
                            "environment": environment,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to lock publication pointer (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        now_dt = datetime.now().astimezone()

        if pointer_res is None:
            # Case A: First publication to this environment
            if expected_publication_revision != 0:
                raise AgentStaleRevisionError(
                    f"Stale publication revision: expected revision {expected_publication_revision} "
                    f"but publication pointer does not exist (expected 0)",
                    code="stale_revision",
                )

            pub_id = uuid4()
            insert_pub_stmt = text(
                "INSERT INTO catalog.agent_publications ("
                "    tenant_id, workspace_id, id, agent_id, agent_version_id, environment, revision, published_at"
                ") VALUES ("
                "    :tenant_id, :workspace_id, :id, :agent_id, :agent_version_id, :environment, 1, :published_at"
                ")"
            )
            try:
                await session.execute(
                    insert_pub_stmt,
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "id": pub_id,
                        "agent_id": agent_id,
                        "agent_version_id": agent_version_id,
                        "environment": environment,
                        "published_at": now_dt,
                    },
                )
            except IntegrityError as exc:
                orig_msg = str(getattr(exc, "orig", exc)).lower()
                if "unique" in orig_msg or "agent_publications" in orig_msg:
                    raise AgentStaleRevisionError(
                        "Concurrent promotion detected: publication pointer was created by another request (stale expected revision 0)",
                        code="stale_revision",
                    ) from exc
                logger.warning("Failed to insert publication pointer (%s)", type(exc).__name__)
                raise AgentDatabaseUnavailableError() from exc
            except SQLAlchemyError as exc:
                logger.warning("Failed to insert publication pointer (%s)", type(exc).__name__)
                raise AgentDatabaseUnavailableError() from exc

            # Audit event
            await write_audit_event(
                session,
                AuditEventWrite(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_subject=str(actor_user_id),
                    action="agent.promote",
                    resource_type="agent_publication",
                    resource_id=str(pub_id),
                    outcome="succeeded",
                    request_id=request_id,
                    trace_id=trace_id,
                    details_redacted={
                        "agent_id": str(agent_id),
                        "environment": environment,
                        "target_version_id": str(agent_version_id),
                        "version": target_version_number,
                        "previous_revision": 0,
                        "new_revision": 1,
                        "is_first": True,
                        "is_rollback": False,
                    },
                ),
            )

            # Outbox event
            outbox_id = uuid4()
            outbox_payload = {
                "id": str(outbox_id),
                "type": "agent.promoted",
                "aggregate_id": str(agent_id),
                "publication_id": str(pub_id),
                "environment": environment,
                "version": target_version_number,
                "agent_version_id": str(agent_version_id),
                "revision": 1,
                "is_first": True,
                "is_rollback": False,
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
                        "    :tenant_id, :workspace_id, :id, 'agent', :aggregate_id, 'agent.promoted', "
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

            return PromoteResultEntity(
                publication_id=pub_id,
                agent_id=agent_id,
                agent_version_id=agent_version_id,
                version=target_version_number,
                environment=environment,
                revision=1,
                bundle_sha256=target_bundle_sha256,
                published_at=now_dt,
                is_noop=False,
            )

        # Case B: Existing pointer found
        current_revision = int(pointer_res["revision"])
        current_version_id = pointer_res["agent_version_id"]
        current_version_number = int(pointer_res["current_version"])

        if current_revision != expected_publication_revision:
            raise AgentStaleRevisionError(
                f"Stale publication revision: expected revision {expected_publication_revision} "
                f"but current revision is {current_revision}",
                code="stale_revision",
            )

        if current_version_id == agent_version_id:
            # Authorized NO-OP: target version is already active at this revision
            return PromoteResultEntity(
                publication_id=pointer_res["id"],
                agent_id=agent_id,
                agent_version_id=agent_version_id,
                version=target_version_number,
                environment=environment,
                revision=current_revision,
                bundle_sha256=target_bundle_sha256,
                published_at=pointer_res["published_at"],
                is_noop=True,
            )

        # Target version is different (upgrade or rollback)
        new_revision = current_revision + 1
        is_rollback = target_version_number < current_version_number

        update_stmt = text(
            "UPDATE catalog.agent_publications "
            "SET agent_version_id = :agent_version_id, revision = :new_revision, published_at = :published_at "
            "WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :id"
        )
        try:
            await session.execute(
                update_stmt,
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "id": pointer_res["id"],
                    "agent_version_id": agent_version_id,
                    "new_revision": new_revision,
                    "published_at": now_dt,
                },
            )
        except SQLAlchemyError as exc:
            logger.warning("Failed to update publication pointer (%s)", type(exc).__name__)
            raise AgentDatabaseUnavailableError() from exc

        # Audit event
        await write_audit_event(
            session,
            AuditEventWrite(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_subject=str(actor_user_id),
                action="agent.promote",
                resource_type="agent_publication",
                resource_id=str(pointer_res["id"]),
                outcome="succeeded",
                request_id=request_id,
                trace_id=trace_id,
                details_redacted={
                    "agent_id": str(agent_id),
                    "environment": environment,
                    "previous_version_id": str(current_version_id),
                    "target_version_id": str(agent_version_id),
                    "previous_version": current_version_number,
                    "version": target_version_number,
                    "previous_revision": current_revision,
                    "new_revision": new_revision,
                    "is_first": False,
                    "is_rollback": is_rollback,
                },
            ),
        )

        # Outbox event
        outbox_id = uuid4()
        outbox_payload = {
            "id": str(outbox_id),
            "type": "agent.promoted",
            "aggregate_id": str(agent_id),
            "publication_id": str(pointer_res["id"]),
            "environment": environment,
            "previous_version": current_version_number,
            "version": target_version_number,
            "agent_version_id": str(agent_version_id),
            "previous_revision": current_revision,
            "revision": new_revision,
            "is_first": False,
            "is_rollback": is_rollback,
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
                    "    :tenant_id, :workspace_id, :id, 'agent', :aggregate_id, 'agent.promoted', "
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

        return PromoteResultEntity(
            publication_id=pointer_res["id"],
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            version=target_version_number,
            environment=environment,
            revision=new_revision,
            bundle_sha256=target_bundle_sha256,
            published_at=now_dt,
            is_noop=False,
        )


agent_repository = AgentRepository()

__all__ = ["AgentRepository", "agent_repository"]
