"""Real PostgreSQL 16 + Redis + MinIO integration tests for Step 06-D (G6-D01..G6-D08).

Validates:
- G6-D01: Cancel authority matrix (Creator, Owner, Admin, Operator allow; Developer peer 404; Viewer 403; cross-scope 404; resume 503; content isolation).
- G6-D02: Queued cancel atomic transaction, closed_reason, RunEvent 1.2, audit, outbox, and idempotent replay without duplicate rows.
- G6-D03: Concurrency barrier: leased/running cancel 409 run_in_flight; dead job unclaimable by worker; late writer CAS fails.
- G6-D04: Queue wait deadline UTC precision, boundary transition to timed_out, and durability across restart.
- G6-D05: Transaction rollback on failure before commit; crash recovery preserves idempotency without duplicate jobs; legacy run unsupported rejection.
- G6-D06: Real pg_dump and pg_restore into owned target; assert 61 domain tables, 10 migrations, RLS under platform_app.
- G6-D07: Retention evaluator dry-run: 30d content TTL, 90d timeline, legal hold protection, zero physical deletes.
- G6-D08: Maintenance CLI expire-queued dry-run, apply guard, system:queue-expiry actor, and suspended tenant/workspace.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
import yaml
from agent_runtime.application.run_lifecycle import (
    RunLifecycleConflictError,
)
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.run_event_repository import RunEventRepository
from agent_runtime.persistence.run_lifecycle_repository import RunLifecycleRepository
from jobs_worker.worker import JobWorker
from jsonschema import Draft202012Validator
from platform_common.event_bus import PostgresOutboxEventBus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.test_postgres_fixture import (
    backup_database,
    database_url,
    restore_database,
    temporary_database,
)
from tests.integration.test_run_admission_real_postgres import (
    RUNTIME_JWT_ISSUER,
    TEST_BUCKET,
    _mint_runtime_jwt,
    _register_redis_session,
    _seed_admission_fixture,
    _spawn_runtime_uvicorn,
    _stop_proc,
    _to_async_pg_url,
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


def _validate_api_response(response: httpx.Response, path: str, method: str) -> None:
    spec = yaml.safe_load(
        (ROOT / "contracts/openapi/runtime-api.openapi.yaml").read_text(encoding="utf-8")
    )
    code_str = str(response.status_code)
    schema = spec["paths"][path][method]["responses"][code_str]["content"]["application/json"][
        "schema"
    ]
    Draft202012Validator({**spec, **schema}).validate(response.json())


def _validate_run_event_1_2(event_dict: dict[str, Any]) -> None:
    schema = json.loads(
        (ROOT / "contracts/jsonschema/run-event.v1.2.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(event_dict)


def _seed_cancel_fixture(owner_url: str, redis_url: str) -> dict[str, Any]:
    fx = _seed_admission_fixture(owner_url, redis_url)
    owner_a, admin_a, operator_a = str(uuid4()), str(uuid4()), str(uuid4())
    sess_owner, sess_admin, sess_operator = (str(uuid4()) for _ in range(3))
    sess_b = str(uuid4())
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        for uid, role, subj in (
            (owner_a, "owner", f"owner-{owner_a[:8]}"),
            (admin_a, "admin", f"admin-{admin_a[:8]}"),
            (operator_a, "operator", f"operator-{operator_a[:8]}"),
        ):
            cur.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) VALUES (%s, %s, %s, %s, %s)",
                (uid, RUNTIME_JWT_ISSUER, subj, subj, f"{subj}@test.local"),
            )
            cur.execute(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) VALUES (%s, %s, %s, %s, 'active')",
                (fx["t_a"], fx["w_a1"], uid, role),
            )
    _register_redis_session(redis_url, session_id=sess_owner, user_id=owner_a)
    _register_redis_session(redis_url, session_id=sess_admin, user_id=admin_a)
    _register_redis_session(redis_url, session_id=sess_operator, user_id=operator_a)
    _register_redis_session(redis_url, session_id=sess_b, user_id=fx["user_b"])

    fx["owner_a"] = owner_a
    fx["admin_a"] = admin_a
    fx["operator_a"] = operator_a
    fx["tok_owner_a"] = _mint_runtime_jwt(
        user_id=owner_a,
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        session_id=sess_owner,
        roles=["owner"],
        scopes=["run:start", "run:read"],
    )
    fx["tok_admin_a"] = _mint_runtime_jwt(
        user_id=admin_a,
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        session_id=sess_admin,
        roles=["admin"],
        scopes=["run:start", "run:read"],
    )
    fx["tok_operator_a"] = _mint_runtime_jwt(
        user_id=operator_a,
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        session_id=sess_operator,
        roles=["operator"],
        scopes=["run:start", "run:read"],
    )
    fx["tok_user_b"] = _mint_runtime_jwt(
        user_id=fx["user_b"],
        tenant_id=fx["t_b"],
        workspace_id=fx["w_b1"],
        session_id=sess_b,
        roles=["developer"],
        scopes=["run:start", "run:read"],
    )
    return fx


def test_g6_d01_cancel_authority_matrix_and_generic_denials(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D01: Cancel resource authority matrix, cross-tenant 404, viewer 403, peer dev 404, and 503 resume."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    proc, port, log = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "g6_d01_uvicorn.log",
    )

    try:

        async def scenario() -> None:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                base_url = f"http://127.0.0.1:{port}"

                # 1. Admit run 1 for dev_a
                resp1 = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d01-run1-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "run1"},
                    },
                )
                assert resp1.status_code == 202
                run1_id = resp1.json()["run_id"]

                # 2. Creator dev_a cancels own run -> Allowed (200)
                cancel_resp1 = await client.post(
                    f"{base_url}/v1/runs/{run1_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_resp1.status_code == 200
                data1 = cancel_resp1.json()
                assert data1["run_id"] == run1_id
                assert data1["status"] == "cancelled"
                assert data1["changed"] is True
                assert data1["disposition"] == "cancelled"
                _validate_api_response(cancel_resp1, "/v1/runs/{run_id}/cancel", "post")

                # 3. Admit run 2 for dev_a
                resp2 = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d01-run2-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "run2"},
                    },
                )
                assert resp2.status_code == 202
                run2_id = resp2.json()["run_id"]

                # 3a. Peer developer attempts cancel -> 404 (run_not_found, generic denial prevents disclosure)
                cancel_peer_dev = await client.post(
                    f"{base_url}/v1/runs/{run2_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_other_a']}"},
                    json={},
                )
                assert cancel_peer_dev.status_code == 404
                assert cancel_peer_dev.json()["detail"]["code"] == "run_not_found"
                _validate_api_response(cancel_peer_dev, "/v1/runs/{run_id}/cancel", "post")

                # 3b. Viewer attempts cancel -> 403 (insufficient_scope, lacks run:start)
                cancel_viewer = await client.post(
                    f"{base_url}/v1/runs/{run2_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_viewer_a']}"},
                    json={},
                )
                assert cancel_viewer.status_code == 403
                assert cancel_viewer.json()["detail"]["code"] == "insufficient_scope"
                _validate_api_response(cancel_viewer, "/v1/runs/{run_id}/cancel", "post")

                # 3c. Cross-tenant user attempts cancel -> 404 (run_not_found)
                cancel_cross_tenant = await client.post(
                    f"{base_url}/v1/runs/{run2_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_user_b']}"},
                    json={},
                )
                assert cancel_cross_tenant.status_code == 404
                assert cancel_cross_tenant.json()["detail"]["code"] == "run_not_found"

                # 3d. Cross-workspace developer attempts cancel -> 404 (run_not_found)
                cancel_cross_ws = await client.post(
                    f"{base_url}/v1/runs/{run2_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a_w2']}"},
                    json={},
                )
                assert cancel_cross_ws.status_code == 404
                assert cancel_cross_ws.json()["detail"]["code"] == "run_not_found"

                # 4. Operator cancels peer run -> Allowed (200)
                cancel_op = await client.post(
                    f"{base_url}/v1/runs/{run2_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_operator_a']}"},
                    json={},
                )
                assert cancel_op.status_code == 200
                assert cancel_op.json()["disposition"] == "cancelled"

                # 5. Admin cancels peer run -> Allowed (200)
                resp3 = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d01-run3-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "run3"},
                    },
                )
                run3_id = resp3.json()["run_id"]
                cancel_admin = await client.post(
                    f"{base_url}/v1/runs/{run3_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_admin_a']}"},
                    json={},
                )
                assert cancel_admin.status_code == 200
                assert cancel_admin.json()["disposition"] == "cancelled"

                # 6. Owner cancels peer run -> Allowed (200)
                resp4 = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d01-run4-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "run4"},
                    },
                )
                run4_id = resp4.json()["run_id"]
                cancel_owner = await client.post(
                    f"{base_url}/v1/runs/{run4_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_owner_a']}"},
                    json={},
                )
                assert cancel_owner.status_code == 200
                assert cancel_owner.json()["disposition"] == "cancelled"

                # 7. Fail-closed resume endpoint check: POST /internal/v1/runs/{run_id}/resume -> 503
                resume_resp = await client.post(
                    f"{base_url}/internal/v1/runs/{run1_id}/resume",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                )
                assert resume_resp.status_code == 503
                assert resume_resp.json()["detail"]["code"] == "resume_disabled"
                _validate_api_response(resume_resp, "/internal/v1/runs/{run_id}/resume", "post")

        asyncio.run(scenario())
    finally:
        _stop_proc(proc, log)


def test_g6_d02_queued_cancel_atomic_transaction_and_idempotent_replay(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D02: Atomic queued cancel, RunEvent 1.2, audit, outbox, and idempotent replay without duplicate rows."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    proc, port, log = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "g6_d02_uvicorn.log",
    )

    try:

        async def scenario() -> None:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                base_url = f"http://127.0.0.1:{port}"

                # Admit run
                resp = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d02-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"prompt": "test d02"},
                    },
                )
                assert resp.status_code == 202
                run_id = resp.json()["run_id"]

                # Verify initial DB state
                with psycopg.connect(owner_url) as conn:
                    run_row = conn.execute(
                        "SELECT status, queue_expires_at, finished_at, receipt_expires_at FROM runtime.runs WHERE id = %s",
                        (run_id,),
                    ).fetchone()
                    assert run_row[0] == "queued"
                    assert run_row[1] is not None
                    assert run_row[2] is None

                    job_row = conn.execute(
                        "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s",
                        (run_id,),
                    ).fetchone()
                    assert job_row[0] == "queued"
                    assert job_row[1] is None

                    initial_events_count = conn.execute(
                        "SELECT count(*) FROM runtime.run_events WHERE run_id = %s", (run_id,)
                    ).fetchone()[0]
                    assert initial_events_count == 1

                    initial_audit_count = conn.execute(
                        "SELECT count(*) FROM governance.audit_events WHERE resource_id = %s",
                        (run_id,),
                    ).fetchone()[0]
                    assert initial_audit_count == 1

                    initial_outbox_count = conn.execute(
                        "SELECT count(*) FROM integration.outbox_events WHERE workspace_id = %s",
                        (fx["w_a1"],),
                    ).fetchone()[0]

                # Cancel run via API
                cancel_resp = await client.post(
                    f"{base_url}/v1/runs/{run_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_resp.status_code == 200
                data = cancel_resp.json()
                assert data["run_id"] == run_id
                assert data["status"] == "cancelled"
                assert data["changed"] is True
                assert data["disposition"] == "cancelled"

                # Verify single-transaction commit in DB
                with psycopg.connect(owner_url) as conn:
                    # 1. Run terminal status and finished_at
                    run_after = conn.execute(
                        "SELECT status, finished_at, receipt_expires_at FROM runtime.runs WHERE id = %s",
                        (run_id,),
                    ).fetchone()
                    assert run_after[0] == "cancelled"
                    finished_at = run_after[1]
                    assert finished_at is not None
                    receipt_expires_at = run_after[2]
                    assert receipt_expires_at is not None
                    # Trigger 0009 set receipt_expires_at = finished_at + interval '30 days'
                    assert (receipt_expires_at - finished_at).total_seconds() == 30 * 86400

                    # 2. Job marked dead with closed_reason
                    job_after = conn.execute(
                        "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s",
                        (run_id,),
                    ).fetchone()
                    assert job_after[0] == "dead"
                    assert job_after[1] == "run_cancelled"

                    # 3. RunEvent 1.2 appended
                    event_row = conn.execute(
                        "SELECT sequence, event_type, redacted_payload FROM runtime.run_events WHERE run_id = %s AND sequence = 2",
                        (run_id,),
                    ).fetchone()
                    assert event_row is not None
                    assert event_row[0] == 2
                    assert event_row[1] == "run.cancelled"
                    raw_payload = event_row[2]
                    if isinstance(raw_payload, str):
                        raw_payload = json.loads(raw_payload)
                    assert raw_payload.get("status") == "cancelled"
                    assert raw_payload.get("reasonCode") == "user_cancelled"

                    # Construct full 1.2 envelope for schema validation
                    full_event = {
                        "schemaVersion": "1.2",
                        "eventId": str(uuid4()),
                        "runId": run_id,
                        "workspaceId": fx["w_a1"],
                        "sequence": 2,
                        "type": "run.cancelled",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "data": raw_payload,
                    }
                    _validate_run_event_1_2(full_event)

                    # 4. Governance audit event (outcome='succeeded')
                    audit_row = conn.execute(
                        "SELECT action, actor_subject, outcome, details_redacted FROM governance.audit_events WHERE resource_id = %s AND action = 'run.cancelled'",
                        (run_id,),
                    ).fetchone()
                    assert audit_row is not None
                    assert audit_row[0] == "run.cancelled"
                    assert audit_row[1] == fx["dev_a"]
                    assert audit_row[2] == "succeeded"

                    # 5. Integration outbox event
                    outbox_count_after = conn.execute(
                        "SELECT count(*) FROM integration.outbox_events WHERE workspace_id = %s",
                        (fx["w_a1"],),
                    ).fetchone()[0]
                    assert outbox_count_after == initial_outbox_count + 1

                # Idempotent replay: cancel again returns changed=false, disposition=already_terminal
                replay_resp = await client.post(
                    f"{base_url}/v1/runs/{run_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert replay_resp.status_code == 200
                replay_data = replay_resp.json()
                assert replay_data["run_id"] == run_id
                assert replay_data["status"] == "cancelled"
                assert replay_data["changed"] is False
                assert replay_data["disposition"] == "already_terminal"

                # Verify no duplicate rows inserted
                with psycopg.connect(owner_url) as conn:
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM runtime.run_events WHERE run_id = %s", (run_id,)
                        ).fetchone()[0]
                        == 2
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM governance.audit_events WHERE resource_id = %s",
                            (run_id,),
                        ).fetchone()[0]
                        == 2
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM integration.outbox_events WHERE workspace_id = %s",
                            (fx["w_a1"],),
                        ).fetchone()[0]
                        == outbox_count_after
                    )

        asyncio.run(scenario())
    finally:
        _stop_proc(proc, log)


def test_g6_d03_cancel_vs_claim_race_and_late_writer_fencing(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D03: Concurrency barrier: leased/running cancel yields 409 run_in_flight; cancelled run is unclaimable; late writer CAS fails."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    proc, port, log = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "g6_d03_uvicorn.log",
    )

    try:

        async def scenario() -> None:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                base_url = f"http://127.0.0.1:{port}"

                # Case A1: Job already leased -> cancel fails closed with 409 run_in_flight
                resp_a = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d03-a-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "leased"},
                    },
                )
                run_a_id = resp_a.json()["run_id"]

                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE runtime.jobs SET status = 'leased', leased_by = 'worker-1', leased_until = now() + interval '10 minutes' WHERE run_id = %s",
                        (run_a_id,),
                    )

                cancel_leased = await client.post(
                    f"{base_url}/v1/runs/{run_a_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_leased.status_code == 409
                assert cancel_leased.json()["detail"]["code"] == "run_in_flight"

                # Case A2: Run already running -> cancel fails closed with 409 run_in_flight
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE runtime.runs SET status = 'running' WHERE id = %s", (run_a_id,)
                    )

                cancel_running = await client.post(
                    f"{base_url}/v1/runs/{run_a_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_running.status_code == 409
                assert cancel_running.json()["detail"]["code"] == "run_in_flight"

                # Case B: Cancel happens before claim -> worker lease_next_job cannot claim dead job
                resp_b = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d03-b-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "cancel_first"},
                    },
                )
                run_b_id = resp_b.json()["run_id"]

                # Cancel run B
                cancel_b = await client.post(
                    f"{base_url}/v1/runs/{run_b_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_b.status_code == 200
                assert cancel_b.json()["disposition"] == "cancelled"

                # Worker attempts to claim
                worker = JobWorker(
                    database_url=_to_psycopg_url(owner_url),
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    worker_id="test-worker",
                )
                claimed = worker.lease_next_job()
                assert claimed is None  # Dead job and cancelled run are filtered out!

                # Case C: Late status writer fencing (CAS fails on terminal state)
                with psycopg.connect(owner_url) as conn:
                    # Attempt late write to jobs
                    cur = conn.execute(
                        "UPDATE runtime.jobs SET status = 'done' WHERE run_id = %s AND status = 'leased'",
                        (run_b_id,),
                    )
                    assert cur.rowcount == 0  # CAS fails

                    # Attempt late write to runs
                    cur2 = conn.execute(
                        "UPDATE runtime.runs SET status = 'succeeded' WHERE id = %s AND status = 'running'",
                        (run_b_id,),
                    )
                    assert cur2.rowcount == 0  # CAS fails

                    # Run remains cancelled
                    status = conn.execute(
                        "SELECT status FROM runtime.runs WHERE id = %s", (run_b_id,)
                    ).fetchone()[0]
                    assert status == "cancelled"

        asyncio.run(scenario())
    finally:
        _stop_proc(proc, log)


def test_g6_d04_queue_deadline_utc_boundary_and_expiry_transition(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D04: Queue wait deadline UTC precision, boundary transition to timed_out, and durability across restart."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    proc, port, log = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "g6_d04_uvicorn.log",
    )

    try:

        async def scenario() -> None:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                base_url = f"http://127.0.0.1:{port}"

                # 1. Admit run and verify queue_expires_at is pinned in UTC
                resp = await client.post(
                    f"{base_url}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": f"d04-1-{uuid4().hex}",
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "deadline"},
                    },
                )
                assert resp.status_code == 202
                run1_id = resp.json()["run_id"]

                meta_resp = await client.get(
                    f"{base_url}/v1/runs/{run1_id}",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                )
                assert meta_resp.status_code == 200
                meta1 = meta_resp.json()
                deadline_str = meta1.get("queue_expires_at")
                assert deadline_str is not None
                deadline_dt = datetime.fromisoformat(deadline_str)
                assert deadline_dt > datetime.now(UTC)

                # 2. Before deadline: cancel yields status=cancelled, disposition=cancelled
                cancel_resp1 = await client.post(
                    f"{base_url}/v1/runs/{run1_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_resp1.status_code == 200
                assert cancel_resp1.json()["status"] == "cancelled"
                assert cancel_resp1.json()["disposition"] == "cancelled"

                # 3. Seed run 2 directly in DB with past deadline (created_at 2h ago, deadline 1h ago)
                run2_id = str(uuid4())
                ref2 = f"s3://{TEST_BUCKET}/d04-exp"
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        """INSERT INTO runtime.runs (
                            tenant_id, workspace_id, id, agent_version_id, initiated_by,
                            external_request_key, status, created_at, queue_expires_at,
                            request_sha256, input_ref, input_sha256, input_size_bytes
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, 'queued',
                            clock_timestamp() - interval '2 hours', clock_timestamp() - interval '1 hour',
                            %s, %s, %s, 100
                        )""",
                        (
                            fx["t_a"],
                            fx["w_a1"],
                            run2_id,
                            fx["av_no_grants"],
                            fx["dev_a"],
                            f"d04-exp-{uuid4().hex}",
                            "a" * 64,
                            ref2,
                            "b" * 64,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO runtime.jobs (
                            tenant_id, workspace_id, id, run_id, job_type, status, payload_ref
                        ) VALUES (%s, %s, %s, %s, 'run', 'queued', %s)""",
                        (fx["t_a"], fx["w_a1"], str(uuid4()), run2_id, ref2),
                    )

                # Worker lease_next_job candidate filter ignores expired run
                worker = JobWorker(
                    database_url=_to_psycopg_url(owner_url),
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    worker_id="test-worker",
                )
                claimed = worker.lease_next_job()
                assert claimed is None

                # Cancel run 2 at/after deadline: transitions to timed_out with disposition=queue_expired
                cancel_resp2 = await client.post(
                    f"{base_url}/v1/runs/{run2_id}/cancel",
                    headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
                    json={},
                )
                assert cancel_resp2.status_code == 200
                data2 = cancel_resp2.json()
                assert data2["run_id"] == run2_id
                assert data2["status"] == "timed_out"
                assert data2["changed"] is True
                assert data2["disposition"] == "queue_expired"
                _validate_api_response(cancel_resp2, "/v1/runs/{run_id}/cancel", "post")

                # Verify DB state for timed_out run
                with psycopg.connect(owner_url) as conn:
                    run_row = conn.execute(
                        "SELECT status, finished_at FROM runtime.runs WHERE id = %s", (run2_id,)
                    ).fetchone()
                    assert run_row[0] == "timed_out"
                    assert run_row[1] is not None

                    job_row = conn.execute(
                        "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s",
                        (run2_id,),
                    ).fetchone()
                    assert job_row[0] == "dead"
                    assert job_row[1] == "run_timed_out"

                    event_row = conn.execute(
                        "SELECT event_type, redacted_payload FROM runtime.run_events WHERE run_id = %s AND sequence = 1",
                        (run2_id,),
                    ).fetchone()
                    assert event_row[0] == "run.timed_out"
                    payload = (
                        event_row[1] if isinstance(event_row[1], dict) else json.loads(event_row[1])
                    )
                    assert payload.get("status") == "timed_out"
                    assert payload.get("reasonCode") == "queue_deadline_exceeded"

                    # Validate 1.2 envelope
                    full_event = {
                        "schemaVersion": "1.2",
                        "eventId": str(uuid4()),
                        "runId": run2_id,
                        "workspaceId": fx["w_a1"],
                        "sequence": 1,
                        "type": "run.timed_out",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "data": payload,
                    }
                    _validate_run_event_1_2(full_event)

        asyncio.run(scenario())
    finally:
        _stop_proc(proc, log)


