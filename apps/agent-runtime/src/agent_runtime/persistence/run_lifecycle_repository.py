"""PostgreSQL persistence repository for atomic run cancellation, queue expiry, and maintenance (06-D).

Follows DEC-06-CD lock order:
1. iam.tenants / iam.workspaces FOR SHARE
2. runtime.runs FOR UPDATE
3. runtime.jobs ordered by UUID FOR UPDATE
4. Sample clock_timestamp() post-lock wait
5. Atomic updates to run, job (dead + closed_reason), run_events (RunEvent 1.2), audit_events, outbox.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from platform_common.event_bus import PostgresOutboxEventBus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_admission import OUTBOX_RUN_EVENTS_TOPIC
from agent_runtime.application.run_lifecycle import (
    CancelRunResult,
    RunDisposition,
    RunLifecycleAccessDeniedError,
    RunLifecycleConflictError,
    RunLifecycleNotFoundError,
    RunLifecycleUnavailableError,
)
from agent_runtime.auth import RUNTIME_ROLE_SCOPES
from agent_runtime.persistence.run_event_repository import RunEventRepository

logger = logging.getLogger(__name__)


def _caller_transaction_only() -> AsyncSession:
    raise RuntimeError("Run lifecycle outbox requires a caller-owned transaction")


class RunLifecycleRepository:
    """Performs atomic queued run cancellation and deadline expiry in caller's AsyncSession."""

    def __init__(
        self,
        *,
        event_repository: RunEventRepository | None = None,
        outbox_publisher: PostgresOutboxEventBus | None = None,
    ) -> None:
        self._event_repo = (
            event_repository if event_repository is not None else RunEventRepository()
        )
        self._outbox = (
            outbox_publisher
            if outbox_publisher is not None
            else PostgresOutboxEventBus(_caller_transaction_only)
        )

    async def verify_scope_and_lock_share(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        is_system_maintenance: bool = False,
    ) -> tuple[str, str]:
        """Lock tenant and workspace FOR SHARE; return (tenant_status, workspace_status)."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)

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
        if scope_row is None:
            raise RunLifecycleNotFoundError(
                "Scoped workspace resource not found",
                code="workspace_not_found",
            )

        tenant_status = str(scope_row["tenant_status"])
        workspace_status = str(scope_row["workspace_status"])

        if not is_system_maintenance and (
            tenant_status != "active" or workspace_status != "active"
        ):
            raise RunLifecycleAccessDeniedError(
                "Active tenant and workspace are required",
                code="workspace_inactive",
            )

        return tenant_status, workspace_status

    async def cancel_or_expire_run(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        actor_subject: str,
        caller_role: str | None = None,
        caller_user_id: str | None = None,
        is_system_maintenance: bool = False,
        now_override: datetime | None = None,
        skip_locked: bool = False,
        dry_run: bool = False,
    ) -> CancelRunResult:
        """Atomically cancel or expire a run obeying the DEC-06-CD lock order and state matrix."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        r_uuid = UUID(run_id)

        # 1. Tenant and workspace lock FOR SHARE
        await self.verify_scope_and_lock_share(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            is_system_maintenance=is_system_maintenance,
        )

        # Workspace SHARE precedes membership/run/job locks, matching IAM writers.
        # Never authorize the mutation using the service's pre-lock role snapshot.
        if caller_user_id is not None:
            member = (
                (
                    await session.execute(
                        text("""
                SELECT role, status FROM iam.memberships
                WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id AND user_id=:user_id
                FOR SHARE
            """),
                        {
                            "tenant_id": t_uuid,
                            "workspace_id": w_uuid,
                            "user_id": UUID(caller_user_id),
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if member is None or member["status"] != "active":
                raise RunLifecycleAccessDeniedError(
                    "Active membership required", code="access_denied"
                )
            caller_role = str(member["role"])
            if "run:start" not in RUNTIME_ROLE_SCOPES.get(caller_role, frozenset()):
                raise RunLifecycleAccessDeniedError(
                    "Run cancellation not permitted", code="insufficient_scope"
                )

        # 2. Run row lock FOR UPDATE
        run_row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT id, agent_version_id, conversation_id, initiated_by, status,
                               request_sha256, input_ref, input_sha256, input_size_bytes,
                               created_at, queue_expires_at, finished_at, trace_id, next_event_seq
                        FROM runtime.runs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND id = :run_id
                        FOR UPDATE
                    """
                        + (" SKIP LOCKED" if skip_locked else "")
                    ),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            if skip_locked:
                raise RunLifecycleConflictError(
                    "Run unavailable for maintenance", code="row_locked"
                )
            raise RunLifecycleNotFoundError(
                f"Run '{run_id}' not found in workspace",
                code="run_not_found",
            )

        # 2b. Resource authority check: creator or Owner/Admin/Operator; Developer peer -> 404
        if caller_role is not None and caller_user_id is not None:
            is_creator = run_row["initiated_by"] is not None and str(
                UUID(str(run_row["initiated_by"]))
            ) == str(UUID(caller_user_id))
            if not is_creator and caller_role.lower() not in ("owner", "admin", "operator"):
                raise RunLifecycleNotFoundError(
                    f"Run '{run_id}' not found in workspace",
                    code="run_not_found",
                )

        status = str(run_row["status"])

        # 3. Terminal state check: idempotent 200 without mutation
        if status in ("succeeded", "failed", "cancelled", "timed_out"):
            return CancelRunResult(
                run_id=run_id,
                status=status,
                changed=False,
                disposition="already_terminal",
            )

        # 4. In-flight state check: 409 conflict
        if status in ("running", "waiting_approval"):
            raise RunLifecycleConflictError(
                f"Run '{run_id}' is currently executing and cannot be cancelled before worker lease termination",
                code="run_in_flight",
            )

        # 5. Canonical vs legacy receipt check
        if run_row["request_sha256"] is None or run_row["queue_expires_at"] is None:
            raise RunLifecycleConflictError(
                "Legacy run without verified admission receipt cannot be safely cancelled",
                code="legacy_run_unsupported",
            )

        # 6. Lock linked jobs ordered by UUID FOR UPDATE
        job_rows = (
            (
                await session.execute(
                    text(
                        """
                        SELECT id, job_type, status, payload_ref, closed_reason
                        FROM runtime.jobs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND run_id = :run_id
                          AND job_type = 'run'
                        ORDER BY id
                        FOR UPDATE
                    """
                        + (" NOWAIT" if skip_locked else "")
                    ),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
                )
            )
            .mappings()
            .all()
        )

        # Check for leased jobs
        for j in job_rows:
            if j["status"] == "leased":
                raise RunLifecycleConflictError(
                    f"Run job '{j['id']}' is leased by a worker; in-flight cancellation is deferred to step 07",
                    code="run_in_flight",
                )

        # Canonical queued run requires exactly one queued run job
        run_jobs = [j for j in job_rows if j["job_type"] == "run"]
        if len(run_jobs) != 1:
            raise RunLifecycleUnavailableError(
                f"Run '{run_id}' has {len(run_jobs)} run jobs; expected exactly 1",
                code="run_state_inconsistent",
            )

        target_job = run_jobs[0]
        if target_job["status"] != "queued":
            raise RunLifecycleUnavailableError(
                f"Run job '{target_job['id']}' is in state '{target_job['status']}'; expected 'queued'",
                code="run_state_inconsistent",
            )

        if target_job["payload_ref"] != run_row["input_ref"]:
            raise RunLifecycleUnavailableError(
                "Run job payload_ref does not match run input_ref association",
                code="run_state_inconsistent",
            )

        # 7. Sample clock_timestamp() post-lock wait
        if now_override is not None:
            now_ts = now_override
        else:
            clock_res = await session.execute(text("SELECT clock_timestamp() AS ts"))
            now_ts = clock_res.scalar_one()

        queue_expires_at = run_row["queue_expires_at"]
        disposition: RunDisposition
        if queue_expires_at is None or now_ts < queue_expires_at:
            target_status = "cancelled"
            closed_reason = "run_cancelled"
            event_type = "run.cancelled"
            reason_code = "user_cancelled"
            disposition = "cancelled"
        else:
            target_status = "timed_out"
            closed_reason = "run_timed_out"
            event_type = "run.timed_out"
            reason_code = "queue_deadline_exceeded"
            disposition = "queue_expired"

        if is_system_maintenance and disposition != "queue_expired":
            raise RunLifecycleConflictError("Queue deadline has not elapsed", code="not_expired")
        if dry_run:
            if not is_system_maintenance:
                raise RunLifecycleAccessDeniedError("Dry-run requires maintenance context")
            return CancelRunResult(
                run_id=run_id, status=status, changed=False, disposition="queue_expired"
            )

        # 8. Mutate run row
        run_update_res = await session.execute(
            text("""
                UPDATE runtime.runs
                SET status = :target_status,
                    finished_at = :finished_at
                WHERE tenant_id = :tenant_id
                  AND workspace_id = :workspace_id
                  AND id = :run_id
                  AND status = 'queued'
                RETURNING id
            """),
            {
                "target_status": target_status,
                "finished_at": now_ts,
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "run_id": r_uuid,
            },
        )
        if run_update_res.scalar_one_or_none() is None:
            raise RunLifecycleUnavailableError(
                "Run state changed concurrently during cancellation CAS",
                code="run_state_inconsistent",
            )

        # 9. Mutate run job to dead with closed_reason
        job_update_res = await session.execute(
            text("""
                UPDATE runtime.jobs
                SET status = 'dead',
                    closed_reason = :closed_reason,
                    leased_until = NULL,
                    leased_by = NULL
                WHERE tenant_id = :tenant_id
                  AND workspace_id = :workspace_id
                  AND id = :job_id
                  AND status = 'queued'
                RETURNING id
            """),
            {
                "closed_reason": closed_reason,
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "job_id": target_job["id"],
            },
        )
        if job_update_res.scalar_one_or_none() is None:
            raise RunLifecycleUnavailableError(
                "Run job state changed concurrently during cancellation CAS",
                code="run_state_inconsistent",
            )

        # 10. Append terminal event via RunEventRepository (allocates monotonic sequence under run lock)
        event_dto = await self._event_repo.append_run_event(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
            data={"status": target_status, "reasonCode": reason_code},
            trace_id=run_row["trace_id"],
        )

        # 11. Record governance audit event
        audit_details = {
            "status": target_status,
            "reasonCode": reason_code,
            "sequence": event_dto.sequence,
        }
        await session.execute(
            text("""
                INSERT INTO governance.audit_events (
                    tenant_id, workspace_id, actor_subject, action,
                    resource_type, resource_id, outcome, trace_id, details_redacted
                ) VALUES (
                    :tenant_id, :workspace_id, :actor_subject, :action,
                    'run', :resource_id, 'succeeded', :trace_id, CAST(:details_redacted AS jsonb)
                )
            """),
            {
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "actor_subject": actor_subject,
                "action": event_type,
                "resource_id": run_id,
                "trace_id": run_row["trace_id"],
                "details_redacted": json.dumps(
                    audit_details, sort_keys=True, separators=(",", ":")
                ),
            },
        )

        # 12. Publish outbox event
        outbox_event_id = str(uuid4())
        await self._outbox.publish_in_transaction(
            session,
            OUTBOX_RUN_EVENTS_TOPIC,
            f"{tenant_id}:{workspace_id}",
            event_dto.to_wire_envelope(),
            aggregate_type="run",
            aggregate_id=run_id,
            event_id=outbox_event_id,
            set_rls=False,
        )

        return CancelRunResult(
            run_id=run_id,
            status=target_status,
            changed=True,
            disposition=disposition,
        )

    async def get_candidate_expired_runs(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query candidate queued runs whose queue deadline has passed."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        rows = (
            (
                await session.execute(
                    text("""
                        SELECT id, request_sha256, queue_expires_at, created_at
                        FROM runtime.runs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND status = 'queued'
                          AND queue_expires_at IS NOT NULL
                          AND queue_expires_at <= clock_timestamp()
                        ORDER BY queue_expires_at, id
                        LIMIT :limit
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
