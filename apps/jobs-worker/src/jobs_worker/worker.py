"""PostgreSQL-backed worker for the DB-56 ``runtime.jobs`` queue (DEC-07 / DATA-07).

This worker deliberately has no in-memory queue. A process without a scoped
PostgreSQL connection must fail during boot instead of accepting jobs that it
would lose on restart.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import unquote, urlparse
from uuid import UUID

from platform_common.execution import (
    AsyncRunExecutor,
    ExecutionCommand,
    ExecutionDispositionType,
    LeaseIdentity,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("jobs_worker")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_bool_env(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    DEAD = "dead"


class JobType(enum.StrEnum):
    RUN = "run"
    RESUME = "resume"
    INGEST = "ingest"
    RECONCILE = "reconcile"


class JobExecutionError(RuntimeError):
    """A job could not be completed by a real configured handler."""


class RunExecutor(Protocol):
    """Synchronous executor protocol retained for backward compatibility."""

    def execute(self, job: JobRecord) -> None:
        """Execute the pinned run and raise if it did not reach a real terminal result."""


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    tenant_id: str
    workspace_id: str
    job_type: JobType
    run_id: str | None
    payload_ref: str | None
    status: JobStatus
    available_at: datetime
    leased_until: datetime | None
    leased_by: str | None
    attempt: int
    max_attempts: int
    created_at: datetime
    lease_token: str | None = None

    def to_lease_identity(self) -> LeaseIdentity:
        if not self.lease_token:
            raise JobExecutionError("Job has no lease_token; cannot construct LeaseIdentity")
        return LeaseIdentity(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            job_id=self.id,
            run_id=self.run_id,
            leased_by=self.leased_by or "",
            attempt=self.attempt,
            lease_token=self.lease_token,
        )


JobHandler = Callable[[JobRecord], None]


class JobWorker:
    """Claim jobs transactionally with ``FOR UPDATE SKIP LOCKED`` in one scope."""

    def __init__(
        self,
        database_url: str,
        tenant_id: str,
        workspace_id: str,
        *,
        worker_id: str | None = None,
        lease_duration_seconds: int = 30,
        heartbeat_interval_seconds: float = 5.0,
        workspace_concurrency_cap: int = 2,
        max_attempts: int = 3,
        engine: Engine | None = None,
        run_executor: RunExecutor | None = None,
        async_run_executor: AsyncRunExecutor | None = None,
        handlers: dict[JobType, JobHandler] | None = None,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("JobWorker requires a PostgreSQL DATABASE_URL")
        self.tenant_id = str(UUID(tenant_id))
        self.workspace_id = str(UUID(workspace_id))
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.lease_duration_seconds = lease_duration_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.workspace_concurrency_cap = workspace_concurrency_cap
        self.max_attempts = max_attempts
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive")
        self._engine = engine or create_engine(database_url, pool_pre_ping=True)
        self._run_executor = run_executor
        self._async_run_executor = async_run_executor
        self._handlers = handlers or {}
        self._running = False

    @classmethod
    def from_environment(cls) -> JobWorker:
        run_consumer_enabled = parse_bool_env("RUN_WORKER_EXECUTION_ENABLED", default=False)
        async_executor: AsyncRunExecutor | None = None

        if run_consumer_enabled:
            # Composition boundary: import facade only, never Runtime internals or Control API
            from agent_runtime.execution_api import (
                create_execution_state_owner,
                create_sentinel_executor,
                validate_execution_configuration,
            )

            validate_execution_configuration()
            state_owner = create_execution_state_owner()
            async_executor = create_sentinel_executor(state_owner=state_owner)

        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            tenant_id=os.environ.get("WORKER_TENANT_ID", ""),
            workspace_id=os.environ.get("WORKER_WORKSPACE_ID", ""),
            async_run_executor=async_executor,
        )

    @property
    def has_run_executor(self) -> bool:
        """Return True only when a real run executor is configured."""
        return self._async_run_executor is not None or self._run_executor is not None

    def _claimable_job_types(self) -> tuple[str, ...]:
        """Claimable types: RUN only if executor present (RESUME is not auto-claimed; RD-09 disabled)."""
        types: list[str] = []
        if self.has_run_executor:
            # DEC-07: RESUME does NOT claim automatically when executor is present
            types.append(JobType.RUN.value)
        for ext_type in (JobType.INGEST, JobType.RECONCILE):
            if ext_type in self._handlers:
                types.append(ext_type.value)
        return tuple(types)

    @staticmethod
    def validate_run_payload_ref(
        job: JobRecord, *, expected_bucket: str | None = None
    ) -> tuple[str, str, str, int]:
        """Validate that a run/resume job payload_ref matches the 06-A scoped S3 contract."""
        ref = job.payload_ref
        if not ref or "\\" in ref or "\x00" in ref:
            raise JobExecutionError("Run job is missing a valid payload_ref")
        decoded = unquote(ref)
        if ".." in decoded or "\\" in decoded:
            raise JobExecutionError("Job payload_ref contains forbidden path traversal")
        parsed = urlparse(ref)
        if parsed.scheme != "s3" or parsed.query or parsed.fragment or parsed.params:
            raise JobExecutionError("Job payload_ref must use canonical s3:// format")
        bucket = parsed.netloc
        if not bucket or (expected_bucket is not None and bucket != expected_bucket.strip()):
            raise JobExecutionError("Job payload_ref bucket mismatch")
        parts = parsed.path.lstrip("/").split("/")
        if (
            len(parts) != 8
            or parts[0] != "tenants"
            or parts[2] != "workspaces"
            or parts[4] != "runs"
            or parts[6] != "input"
            or not parts[7].endswith(".json")
        ):
            raise JobExecutionError("Job payload_ref does not match canonical scoped layout")
        if (
            str(UUID(parts[1])) != job.tenant_id
            or str(UUID(parts[3])) != job.workspace_id
            or (job.run_id is not None and str(UUID(parts[5])) != job.run_id)
        ):
            raise JobExecutionError("Job payload_ref scope mismatch")
        stem = parts[7].removesuffix(".json")
        if "-" not in stem:
            raise JobExecutionError("Job payload_ref missing sha256-size integrity binding")
        sha_part, size_part = stem.split("-", 1)
        if not _SHA256_RE.match(sha_part) or not size_part.isdigit():
            raise JobExecutionError("Job payload_ref has invalid sha256-size binding")
        return bucket, parsed.path.lstrip("/"), sha_part, int(size_part)

    def _set_scope(self, connection: Any) -> None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": self.tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
            {"workspace_id": self.workspace_id},
        )

    @staticmethod
    def _record(row: Any) -> JobRecord:
        data = row._mapping
        return JobRecord(
            id=str(data["id"]),
            tenant_id=str(data["tenant_id"]),
            workspace_id=str(data["workspace_id"]),
            job_type=JobType(data["job_type"]),
            run_id=str(data["run_id"]) if data["run_id"] else None,
            payload_ref=data["payload_ref"],
            status=JobStatus(data["status"]),
            available_at=data["available_at"],
            leased_until=data["leased_until"],
            leased_by=data["leased_by"],
            attempt=data["attempt"],
            max_attempts=data["max_attempts"],
            created_at=data["created_at"],
            lease_token=str(data["lease_token"]) if data.get("lease_token") else None,
        )

    def lease_next_job(self) -> JobRecord | None:
        """Atomically claim one due job under workspace advisory xact lock and workspace limit cap.

        Locks ONLY runtime.jobs via FOR UPDATE OF j SKIP LOCKED and commits immediately.
        """
        claimable = self._claimable_job_types()
        if not claimable:
            return None

        lease_token = str(uuid.uuid4())
        with self._engine.begin() as connection:
            self._set_scope(connection)

            # 1. Scoped advisory xact lock per workspace
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"run_claim_ws:{self.tenant_id}:{self.workspace_id}"},
            )

            # 2. Workspace concurrency cap: max 2 active leased jobs in this workspace
            active_val = connection.execute(
                text("""
                    SELECT COUNT(*)
                    FROM runtime.jobs
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status = 'leased'
                      AND leased_until > clock_timestamp()
                """),
                {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id},
            ).scalar_one()

            if int(active_val) >= self.workspace_concurrency_cap:
                return None

            # 3. Candidate job selection with SKIP LOCKED on runtime.jobs only
            result = connection.execute(
                text("""
                    WITH candidate AS (
                        SELECT j.id
                        FROM runtime.jobs AS j
                        LEFT JOIN runtime.runs AS r
                          ON r.tenant_id = j.tenant_id
                         AND r.workspace_id = j.workspace_id
                         AND r.id = j.run_id
                        WHERE j.tenant_id = CAST(:tenant_id AS uuid)
                          AND j.workspace_id = CAST(:workspace_id AS uuid)
                          AND j.status = 'queued'
                          AND j.job_type = ANY(:claimable_types)
                          AND j.available_at <= clock_timestamp()
                          AND j.attempt < :max_attempts
                          AND (
                              j.run_id IS NULL
                              OR (
                                  r.status = 'queued'
                                  AND (r.queue_expires_at IS NULL OR r.queue_expires_at > clock_timestamp())
                              )
                              OR (
                                  r.status = 'running'
                                  AND (r.execution_deadline_at IS NULL OR r.execution_deadline_at > clock_timestamp())
                              )
                          )
                        ORDER BY j.available_at, j.id
                        FOR UPDATE OF j SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE runtime.jobs AS job
                    SET status = 'leased',
                        leased_by = :worker_id,
                        lease_token = CAST(:lease_token AS uuid),
                        leased_until = clock_timestamp() + make_interval(secs => :lease_seconds),
                        attempt = attempt + 1
                    FROM candidate
                    WHERE job.id = candidate.id
                    RETURNING job.id, job.tenant_id, job.workspace_id, job.run_id, job.job_type,
                              job.payload_ref, job.status, job.available_at, job.leased_until,
                              job.leased_by, job.attempt, job.max_attempts, job.created_at, job.lease_token
                """),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "worker_id": self.worker_id,
                    "lease_token": lease_token,
                    "lease_seconds": self.lease_duration_seconds,
                    "max_attempts": self.max_attempts,
                    "claimable_types": list(claimable),
                },
            ).first()

        return self._record(result) if result is not None else None

    def extend_lease(self, job: JobRecord) -> bool:
        """Heartbeat extension. Valid only if lease is currently active and held by this worker."""
        if not job.lease_token:
            return False
        with self._engine.begin() as connection:
            self._set_scope(connection)
            res = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET leased_until = clock_timestamp() + make_interval(secs => :lease_seconds)
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND id = CAST(:job_id AS uuid)
                      AND status = 'leased'
                      AND leased_by = :worker_id
                      AND lease_token = CAST(:lease_token AS uuid)
                      AND leased_until > clock_timestamp()
                """),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "job_id": job.id,
                    "worker_id": self.worker_id,
                    "lease_token": job.lease_token,
                    "lease_seconds": self.lease_duration_seconds,
                },
            )
            return bool(res.rowcount == 1)

    def reclaim_expired_leases(self) -> int:
        """Return expired leases to the queue or dead-letter them at their attempt limit."""
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = CASE WHEN attempt >= :max_attempts THEN 'dead' ELSE 'queued' END,
                        closed_reason = CASE WHEN attempt >= :max_attempts THEN 'attempts_exhausted' ELSE NULL END,
                        leased_by = NULL,
                        lease_token = NULL,
                        leased_until = NULL,
                        available_at = CASE
                            WHEN attempt >= :max_attempts THEN available_at
                            ELSE clock_timestamp() + make_interval(secs => LEAST(POWER(2, attempt)::integer, 60))
                        END
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status = 'leased'
                      AND leased_until < clock_timestamp()
                """),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "max_attempts": self.max_attempts,
                },
            )
            count = int(result.rowcount or 0)

            # Reclaim also transitions associated runs if attempts exhausted
            connection.execute(
                text("""
                    UPDATE runtime.runs
                    SET status = 'failed',
                        error_code = 'attempts_exhausted',
                        finished_at = clock_timestamp()
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND id IN (
                          SELECT run_id FROM runtime.jobs
                          WHERE tenant_id = CAST(:tenant_id AS uuid)
                            AND workspace_id = CAST(:workspace_id AS uuid)
                            AND status = 'dead'
                            AND closed_reason = 'attempts_exhausted'
                            AND run_id IS NOT NULL
                      )
                      AND status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out')
                """),
                {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id},
            )
            return count

    def _set_run_status(self, job: JobRecord, status: str, *, terminal: bool = False) -> None:
        """Retained for extension job backwards compatibility."""
        if not job.run_id:
            raise JobExecutionError("Run and resume jobs require run_id")
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.runs
                    SET status = :status,
                        started_at = CASE WHEN :status = 'running' THEN coalesce(started_at, now()) ELSE started_at END,
                        finished_at = CASE WHEN :terminal THEN now() ELSE finished_at END
                    WHERE id = CAST(:run_id AS uuid)
                      AND tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status IN ('queued', 'running', 'waiting_approval')
                """),
                {
                    "status": status,
                    "terminal": terminal,
                    "run_id": job.run_id,
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                },
            )
            if result.rowcount != 1:
                raise JobExecutionError("Run is not available for the requested state transition")

    def _handle_extension(self, job: JobRecord) -> None:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            raise JobExecutionError(f"No real handler is configured for {job.job_type.value}")
        handler(job)

    def _complete(self, job: JobRecord) -> None:
        """Extension job complete path."""
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = 'done', leased_until = NULL, lease_token = NULL
                    WHERE id = CAST(:job_id AS uuid)
                      AND tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status = 'leased' AND leased_by = :worker_id
                """),
                {
                    "job_id": job.id,
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "worker_id": self.worker_id,
                },
            )
            if result.rowcount != 1:
                raise JobExecutionError("Lost job lease before completion")

    def _fail(self, job: JobRecord, error: Exception) -> None:
        """Extension job fail path."""
        next_status = "dead" if job.attempt >= job.max_attempts else "queued"
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = :status,
                        leased_by = NULL,
                        lease_token = NULL,
                        leased_until = NULL,
                        available_at = CASE WHEN :status = 'queued'
                            THEN now() + make_interval(secs => LEAST(POWER(2, :attempt)::integer, 60))
                            ELSE available_at END
                    WHERE id = CAST(:job_id AS uuid)
                      AND tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status = 'leased' AND leased_by = :worker_id
                """),
                {
                    "status": next_status,
                    "attempt": job.attempt,
                    "job_id": job.id,
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "worker_id": self.worker_id,
                },
            )
            if result.rowcount != 1:
                raise JobExecutionError("Lost job lease before failure transition") from error
        if job.run_id and next_status == "dead":
            self._set_run_status(job, "failed", terminal=True)

    async def process_job_async(self, job: JobRecord) -> bool:
        """Pure async execution path with dedicated heartbeat loop task."""
        if job.job_type is JobType.RUN:
            if self._async_run_executor is not None:
                lease_identity = job.to_lease_identity()
                command = ExecutionCommand(
                    lease_identity=lease_identity,
                    payload_ref=job.payload_ref or "",
                )

                # Dedicated heartbeat loop task
                cancel_heartbeat = asyncio.Event()

                async def _heartbeat_loop() -> None:
                    while not cancel_heartbeat.is_set():
                        try:
                            await asyncio.sleep(self.heartbeat_interval_seconds)
                            if cancel_heartbeat.is_set():
                                break
                            ok = await asyncio.to_thread(self.extend_lease, job)
                            if not ok:
                                logger.warning(
                                    "Heartbeat failed for job %s: lease lost or taken over",
                                    job.id,
                                    extra={
                                        "tenant_id": self.tenant_id,
                                        "workspace_id": self.workspace_id,
                                        "job_id": job.id,
                                        "run_id": job.run_id,
                                        "worker_id": self.worker_id,
                                        "lease_token": job.lease_token,
                                    },
                                )
                                break
                        except asyncio.CancelledError:
                            break
                        except (OSError, RuntimeError, Exception) as exc:  # noqa: BLE001
                            logger.warning(
                                "Heartbeat error on job %s (%s)",
                                job.id,
                                exc,
                                extra={
                                    "tenant_id": self.tenant_id,
                                    "workspace_id": self.workspace_id,
                                    "job_id": job.id,
                                    "run_id": job.run_id,
                                    "error_type": type(exc).__name__,
                                },
                            )

                hb_task = asyncio.create_task(_heartbeat_loop())
                try:
                    disposition = await self._async_run_executor.execute(command)
                finally:
                    cancel_heartbeat.set()
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass

                if disposition is None:
                    raise JobExecutionError("Run executor returned None; None is never success")

                if disposition.disposition == ExecutionDispositionType.COMPLETED:
                    return True
                if disposition.disposition == ExecutionDispositionType.LOST_LEASE:
                    logger.warning(
                        "Worker lost lease fence during execution of job %s",
                        job.id,
                        extra={
                            "tenant_id": self.tenant_id,
                            "workspace_id": self.workspace_id,
                            "job_id": job.id,
                            "run_id": job.run_id,
                            "worker_id": self.worker_id,
                        },
                    )
                    return False
                if disposition.disposition == ExecutionDispositionType.RETRY_SCHEDULED:
                    logger.info(
                        "Job %s retry scheduled in %s seconds",
                        job.id,
                        disposition.retry_delay_seconds,
                        extra={
                            "tenant_id": self.tenant_id,
                            "workspace_id": self.workspace_id,
                            "job_id": job.id,
                            "run_id": job.run_id,
                            "retry_delay_seconds": disposition.retry_delay_seconds,
                        },
                    )
                    return False
                logger.warning(
                    "Job %s terminal non-success disposition: %s (%s)",
                    job.id,
                    disposition.disposition,
                    disposition.error_code,
                    extra={
                        "tenant_id": self.tenant_id,
                        "workspace_id": self.workspace_id,
                        "job_id": job.id,
                        "run_id": job.run_id,
                        "disposition": disposition.disposition.value,
                        "error_code": disposition.error_code,
                    },
                )
                return False

            if self._run_executor is not None:
                self._set_run_status(job, "running")
                self._run_executor.execute(job)
                self._set_run_status(job, "succeeded", terminal=True)
                self._complete(job)
                return True

            raise JobExecutionError("No run executor configured")

        if job.job_type is JobType.RESUME:
            if self._run_executor is not None:
                self._set_run_status(job, "running")
                self._run_executor.execute(job)
                self._set_run_status(job, "succeeded", terminal=True)
                self._complete(job)
                return True
            raise JobExecutionError("No resume executor configured")

        # Extension jobs (ingest, reconcile)
        try:
            self._handle_extension(job)
            self._complete(job)
            return True
        except Exception as exc:
            logger.exception("Extension job %s failed", job.id)
            self._fail(job, exc)
            return False

    def process_job(self, job: JobRecord) -> bool:
        """Synchronous wrapper for process_job_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.process_job_async(job)).result()
        return asyncio.run(self.process_job_async(job))

    async def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        self._running = True
        try:
            while self._running:
                job = self.lease_next_job()
                if job is None:
                    self.reclaim_expired_leases()
                    try:
                        await asyncio.sleep(poll_interval_seconds)
                    except asyncio.CancelledError:
                        break
                    continue
                await self.process_job_async(job)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
