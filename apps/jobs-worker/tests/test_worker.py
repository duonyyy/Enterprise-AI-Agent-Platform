"""Worker boot guards and D6-03 no-executor admission compatibility tests.

Queue leasing and RLS are PostgreSQL integration concerns; no dict-backed queue
is used here as a replacement for runtime behaviour.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from jobs_worker.worker import JobExecutionError, JobRecord, JobStatus, JobType, JobWorker


def test_worker_rejects_non_postgresql_database_url():
    with pytest.raises(ValueError, match="PostgreSQL"):
        JobWorker(
            database_url="sqlite:///:memory:",
            tenant_id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000011",
        )


def test_worker_requires_valid_scope_ids():
    with pytest.raises(ValueError):
        JobWorker(
            database_url="postgresql+psycopg://platform_app:secret@db/platform",
            tenant_id="not-a-uuid",
            workspace_id="00000000-0000-0000-0000-000000000011",
        )


def test_worker_without_executor_disables_run_consumer_claim():
    worker = JobWorker(
        database_url="postgresql+psycopg://platform_app:secret@db/platform",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000011",
    )
    assert worker.has_run_executor is False
    assert worker._claimable_job_types() == ()
    assert worker.lease_next_job() is None


def test_worker_validates_admitted_run_payload_ref_contract():
    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())
    digest = "a" * 64
    valid_ref = f"s3://agent-platform-docs/tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/input/{digest}-128.json"
    now = datetime.now(UTC)

    job = JobRecord(
        id=str(uuid4()),
        tenant_id=t_id,
        workspace_id=w_id,
        job_type=JobType.RUN,
        run_id=r_id,
        payload_ref=valid_ref,
        status=JobStatus.QUEUED,
        available_at=now,
        leased_until=None,
        leased_by=None,
        attempt=0,
        max_attempts=5,
        created_at=now,
    )
    bucket, key, parsed_sha, parsed_size = JobWorker.validate_run_payload_ref(
        job, expected_bucket="agent-platform-docs"
    )
    assert bucket == "agent-platform-docs"
    assert key.endswith(f"{digest}-128.json")
    assert parsed_sha == digest
    assert parsed_size == 128

    for bad_ref in (
        None,
        "https://evil.example/input.json",
        f"s3://other-bucket/tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/input/{digest}-128.json",
        f"s3://agent-platform-docs/tenants/{uuid4()}/workspaces/{w_id}/runs/{r_id}/input/{digest}-128.json",
        f"s3://agent-platform-docs/tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/input/../{digest}-128.json",
        f"s3://agent-platform-docs/tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/input/bad.json",
    ):
        bad_job = JobRecord(
            id=job.id,
            tenant_id=t_id,
            workspace_id=w_id,
            job_type=JobType.RUN,
            run_id=r_id,
            payload_ref=bad_ref,
            status=JobStatus.QUEUED,
            available_at=now,
            leased_until=None,
            leased_by=None,
            attempt=0,
            max_attempts=5,
            created_at=now,
        )
        with pytest.raises(JobExecutionError):
            JobWorker.validate_run_payload_ref(bad_job, expected_bucket="agent-platform-docs")
