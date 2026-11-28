"""PostgreSQL repository for atomic run admission, locking, and receipt lookup (06-B).

Operates on migrations 0001..0009 using caller-owned
AsyncSession transactions with PostgreSQL RLS context already bound.
Lock order:
  1. iam.workspaces / iam.memberships (FOR SHARE)
  2. workspace advisory transaction lock, then runtime.runs (no outer-join row lock)
  3. catalog.agent_versions + catalog.agents + dependencies (FOR SHARE)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_admission import (
    AdmittedRunMetadata,
    ExistingRunAdmission,
    PinnedAgentVersionRecord,
    RunAdmissionAccessDeniedError,
    RunAdmissionConflictError,
    RunAdmissionNotFoundError,
    RunAdmissionValidationError,
)
from agent_runtime.application.run_events import RunEventDTO
from agent_runtime.auth import RUNTIME_ROLE_SCOPES
from agent_runtime.persistence.run_event_repository import RunEventRepository

logger = logging.getLogger(__name__)

_SUPPORTED_MODES: frozenset[str] = frozenset({"fixed_plan"})
_SUPPORTED_TOOL_KINDS: frozenset[str] = frozenset({"builtin", "http"})


class RunAdmissionRepository:
    """Caller-session-scoped repository for atomic run admission on schema 0008."""

    async def verify_active_workspace_membership(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        for_share: bool = False,
    ) -> str:
        """Re-verify active tenant, workspace, and user membership; return live DB role."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        u_uuid = UUID(user_id)

        if for_share:
            # Lock tenant/workspace/membership rows in deterministic order to guard concurrent suspension/revocation.
            scope_row = (
                (
                    await session.execute(
                        text("""
                            SELECT t.status AS tenant_status, w.status AS workspace_status
                            FROM iam.tenants AS t
                            JOIN iam.workspaces AS w
                              ON w.tenant_id = t.id
                            WHERE t.id = :tenant_id
                              AND w.id = :workspace_id
                            FOR SHARE OF t, w
                        """),
                        {"tenant_id": t_uuid, "workspace_id": w_uuid},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                scope_row is None
                or scope_row["tenant_status"] != "active"
                or scope_row["workspace_status"] != "active"
            ):
                raise RunAdmissionAccessDeniedError(
                    "Active tenant and workspace are required",
                    code="workspace_inactive",
                )

        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)"
                    ),
                    {"user_id": u_uuid, "workspace_id": w_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row.get("tenant_status", "active") != "active"
            or row.get("workspace_status", "active") != "active"
            or str(row["tenant_id"]) != str(t_uuid)
        ):
            raise RunAdmissionAccessDeniedError(
                "Active workspace membership required",
                code="membership_inactive",
            )
        role = str(row["role"])
        if role not in RUNTIME_ROLE_SCOPES:
            raise RunAdmissionAccessDeniedError(
                "Workspace membership role is not permitted",
                code="role_not_permitted",
            )
        return role

    async def acquire_workspace_admission_lock(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        """Serialize different-key capacity checks/admissions per workspace within the transaction."""
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"run_admission_ws:{UUID(tenant_id)}:{UUID(workspace_id)}"},
        )

    async def lookup_existing_admission(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        external_request_key: str,
        for_update: bool = False,
    ) -> ExistingRunAdmission | None:
        """Look up existing run + durable receipt hash without nullable outer-join FOR UPDATE."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        lock_clause = " FOR UPDATE" if for_update else ""
        run_row = (
            (
                await session.execute(
                    text(f"""
                        SELECT id, agent_version_id, conversation_id, initiated_by, status,
                               request_sha256,
                               (execution_authority_ciphertext IS NOT NULL) AS has_execution_authority,
                               (receipt_expires_at IS NOT NULL
                                AND clock_timestamp() >= receipt_expires_at
                                AND NOT EXISTS (
                                    SELECT 1 FROM governance.retention_policies AS p
                                    WHERE p.tenant_id = runtime.runs.tenant_id
                                      AND p.workspace_id = runtime.runs.workspace_id
                                      AND p.data_class IN ('run_content', 'run_receipt')
                                      AND p.legal_hold
                                )) AS receipt_expired
                        FROM runtime.runs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND external_request_key = :external_request_key
                        {lock_clause}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "external_request_key": external_request_key,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            return None

        run_id_str = str(run_row["id"])
        # Receipt fields live on the run, independent of timeline/job/audit pruning.
        request_sha256 = str(run_row["request_sha256"]) if run_row["request_sha256"] else None

        return ExistingRunAdmission(
            run_id=run_id_str,
            agent_version_id=str(run_row["agent_version_id"]),
            conversation_id=str(run_row["conversation_id"])
            if run_row["conversation_id"] is not None
            else None,
            initiated_by=str(run_row["initiated_by"])
            if run_row["initiated_by"] is not None
            else None,
            status=str(run_row["status"]),
            request_sha256=request_sha256,
            receipt_expired=bool(run_row["receipt_expired"]),
            has_execution_authority=bool(run_row.get("has_execution_authority", False)),
        )

    async def validate_pinned_agent_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        agent_version_id: str,
        actor_user_id: str,
        caller_role: str,
        for_share: bool = False,
    ) -> PinnedAgentVersionRecord:
        """Verify pinned sealed AgentVersion and all bound dependencies in caller scope."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        av_uuid = UUID(agent_version_id)
        u_uuid = UUID(actor_user_id)
        share_clause = " FOR SHARE OF av, a" if for_share else ""

        row = (
            (
                await session.execute(
                    text(f"""
                        SELECT av.id AS agent_version_id, av.agent_id, av.version,
                               av.frozen_spec, av.bundle_manifest, av.model_config_id,
                               av.bindings_sealed_at, a.archived_at
                        FROM catalog.agent_versions AS av
                        JOIN catalog.agents AS a
                          ON a.tenant_id = av.tenant_id
                         AND a.workspace_id = av.workspace_id
                         AND a.id = av.agent_id
                        WHERE av.tenant_id = :tenant_id
                          AND av.workspace_id = :workspace_id
                          AND av.id = :agent_version_id
                        {share_clause}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "agent_version_id": av_uuid,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RunAdmissionNotFoundError(
                "Agent version not found in workspace",
                code="agent_version_not_found",
            )
        if row["archived_at"] is not None:
            raise RunAdmissionConflictError(
                "Agent is archived and cannot admit new runs",
                code="agent_archived",
            )
        if row["bindings_sealed_at"] is None:
            raise RunAdmissionConflictError(
                "Agent version is not sealed for execution",
                code="agent_version_unsealed",
            )

        frozen_spec = dict(row["frozen_spec"] or {})
        bundle_manifest = dict(row["bundle_manifest"] or {})
        mode = frozen_spec.get("mode")
        if mode is not None and mode not in _SUPPORTED_MODES:
            raise RunAdmissionValidationError(
                f"Agent execution mode '{mode}' is not supported",
                code="unsupported_capability",
            )
        if frozen_spec.get("workflowRef") or frozen_spec.get("approvalPolicyRef"):
            raise RunAdmissionValidationError(
                "Agent version contains unsupported workflow or approval policy references",
                code="unsupported_capability",
            )

        # 1. Verify bound model configuration is active in scope
        model_config_id = row["model_config_id"]
        if model_config_id is None:
            raise RunAdmissionConflictError(
                "Agent version does not pin a valid model configuration",
                code="model_config_missing",
            )
        model_share = " FOR SHARE" if for_share else ""
        model_row = (
            (
                await session.execute(
                    text(f"""
                        SELECT id, status
                        FROM catalog.model_configs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND id = :model_config_id
                        {model_share}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "model_config_id": model_config_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if model_row is None or model_row["status"] != "active":
            raise RunAdmissionConflictError(
                "Pinned model configuration is unavailable or disabled",
                code="dependency_disabled",
            )

        # 2. Verify bound tools (supported kinds + active invoke grant for caller role)
        # Note: agent_*_bindings are immutable (0007 revokes UPDATE from platform_app), so lock parent/grant rows only.
        tool_share = " FOR SHARE OF tv, t" if for_share else ""
        tool_rows = (
            (
                await session.execute(
                    text(f"""
                        SELECT b.tool_version_id, t.id AS tool_id, t.kind, t.tool_key
                        FROM catalog.agent_tool_bindings AS b
                        JOIN catalog.tool_versions AS tv
                          ON tv.tenant_id = b.tenant_id
                         AND tv.workspace_id = b.workspace_id
                         AND tv.id = b.tool_version_id
                        JOIN catalog.tools AS t
                          ON t.tenant_id = tv.tenant_id
                         AND t.workspace_id = tv.workspace_id
                         AND t.id = tv.tool_id
                        WHERE b.tenant_id = :tenant_id
                          AND b.workspace_id = :workspace_id
                          AND b.agent_version_id = :agent_version_id
                        {tool_share}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "agent_version_id": av_uuid,
                    },
                )
            )
            .mappings()
            .all()
        )
        for tr in tool_rows:
            if tr["kind"] not in _SUPPORTED_TOOL_KINDS:
                raise RunAdmissionValidationError(
                    f"Bound tool kind '{tr['kind']}' is not supported",
                    code="unsupported_capability",
                )
            grant_share = " FOR SHARE" if for_share else ""
            grant_row = (
                await session.execute(
                    text(f"""
                        SELECT 1
                        FROM catalog.tool_grants
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND tool_id = :tool_id
                          AND principal_kind = 'role'
                          AND principal_key = :caller_role
                          AND allowed_operation = 'invoke'
                        LIMIT 1
                        {grant_share}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "tool_id": tr["tool_id"],
                        "caller_role": caller_role,
                    },
                )
            ).scalar_one_or_none()
            if grant_row is None:
                raise RunAdmissionAccessDeniedError(
                    "Caller role lacks invoke grant for bound tool",
                    code="dependency_not_permitted",
                )

        # 3. Verify bound skills are active (not disabled/archived)
        skill_share = " FOR SHARE OF sv, s" if for_share else ""
        skill_rows = (
            (
                await session.execute(
                    text(f"""
                        SELECT b.skill_id, b.skill_version_id, s.status AS skill_status
                        FROM catalog.agent_skill_bindings AS b
                        JOIN catalog.skill_versions AS sv
                          ON sv.tenant_id = b.tenant_id
                         AND sv.workspace_id = b.workspace_id
                         AND sv.skill_id = b.skill_id
                         AND sv.id = b.skill_version_id
                        JOIN catalog.skills AS s
                          ON s.tenant_id = sv.tenant_id
                         AND s.workspace_id = sv.workspace_id
                         AND s.id = sv.skill_id
                        WHERE b.tenant_id = :tenant_id
                          AND b.workspace_id = :workspace_id
                          AND b.agent_version_id = :agent_version_id
                        {skill_share}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "agent_version_id": av_uuid,
                    },
                )
            )
            .mappings()
            .all()
        )
        for sr in skill_rows:
            if sr["skill_status"] != "active":
                raise RunAdmissionConflictError(
                    f"Bound skill is {sr['skill_status']}",
                    code="dependency_disabled",
                )

        # 4. Verify bound knowledge bases remain accessible to caller
        kb_share = " FOR SHARE OF kb" if for_share else ""
        kb_rows = (
            (
                await session.execute(
                    text(f"""
                        SELECT kb.id AS base_id, kb.visibility
                        FROM catalog.agent_knowledge_bindings AS b
                        JOIN knowledge.bases AS kb
                          ON kb.tenant_id = b.tenant_id
                         AND kb.workspace_id = b.workspace_id
                         AND kb.id = b.base_id
                        WHERE b.tenant_id = :tenant_id
                          AND b.workspace_id = :workspace_id
                          AND b.agent_version_id = :agent_version_id
                        {kb_share}
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "agent_version_id": av_uuid,
                    },
                )
            )
            .mappings()
            .all()
        )
        for kr in kb_rows:
            if kr["visibility"] != "workspace":
                kb_grant_share = " FOR SHARE" if for_share else ""
                kb_grant = (
                    await session.execute(
                        text(f"""
                            SELECT 1
                            FROM knowledge.base_grants
                            WHERE tenant_id = :tenant_id
                              AND workspace_id = :workspace_id
                              AND base_id = :base_id
                              AND permission IN ('read', 'write', 'manage')
                              AND (
                                  (principal_kind = 'user' AND principal_key = :user_key)
                                  OR (principal_kind = 'role' AND principal_key = :caller_role)
                              )
                            LIMIT 1
                            {kb_grant_share}
                        """),
                        {
                            "tenant_id": t_uuid,
                            "workspace_id": w_uuid,
                            "base_id": kr["base_id"],
                            "user_key": str(u_uuid),
                            "caller_role": caller_role,
                        },
                    )
                ).scalar_one_or_none()
                if kb_grant is None:
                    raise RunAdmissionAccessDeniedError(
                        "Access denied to bound restricted knowledge base",
                        code="dependency_not_permitted",
                    )

        return PinnedAgentVersionRecord(
            agent_id=str(row["agent_id"]),
            agent_version_id=str(row["agent_version_id"]),
            version=int(row["version"]),
            archived_at=row["archived_at"],
            bindings_sealed_at=row["bindings_sealed_at"],
            model_config_id=str(model_config_id),
            frozen_spec=frozen_spec,
            bundle_manifest=bundle_manifest,
        )

    async def count_active_workspace_runs(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> int:
        """Count active (queued/running/waiting_approval) runs in caller's workspace."""
        count_val = (
            await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM runtime.runs
                    WHERE tenant_id = :tenant_id
                      AND workspace_id = :workspace_id
                      AND status IN ('queued', 'running', 'waiting_approval')
                """),
                {"tenant_id": UUID(tenant_id), "workspace_id": UUID(workspace_id)},
            )
        ).scalar_one()
        return int(count_val)

    async def insert_run_if_absent(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        tenant_id: str,
        workspace_id: str,
        agent_version_id: str,
        conversation_id: str | None,
        initiated_by: str,
        external_request_key: str,
        trace_id: str | None,
        request_sha256: str,
        input_ref: str,
        input_sha256: str,
        input_size_bytes: int,
        queue_ttl_seconds: int = 86400,
        execution_authority_ciphertext: bytes | None = None,
        execution_authority_key_id: str | None = None,
        authority_expires_at: datetime | None = None,
    ) -> bool:
        """Atomically insert queued run on (tenant_id, workspace_id, external_request_key)."""
        res = await session.execute(
            text("""
                WITH clock AS (
                    SELECT clock_timestamp() AS ts
                )
                INSERT INTO runtime.runs (
                    id, tenant_id, workspace_id, agent_version_id, conversation_id,
                    initiated_by, external_request_key, status, trace_id, next_event_seq, created_at,
                    request_sha256, input_ref, input_sha256, input_size_bytes, queue_expires_at,
                    execution_authority_ciphertext, execution_authority_key_id, authority_expires_at
                )
                SELECT
                    :run_id, :tenant_id, :workspace_id, :agent_version_id, :conversation_id,
                    :initiated_by, :external_request_key, 'queued', :trace_id, 1, clock.ts,
                    :request_sha256, :input_ref, :input_sha256, :input_size_bytes,
                    clock.ts + make_interval(secs => :queue_ttl_seconds),
                    :execution_authority_ciphertext, :execution_authority_key_id, :authority_expires_at
                FROM clock
                ON CONFLICT (tenant_id, workspace_id, external_request_key) DO NOTHING
                RETURNING id
            """),
            {
                "run_id": UUID(run_id),
                "tenant_id": UUID(tenant_id),
                "workspace_id": UUID(workspace_id),
                "agent_version_id": UUID(agent_version_id),
                "conversation_id": UUID(conversation_id) if conversation_id else None,
                "initiated_by": UUID(initiated_by),
                "external_request_key": external_request_key,
                "trace_id": trace_id,
                "request_sha256": request_sha256,
                "input_ref": input_ref,
                "input_sha256": input_sha256,
                "input_size_bytes": input_size_bytes,
                "queue_ttl_seconds": queue_ttl_seconds,
                "execution_authority_ciphertext": execution_authority_ciphertext,
                "execution_authority_key_id": execution_authority_key_id,
                "authority_expires_at": authority_expires_at,
            },
        )
        return res.scalar_one_or_none() is not None

    async def insert_queued_run_job(
        self,
        session: AsyncSession,
        *,
        job_id: str,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        payload_ref: str,
    ) -> str:
        """Insert the queued run job referencing the verified immutable input object."""
        await session.execute(
            text("""
                INSERT INTO runtime.jobs (
                    id, tenant_id, workspace_id, run_id, job_type, payload_ref, status, created_at
                ) VALUES (
                    :job_id, :tenant_id, :workspace_id, :run_id, 'run', :payload_ref, 'queued', now()
                )
            """),
            {
                "job_id": UUID(job_id),
                "tenant_id": UUID(tenant_id),
                "workspace_id": UUID(workspace_id),
                "run_id": UUID(run_id),
                "payload_ref": payload_ref,
            },
        )
        return job_id

    async def insert_initial_run_event(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        event_type: str,
        redacted_payload: dict[str, Any],
    ) -> RunEventDTO:
        """Insert sequence=1 initial run event with redacted metadata only."""
        return await RunEventRepository().append_run_event(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
            data=redacted_payload,
        )

    async def insert_admission_audit_event(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_subject: str,
        run_id: str,
        operation_key: str,
        request_sha256: str,
        agent_version_id: str,
        input_ref: str,
        input_sha256: str,
        input_size_bytes: int,
        execution_available: bool,
        trace_id: str | None,
    ) -> str:
        """Insert immutable governance.audit_events row carrying redacted admission receipt metadata."""
        details = {
            "operation_key": operation_key,
            "request_sha256": request_sha256,
            "actor_user_id": actor_subject,
            "agent_version_id": agent_version_id,
            "input_ref": input_ref,
            "input_sha256": input_sha256,
            "input_size_bytes": input_size_bytes,
            "execution_available": execution_available,
        }
        row = (
            (
                await session.execute(
                    text("""
                        INSERT INTO governance.audit_events (
                            tenant_id, workspace_id, actor_subject, action,
                            resource_type, resource_id, outcome, trace_id, details_redacted
                        ) VALUES (
                            :tenant_id, :workspace_id, :actor_subject, 'run.admitted',
                            'run', :resource_id, 'succeeded', :trace_id, CAST(:details_redacted AS jsonb)
                        ) RETURNING id
                    """),
                    {
                        "tenant_id": UUID(tenant_id),
                        "workspace_id": UUID(workspace_id),
                        "actor_subject": actor_subject,
                        "resource_id": run_id,
                        "trace_id": trace_id,
                        "details_redacted": json.dumps(
                            details, sort_keys=True, separators=(",", ":")
                        ),
                    },
                )
            )
            .mappings()
            .one()
        )
        return str(row["id"])

    async def get_run_metadata(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> AdmittedRunMetadata | None:
        """Read redacted metadata for a single run inside caller's scope."""
        row = (
            (
                await session.execute(
                    text("""
                        SELECT id, agent_version_id, conversation_id, workflow_version_id,
                               initiated_by, status, trace_id, next_event_seq,
                               started_at, finished_at, created_at, queue_expires_at,
                               execution_deadline_at, error_code
                        FROM runtime.runs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND id = :run_id
                    """),
                    {
                        "tenant_id": UUID(tenant_id),
                        "workspace_id": UUID(workspace_id),
                        "run_id": UUID(run_id),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return AdmittedRunMetadata(
            run_id=str(row["id"]),
            agent_version_id=str(row["agent_version_id"]),
            conversation_id=str(row["conversation_id"])
            if row["conversation_id"] is not None
            else None,
            workflow_version_id=str(row["workflow_version_id"])
            if row["workflow_version_id"] is not None
            else None,
            initiated_by=str(row["initiated_by"]) if row["initiated_by"] is not None else None,
            status=str(row["status"]),
            trace_id=row["trace_id"],
            next_event_seq=int(row["next_event_seq"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            queue_expires_at=row["queue_expires_at"],
            execution_deadline_at=row["execution_deadline_at"],
            error_code=row["error_code"],
        )
