"""Subprocess HTTP barrier helper for Step 06-B post-COMMIT crash recovery test (G6-B06 / R06B-05).

Runs the real ``agent_runtime.main:app`` HTTP API against real PostgreSQL, MinIO,
and Redis, while subclassing SQLAlchemy ``AsyncSession`` so the Phase-3 admission
transaction calls ``await super().commit()`` in PostgreSQL, emits
``BARRIER:committed`` to stdout, and then freezes before returning the HTTP 202
response so the test harness can kill the OS process mid-flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import uvicorn
from agent_runtime.api.runs import get_run_admission_service
from agent_runtime.application.run_admission import AdmittedRunResult
from agent_runtime.application.run_admission_service import RunAdmissionService
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.main import app
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.object_store import create_object_store
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_data_repository import RunDataRepository
from agent_runtime.settings import get_settings
from platform_common.event_bus import PostgresOutboxEventBus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class BarrierSession(AsyncSession):
    """AsyncSession that signals stdout right after PostgreSQL COMMIT succeeds."""

    async def commit(self) -> None:
        await super().commit()
        if self.info.get("barrier_on_commit"):
            print("BARRIER:committed", flush=True)
            await asyncio.Event().wait()


class _BarrierRunAdmissionService(RunAdmissionService):
    async def admit_run(
        self,
        *,
        principal: RuntimePrincipal,
        workspace_id: str,
        agent_version_id: str,
        inputs: dict[str, Any],
        conversation_id: str | None = None,
        attachments: Any = (),
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        after_write_hook: Callable[[str, Any], Awaitable[None]] | None = None,
    ) -> AdmittedRunResult:
        async def _mark_barrier(stage: str, session: Any) -> None:
            if stage == "after_outbox_insert":
                session.info["barrier_on_commit"] = True
            if after_write_hook is not None:
                await after_write_hook(stage, session)

        return await super().admit_run(
            principal=principal,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            inputs=inputs,
            conversation_id=conversation_id,
            attachments=attachments,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            after_write_hook=_mark_barrier,
        )


def main() -> int:
    port = int(os.environ["CRASH_HELPER_PORT"])
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=BarrierSession, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _scoped_session(
        tenant_id: str, workspace_id: str, user_id: str | None = None
    ) -> AsyncIterator[BarrierSession]:
        async with factory() as session:
            try:
                await session.begin()
                await set_rls_context(
                    session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id
                )
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    object_store = create_object_store(settings)
    run_data_repo = RunDataRepository()
    run_data_service = RunDataService(
        object_store=object_store,
        bucket=settings.s3_bucket,
        repository=run_data_repo,
        max_input_bytes=settings.run_data_max_input_bytes,
        max_output_bytes=settings.run_data_max_output_bytes,
        default_retention_days=settings.run_data_default_retention_days,
    )
    crash_service = _BarrierRunAdmissionService(
        session_factory=_scoped_session,
        run_data_service=run_data_service,
        admission_repository=RunAdmissionRepository(),
        outbox_publisher=PostgresOutboxEventBus(factory),
        run_data_repository=run_data_repo,
        max_queued_per_workspace=settings.run_admission_max_queued_per_workspace,
        execution_available=settings.run_admission_execution_enabled,
        availability_reason=None
        if settings.run_admission_execution_enabled
        else "no_executor_configured",
    )
    app.dependency_overrides[get_run_admission_service] = lambda: crash_service
    print(f"BARRIER:listening:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
