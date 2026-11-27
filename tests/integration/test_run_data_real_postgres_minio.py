"""Real PostgreSQL 16 + MinIO integration tests for Step 06-A (G6-A01..G6-A06).

Uses owned Docker PostgreSQL (app_role platform_app with RLS) and owned Docker MinIO
container/volume, separate OS subprocesses for Process A / Process B, PG/MinIO restart,
and PG backup/restore. Zero mocks for DB or object store.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import subprocess
import sys
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TESTS_INTEGRATION_DIR = Path(__file__).resolve().parent
if str(TESTS_INTEGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_INTEGRATION_DIR))

from agent_runtime.application.run_data import (
    RunDataAccessDeniedError,
    RunDataConflictError,
    RunDataIntegrityError,
    RunDataNotFoundError,
    RunDataRetentionError,
    RunDataValidationError,
)
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.object_store import S3SigV4ObjectStore
from agent_runtime.persistence.run_data_repository import RunDataRepository
from test_postgres_fixture import (
    LABEL,
    backup_database,
    database_url,
    docker,
    restart_cluster,
    restore_database,
    temporary_database,
    verify_owned,
)
from verify_local_stack import s3_sigv4_headers

pytestmark = pytest.mark.integration

MINIO_IMAGE = "enterprise-agent-platform-minio:2025-10-15"
TEST_BUCKET = "step06a-run-data"
SUBPROCESS_HELPER = TESTS_INTEGRATION_DIR / "run_data_subprocess_helper.py"


@contextlib.contextmanager
def owned_minio_cluster() -> Generator[dict[str, Any], None, None]:
    """Provision an isolated, labelled MinIO container and volume for 06-A tests."""
    try:
        docker("info")
    except RuntimeError as exc:
        pytest.skip(f"Docker daemon unavailable for MinIO fixture: {exc}")

    run_id = uuid4().hex
    root_secret = secrets.token_hex(24)
    app_secret = secrets.token_hex(24)
    resource: dict[str, Any] = {
        "run_id": run_id,
        "volume": f"step06a-minio-{run_id}",
        "root_user": "fixture_root",
        "root_secret": root_secret,
        "app_user": "fixture_app",
        "app_secret": app_secret,
        "port": 9000,
    }
    docker("volume", "create", "--label", f"{LABEL}={run_id}", resource["volume"])

    def _start() -> None:
        resource["container_id"] = docker(
            "create",
            "--label",
            f"{LABEL}={run_id}",
            "--publish",
            "127.0.0.1::9000",
            "--mount",
            f"type=volume,source={resource['volume']},target=/data",
            "--env",
            "MINIO_ROOT_USER=fixture_root",
            "--env",
            f"MINIO_ROOT_PASSWORD={root_secret}",
            MINIO_IMAGE,
            "server",
            "/data",
        )
        verify_owned(resource)
        docker("start", resource["container_id"])
        ports = json.loads(
            docker(
                "inspect", "--format", "{{json .NetworkSettings.Ports}}", resource["container_id"]
            )
        )
        binding = ports["9000/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        url = f"http://127.0.0.1:{binding['HostPort']}"
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + "/minio/health/ready", timeout=2).status_code == 200:
                    resource["url"] = url
                    return
            except httpx.TransportError:
                pass
            Event().wait(0.3)
        raise AssertionError("Owned MinIO fixture did not become ready")

    resource["start"] = _start
    try:
        _start()
        cid = resource["container_id"]
        docker(
            "exec",
            cid,
            "mc",
            "alias",
            "set",
            "fixture",
            "http://127.0.0.1:9000",
            "fixture_root",
            root_secret,
        )
        docker("exec", cid, "mc", "admin", "user", "add", "fixture", "fixture_app", app_secret)
        docker(
            "exec",
            cid,
            "mc",
            "admin",
            "policy",
            "attach",
            "fixture",
            "readwrite",
            "--user",
            "fixture_app",
        )
        host = resource["url"].removeprefix("http://")
        headers = s3_sigv4_headers("PUT", host, f"/{TEST_BUCKET}", b"", "fixture_app", app_secret)
        httpx.put(f"{resource['url']}/{TEST_BUCKET}", headers=headers, timeout=5).raise_for_status()
        yield resource
    finally:
        if "container_id" in resource:
            verify_owned(resource)
            docker("rm", "--force", resource["container_id"])
        verify_owned(resource, volume=True)
        docker("volume", "rm", resource["volume"])


@pytest.fixture(scope="module")
def minio_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_minio_cluster() as cluster:
        yield cluster


def _to_async_pg_url(conninfo: str) -> URL:
    parts = conninfo_to_dict(conninfo)
    return URL.create(
        "postgresql+asyncpg",
        username=str(parts["user"]),
        password=str(parts["password"]),
        host=str(parts["host"]),
        port=int(parts["port"]),
        database=str(parts["dbname"]),
    )


def _seed_two_tenants_and_users(owner_url: str) -> dict[str, str]:
    t_a, t_b = str(uuid4()), str(uuid4())
    w_a, w_b = str(uuid4()), str(uuid4())
    creator_a, admin_a, user_b = str(uuid4()), str(uuid4()), str(uuid4())
    agent_a, av_a = str(uuid4()), str(uuid4())
    agent_b, av_b = str(uuid4()), str(uuid4())

    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        for tid, slug in ((t_a, f"ta-{t_a[:8]}"), (t_b, f"tb-{t_b[:8]}")):
            cur.execute(
                "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
                (tid, slug, f"Tenant {slug}"),
            )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t_a, w_a, f"wa-{w_a[:8]}", "Workspace A"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t_b, w_b, f"wb-{w_b[:8]}", "Workspace B"),
        )
        for uid, email in (
            (creator_a, f"creator-{creator_a[:8]}@test.local"),
            (admin_a, f"admin-{admin_a[:8]}@test.local"),
            (user_b, f"userb-{user_b[:8]}@test.local"),
        ):
            cur.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) VALUES (%s, %s, %s, %s, %s)",
                (uid, "https://issuer.test", uid, email, email),
            )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t_a, w_a, agent_a, f"ag-a-{agent_a[:8]}", "Agent A"),
        )
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, published_by) VALUES (%s, %s, %s, %s, 1, '1.0', '{}'::jsonb, %s, '{}'::jsonb, %s)",
            (t_a, w_a, av_a, agent_a, "a" * 64, creator_a),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_a,),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t_b, w_b, agent_b, f"ag-b-{agent_b[:8]}", "Agent B"),
        )
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, published_by) VALUES (%s, %s, %s, %s, 1, '1.0', '{}'::jsonb, %s, '{}'::jsonb, %s)",
            (t_b, w_b, av_b, agent_b, "b" * 64, user_b),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_b,),
        )
    return {
        "t_a": t_a,
        "t_b": t_b,
        "w_a": w_a,
        "w_b": w_b,
        "creator_a": creator_a,
        "admin_a": admin_a,
        "user_b": user_b,
        "av_a": av_a,
        "av_b": av_b,
    }


def _make_store(minio: dict[str, Any]) -> S3SigV4ObjectStore:
    return S3SigV4ObjectStore(
        endpoint=minio["url"],
        access_key=minio["app_user"],
        secret_key=minio["app_secret"],
        bucket=TEST_BUCKET,
    )


def _run_subprocess(mode: str, payload: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SUBPROCESS_HELPER), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"Subprocess {mode} failed: {proc.stderr}")
    return json.loads(proc.stdout.strip())


@pytest.mark.anyio
async def test_existing_immutable_object_cannot_be_replaced_and_race(
    minio_cluster: dict[str, Any],
) -> None:
    """R06A-03: Conditional create rejects overwrite with different content; same bytes replay."""
    store = _make_store(minio_cluster)
    key = f"tenants/{uuid4()}/workspaces/{uuid4()}/runs/{uuid4()}/input/{'c' * 64}-10.json"
    body_one = b'{"v":1,"msg":"first"}'
    sha_one = hashlib.sha256(body_one).hexdigest()
    body_two = b'{"v":2,"msg":"second"}'
    sha_two = hashlib.sha256(body_two).hexdigest()

    try:
        receipt1 = await store.put_immutable_object(
            bucket=TEST_BUCKET, key=key, data=body_one, expected_sha256=sha_one
        )
        assert receipt1.sha256 == sha_one

        # Same key, same bytes -> idempotent replay succeeds.
        replay = await store.put_immutable_object(
            bucket=TEST_BUCKET, key=key, data=body_one, expected_sha256=sha_one
        )
        assert replay.sha256 == sha_one

        # Same key, different bytes -> conflict, original bytes preserved.
        with pytest.raises(RunDataConflictError):
            await store.put_immutable_object(
                bucket=TEST_BUCKET, key=key, data=body_two, expected_sha256=sha_two
            )
        assert (
            await store.get_verified_object(bucket=TEST_BUCKET, key=key, expected_sha256=sha_one)
            == body_one
        )

        # Two-writer concurrent barrier race on a fresh key with different bytes.
        race_key = f"tenants/{uuid4()}/workspaces/{uuid4()}/runs/{uuid4()}/input/{'d' * 64}-10.json"
        r1 = await asyncio.gather(
            store.put_immutable_object(
                bucket=TEST_BUCKET, key=race_key, data=body_one, expected_sha256=sha_one
            ),
            store.put_immutable_object(
                bucket=TEST_BUCKET, key=race_key, data=body_two, expected_sha256=sha_two
            ),
            return_exceptions=True,
        )
        successes = [item for item in r1 if not isinstance(item, BaseException)]
        conflicts = [item for item in r1 if isinstance(item, RunDataConflictError)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        winner_bytes = body_one if successes[0].sha256 == sha_one else body_two
        assert await store.get_verified_object(bucket=TEST_BUCKET, key=race_key) == winner_bytes
    finally:
        await store.aclose()


@pytest.mark.parametrize(
    ("existing", "incoming"),
    [
        (b'{"value":"long original accepted payload"}', b"{}"),
        (b"{}", b'{"value":"long replacement payload"}'),
        (b'{"v":1}', b'{"v":2}'),
    ],
    ids=["existing-larger", "incoming-larger", "equal-size-different-content"],
)
@pytest.mark.anyio
async def test_immutable_conflict_preserves_existing_object_for_all_sizes(
    minio_cluster: dict[str, Any], existing: bytes, incoming: bytes
) -> None:
    """R06A-07: every different-body replay conflicts, regardless of size order."""
    store = _make_store(minio_cluster)
    key = f"size-conflict/{uuid4().hex}.json"
    existing_hash = hashlib.sha256(existing).hexdigest()
    try:
        await store.put_immutable_object(
            bucket=TEST_BUCKET, key=key, data=existing, expected_sha256=existing_hash
        )
        with pytest.raises(RunDataConflictError):
            await store.put_immutable_object(
                bucket=TEST_BUCKET,
                key=key,
                data=incoming,
                expected_sha256=hashlib.sha256(incoming).hexdigest(),
            )
        assert (
            await store.get_verified_object(
                bucket=TEST_BUCKET, key=key, expected_sha256=existing_hash
            )
            == existing
        )
    finally:
        await store.aclose()


@pytest.mark.anyio
async def test_06a_end_to_end_real_postgres_and_minio(
    postgres_cluster: dict[str, Any],
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
) -> None:
    """Covers G6-A01..G6-A06 + R06A-02/06 with separate OS processes, PG+MinIO restart & restore."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    ids = _seed_two_tenants_and_users(owner_url)

    creator_principal = RuntimePrincipal(
        subject="creator_a",
        user_id=ids["creator_a"],
        tenant_id=ids["t_a"],
        workspace_id=ids["w_a"],
        roles=("developer",),
        scopes=frozenset({"run:start", "run:read"}),
    )
    admin_principal = RuntimePrincipal(
        subject="admin_a",
        user_id=ids["admin_a"],
        tenant_id=ids["t_a"],
        workspace_id=ids["w_a"],
        roles=("owner", "admin", "operator"),
        scopes=frozenset({"run:read", "run:*"}),
    )
    tenant_b_principal = RuntimePrincipal(
        subject="user_b",
        user_id=ids["user_b"],
        tenant_id=ids["t_b"],
        workspace_id=ids["w_b"],
        roles=("developer",),
        scopes=frozenset({"run:start", "run:read"}),
    )

    run_a_id = str(uuid4())
    run_b_id = str(uuid4())
    conv_a_id = str(uuid4())
    expected_inputs = {"question": "Tiếng Việt có dấu & null", "nullable": None}
    expected_attachments = [
        {"id": "doc-1", "sha256": "1" * 64},
        {"id": "doc-2", "sha256": "2" * 64},
    ]

    # 1. Process A (separate OS subprocess) writes & activates Run A.
    proc_a_out = _run_subprocess(
        "write",
        {
            "app_url": app_url,
            "minio_url": minio_cluster["url"],
            "app_user": minio_cluster["app_user"],
            "app_secret": minio_cluster["app_secret"],
            "bucket": TEST_BUCKET,
            "tenant_id": ids["t_a"],
            "workspace_id": ids["w_a"],
            "user_id": ids["creator_a"],
            "agent_version_id": ids["av_a"],
            "conversation_id": conv_a_id,
            "run_id": run_a_id,
            "inputs": expected_inputs,
            "attachments": expected_attachments,
        },
    )

    # 2. Restart BOTH owned PostgreSQL and owned MinIO containers (G6-A06).
    restart_cluster(postgres_cluster, recreate=False)
    owner_url = postgres_cluster["owner_url"]
    app_url = postgres_cluster["app_url"]

    verify_owned(minio_cluster)
    docker("restart", minio_cluster["container_id"])
    ports = json.loads(
        docker(
            "inspect", "--format", "{{json .NetworkSettings.Ports}}", minio_cluster["container_id"]
        )
    )
    minio_cluster["url"] = f"http://127.0.0.1:{ports['9000/tcp'][0]['HostPort']}"
    deadline = time.monotonic() + 40
    async with httpx.AsyncClient(timeout=2.0) as health_client:
        while time.monotonic() < deadline:
            try:
                resp = await health_client.get(minio_cluster["url"] + "/minio/health/ready")
                if resp.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.3)

    # 3. Process B (fresh OS subprocess, zero RAM hash passed) reads durable PG ref+hash & MinIO.
    proc_b_out = _run_subprocess(
        "read",
        {
            "app_url": app_url,
            "minio_url": minio_cluster["url"],
            "app_user": minio_cluster["app_user"],
            "app_secret": minio_cluster["app_secret"],
            "bucket": TEST_BUCKET,
            "tenant_id": ids["t_a"],
            "workspace_id": ids["w_a"],
            "user_id": ids["creator_a"],
            "run_id": run_a_id,
        },
    )
    assert proc_a_out["pid"] != proc_b_out["pid"]
    assert proc_b_out["sha256"] == proc_a_out["sha256"]
    assert proc_b_out["size_bytes"] == proc_a_out["size_bytes"]
    assert proc_b_out["inputs"] == expected_inputs
    assert proc_b_out["attachments"] == expected_attachments

    # 4. PG backup & restore to temporary database -> fresh service reads under app_role RLS.
    evidence_dir = postgres_cluster["evidence_dir"]
    evidence_dir.mkdir(parents=True, exist_ok=True)
    backup_file = evidence_dir / "step06a_backup.dump"
    backup_database(postgres_cluster, backup_file)
    with temporary_database(postgres_cluster, "step06a_restore") as restored_db:
        restore_database(postgres_cluster, backup_file, restored_db)
        restored_app_url = database_url(postgres_cluster, restored_db, app=True)
        restored_engine = create_async_engine(
            _to_async_pg_url(restored_app_url), pool_pre_ping=True
        )
        restored_factory = async_sessionmaker(restored_engine, expire_on_commit=False)
        restored_store = _make_store(minio_cluster)
        restored_service = RunDataService(object_store=restored_store, bucket=TEST_BUCKET)
        try:
            async with restored_factory() as session, session.begin():
                await set_rls_context(
                    session,
                    tenant_id=ids["t_a"],
                    workspace_id=ids["w_a"],
                    user_id=ids["creator_a"],
                )
                restored_auth = await restored_service.authorize_run_input_read(
                    session, principal=creator_principal, run_id=run_a_id
                )
            restored_manifest = await restored_service.load_authorized_manifest(restored_auth)
            assert restored_manifest["inputs"] == expected_inputs
        finally:
            await restored_store.aclose()
            await restored_engine.dispose()

    # 5. Remaining G6-A02..A05 + R06A-02 tamper detection on real PG + MinIO.
    engine = create_async_engine(_to_async_pg_url(app_url), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = _make_store(minio_cluster)
    service = RunDataService(object_store=store, bucket=TEST_BUCKET)
    repo = RunDataRepository()

    try:
        # Positive Tenant B control before negative assertions.
        staged_b = await service.stage_run_input(
            principal=tenant_b_principal,
            run_id=run_b_id,
            agent_version_id=ids["av_b"],
            inputs={"question": "Tenant B secret"},
        )
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_b"],
                workspace_id=ids["w_b"],
                user_id=ids["user_b"],
            )
            await service.activate_staged_input(
                session,
                principal=tenant_b_principal,
                run_id=run_b_id,
                agent_version_id=ids["av_b"],
                external_request_key=f"req-b-{run_b_id}",
                staged=staged_b,
            )

        # G6-A04: body_ref XOR body_ciphertext CHECK constraint.
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["creator_a"],
            )
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        """
                        INSERT INTO runtime.messages (
                            tenant_id, workspace_id, conversation_id, role, body_ref, body_ciphertext
                        ) VALUES (
                            CAST(:tid AS uuid), CAST(:wid AS uuid), CAST(:cid AS uuid),
                            'user', 's3://x/y.json', E'\\\\x01'
                        )
                        """
                    ),
                    {"tid": ids["t_a"], "wid": ids["w_a"], "cid": conv_a_id},
                )

        # G6-A02: Same-workspace Owner/Admin/Operator denied; cross-tenant denied.
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["admin_a"],
            )
            with pytest.raises(RunDataAccessDeniedError):
                await service.authorize_run_input_read(
                    session, principal=admin_principal, run_id=run_a_id
                )
            with pytest.raises(RunDataAccessDeniedError):
                await service.validate_conversation_ownership(
                    session, principal=admin_principal, conversation_id=conv_a_id
                )
            with pytest.raises(RunDataNotFoundError):
                await service.authorize_run_input_read(
                    session, principal=admin_principal, run_id=run_b_id
                )

        # G6-A06: Object-before-DB rollback leaves staging object unactivated (0 DB rows).
        orphan_run_id = str(uuid4())
        staged_orphan = await service.stage_run_input(
            principal=creator_principal,
            run_id=orphan_run_id,
            agent_version_id=ids["av_a"],
            inputs={"question": "Should not activate on rollback"},
        )
        assert (
            await store.head_object(bucket=TEST_BUCKET, key=staged_orphan.receipt.key) is not None
        )
        try:
            async with factory() as session, session.begin():
                await set_rls_context(
                    session,
                    tenant_id=ids["t_a"],
                    workspace_id=ids["w_a"],
                    user_id=ids["creator_a"],
                )
                await service.activate_staged_input(
                    session,
                    principal=creator_principal,
                    run_id=orphan_run_id,
                    agent_version_id=ids["av_a"],
                    external_request_key=f"req-orphan-{orphan_run_id}",
                    staged=staged_orphan,
                )
                raise RuntimeError("Simulated crash before DB commit")
        except RuntimeError:
            pass

        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["creator_a"],
            )
            assert (
                await repo.get_run_record(
                    session, tenant_id=ids["t_a"], workspace_id=ids["w_a"], run_id=orphan_run_id
                )
                is None
            )

        # G6-A04: Appending a newer message does not alter run_a_id's pinned input.
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["creator_a"],
            )
            await repo.append_message(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                conversation_id=conv_a_id,
                role="user",
                body_ref=f"s3://{TEST_BUCKET}/tenants/{ids['t_a']}/workspaces/{ids['w_a']}/runs/{run_a_id}/message/{'e' * 64}-2.json",
            )
            auth_pinned = await service.authorize_run_input_read(
                session, principal=creator_principal, run_id=run_a_id
            )
        assert (await service.load_authorized_manifest(auth_pinned))["inputs"] == expected_inputs

        # G6-A04: Closed conversation rejected.
        with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runtime.conversations SET status = 'closed' WHERE id = %s", (conv_a_id,)
            )
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["creator_a"],
            )
            with pytest.raises(RunDataValidationError, match="closed"):
                await service.validate_conversation_ownership(
                    session, principal=creator_principal, conversation_id=conv_a_id
                )

        # R06A-02: Normal read (no hash arg) detects valid-JSON tamper in MinIO using durable PG hash.
        tampered_valid_json = json.dumps(
            {
                "schemaVersion": "1.0",
                "kind": "run_input",
                "tenantId": ids["t_a"],
                "workspaceId": ids["w_a"],
                "runId": run_a_id,
                "initiatedBy": ids["creator_a"],
                "agentVersionId": ids["av_a"],
                "inputs": {"question": "TAMPERED INPUT"},
            }
        ).encode("utf-8")
        host = minio_cluster["url"].removeprefix("http://")
        put_tampered_headers = s3_sigv4_headers(
            "PUT",
            host,
            f"/{TEST_BUCKET}/{auth_pinned.parsed_ref.key}",
            tampered_valid_json,
            minio_cluster["app_user"],
            minio_cluster["app_secret"],
        )
        async with httpx.AsyncClient(timeout=5.0) as raw_client:
            (
                await raw_client.put(
                    f"{minio_cluster['url']}/{TEST_BUCKET}/{auth_pinned.parsed_ref.key}",
                    headers=put_tampered_headers,
                    content=tampered_valid_json,
                )
            ).raise_for_status()
        with pytest.raises(RunDataIntegrityError):
            await service.load_authorized_manifest(auth_pinned)
        assert await store.delete_object(bucket=TEST_BUCKET, key=auth_pinned.parsed_ref.key) is True
        with pytest.raises(RunDataNotFoundError):
            await service.load_authorized_manifest(auth_pinned)

        # G6-A05: Retention boundary (30 days) & legal hold.
        finished_at = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
        with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runtime.runs SET status = 'succeeded', finished_at = %s WHERE id = %s",
                (finished_at, run_a_id),
            )
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["creator_a"],
            )
            assert (
                await service.evaluate_run_content_retention(
                    session,
                    tenant_id=ids["t_a"],
                    workspace_id=ids["w_a"],
                    run_id=run_a_id,
                    now=finished_at + timedelta(days=29, hours=23),
                )
            ).eligible_for_cleanup is False
            assert (
                await service.evaluate_run_content_retention(
                    session,
                    tenant_id=ids["t_a"],
                    workspace_id=ids["w_a"],
                    run_id=run_a_id,
                    now=finished_at + timedelta(days=30),
                )
            ).eligible_for_cleanup is True
            with pytest.raises(RunDataRetentionError):
                await service.authorize_run_input_read(
                    session,
                    principal=creator_principal,
                    run_id=run_a_id,
                    now=finished_at + timedelta(days=30),
                )

        with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO governance.retention_policies (tenant_id, workspace_id, data_class, retention_days, legal_hold)
                VALUES (%s, %s, 'run_content', 30, true)
                """,
                (ids["t_a"], ids["w_a"]),
            )
        async with factory() as session, session.begin():
            await set_rls_context(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                user_id=ids["creator_a"],
            )
            eval_held = await service.evaluate_run_content_retention(
                session,
                tenant_id=ids["t_a"],
                workspace_id=ids["w_a"],
                run_id=run_a_id,
                now=finished_at + timedelta(days=31),
            )
            assert eval_held.eligible_for_cleanup is False
            assert eval_held.reason == "legal_hold_active"
    finally:
        await store.aclose()
        await engine.dispose()
