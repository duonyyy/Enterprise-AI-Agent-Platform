"""PostgreSQL-backed worker for the DB-56 ``runtime.jobs`` queue.

This worker deliberately has no in-memory queue. A process without a scoped
PostgreSQL connection must fail during boot instead of accepting jobs that it
would lose on restart.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("jobs_worker")


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    DEAD = "dead"


class JobType(str, enum.Enum):
    RUN = "run"
    RESUME = "resume"
    INGEST = "ingest"
    RECONCILE = "reconcile"


class JobExecutionError(RuntimeError):
    """A job could not be completed by a real configured handler."""


class RunExecutor(Protocol):
    def execute(self, job: "JobRecord") -> None:
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
        engine: Engine | None = None,
        run_executor: RunExecutor | None = None,
        handlers: dict[JobType, JobHandler] | None = None,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("JobWorker requires a PostgreSQL DATABASE_URL")
        self.tenant_id = str(UUID(tenant_id))
        self.workspace_id = str(UUID(workspace_id))
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.lease_duration_seconds = lease_duration_seconds
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive")
        self._engine = engine or create_engine(database_url, pool_pre_ping=True)
        self._run_executor = run_executor
        self._handlers = handlers or {}
        self._running = False

    @classmethod
    def from_environment(cls) -> "JobWorker":
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            tenant_id=os.environ.get("WORKER_TENANT_ID", ""),
            workspace_id=os.environ.get("WORKER_WORKSPACE_ID", ""),
        )

    def _set_scope(self, connection: object) -> None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": self.tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
            {"workspace_id": self.workspace_id},
        )

    @staticmethod
    def _record(row: object) -> JobRecord:
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
        )

    def lease_next_job(self) -> JobRecord | None:
        """Atomically claim one due job. Network or model work never runs in this transaction."""
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    WITH candidate AS (
                        SELECT id
                        FROM runtime.jobs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND status = 'queued'
                          AND available_at <= now()
                        ORDER BY available_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE runtime.jobs AS job
                    SET status = 'leased',
                        leased_by = :worker_id,
                        leased_until = now() + make_interval(secs => :lease_seconds),
                        attempt = attempt + 1
                    FROM candidate
                    WHERE job.id = candidate.id
                    RETURNING job.id, job.tenant_id, job.workspace_id, job.run_id, job.job_type,
                              job.payload_ref, job.status, job.available_at, job.leased_until,
                              job.leased_by, job.attempt, job.max_attempts, job.created_at
                """),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "worker_id": self.worker_id,
                    "lease_seconds": self.lease_duration_seconds,
                },
            ).first()
        return self._record(result) if result is not None else None

    def reclaim_expired_leases(self) -> int:
        """Return expired leases to the queue or dead-letter them at their attempt limit."""
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = CASE WHEN attempt >= max_attempts THEN 'dead' ELSE 'queued' END,
                        leased_by = NULL,
                        leased_until = NULL,
                        available_at = CASE
                            WHEN attempt >= max_attempts THEN available_at
                            ELSE now() + make_interval(secs => LEAST(POWER(2, attempt)::integer, 60))
                        END
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status = 'leased'
                      AND leased_until < now()
                """),
                {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id},
            )
            return result.rowcount

    def _set_run_status(self, job: JobRecord, status: str, *, terminal: bool = False) -> None:
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

    def _handle_run(self, job: JobRecord) -> None:
        self._set_run_status(job, "running")
        if self._run_executor is None:
            raise JobExecutionError("No real run executor is configured")
        self._run_executor.execute(job)
        self._set_run_status(job, "succeeded", terminal=True)

    def _handle_resume(self, job: JobRecord) -> None:
        self._handle_run(job)

    def _handle_extension(self, job: JobRecord) -> None:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            raise JobExecutionError(f"No real handler is configured for {job.job_type.value}")
        handler(job)

    def _complete(self, job: JobRecord) -> None:
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = 'done', leased_until = NULL
                    WHERE id = CAST(:job_id AS uuid)
                      AND tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND status = 'leased' AND leased_by = :worker_id
                """),
                {"job_id": job.id, "tenant_id": self.tenant_id, "workspace_id": self.workspace_id, "worker_id": self.worker_id},
            )
            if result.rowcount != 1:
                raise JobExecutionError("Lost job lease before completion")

    def _fail(self, job: JobRecord, error: Exception) -> None:
        next_status = "dead" if job.attempt >= job.max_attempts else "queued"
        with self._engine.begin() as connection:
            self._set_scope(connection)
            result = connection.execute(
                text("""
                    UPDATE runtime.jobs
                    SET status = :status,
                        leased_by = NULL,
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

    def process_job(self, job: JobRecord) -> bool:
        try:
            if job.job_type is JobType.RUN:
                self._handle_run(job)
            elif job.job_type is JobType.RESUME:
                self._handle_resume(job)
            else:
                self._handle_extension(job)
            self._complete(job)
            return True
        except Exception as exc:
            logger.exception("Job failed without a durable success receipt", extra={"job_id": job.id})
            self._fail(job, exc)
            return False

    async def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        self._running = True
        while self._running:
            job = self.lease_next_job()
            if job is None:
                self.reclaim_expired_leases()
                await asyncio.sleep(poll_interval_seconds)
                continue
            self.process_job(job)

    def stop(self) -> None:
        self._running = False
