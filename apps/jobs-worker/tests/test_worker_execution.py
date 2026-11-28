"""Unit tests for jobs-worker execution composition and boundaries (DEC-07, T01, T03)."""

from __future__ import annotations

import ast
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from agent_runtime.application.execution.grant import ExecutionKeyUnavailableError
from agent_runtime.execution_api import (
    SentinelRunExecutor,
    validate_execution_configuration,
)
from agent_runtime.settings import RuntimeSettings
from jobs_worker.worker import JobExecutionError, JobRecord, JobStatus, JobType, JobWorker
from platform_common.execution import (
    ExecutionCommand,
    ExecutionDisposition,
    ExecutionDispositionType,
    LeaseIdentity,
)


def test_worker_source_import_boundary_isolation() -> None:
    """Enforce architectural boundary: jobs_worker must only import from execution_api,

    never agent_runtime internal routers, repositories, models, or control API.
    """
    worker_src_dir = Path(__file__).resolve().parents[1] / "src" / "jobs_worker"
    assert worker_src_dir.is_dir()

    forbidden_prefixes = (
        "agent_runtime.persistence",
        "agent_runtime.api",
        "agent_runtime.llm",
        "agent_runtime.application.run_admission_repository",
        "control_plane",
        "control_api",
    )

    for py_file in worker_src_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_prefixes:
                        assert not alias.name.startswith(forbidden), (
                            f"{py_file.name} illegally imports {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_prefixes:
                    assert not node.module.startswith(forbidden), (
                        f"{py_file.name} illegally imports from {node.module}"
                    )


def test_validate_execution_configuration_fail_closed() -> None:
    # 1. Disabled flag: passes even without keys
    settings_disabled = RuntimeSettings(
        run_execution_grants_enabled=False,
        run_worker_execution_enabled=False,
        run_execution_key="",
    )
    # Does not raise
    validate_execution_configuration(settings_disabled)

    # 2. Enabled flag without key: fails closed
    settings_enabled_no_key = RuntimeSettings(
        run_execution_grants_enabled=True,
        run_worker_execution_enabled=True,
        run_execution_key="",
    )
    with pytest.raises((ValueError, ExecutionKeyUnavailableError)):
        validate_execution_configuration(settings_enabled_no_key)

    # 3. Enabled flag with too short key: fails closed
    settings_enabled_short_key = RuntimeSettings(
        run_execution_grants_enabled=True,
        run_worker_execution_enabled=True,
        run_execution_key="too-short",
    )
    with pytest.raises((ValueError, ExecutionKeyUnavailableError)):
        validate_execution_configuration(settings_enabled_short_key)

    # 4. Enabled flag with valid 32-byte key: succeeds
    valid_key = os.urandom(32).hex()
    settings_valid = RuntimeSettings(
        run_execution_grants_enabled=True,
        run_worker_execution_enabled=True,
        run_execution_key=valid_key,
        database_url="postgresql+psycopg://user:pass@localhost:5432/platform",
    )
    # Does not raise
    validate_execution_configuration(settings_valid)


def test_claimable_job_types_excludes_resume() -> None:
    """Per DEC-07 D7-01: RESUME is not auto-claimed (requires signed approval proof verification)."""
    # Without executor
    worker_no_exec = JobWorker(
        database_url="postgresql+psycopg://platform_app:secret@localhost:5432/platform",
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        async_run_executor=None,
    )
    assert worker_no_exec._claimable_job_types() == ()

    # With executor
    mock_executor = AsyncMock()
    worker_with_exec = JobWorker(
        database_url="postgresql+psycopg://platform_app:secret@localhost:5432/platform",
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        async_run_executor=mock_executor,
    )
    claimable = worker_with_exec._claimable_job_types()
    assert JobType.RUN in claimable
    assert JobType.RESUME not in claimable


@pytest.mark.anyio
async def test_sentinel_executor_returns_typed_completed_disposition() -> None:
    mock_state_owner = AsyncMock()
    mock_begin = MagicMock()
    mock_begin.success = True
    mock_begin.started = True
    mock_state_owner.begin_execution = AsyncMock(return_value=(mock_begin, None, None))

    lease_ident = LeaseIdentity(
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        job_id=str(uuid4()),
        run_id=str(uuid4()),
        leased_by="worker-1",
        lease_token=str(uuid4()),
        attempt=1,
    )

    mock_state_owner.complete_execution = AsyncMock(
        return_value=ExecutionDisposition(
            disposition=ExecutionDispositionType.COMPLETED,
            lease_identity=lease_ident,
            terminal=True,
        )
    )

    executor = SentinelRunExecutor(state_owner=mock_state_owner)
    cmd = ExecutionCommand(
        lease_identity=lease_ident,
        payload_ref="s3://agent-platform-docs/payload.json",
    )
    disp = await executor.execute(cmd)
    assert isinstance(disp, ExecutionDisposition)
    assert disp.disposition == ExecutionDispositionType.COMPLETED
    assert disp.terminal is True
    assert disp.lease_identity == lease_ident


@pytest.mark.anyio
async def test_worker_process_job_async_heartbeat_cancels_on_completion() -> None:
    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())
    j_id = str(uuid4())
    token = str(uuid4())
    now = datetime.now(UTC)

    job = JobRecord(
        id=j_id,
        tenant_id=t_id,
        workspace_id=w_id,
        job_type=JobType.RUN,
        run_id=r_id,
        payload_ref=f"s3://agent-platform-docs/tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/input/{'a' * 64}-128.json",
        status=JobStatus.LEASED,
        available_at=now,
        leased_until=now,
        leased_by="worker-1",
        lease_token=token,
        attempt=1,
        max_attempts=3,
        created_at=now,
    )

    lease_ident = LeaseIdentity(
        tenant_id=t_id,
        workspace_id=w_id,
        job_id=j_id,
        run_id=r_id,
        leased_by="worker-1",
        lease_token=token,
        attempt=1,
    )

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(
        return_value=ExecutionDisposition(
            disposition=ExecutionDispositionType.COMPLETED,
            lease_identity=lease_ident,
            terminal=True,
        )
    )

    worker = JobWorker(
        database_url="postgresql+psycopg://platform_app:secret@localhost:5432/platform",
        tenant_id=t_id,
        workspace_id=w_id,
        async_run_executor=mock_executor,
        heartbeat_interval_seconds=0.1,
    )
    worker.extend_lease = MagicMock(return_value=True)

    result = await worker.process_job_async(job)
    assert result is True
    assert mock_executor.execute.await_count == 1


@pytest.mark.anyio
async def test_worker_process_job_async_rejects_none_disposition() -> None:
    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())
    j_id = str(uuid4())
    token = str(uuid4())
    now = datetime.now(UTC)

    job = JobRecord(
        id=j_id,
        tenant_id=t_id,
        workspace_id=w_id,
        job_type=JobType.RUN,
        run_id=r_id,
        payload_ref=f"s3://agent-platform-docs/tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/input/{'a' * 64}-128.json",
        status=JobStatus.LEASED,
        available_at=now,
        leased_until=now,
        leased_by="worker-1",
        lease_token=token,
        attempt=1,
        max_attempts=3,
        created_at=now,
    )

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=None)  # None disposition

    worker = JobWorker(
        database_url="postgresql+psycopg://platform_app:secret@localhost:5432/platform",
        tenant_id=t_id,
        workspace_id=w_id,
        async_run_executor=mock_executor,
    )

    with pytest.raises(JobExecutionError, match="None is never success"):
        await worker.process_job_async(job)
