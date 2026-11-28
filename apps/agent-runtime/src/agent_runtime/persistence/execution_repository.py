"""PostgreSQL repository for fenced run claims, heartbeats, and transactional state ownership (DEC-07 / DATA-07)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from platform_common.execution import LeaseIdentity
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_events import RunEventDTO
from agent_runtime.persistence.run_event_repository import RunEventRepository

logger = logging.getLogger(__name__)


class ExecutionRepositoryError(Exception):
    """Base error for execution repository operations."""


class LostLeaseError(ExecutionRepositoryError):
    """Fencing token, worker identity, attempt, or lease expired."""


class RunAlreadyTerminalError(ExecutionRepositoryError):
    """Run has already reached a terminal state (succeeded/failed/cancelled/timed_out)."""


@dataclass(frozen=True, slots=True)
class ClaimedJobRecord:
    """Authoritative claimed job record from PostgreSQL."""

    id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    job_type: str
    payload_ref: str
    status: str
    available_at: datetime
    leased_until: datetime
    leased_by: str
    attempt: int
    max_attempts: int
    created_at: datetime
    lease_token: str

    def to_lease_identity(self) -> LeaseIdentity:
        return LeaseIdentity(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            job_id=self.id,
            run_id=self.run_id,
            leased_by=self.leased_by,
            attempt=self.attempt,
            lease_token=self.lease_token,
        )


@dataclass(frozen=True, slots=True)
class BeginExecutionResult:
    """Outcome of transitioning an admitted run to running under active lease fence."""

    success: bool
    started: bool
    timed_out: bool = False
    error_code: str | None = None
    authority_ciphertext: bytes | None = None
    authority_key_id: str | None = None
    authority_expires_at: datetime | None = None
    initiated_by: str | None = None
    trace_id: str | None = None


MIN_EXECUTION_WALL_TIME_SECONDS: Final[int] = 1
MAX_EXECUTION_WALL_TIME_SECONDS: Final[int] = 900


def is_lease_fence_valid(
    job_row: Mapping[str, Any],
    *,
    worker_id: str,
    lease_token: UUID,
    attempt: int,
    db_now: datetime,
) -> bool:
    """Pure evaluation of job lease fencing invariant (CT-016)."""
    return not (
        job_row["status"] != "leased"
        or job_row["leased_by"] != worker_id
        or job_row["lease_token"] != lease_token
        or int(job_row["attempt"]) != attempt
        or job_row["leased_until"] is None
        or job_row["leased_until"] <= db_now
    )


def is_queue_deadline_exceeded(
    run_status: str,
    queue_expires_at: datetime | None,
    db_now: datetime,
) -> bool:
    """Pure evaluation of queue deadline expiry (CT-016)."""
    return run_status == "queued" and queue_expires_at is not None and db_now >= queue_expires_at


def is_execution_deadline_exceeded(
    started_at: datetime | None,
    execution_deadline_at: datetime | None,
    db_now: datetime,
) -> bool:
    """Pure evaluation of execution deadline expiry (CT-016)."""
    return (
        started_at is not None
        and execution_deadline_at is not None
        and db_now >= execution_deadline_at
    )


MIN_EXECUTION_WALL_TIME_SECONDS: Final[int] = 1
MAX_EXECUTION_WALL_TIME_SECONDS: Final[int] = 900


def is_lease_fence_valid(
    job_row: Mapping[str, Any],
    *,
    worker_id: str,
    lease_token: UUID,
    attempt: int,
    db_now: datetime,
) -> bool:
    """Pure evaluation of job lease fencing invariant (CT-016)."""
    return not (
        job_row["status"] != "leased"
        or job_row["leased_by"] != worker_id
        or job_row["lease_token"] != lease_token
        or int(job_row["attempt"]) != attempt
        or job_row["leased_until"] is None
        or job_row["leased_until"] <= db_now
    )


def is_queue_deadline_exceeded(
    run_status: str,
    queue_expires_at: datetime | None,
    db_now: datetime,
) -> bool:
    """Pure evaluation of queue deadline expiry (CT-016)."""
    return (
        run_status == "queued"
        and queue_expires_at is not None
        and db_now >= queue_expires_at
    )


def is_execution_deadline_exceeded(
    started_at: datetime | None,
    execution_deadline_at: datetime | None,
    db_now: datetime,
) -> bool:
    """Pure evaluation of execution deadline expiry (CT-016)."""
    return (
        started_at is not None
        and execution_deadline_at is not None
        and db_now >= execution_deadline_at
    )


class ExecutionRepository:
    """Caller-session-scoped repository for atomic job claiming, fencing, and state writes."""

    def __init__(self, event_repository: RunEventRepository | None = None) -> None:
        self._event_repo = (
            event_repository if event_repository is not None else RunEventRepository()
        )

    async def claim_next_job(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        worker_id: str,
        lease_token: str,
        lease_duration_seconds: int = 30,
        max_attempts: int = 3,
        workspace_concurrency_cap: int = 2,
        require_grant: bool = False,
    ) -> ClaimedJobRecord | None:
        """Claim one due run job under workspace advisory transaction lock and workspace limit cap.

        Claim locks ONLY runtime.jobs via FOR UPDATE SKIP LOCKED and commits immediately.
        """
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        lt_uuid = UUID(lease_token)

        # 1. Advisory transaction lock serializes claim admission per workspace
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"run_claim_ws:{t_uuid}:{w_uuid}"},
        )

        # 2. Workspace concurrency cap (max active executions in this workspace)
        active_count_val = (
            await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM runtime.jobs
                    WHERE tenant_id = :tenant_id
                      AND workspace_id = :workspace_id
                      AND status = 'leased'
                      AND leased_until > clock_timestamp()
                """),
                {"tenant_id": t_uuid, "workspace_id": w_uuid},
            )
        ).scalar_one()

        if int(active_count_val) >= workspace_concurrency_cap:
            return None

        # 3. Candidate job selection with SKIP LOCKED on runtime.jobs only
        result = (
            (
                await session.execute(
                    text("""
                        WITH candidate AS (
                            SELECT j.id
                            FROM runtime.jobs AS j
                            JOIN runtime.runs AS r
                              ON r.tenant_id = j.tenant_id
                             AND r.workspace_id = j.workspace_id
                             AND r.id = j.run_id
                            WHERE j.tenant_id = :tenant_id
                              AND j.workspace_id = :workspace_id
                              AND j.status = 'queued'
                              AND j.job_type = 'run'
                              AND j.available_at <= clock_timestamp()
                              AND j.attempt < :max_attempts
                              AND (
                                  (
                                      r.status = 'queued'
                                      AND (r.queue_expires_at IS NULL OR r.queue_expires_at > clock_timestamp())
                                  )
                                  OR (
                                      r.status = 'running'
                                      AND (r.execution_deadline_at IS NULL OR r.execution_deadline_at > clock_timestamp())
                                  )
                              )
                              AND (:require_grant = false OR (
                                  r.execution_authority_ciphertext IS NOT NULL
                                  AND r.authority_expires_at > clock_timestamp()
                              ))
                            ORDER BY j.available_at, j.id
                            FOR UPDATE OF j SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE runtime.jobs AS job
                        SET status = 'leased',
                            leased_by = :worker_id,
                            lease_token = :lease_token,
                            leased_until = clock_timestamp() + make_interval(secs => :lease_seconds),
                            attempt = attempt + 1
                        FROM candidate
                        WHERE job.id = candidate.id
                        RETURNING job.id, job.tenant_id, job.workspace_id, job.run_id, job.job_type,
                                  job.payload_ref, job.status, job.available_at, job.leased_until,
                                  job.leased_by, job.attempt, job.max_attempts, job.created_at,
                                  job.lease_token
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "worker_id": worker_id,
                        "lease_token": lt_uuid,
                        "lease_seconds": lease_duration_seconds,
                        "max_attempts": max_attempts,
                        "require_grant": require_grant,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )

        if result is None:
            return None

        return ClaimedJobRecord(
            id=str(result["id"]),
            tenant_id=str(result["tenant_id"]),
            workspace_id=str(result["workspace_id"]),
            run_id=str(result["run_id"]),
            job_type=str(result["job_type"]),
            payload_ref=str(result["payload_ref"]),
            status=str(result["status"]),
            available_at=result["available_at"],
            leased_until=result["leased_until"],
            leased_by=str(result["leased_by"]),
            attempt=int(result["attempt"]),
            max_attempts=int(result["max_attempts"]),
            created_at=result["created_at"],
            lease_token=str(result["lease_token"]),
        )

    async def extend_lease(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_duration_seconds: int = 30,
    ) -> bool:
        """Heartbeat extension. Valid only if lease is currently active and held by this worker."""
        res = await session.execute(
            text("""
                UPDATE runtime.jobs
                SET leased_until = clock_timestamp() + make_interval(secs => :lease_seconds)
                WHERE tenant_id = :tenant_id
                  AND workspace_id = :workspace_id
                  AND id = :job_id
                  AND status = 'leased'
                  AND leased_by = :worker_id
                  AND lease_token = :lease_token
                  AND leased_until > clock_timestamp()
            """),
            {
                "tenant_id": UUID(tenant_id),
                "workspace_id": UUID(workspace_id),
                "job_id": UUID(job_id),
                "worker_id": worker_id,
                "lease_token": UUID(lease_token),
                "lease_seconds": lease_duration_seconds,
            },
        )
        return bool(res.rowcount == 1)

    async def begin_execution(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        job_id: str,
        run_id: str,
        worker_id: str,
        lease_token: str,
        attempt: int,
        max_wall_time_seconds: int = 180,
    ) -> BeginExecutionResult:
        """Lock order: iam.workspaces SHARE -> runtime.runs FOR UPDATE -> runtime.jobs FOR UPDATE.

        Rechecks grant, deadline, and fencing. Pins started_at and execution_deadline_at on first begin.
        """
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        j_uuid = UUID(job_id)
        r_uuid = UUID(run_id)
        lt_uuid = UUID(lease_token)

        # 1. Lock workspace SHARE
        ws_row = (
            (
                await session.execute(
                    text("""
                        SELECT t.status AS tenant_status, w.status AS workspace_status
                        FROM iam.tenants t
                        JOIN iam.workspaces w ON w.tenant_id = t.id
                        WHERE t.id = :tenant_id AND w.id = :workspace_id
                        FOR SHARE OF t, w
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            ws_row is None
            or ws_row["tenant_status"] != "active"
            or ws_row["workspace_status"] != "active"
        ):
            return BeginExecutionResult(
                success=False, started=False, error_code="execution_authority_denied"
            )

        # 2. Lock runtime.runs FOR UPDATE
        run_row = (
            (
                await session.execute(
                    text("""
                        SELECT id, status, initiated_by, trace_id, started_at,
                               execution_deadline_at, queue_expires_at,
                               execution_authority_ciphertext, execution_authority_key_id,
                               authority_expires_at,
                               clock_timestamp() AS db_now
                        FROM runtime.runs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND id = :run_id
                        FOR UPDATE
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise LostLeaseError("Run not found in scope")

        if run_row["status"] in ("succeeded", "failed", "cancelled", "timed_out"):
            raise RunAlreadyTerminalError(f"Run is already terminal ({run_row['status']})")

        # 3. Lock runtime.jobs FOR UPDATE and verify fence
        job_row = (
            (
                await session.execute(
                    text("""
                        SELECT id, status, leased_by, lease_token, attempt, leased_until
                        FROM runtime.jobs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND id = :job_id
                        FOR UPDATE
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": j_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if job_row is None:
            raise LostLeaseError("Job not found in scope")

        db_now: datetime = run_row["db_now"]
        if not is_lease_fence_valid(
            job_row,
            worker_id=worker_id,
            lease_token=lt_uuid,
            attempt=attempt,
            db_now=db_now,
        ):
            raise LostLeaseError("Fencing check failed or lease expired")

        # 4. Check deadline after acquiring locks
        # If run is queued, check queue deadline
        if is_queue_deadline_exceeded(run_row["status"], run_row["queue_expires_at"], db_now):
            await self._close_timed_out(
                session,
                tenant_id=t_uuid,
                workspace_id=w_uuid,
                run_id=r_uuid,
                job_id=j_uuid,
                reason_code="queue_deadline_exceeded",
                job_closed_reason="run_timed_out",
            )
            return BeginExecutionResult(
                success=False,
                started=False,
                timed_out=True,
                error_code="execution_deadline_exceeded",
            )

        # If already started, check execution deadline
        if is_execution_deadline_exceeded(
            run_row["started_at"], run_row["execution_deadline_at"], db_now
        ):
            await self._close_timed_out(
                session,
                tenant_id=t_uuid,
                workspace_id=w_uuid,
                run_id=r_uuid,
                job_id=j_uuid,
                reason_code="execution_deadline_exceeded",
                job_closed_reason="execution_timed_out",
            )
            return BeginExecutionResult(
                success=False,
                started=False,
                timed_out=True,
                error_code="execution_deadline_exceeded",
            )

        # 5. First begin: transition to running, pin started_at and execution_deadline_at
        effective_wall_time = max(
            MIN_EXECUTION_WALL_TIME_SECONDS,
            min(max_wall_time_seconds, MAX_EXECUTION_WALL_TIME_SECONDS),
        )
        if run_row["status"] == "queued":
            await session.execute(
                text("""
                    UPDATE runtime.runs
                    SET status = 'running',
                        started_at = clock_timestamp(),
                        execution_deadline_at = clock_timestamp() + make_interval(secs => :wall_time)
                    WHERE tenant_id = :tenant_id
                      AND workspace_id = :workspace_id
                      AND id = :run_id
                """),
                {
                    "tenant_id": t_uuid,
                    "workspace_id": w_uuid,
                    "run_id": r_uuid,
                    "wall_time": effective_wall_time,
                },
            )
            # Append run.started event
            await self._event_repo.append_run_event(
                session,
                tenant_id=str(t_uuid),
                workspace_id=str(w_uuid),
                run_id=str(r_uuid),
                event_type="run.started",
                data={"status": "running"},
                trace_id=run_row["trace_id"],
            )

        return BeginExecutionResult(
            success=True,
            started=True,
            authority_ciphertext=run_row["execution_authority_ciphertext"],
            authority_key_id=run_row["execution_authority_key_id"],
            authority_expires_at=run_row["authority_expires_at"],
            initiated_by=str(run_row["initiated_by"]) if run_row["initiated_by"] else None,
            trace_id=run_row["trace_id"],
        )

    async def _close_timed_out(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        job_id: UUID,
        reason_code: str,
        job_closed_reason: str,
    ) -> None:
        """Helper to atomically close timed out run and job."""
        await session.execute(
            text("""
                UPDATE runtime.runs
                SET status = 'timed_out',
                    error_code = 'execution_deadline_exceeded',
                    finished_at = clock_timestamp()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id
            """),
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "run_id": run_id},
        )
        await session.execute(
            text("""
                UPDATE runtime.jobs
                SET status = 'dead',
                    closed_reason = :closed_reason,
                    leased_by = NULL,
                    lease_token = NULL,
                    leased_until = NULL
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
            """),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "job_id": job_id,
                "closed_reason": job_closed_reason,
            },
        )
        await self._event_repo.append_run_event(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            run_id=str(run_id),
            event_type="run.timed_out",
            data={"status": "timed_out", "reasonCode": reason_code},
        )

    async def complete_execution(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        job_id: str,
        run_id: str,
        worker_id: str,
        lease_token: str,
        attempt: int,
    ) -> RunEventDTO:
        """Lock order: iam.workspaces SHARE -> runtime.runs FOR UPDATE -> runtime.jobs FOR UPDATE."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        j_uuid = UUID(job_id)
        r_uuid = UUID(run_id)
        lt_uuid = UUID(lease_token)

        # 1. Lock workspace SHARE
        await session.execute(
            text(
                "SELECT 1 FROM iam.workspaces WHERE tenant_id = :tenant_id AND id = :workspace_id FOR SHARE"
            ),
            {"tenant_id": t_uuid, "workspace_id": w_uuid},
        )

        # 2. Lock runtime.runs FOR UPDATE
        run_row = (
            (
                await session.execute(
                    text(
                        "SELECT id, status, trace_id FROM runtime.runs WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id FOR UPDATE"
                    ),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise LostLeaseError("Run not found")
        if run_row["status"] in ("succeeded", "failed", "cancelled", "timed_out"):
            raise RunAlreadyTerminalError(f"Run is already terminal ({run_row['status']})")

        # 3. Lock runtime.jobs FOR UPDATE and verify fence
        job_row = (
            (
                await session.execute(
                    text("""
                        SELECT id, status, leased_by, lease_token, attempt, leased_until, clock_timestamp() AS now
                        FROM runtime.jobs
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
                        FOR UPDATE
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": j_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if job_row is None:
            raise LostLeaseError("Job not found")

        if not is_lease_fence_valid(
            job_row,
            worker_id=worker_id,
            lease_token=lt_uuid,
            attempt=attempt,
            db_now=job_row["now"],
        ):
            raise LostLeaseError("Lost lease fence before complete")

        await session.execute(
            text("""
                UPDATE runtime.runs
                SET status = 'succeeded',
                    finished_at = clock_timestamp()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id
            """),
            {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
        )
        await session.execute(
            text("""
                UPDATE runtime.jobs
                SET status = 'done',
                    leased_by = NULL,
                    lease_token = NULL,
                    leased_until = NULL,
                    closed_reason = NULL
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
            """),
            {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": j_uuid},
        )
        return await self._event_repo.append_run_event(
            session,
            tenant_id=str(t_uuid),
            workspace_id=str(w_uuid),
            run_id=str(r_uuid),
            event_type="run.completed",
            data={"status": "succeeded"},
            trace_id=run_row["trace_id"],
        )

    async def fail_execution(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        job_id: str,
        run_id: str,
        worker_id: str,
        lease_token: str,
        attempt: int,
        error_code: str,
        closed_reason: str = "execution_failed",
    ) -> RunEventDTO:
        """Lock order: iam.workspaces SHARE -> runtime.runs FOR UPDATE -> runtime.jobs FOR UPDATE."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        j_uuid = UUID(job_id)
        r_uuid = UUID(run_id)
        lt_uuid = UUID(lease_token)

        # 1. Lock workspace SHARE
        await session.execute(
            text(
                "SELECT 1 FROM iam.workspaces WHERE tenant_id = :tenant_id AND id = :workspace_id FOR SHARE"
            ),
            {"tenant_id": t_uuid, "workspace_id": w_uuid},
        )

        # 2. Lock runtime.runs FOR UPDATE
        run_row = (
            (
                await session.execute(
                    text(
                        "SELECT id, status, trace_id FROM runtime.runs WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id FOR UPDATE"
                    ),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise LostLeaseError("Run not found")
        if run_row["status"] in ("succeeded", "failed", "cancelled", "timed_out"):
            raise RunAlreadyTerminalError(f"Run is already terminal ({run_row['status']})")

        # 3. Lock runtime.jobs FOR UPDATE and verify fence
        job_row = (
            (
                await session.execute(
                    text("""
                        SELECT id, status, leased_by, lease_token, attempt, leased_until, clock_timestamp() AS now
                        FROM runtime.jobs
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
                        FOR UPDATE
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": j_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if job_row is None:
            raise LostLeaseError("Job not found")

        if not is_lease_fence_valid(
            job_row,
            worker_id=worker_id,
            lease_token=lt_uuid,
            attempt=attempt,
            db_now=job_row["now"],
        ):
            raise LostLeaseError("Lost lease fence before fail")

        await session.execute(
            text("""
                UPDATE runtime.runs
                SET status = 'failed',
                    error_code = :error_code,
                    finished_at = clock_timestamp()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id
            """),
            {
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "run_id": r_uuid,
                "error_code": error_code,
            },
        )
        await session.execute(
            text("""
                UPDATE runtime.jobs
                SET status = 'dead',
                    closed_reason = :closed_reason,
                    leased_by = NULL,
                    lease_token = NULL,
                    leased_until = NULL
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
            """),
            {
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "job_id": j_uuid,
                "closed_reason": closed_reason,
            },
        )
        return await self._event_repo.append_run_event(
            session,
            tenant_id=str(t_uuid),
            workspace_id=str(w_uuid),
            run_id=str(r_uuid),
            event_type="run.failed",
            data={
                "status": "failed",
                "errorCode": "run_failed",
                "metadata": {"run": {"error_code": error_code}},
            },
            trace_id=run_row["trace_id"],
        )

    async def schedule_retry(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        job_id: str,
        run_id: str,
        worker_id: str,
        lease_token: str,
        attempt: int,
        delay_seconds: float,
        max_attempts: int = 3,
    ) -> bool:
        """Schedule job retry if attempt < max_attempts; if exhausted, fail run and mark job dead."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        j_uuid = UUID(job_id)
        r_uuid = UUID(run_id)
        lt_uuid = UUID(lease_token)

        # 1. Lock workspace SHARE
        await session.execute(
            text(
                "SELECT 1 FROM iam.workspaces WHERE tenant_id = :tenant_id AND id = :workspace_id FOR SHARE"
            ),
            {"tenant_id": t_uuid, "workspace_id": w_uuid},
        )

        # 2. Lock runtime.runs FOR UPDATE
        run_row = (
            (
                await session.execute(
                    text(
                        "SELECT id, status, trace_id FROM runtime.runs WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id FOR UPDATE"
                    ),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise LostLeaseError("Run not found")
        if run_row["status"] in ("succeeded", "failed", "cancelled", "timed_out"):
            raise RunAlreadyTerminalError(f"Run is already terminal ({run_row['status']})")

        # 3. Lock runtime.jobs FOR UPDATE and verify fence
        job_row = (
            (
                await session.execute(
                    text("""
                        SELECT id, status, leased_by, lease_token, attempt, leased_until, clock_timestamp() AS now
                        FROM runtime.jobs
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
                        FOR UPDATE
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": j_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if job_row is None:
            raise LostLeaseError("Job not found")

        if not is_lease_fence_valid(
            job_row,
            worker_id=worker_id,
            lease_token=lt_uuid,
            attempt=attempt,
            db_now=job_row["now"],
        ):
            raise LostLeaseError("Lost lease fence before retry")

        if attempt >= max_attempts:
            # Exhausted!
            await session.execute(
                text("""
                    UPDATE runtime.runs
                    SET status = 'failed',
                        error_code = 'attempts_exhausted',
                        finished_at = clock_timestamp()
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id
                """),
                {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": r_uuid},
            )
            await session.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = 'dead',
                        closed_reason = 'attempts_exhausted',
                        leased_by = NULL,
                        lease_token = NULL,
                        leased_until = NULL
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
                """),
                {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": j_uuid},
            )
            await self._event_repo.append_run_event(
                session,
                tenant_id=str(t_uuid),
                workspace_id=str(w_uuid),
                run_id=str(r_uuid),
                event_type="run.failed",
                data={
                    "status": "failed",
                    "errorCode": "run_failed",
                    "metadata": {"run": {"error_code": "attempts_exhausted"}},
                },
                trace_id=run_row["trace_id"],
            )
            return False

        # Return to queue with exponential delay
        await session.execute(
            text("""
                UPDATE runtime.jobs
                SET status = 'queued',
                    leased_by = NULL,
                    lease_token = NULL,
                    leased_until = NULL,
                    available_at = clock_timestamp() + make_interval(secs => :delay)
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
            """),
            {
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "job_id": j_uuid,
                "delay": max(0.1, delay_seconds),
            },
        )
        return True

    async def reclaim_expired_leases(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        max_attempts: int = 3,
    ) -> int:
        """Sweep expired leased jobs: if attempts >= max_attempts mark dead and fail run; else requeue with backoff."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)

        rows = (
            (
                await session.execute(
                    text("""
                        SELECT j.id, j.run_id, j.attempt, j.max_attempts
                        FROM runtime.jobs j
                        WHERE j.tenant_id = :tenant_id
                          AND j.workspace_id = :workspace_id
                          AND j.status = 'leased'
                          AND j.leased_until < clock_timestamp()
                        ORDER BY j.id
                        FOR UPDATE SKIP LOCKED
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid},
                )
            )
            .mappings()
            .all()
        )

        reclaimed = 0
        for r in rows:
            job_id = r["id"]
            run_id = r["run_id"]
            att = int(r["attempt"])
            lim = min(int(r["max_attempts"]), max_attempts)

            if att >= lim:
                # Attempts exhausted
                await session.execute(
                    text("""
                        UPDATE runtime.jobs
                        SET status = 'dead',
                            closed_reason = 'attempts_exhausted',
                            leased_by = NULL,
                            lease_token = NULL,
                            leased_until = NULL
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": job_id},
                )
                if run_id:
                    await session.execute(
                        text("""
                            UPDATE runtime.runs
                            SET status = 'failed',
                                error_code = 'attempts_exhausted',
                                finished_at = clock_timestamp()
                            WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :run_id
                              AND status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out')
                        """),
                        {"tenant_id": t_uuid, "workspace_id": w_uuid, "run_id": run_id},
                    )
                    await self._event_repo.append_run_event(
                        session,
                        tenant_id=str(t_uuid),
                        workspace_id=str(w_uuid),
                        run_id=str(run_id),
                        event_type="run.failed",
                        data={
                            "status": "failed",
                            "errorCode": "run_failed",
                            "metadata": {"run": {"error_code": "attempts_exhausted"}},
                        },
                    )
            else:
                delay = min(2**att, 60)
                await session.execute(
                    text("""
                        UPDATE runtime.jobs
                        SET status = 'queued',
                            leased_by = NULL,
                            lease_token = NULL,
                            leased_until = NULL,
                            available_at = clock_timestamp() + make_interval(secs => :delay)
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :job_id
                    """),
                    {"tenant_id": t_uuid, "workspace_id": w_uuid, "job_id": job_id, "delay": delay},
                )
            reclaimed += 1

        return reclaimed
