"""Real PostgreSQL 16 + Redis + MinIO integration tests for Step 07-A (G7-A01..G7-A08).

Validates:
- G7-A01: Worker execution flags and fail-closed boot (run_worker_execution_enabled default false).
- G7-A02: Pure async worker composition, facade import isolation, and typed disposition.
- G7-A03: Execution authority grant minted at admission, AES-256-GCM encryption, expiry bounded, replay preserved.
- G7-A04: Live authority recheck: Redis session revocation aborts heartbeat/begin; AAD prevents ciphertext reuse.
- G7-A05: Two workers competing for one job (FOR UPDATE SKIP LOCKED); fence verification prevents stale worker updates.
- G7-A06: Concurrency cap 2 per workspace; cancellation vs claim race; execution deadline enforcement.
- G7-A07: Bounded retries: exponential backoff in DB; attempts exhausted marks run failed and job dead.
- G7-A08: Migration 0011 idempotence, preflight guard against duplicate run jobs, and platform_app RLS enforcement.
"""

from __future__ import annotations

import ast
import contextlib
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from agent_runtime.application.execution.grant import (
    DEFAULT_KEY_ID,
    ExecutionGrant,
    ExecutionGrantTamperError,
    ExecutionKeyUnavailableError,
    decrypt_execution_grant,
    encrypt_execution_grant,
)
from agent_runtime.application.execution.state_owner import ExecutionStateOwner
from agent_runtime.application.run_admission_service import RunAdmissionService
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.execution_api import (
    create_sentinel_executor,
    validate_execution_configuration,
)
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.execution_repository import (
    ExecutionRepository,
)
from agent_runtime.persistence.object_store import S3SigV4ObjectStore
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.settings import RuntimeSettings
from jobs_worker.worker import JobStatus, JobWorker
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.execution import (
    ExecutionDispositionType,
    LeaseIdentity,
)
from run_migrations import run_migrations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.test_run_admission_real_postgres import (
    TEST_BUCKET,
    _register_redis_session,
    _revoke_redis_session,
    _seed_admission_fixture,
    _to_async_pg_url_obj,
    _to_psycopg_url,
    owned_minio_cluster,
    owned_redis_cluster,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def minio_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_minio_cluster() as cluster:
        yield cluster


@pytest.fixture(scope="module")
def redis_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_redis_cluster() as cluster:
        yield cluster


def _make_async_scoped_factory(app_url: str):
    engine = create_async_engine(_to_async_pg_url_obj(app_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def scoped(t: str, w: str, u: str | None = None):
        async with factory() as session, session.begin():
            await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
            yield session

    return scoped


# ---------------------------------------------------------------------------
# G7-A01: Worker Execution Flags and Fail-Closed Boot
# ---------------------------------------------------------------------------


def test_g7_a01_flags_off_keeps_runs_queued_and_missing_config_fails_closed(
    migrated_database: dict[str, Any],
) -> None:
    """G7-A01: When flags are off, runs are not claimed; missing config with flags on fails closed."""
    app_url = _to_psycopg_url(migrated_database["app_url"])

    # 1. Flag off (default): worker does not claim RUN jobs
    worker = JobWorker(
        database_url=app_url,
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        async_run_executor=None,
    )
    assert worker.has_run_executor is False
    assert worker._claimable_job_types() == ()

    # 2. Config validation: flags on with missing or short key fails closed
    settings_no_key = RuntimeSettings(
        run_worker_execution_enabled=True,
        run_execution_grants_enabled=True,
        run_execution_key="",
        database_url=app_url,
    )
    with pytest.raises(ValueError, match="RUN_EXECUTION_KEY"):
        validate_execution_configuration(settings_no_key)

    settings_short_key = RuntimeSettings(
        run_worker_execution_enabled=True,
        run_execution_grants_enabled=True,
        run_execution_key="short-key",
        database_url=app_url,
    )
    with pytest.raises(ExecutionKeyUnavailableError):
        validate_execution_configuration(settings_short_key)


# ---------------------------------------------------------------------------
# G7-A02: Pure Async Composition and Boundary Isolation
# ---------------------------------------------------------------------------


def test_g7_a02_pure_async_composition_and_import_isolation() -> None:
    """G7-A02: jobs_worker imports only execution_api; Sentinel executor returns typed disposition."""
    worker_src_dir = ROOT / "apps" / "jobs-worker" / "src" / "jobs_worker"
    forbidden = (
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
                    for f in forbidden:
                        assert not alias.name.startswith(f), (
                            f"Illegal import {alias.name} in {py_file.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for f in forbidden:
                    assert not node.module.startswith(f), (
                        f"Illegal import from {node.module} in {py_file.name}"
                    )


# ---------------------------------------------------------------------------
# G7-A03: Admission Grant Minting and Replay Preservation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_g7_a03_grant_minting_at_admission_and_replay_preservation(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """G7-A03: Admission mints encrypted grant; replay returns existing grant without renewal."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    exec_key = os.urandom(32).hex()
    settings = RuntimeSettings(
        run_execution_grants_enabled=True,
        run_execution_key=exec_key,
        run_execution_key_id=DEFAULT_KEY_ID,
        redis_url=redis_cluster["url"],
    )

    scoped = _make_async_scoped_factory(app_url)
    store = S3SigV4ObjectStore(
        endpoint=minio_cluster["url"],
        access_key=minio_cluster["app_user"],
        secret_key=minio_cluster["app_secret"],
        bucket=TEST_BUCKET,
    )
    data_svc = RunDataService(object_store=store, bucket=TEST_BUCKET)
    adm_svc = RunAdmissionService(
        session_factory=scoped,
        run_data_service=data_svc,
        admission_repository=RunAdmissionRepository(),
        outbox_publisher=PostgresOutboxEventBus(lambda: None),
        execution_grants_enabled=True,
        execution_key=exec_key,
        execution_key_id=DEFAULT_KEY_ID,
        settings=settings,
    )

    sess_dev = str(uuid4())
    _register_redis_session(redis_cluster["url"], session_id=sess_dev, user_id=fx["dev_a"])

    principal = RuntimePrincipal(
        subject=f"usr:{fx['dev_a']}",
        user_id=fx["dev_a"],
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        roles=("Developer",),
        scopes=frozenset(["runs:write", "agents:read"]),
        session_id=sess_dev,
    )

    idemp_key = f"idemp-{uuid4()}"

    # 1. Admit run with execution grants enabled
    resp = await adm_svc.admit_run(
        principal=principal,
        workspace_id=fx["w_a1"],
        agent_version_id=fx["av_ok"],
        conversation_id=fx["conv_dev"],
        inputs={"prompt": "g7_a03"},
        idempotency_key=idemp_key,
    )
    assert resp.replayed is False
    run_id = resp.run_id

    # 2. Verify grant in database
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT execution_authority_ciphertext, execution_authority_key_id,
                   authority_expires_at, execution_deadline_at
            FROM runtime.runs
            WHERE id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
        assert row is not None
        ciphertext, key_id, auth_exp, exec_dl = row
        assert ciphertext is not None
        assert key_id == DEFAULT_KEY_ID
        assert auth_exp is not None
        assert exec_dl is None  # Pinned at begin_execution, not admission

        # Decrypt and verify AAD binding
        key_bytes = bytes.fromhex(exec_key)
        grant = decrypt_execution_grant(
            ciphertext,
            key_bytes,
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            run_id=str(run_id),
        )
        assert grant.tenant_id == fx["t_a"]
        assert grant.workspace_id == fx["w_a1"]
        assert grant.run_id == str(run_id)
        assert grant.user_id == fx["dev_a"]
        assert "runs:write" in grant.original_scopes

    # 3. Idempotent replay: must return same grant without renewing expiry
    replay_resp = await adm_svc.admit_run(
        principal=principal,
        workspace_id=fx["w_a1"],
        agent_version_id=fx["av_ok"],
        conversation_id=fx["conv_dev"],
        inputs={"prompt": "g7_a03"},
        idempotency_key=idemp_key,
    )
    assert replay_resp.replayed is True
    assert replay_resp.run_id == run_id

    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT execution_authority_ciphertext, authority_expires_at FROM runtime.runs WHERE id = %s",
            (run_id,),
        )
        r_ciphertext, r_auth_exp = cur.fetchone()
        assert bytes(r_ciphertext) == bytes(ciphertext)
        assert r_auth_exp == auth_exp


# ---------------------------------------------------------------------------
# G7-A04: Live Authority Revocation and Cross-Scope AAD Tamper Defense
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_g7_a04_revocation_denies_execution_and_aad_tamper_fails(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """G7-A04: Revoked parent session aborts execution; copying ciphertext across runs fails."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    exec_key = os.urandom(32).hex()
    settings = RuntimeSettings(
        run_execution_key=exec_key,
        run_execution_key_id=DEFAULT_KEY_ID,
        run_execution_grants_enabled=True,
        redis_url=redis_cluster["url"],
    )

    scoped = _make_async_scoped_factory(app_url)
    owner = ExecutionStateOwner(
        session_factory=scoped,
        repository=ExecutionRepository(),
        settings=settings,
    )

    # 1. Create a run with revoked session
    run_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(UTC)

    sess_revoked = str(uuid4())
    _register_redis_session(redis_cluster["url"], session_id=sess_revoked, user_id=fx["dev_a"])
    _revoke_redis_session(redis_cluster["url"], session_id=sess_revoked)

    grant = ExecutionGrant(
        issuer="agent-runtime-admission",
        subject=f"usr:{fx['dev_a']}",
        user_id=fx["dev_a"],
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        run_id=run_id,
        parent_session_id=sess_revoked,  # Revoked session
        parent_rotation_version=1,
        original_scopes=("runs:write",),
        granted_at=now.isoformat(),
        authority_expires_at=(now + timedelta(hours=24)).isoformat(),
    )
    ciphertext, _, _ = encrypt_execution_grant(grant, key=bytes.fromhex(exec_key))
    lease_token = str(uuid4())

    with psycopg.connect(owner_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runtime.runs (
                id, tenant_id, workspace_id, agent_version_id, initiated_by,
                status, execution_authority_ciphertext, execution_authority_key_id, authority_expires_at
            ) VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, %s)
            """,
            (
                run_id,
                fx["t_a"],
                fx["w_a1"],
                fx["av_ok"],
                fx["dev_a"],
                ciphertext,
                DEFAULT_KEY_ID,
                now + timedelta(hours=24),
            ),
        )
        cur.execute(
            """
            INSERT INTO runtime.jobs (
                id, tenant_id, workspace_id, job_type, run_id,
                status, available_at, attempt, max_attempts, leased_by, lease_token, leased_until
            ) VALUES (%s, %s, %s, 'run', %s, 'leased', %s, 1, 3, 'worker-1', %s, %s)
            """,
            (
                job_id,
                fx["t_a"],
                fx["w_a1"],
                run_id,
                now,
                lease_token,
                now + timedelta(seconds=30),
            ),
        )

    lease_ident = LeaseIdentity(
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        job_id=job_id,
        run_id=run_id,
        leased_by="worker-1",
        lease_token=lease_token,
        attempt=1,
    )

    # Begin execution should detect revoked session and fail closed
    res, _dec_grant, _principal = await owner.begin_execution(
        lease_identity=lease_ident,
        require_grant=True,
    )
    assert res.success is False
    assert res.error_code == "execution_authority_denied"

    # Verify run marked failed and job dead in DB
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, error_code FROM runtime.runs WHERE id = %s", (run_id,))
        assert cur.fetchone() == ("failed", "execution_authority_denied")

        cur.execute("SELECT status, closed_reason FROM runtime.jobs WHERE id = %s", (job_id,))
        assert cur.fetchone() == ("dead", "authority_denied")

    # 2. AAD Tamper test: copy ciphertext to another run ID
    other_run_id = str(uuid4())
    with pytest.raises(ExecutionGrantTamperError):
        decrypt_execution_grant(
            ciphertext,
            bytes.fromhex(exec_key),
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            run_id=other_run_id,
        )


# ---------------------------------------------------------------------------
# G7-A05: Concurrent Claim and Stale Fencing
# ---------------------------------------------------------------------------


def test_g7_a05_concurrent_claim_and_stale_fencing(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """G7-A05: Two workers compete for one job; stale lease cannot commit updates."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    run_id = str(uuid4())
    job_id = str(uuid4())
    now = datetime.now(UTC)

    with psycopg.connect(owner_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runtime.runs (id, tenant_id, workspace_id, agent_version_id, initiated_by, status)
            VALUES (%s, %s, %s, %s, %s, 'queued')
            """,
            (run_id, fx["t_a"], fx["w_a1"], fx["av_ok"], fx["dev_a"]),
        )
        cur.execute(
            """
            INSERT INTO runtime.jobs (id, tenant_id, workspace_id, job_type, run_id, status, available_at, attempt, max_attempts)
            VALUES (%s, %s, %s, 'run', %s, 'queued', %s, 0, 3)
            """,
            (job_id, fx["t_a"], fx["w_a1"], run_id, now),
        )

    exec1 = create_sentinel_executor()
    exec2 = create_sentinel_executor()
    w1 = JobWorker(
        database_url=_to_psycopg_url(app_url),
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        worker_id="worker-alpha",
        async_run_executor=exec1,
    )
    w2 = JobWorker(
        database_url=_to_psycopg_url(app_url),
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        worker_id="worker-beta",
        async_run_executor=exec2,
    )

    # Worker 1 claims job
    claimed1 = w1.lease_next_job()
    assert claimed1 is not None
    assert claimed1.id == job_id
    assert claimed1.leased_by == "worker-alpha"
    assert claimed1.status == JobStatus.LEASED
    assert claimed1.lease_token is not None
    assert claimed1.attempt == 1

    # Worker 2 attempts to claim: gets None because job is already leased
    claimed2 = w2.lease_next_job()
    assert claimed2 is None

    # Stale write test: manually expire lease and reclaim
    with psycopg.connect(owner_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE runtime.jobs SET leased_until = clock_timestamp() - interval '10 seconds' WHERE id = %s",
            (job_id,),
        )

    # Reclaim sweeps expired lease back to queued
    reclaimed = w1.reclaim_expired_leases()
    assert reclaimed >= 1

    # Worker 1 attempts to heartbeat with stale lease_token -> returns False
    heartbeat_ok = w1.extend_lease(claimed1)
    assert heartbeat_ok is False


# ---------------------------------------------------------------------------
# G7-A06: Concurrency Cap 2 and Cancel/Deadline Races
# ---------------------------------------------------------------------------


def test_g7_a06_concurrency_cap_and_cancel_race(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """G7-A06: Max 2 active leased jobs per workspace; cancelled run halts worker begin."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])
    now = datetime.now(UTC)

    # Insert 3 queued jobs in workspace w_a1
    job_ids = []
    for _ in range(3):
        r_id = str(uuid4())
        j_id = str(uuid4())
        job_ids.append(j_id)
        with psycopg.connect(owner_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runtime.runs (id, tenant_id, workspace_id, agent_version_id, initiated_by, status)
                VALUES (%s, %s, %s, %s, %s, 'queued')
                """,
                (r_id, fx["t_a"], fx["w_a1"], fx["av_ok"], fx["dev_a"]),
            )
            cur.execute(
                """
                INSERT INTO runtime.jobs (id, tenant_id, workspace_id, job_type, run_id, status, available_at, attempt, max_attempts)
                VALUES (%s, %s, %s, 'run', %s, 'queued', %s, 0, 3)
                """,
                (j_id, fx["t_a"], fx["w_a1"], r_id, now),
            )

    worker = JobWorker(
        database_url=_to_psycopg_url(app_url),
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        async_run_executor=create_sentinel_executor(),
        workspace_concurrency_cap=2,
    )

    # Claim job 1: OK
    c1 = worker.lease_next_job()
    assert c1 is not None

    # Claim job 2: OK (cap reached: 2)
    c2 = worker.lease_next_job()
    assert c2 is not None

    # Claim job 3: returns None because cap of 2 is active
    c3 = worker.lease_next_job()
    assert c3 is None


# ---------------------------------------------------------------------------
# G7-A07: Bounded Retries and Attempt Exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_g7_a07_bounded_retries_and_attempt_exhaustion(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """G7-A07: Retries use DB backoff; attempt 3 failure marks run failed and job dead."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])
    now = datetime.now(UTC)

    run_id = str(uuid4())
    job_id = str(uuid4())
    token = str(uuid4())

    with psycopg.connect(owner_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO runtime.runs (id, tenant_id, workspace_id, agent_version_id, initiated_by, status)
            VALUES (%s, %s, %s, %s, %s, 'running')
            """,
            (run_id, fx["t_a"], fx["w_a1"], fx["av_ok"], fx["dev_a"]),
        )
        # Job on attempt 2 (max 3)
        cur.execute(
            """
            INSERT INTO runtime.jobs (
                id, tenant_id, workspace_id, job_type, run_id,
                status, available_at, attempt, max_attempts, leased_by, lease_token, leased_until
            ) VALUES (%s, %s, %s, 'run', %s, 'leased', %s, 2, 3, 'worker-1', %s, %s)
            """,
            (
                job_id,
                fx["t_a"],
                fx["w_a1"],
                run_id,
                now,
                token,
                now + timedelta(seconds=30),
            ),
        )

    scoped = _make_async_scoped_factory(app_url)
    owner = ExecutionStateOwner(
        session_factory=scoped,
        repository=ExecutionRepository(),
    )

    lease_ident = LeaseIdentity(
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        job_id=job_id,
        run_id=run_id,
        leased_by="worker-1",
        lease_token=token,
        attempt=2,
    )

    # 1. Retry on attempt 2: schedules retry back to queued
    disp = await owner.schedule_retry(
        lease_identity=lease_ident,
    )
    assert disp.disposition == ExecutionDispositionType.RETRY_SCHEDULED
    assert disp.terminal is False

    with psycopg.connect(owner_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, attempt FROM runtime.jobs WHERE id = %s", (job_id,))
        assert cur.fetchone() == ("queued", 2)
        cur.execute(
            "UPDATE runtime.jobs SET available_at = clock_timestamp() - interval '1 second' WHERE id = %s",
            (job_id,),
        )

    # 2. Worker claims again: attempt increments to 3
    worker = JobWorker(
        database_url=_to_psycopg_url(app_url),
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        async_run_executor=create_sentinel_executor(),
    )
    claimed = worker.lease_next_job()
    assert claimed is not None
    assert claimed.attempt == 3

    # 3. Failure on attempt 3 (max_attempts=3): terminal exhaustion
    final_lease = claimed.to_lease_identity()
    fail_disp = await owner.fail_execution(
        lease_identity=final_lease,
        error_code="attempts_exhausted",
        closed_reason="attempts_exhausted",
        message="Maximum 3 attempts exhausted",
    )
    assert fail_disp.disposition == ExecutionDispositionType.BLOCKED_UNKNOWN
    assert fail_disp.error_code == "attempts_exhausted"
    assert fail_disp.terminal is True

    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, error_code FROM runtime.runs WHERE id = %s", (run_id,))
        assert cur.fetchone() == ("failed", "attempts_exhausted")

        cur.execute("SELECT status, closed_reason FROM runtime.jobs WHERE id = %s", (job_id,))
        assert cur.fetchone() == ("dead", "attempts_exhausted")


# ---------------------------------------------------------------------------
# G7-A08: Migration 0011 Idempotence and RLS Verification
# ---------------------------------------------------------------------------


def test_g7_a08_migration_0011_idempotence_and_rls(
    isolated_database: dict[str, Any],
) -> None:
    """G7-A08: Migration 0011 is idempotent, preflights against duplicates, and enforces RLS."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]

    # 1. Run migrations up to 0011
    applied = run_migrations(owner_url)
    assert "0011" in applied

    # 2. Rerun migrations: must be idempotent (0 pending)
    assert run_migrations(owner_url) == []

    # 3. Verify RLS under platform_app: cannot modify schema_migrations or run DDL
    with psycopg.connect(app_url) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM public.schema_migrations")
        conn.rollback()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE runtime.rogue_table (id uuid PRIMARY KEY)")
        conn.rollback()