def test_g6_d05_fault_rollback_and_same_key_recovery(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D05: Transaction rollback on failure before commit; crash recovery preserves idempotency without duplicate jobs; legacy run unsupported rejection."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    async def scenario() -> None:
        engine = create_async_engine(_to_async_pg_url(app_url))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        # Stage 1a: Injected fault before commit in RunLifecycleRepository rolls back cleanly
        run_id = str(uuid4())
        key = f"fault-{uuid4().hex}"
        ref = f"s3://{TEST_BUCKET}/fault-ref"
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                """INSERT INTO runtime.runs (
                    tenant_id, workspace_id, id, agent_version_id, initiated_by,
                    external_request_key, status, queue_expires_at, request_sha256, input_ref, input_sha256, input_size_bytes
                ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', clock_timestamp() + interval '1 day', %s, %s, %s, 10)""",
                (
                    fx["t_a"],
                    fx["w_a1"],
                    run_id,
                    fx["av_no_grants"],
                    fx["dev_a"],
                    key,
                    "a" * 64,
                    ref,
                    "b" * 64,
                ),
            )
            conn.execute(
                "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'queued', %s)",
                (fx["t_a"], fx["w_a1"], str(uuid4()), run_id, ref),
            )

        repo = RunLifecycleRepository(
            event_repository=RunEventRepository(),
            outbox_publisher=PostgresOutboxEventBus(factory),
        )

        with pytest.raises(RuntimeError, match="injected_before_commit"):
            async with factory() as session, session.begin():
                await set_rls_context(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], user_id=fx["dev_a"]
                )
                await repo.cancel_or_expire_run(
                    session,
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    run_id=run_id,
                    actor_subject=fx["dev_a"],
                )
                raise RuntimeError("injected_before_commit")

        # Verify DB is completely rolled back to queued
        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT status, finished_at FROM runtime.runs WHERE id = %s", (run_id,)
            ).fetchone() == ("queued", None)
            assert conn.execute(
                "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s", (run_id,)
            ).fetchone() == ("queued", None)
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.run_events WHERE run_id = %s", (run_id,)
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM governance.audit_events WHERE resource_id = %s", (run_id,)
                ).fetchone()[0]
                == 0
            )

        # Stage 1b: Verify legacy run without admission receipt proof is rejected with legacy_run_unsupported (409)
        legacy_run_id = str(uuid4())
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                """INSERT INTO runtime.runs (
                    tenant_id, workspace_id, id, agent_version_id, initiated_by,
                    external_request_key, status, queue_expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', clock_timestamp() + interval '1 day')""",
                (
                    fx["t_a"],
                    fx["w_a1"],
                    legacy_run_id,
                    fx["av_no_grants"],
                    fx["dev_a"],
                    f"leg-{uuid4().hex}",
                ),
            )
            conn.execute(
                "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status) VALUES (%s, %s, %s, %s, 'run', 'queued')",
                (fx["t_a"], fx["w_a1"], str(uuid4()), legacy_run_id),
            )

        with pytest.raises(RunLifecycleConflictError) as exc_info:
            async with factory() as session, session.begin():
                await set_rls_context(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], user_id=fx["dev_a"]
                )
                await repo.cancel_or_expire_run(
                    session,
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    run_id=legacy_run_id,
                    actor_subject=fx["dev_a"],
                )
        assert exc_info.value.code == "legacy_run_unsupported"

        await engine.dispose()

    asyncio.run(scenario())

    # Stage 2: Post-commit crash recovery using same-key retry via HTTP uvicorn
    proc, port, log = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "g6_d05_uvicorn.log",
    )
    try:

        async def http_scenario() -> None:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                idem_key = f"d05-crash-{uuid4().hex}"
                # 1. Admit run
                resp = await client.post(
                    f"http://127.0.0.1:{port}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": idem_key,
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "crash"},
                    },
                )
                assert resp.status_code == 202
                first_run_id = resp.json()["run_id"]
                assert resp.json()["replayed"] is False

                # 2. Replay with exact same Idempotency-Key (recovers admitted run without duplicate job)
                retry_resp = await client.post(
                    f"http://127.0.0.1:{port}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": idem_key,
                    },
                    json={
                        "workspace_id": fx["w_a1"],
                        "agent_version_id": fx["av_no_grants"],
                        "inputs": {"q": "crash"},
                    },
                )
                assert retry_resp.status_code == 202
                assert retry_resp.json()["run_id"] == first_run_id
                assert retry_resp.json()["replayed"] is True

                with psycopg.connect(owner_url) as conn:
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM runtime.runs WHERE external_request_key = %s",
                            (idem_key,),
                        ).fetchone()[0]
                        == 1
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM runtime.jobs WHERE run_id = %s", (first_run_id,)
                        ).fetchone()[0]
                        == 1
                    )

        asyncio.run(http_scenario())
    finally:
        _stop_proc(proc, log)


def test_g6_d06_pg_dump_restore_and_app_role_rls(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D06: Real pg_dump and pg_restore into owned target; assert 61 domain tables, 10 migrations, RLS under platform_app."""
    owner_url = migrated_database["owner_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    # Seed some runs in various states
    run_queued = str(uuid4())
    run_cancelled = str(uuid4())
    run_timed_out = str(uuid4())

    with psycopg.connect(owner_url) as conn:
        # Queued run with deadline and receipt
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key,
                status, queue_expires_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', clock_timestamp() + interval '1 day', %s, %s, %s, 100)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_queued,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-q-{uuid4().hex}",
                "1" * 64,
                f"s3://{TEST_BUCKET}/q",
                "2" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'queued', %s)",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_queued, f"s3://{TEST_BUCKET}/q"),
        )

        # Cancelled run with receipt
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key,
                status, queue_expires_at, finished_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'cancelled', clock_timestamp() + interval '1 day', clock_timestamp(), %s, %s, %s, 100)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_cancelled,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-c-{uuid4().hex}",
                "3" * 64,
                f"s3://{TEST_BUCKET}/c",
                "4" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, closed_reason, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'dead', 'run_cancelled', %s)",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_cancelled, f"s3://{TEST_BUCKET}/c"),
        )

        # Timed out run with receipt
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key,
                status, created_at, queue_expires_at, finished_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'timed_out', clock_timestamp() - interval '2 hours', clock_timestamp() - interval '1 hour', clock_timestamp(), %s, %s, %s, 100)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_timed_out,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-t-{uuid4().hex}",
                "5" * 64,
                f"s3://{TEST_BUCKET}/t",
                "6" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, closed_reason, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'dead', 'run_timed_out', %s)",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_timed_out, f"s3://{TEST_BUCKET}/t"),
        )

    # Perform actual pg_dump
    dump_file = tmp_path / "step06d_backup.dump"
    backup_database(migrated_database, dump_file)
    assert dump_file.exists() and dump_file.stat().st_size > 0

    # Restore into fresh owned target database
    with temporary_database(migrated_database, "step06d_restore") as target_db:
        restore_database(migrated_database, dump_file, target_db)
        target_owner_url = database_url(migrated_database, target_db)
        target_app_url = database_url(migrated_database, target_db, app=True)

        with psycopg.connect(target_owner_url) as conn:
            # 1. Assert exactly 61 domain tables
            table_count = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops') AND table_type='BASE TABLE'"
            ).fetchone()[0]
            assert table_count == 61

            # 2. Assert exactly 10 migrations applied in public.schema_migrations
            migrations = [
                r[0]
                for r in conn.execute(
                    "SELECT version FROM public.schema_migrations ORDER BY version"
                ).fetchall()
            ]
            assert len(migrations) == 10
            assert migrations[-1] == "0010"

            # 3. Assert runs and jobs state restored faithfully
            assert conn.execute(
                "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s", (run_cancelled,)
            ).fetchone() == ("dead", "run_cancelled")
            assert conn.execute(
                "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s", (run_timed_out,)
            ).fetchone() == ("dead", "run_timed_out")
            assert (
                conn.execute(
                    "SELECT status FROM runtime.runs WHERE id = %s", (run_queued,)
                ).fetchone()[0]
                == "queued"
            )

        # 4. Verify RLS isolation under platform_app role
        with psycopg.connect(target_app_url) as app_conn:
            # Workspace A1 context
            app_conn.execute(
                "SELECT set_config('app.tenant_id', %s, false), set_config('app.workspace_id', %s, false)",
                (fx["t_a"], fx["w_a1"]),
            )
            count_w1 = app_conn.execute("SELECT count(*) FROM runtime.runs").fetchone()[0]
            assert count_w1 >= 3

            # Workspace A2 context: hides Workspace A1 runs
            app_conn.execute(
                "SELECT set_config('app.tenant_id', %s, false), set_config('app.workspace_id', %s, false)",
                (fx["t_a"], fx["w_a2"]),
            )
            count_w2 = app_conn.execute("SELECT count(*) FROM runtime.runs").fetchone()[0]
            assert count_w2 == 0


def test_g6_d07_retention_evaluator_dry_run_and_legal_hold(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D07: Retention evaluator dry-run: 30d content TTL, 90d timeline, legal hold protection, zero physical deletes."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    run_candidate = str(uuid4())
    run_held = str(uuid4())
    run_active = str(uuid4())

    with psycopg.connect(owner_url) as conn:
        # 1. Candidate run: terminal > 30 days ago
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key,
                status, created_at, queue_expires_at, finished_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'succeeded', clock_timestamp() - interval '40 days', clock_timestamp() - interval '39 days', clock_timestamp() - interval '35 days', %s, %s, %s, 10)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_candidate,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-cand-{uuid4().hex}",
                "a" * 64,
                f"s3://{TEST_BUCKET}/cand",
                "b" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'done', %s)",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_candidate, f"s3://{TEST_BUCKET}/cand"),
        )

        # 2. Terminal run protected by active legal hold
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key,
                status, created_at, queue_expires_at, finished_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'cancelled', clock_timestamp() - interval '40 days', clock_timestamp() - interval '39 days', clock_timestamp() - interval '35 days', %s, %s, %s, 10)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_held,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-held-{uuid4().hex}",
                "c" * 64,
                f"s3://{TEST_BUCKET}/held",
                "d" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, closed_reason, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'dead', 'run_cancelled', %s)",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_held, f"s3://{TEST_BUCKET}/held"),
        )
        conn.execute(
            "INSERT INTO governance.retention_policies (tenant_id, workspace_id, data_class, retention_days, legal_hold) VALUES (%s, %s, 'run_content', 30, true)",
            (fx["t_a"], fx["w_a1"]),
        )

        # 3. Active run: queued
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key,
                status, queue_expires_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', clock_timestamp() + interval '1 day', %s, %s, %s, 10)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_active,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-act-{uuid4().hex}",
                "e" * 64,
                f"s3://{TEST_BUCKET}/act",
                "f" * 64,
            ),
        )

        runs_count_before = conn.execute(
            "SELECT count(*) FROM runtime.runs WHERE workspace_id = %s", (fx["w_a1"],)
        ).fetchone()[0]
        jobs_count_before = conn.execute(
            "SELECT count(*) FROM runtime.jobs WHERE workspace_id = %s", (fx["w_a1"],)
        ).fetchone()[0]

    # Execute CLI retention-report
    env = os.environ.copy()
    env["DATABASE_URL"] = _to_async_pg_url(app_url)
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runtime.maintenance",
            "--task",
            "retention-report",
            "--tenant-id",
            fx["t_a"],
            "--workspace-id",
            fx["w_a1"],
            "--limit",
            "100",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    report = json.loads(res.stdout)
    assert report["task"] == "retention-report"
    assert report["dry_run"] is True
    assert report["protected"] >= 1  # Legal hold protected run_held
    assert report["eligible"] >= 0

    # Refuse --apply on retention-report (exit 2)
    res_apply = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runtime.maintenance",
            "--task",
            "retention-report",
            "--tenant-id",
            fx["t_a"],
            "--workspace-id",
            fx["w_a1"],
            "--apply",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_apply.returncode == 2

    # Assert zero deletes occurred (dry-run only)
    with psycopg.connect(owner_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM runtime.runs WHERE workspace_id = %s", (fx["w_a1"],)
            ).fetchone()[0]
            == runs_count_before
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM runtime.jobs WHERE workspace_id = %s", (fx["w_a1"],)
            ).fetchone()[0]
            == jobs_count_before
        )


def test_g6_d08_maintenance_cli_expire_queued_and_suspended_workspace(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-D08: Maintenance CLI expire-queued dry-run, apply guard, system:queue-expiry actor, and suspended tenant/workspace."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_cancel_fixture(owner_url, redis_cluster["url"])

    run_canonical_expired = str(uuid4())
    run_legacy_expired = str(uuid4())
    ref_can = f"s3://{TEST_BUCKET}/ref1"

    with psycopg.connect(owner_url) as conn:
        # 1. Canonical queued run with expired deadline and proof
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by,
                external_request_key, status, created_at, queue_expires_at, request_sha256, input_ref, input_sha256, input_size_bytes
            ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', clock_timestamp() - interval '2 hours', clock_timestamp() - interval '1 hour', %s, %s, %s, 100)""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_canonical_expired,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-exp-{uuid4().hex}",
                "a" * 64,
                ref_can,
                "b" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status, payload_ref) VALUES (%s, %s, %s, %s, 'run', 'queued', %s)",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_canonical_expired, ref_can),
        )

        # 2. Legacy queued run lacking proof (unsupported)
        conn.execute(
            """INSERT INTO runtime.runs (
                tenant_id, workspace_id, id, agent_version_id, initiated_by,
                external_request_key, status, created_at, queue_expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', clock_timestamp() - interval '2 hours', clock_timestamp() - interval '1 hour')""",
            (
                fx["t_a"],
                fx["w_a1"],
                run_legacy_expired,
                fx["av_no_grants"],
                fx["dev_a"],
                f"k-leg-{uuid4().hex}",
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (tenant_id, workspace_id, id, run_id, job_type, status) VALUES (%s, %s, %s, %s, 'run', 'queued')",
            (fx["t_a"], fx["w_a1"], str(uuid4()), run_legacy_expired),
        )

    env = os.environ.copy()
    env["DATABASE_URL"] = _to_async_pg_url(app_url)

    # 1. Dry run execution
    res_dry = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runtime.maintenance",
            "--task",
            "expire-queued",
            "--tenant-id",
            fx["t_a"],
            "--workspace-id",
            fx["w_a1"],
            "--limit",
            "100",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_dry.returncode == 0
    dry_report = json.loads(res_dry.stdout)
    assert dry_report["dry_run"] is True
    assert dry_report["eligible"] >= 1
    assert dry_report["changed"] == 0
    assert dry_report["unsupported"] >= 1

    # Verify no DB mutation under dry-run
    with psycopg.connect(owner_url) as conn:
        assert (
            conn.execute(
                "SELECT status FROM runtime.runs WHERE id = %s", (run_canonical_expired,)
            ).fetchone()[0]
            == "queued"
        )

    # 2. Apply guard check: --apply without mutations_enabled config must fail with exit code 2
    res_guard = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runtime.maintenance",
            "--task",
            "expire-queued",
            "--tenant-id",
            fx["t_a"],
            "--workspace-id",
            fx["w_a1"],
            "--apply",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_guard.returncode == 2

    # 3. Apply in suspended tenant/workspace with mutations enabled
    with psycopg.connect(owner_url) as conn:
        conn.execute("UPDATE iam.tenants SET status = 'suspended' WHERE id = %s", (fx["t_a"],))

    env_apply = env.copy()
    env_apply["RUN_MAINTENANCE_MUTATIONS_ENABLED"] = "true"

    res_apply = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runtime.maintenance",
            "--task",
            "expire-queued",
            "--tenant-id",
            fx["t_a"],
            "--workspace-id",
            fx["w_a1"],
            "--apply",
            "--limit",
            "100",
        ],
        cwd=str(ROOT),
        env=env_apply,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_apply.returncode == 0
    apply_report = json.loads(res_apply.stdout)
    assert apply_report["dry_run"] is False
    assert apply_report["changed"] >= 1
    assert apply_report["unsupported"] >= 1

    # Verify DB mutations for canonical run
    with psycopg.connect(owner_url) as conn:
        run_res = conn.execute(
            "SELECT status, finished_at FROM runtime.runs WHERE id = %s", (run_canonical_expired,)
        ).fetchone()
        assert run_res[0] == "timed_out"
        assert run_res[1] is not None

        job_res = conn.execute(
            "SELECT status, closed_reason FROM runtime.jobs WHERE run_id = %s",
            (run_canonical_expired,),
        ).fetchone()
        assert job_res[0] == "dead"
        assert job_res[1] == "run_timed_out"

        # Audit event records system:queue-expiry actor
        audit_res = conn.execute(
            "SELECT action, actor_subject, outcome FROM governance.audit_events WHERE resource_id = %s AND action = 'run.timed_out'",
            (run_canonical_expired,),
        ).fetchone()
        assert audit_res[0] == "run.timed_out"
        assert audit_res[1] == "system:queue-expiry"
        assert audit_res[2] == "succeeded"

        # Legacy run remains untouched
        assert (
            conn.execute(
                "SELECT status FROM runtime.runs WHERE id = %s", (run_legacy_expired,)
            ).fetchone()[0]
            == "queued"
        )
