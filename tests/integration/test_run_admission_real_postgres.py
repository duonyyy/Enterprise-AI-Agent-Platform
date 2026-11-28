"""Real PostgreSQL 16 + MinIO + Redis + Uvicorn integration tests for Step 06-B (G6-B01..G6-B07).

Zero mocks for PostgreSQL, MinIO, Redis, or HTTP transport.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import redis as redis_lib
import yaml
from agent_runtime.application.run_admission import (
    RunAdmissionAccessDeniedError,
    RunAdmissionQuotaExceededError,
    RunAdmissionUnavailableError,
)
from agent_runtime.application.run_admission_service import RunAdmissionService
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.object_store import S3SigV4ObjectStore
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_data_repository import RunDataRepository
from jobs_worker.worker import JobRecord, JobStatus, JobType, JobWorker
from jsonschema import Draft202012Validator
from platform_common.event_bus import PostgresOutboxEventBus
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_postgres_fixture import LABEL, docker, verify_owned
from verify_local_stack import s3_sigv4_headers

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TESTS_INTEGRATION_DIR = Path(__file__).resolve().parent
MINIO_IMAGE = "enterprise-agent-platform-minio:2025-10-15"
REDIS_IMAGE = "redis:7-alpine"
TEST_BUCKET = "step06b-run-admission"
RUNTIME_JWT_SECRET = "step06b-test-runtime-jwt-secret-0123456789abcdef"
RUNTIME_JWT_ISSUER = "https://auth.enterprise.test"
RUNTIME_JWT_AUDIENCE = "agent-runtime"


def test_0009_upgrade_preserves_legacy_and_backfills_only_bound_audit(
    isolated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Upgrade real 0008 data; preserve table count and refuse event-only receipt promotion."""
    from run_migrations import run_migrations

    old_chain = tmp_path / "through-0008"
    old_chain.mkdir()
    for path in sorted((ROOT / "database/migrations").glob("*.sql")):
        if int(path.name.split("_", 1)[0]) <= 8:
            shutil.copyfile(path, old_chain / path.name)
    owner_url = isolated_database["owner_url"]
    assert run_migrations(owner_url, old_chain)[-1] == "0008"
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])
    trusted_id, legacy_id = str(uuid4()), str(uuid4())
    key = f"upgrade-{uuid4().hex}"
    ref = f"s3://{TEST_BUCKET}/tenants/{fx['t_a']}/workspaces/{fx['w_a1']}/runs/{trusted_id}/input/{'a' * 64}-1.json"
    with psycopg.connect(owner_url) as conn:
        for run_id, operation_key in [(trusted_id, key), (legacy_id, key + "-legacy")]:
            conn.execute(
                "INSERT INTO runtime.runs (tenant_id,workspace_id,id,agent_version_id,initiated_by,external_request_key) VALUES (%s,%s,%s,%s,%s,%s)",
                (fx["t_a"], fx["w_a1"], run_id, fx["av_ok"], fx["dev_a"], operation_key),
            )
        conn.execute(
            "INSERT INTO governance.audit_events (tenant_id,workspace_id,actor_subject,action,resource_type,resource_id,outcome,details_redacted) VALUES (%s,%s,%s,'run.admitted','run',%s,'succeeded',%s::jsonb)",
            (
                fx["t_a"],
                fx["w_a1"],
                fx["dev_a"],
                trusted_id,
                json.dumps(
                    {
                        "actor_user_id": fx["dev_a"],
                        "operation_key": key,
                        "request_sha256": "b" * 64,
                        "input_ref": ref,
                        "input_sha256": "a" * 64,
                        "input_size_bytes": 1,
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO runtime.run_events (tenant_id,workspace_id,run_id,sequence,event_type,redacted_payload) VALUES (%s,%s,%s,1,'run.created',%s::jsonb)",
            (fx["t_a"], fx["w_a1"], legacy_id, json.dumps({"request_digest": "c" * 64})),
        )
    assert run_migrations(owner_url) == ["0009", "0010"]
    assert run_migrations(owner_url) == []
    with psycopg.connect(owner_url) as conn:
        assert conn.execute(
            "SELECT request_sha256,input_ref,input_sha256,input_size_bytes,receipt_expires_at FROM runtime.runs WHERE id=%s",
            (trusted_id,),
        ).fetchone() == ("b" * 64, ref, "a" * 64, 1, None)
        assert conn.execute(
            "SELECT request_sha256,input_ref FROM runtime.runs WHERE id=%s", (legacy_id,)
        ).fetchone() == (None, None)
        assert (
            conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops') AND table_type='BASE TABLE'"
            ).fetchone()[0]
            == 61
        )


def _validate_api_response(response: httpx.Response, path: str, method: str) -> None:
    spec = yaml.safe_load(
        (ROOT / "contracts/openapi/runtime-api.openapi.yaml").read_text(encoding="utf-8")
    )
    schema = spec["paths"][path][method]["responses"][str(response.status_code)]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator({**spec, **schema}).validate(response.json())


@pytest.mark.parametrize(
    ("seconds_from_expiry", "held", "active", "can_replay"),
    [
        (-60, False, False, False),
        (0, False, False, False),
        (60, False, False, True),
        (-60, True, False, True),
        (-60, False, True, True),
    ],
)
def test_receipt_columns_expiry_hold_and_pruned_input(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    seconds_from_expiry: int,
    held: bool,
    active: bool,
    can_replay: bool,
    tmp_path: Path,
) -> None:
    """Real PG clock, 30-day terminal deadline, key tombstone and independent input association."""
    from agent_runtime.application.run_admission import RunAdmissionConflictError

    owner_url = migrated_database["owner_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    async def scenario() -> None:
        engine = create_async_engine(_to_async_pg_url_obj(migrated_database["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scoped(t: str, w: str, u: str | None = None):
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                yield session

        store = S3SigV4ObjectStore(
            endpoint=minio_cluster["url"],
            access_key=minio_cluster["app_user"],
            secret_key=minio_cluster["app_secret"],
            bucket=TEST_BUCKET,
        )
        data = RunDataService(object_store=store, bucket=TEST_BUCKET)
        svc = RunAdmissionService(
            session_factory=scoped,
            run_data_service=data,
            admission_repository=RunAdmissionRepository(),
            outbox_publisher=PostgresOutboxEventBus(factory),
        )
        principal = RuntimePrincipal(
            subject="receipt-test",
            user_id=fx["dev_a"],
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            roles=("developer",),
            scopes=frozenset({"run:start", "run:read"}),
            token_scopes=frozenset({"run:start", "run:read"}),
            session_id=str(uuid4()),
        )
        kwargs = {
            "principal": principal,
            "workspace_id": fx["w_a1"],
            "agent_version_id": fx["av_no_grants"],
            "inputs": {"prompt": "Receipt input survives job pruning"},
            "idempotency_key": f"receipt-{uuid4().hex}",
        }
        try:
            admitted = await svc.admit_run(**kwargs)
            with psycopg.connect(owner_url) as conn:
                conn.execute("SET TIME ZONE 'America/New_York'")
                if not active:
                    conn.execute(
                        "UPDATE runtime.runs SET status='succeeded', finished_at=clock_timestamp()-interval '720 hours'+make_interval(secs => %s) WHERE id=%s",
                        (seconds_from_expiry, admitted.run_id),
                    )
                    delta = conn.execute(
                        "SELECT receipt_expires_at-finished_at FROM runtime.runs WHERE id=%s",
                        (admitted.run_id,),
                    ).fetchone()[0]
                    assert delta.total_seconds() == 30 * 86400
                if held:
                    conn.execute(
                        "INSERT INTO governance.retention_policies (tenant_id,workspace_id,data_class,retention_days,legal_hold) VALUES (%s,%s,'run_receipt',30,true)",
                        (fx["t_a"], fx["w_a1"]),
                    )
                conn.execute("DELETE FROM runtime.jobs WHERE run_id=%s", (admitted.run_id,))
                conn.execute("DELETE FROM runtime.run_events WHERE run_id=%s", (admitted.run_id,))

            # No job/event/audit lookup needed to retrieve the stable, scoped input reference.
            async with scoped(fx["t_a"], fx["w_a1"], fx["dev_a"]) as session:
                ref = await RunDataRepository().get_run_input_ref(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], run_id=admitted.run_id
                )
                assert ref is not None and f"/runs/{admitted.run_id}/input/" in ref
                # RLS still hides receipt/ref fields outside this workspace.
                await set_rls_context(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a2"], user_id=fx["dev_a"]
                )
                assert (
                    await RunDataRepository().get_run_input_ref(
                        session,
                        tenant_id=fx["t_a"],
                        workspace_id=fx["w_a1"],
                        run_id=admitted.run_id,
                    )
                    is None
                )

            if can_replay:
                replay = await svc.admit_run(**kwargs)
                assert replay.replayed and replay.run_id == admitted.run_id
                if held:
                    with psycopg.connect(owner_url) as conn:
                        conn.execute(
                            "UPDATE governance.retention_policies SET legal_hold=false WHERE tenant_id=%s AND workspace_id=%s AND data_class='run_receipt'",
                            (fx["t_a"], fx["w_a1"]),
                        )
                    with pytest.raises(RunAdmissionConflictError) as expired:
                        await svc.admit_run(**kwargs)
                    assert expired.value.code == "idempotency_key_expired"
            else:
                with pytest.raises(RunAdmissionConflictError) as err:
                    await svc.admit_run(**kwargs)
                assert err.value.code == "idempotency_key_expired"

            if seconds_from_expiry == 0 and not held and not active:
                proc, port, log = _spawn_runtime_uvicorn(
                    app_url=migrated_database["app_url"],
                    redis_url=redis_cluster["url"],
                    minio=minio_cluster,
                    log_path=tmp_path / "expired-key-http.log",
                )
                try:
                    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                        response = await client.post(
                            f"http://127.0.0.1:{port}/v1/runs",
                            headers={
                                "Authorization": f"Bearer {fx['tok_dev_a']}",
                                "Idempotency-Key": kwargs["idempotency_key"],
                            },
                            json={
                                "workspace_id": fx["w_a1"],
                                "agent_version_id": fx["av_no_grants"],
                                "inputs": kwargs["inputs"],
                            },
                        )
                    assert response.status_code == 409
                    assert response.json()["detail"]["code"] == "idempotency_key_expired"
                    _validate_api_response(response, "/v1/runs", "post")
                finally:
                    _stop_proc(proc, log)

            with psycopg.connect(owner_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.runs WHERE workspace_id=%s", (fx["w_a1"],)
                    ).fetchone()[0]
                    == 1
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.jobs WHERE run_id=%s", (admitted.run_id,)
                    ).fetchone()[0]
                    == 0
                )

            # Restricted app role cannot change trusted receipt identity or delete the key tombstone.
            for mutation in [
                "UPDATE runtime.runs SET request_sha256=repeat('b',64) WHERE id=:rid",
                "UPDATE runtime.runs SET input_ref='s3://other/object' WHERE id=:rid",
                "DELETE FROM runtime.runs WHERE id=:rid",
            ]:
                with pytest.raises(DBAPIError):
                    async with scoped(fx["t_a"], fx["w_a1"], fx["dev_a"]) as session:
                        await session.execute(text(mutation), {"rid": UUID(admitted.run_id)})
            if not active:
                with pytest.raises(DBAPIError):
                    async with scoped(fx["t_a"], fx["w_a1"], fx["dev_a"]) as session:
                        await session.execute(
                            text(
                                "UPDATE runtime.runs SET finished_at=finished_at+interval '1 day' WHERE id=:rid"
                            ),
                            {"rid": UUID(admitted.run_id)},
                        )
        finally:
            await store.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_http_ready(
    url: str,
    *,
    proc: subprocess.Popen[Any] | None = None,
    log_path: Path | None = None,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            log_text = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path and log_path.exists()
                else ""
            )
            raise AssertionError(f"Uvicorn exited early ({proc.returncode}):\n{log_text}")
        try:
            resp = httpx.get(url, timeout=1.5, trust_env=False)
            if resp.status_code == 200:
                return
            last_err = f"HTTP {resp.status_code}"
        except httpx.TransportError as exc:
            last_err = repr(exc)
        time.sleep(0.15)
    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path and log_path.exists()
        else ""
    )
    raise AssertionError(f"Timed out waiting for {url} ({last_err}):\n{log_text}")


@contextlib.contextmanager
def owned_minio_cluster() -> Generator[dict[str, Any], None, None]:
    try:
        docker("info")
    except RuntimeError as exc:
        pytest.skip(f"Docker daemon unavailable for MinIO fixture: {exc}")

    run_id = uuid4().hex
    root_secret = secrets.token_hex(24)
    app_secret = secrets.token_hex(24)
    resource: dict[str, Any] = {
        "run_id": run_id,
        "volume": f"step06b-minio-{run_id}",
        "root_user": "fixture_root",
        "root_secret": root_secret,
        "app_user": "fixture_app",
        "app_secret": app_secret,
    }
    docker("volume", "create", "--label", f"{LABEL}={run_id}", resource["volume"])
    try:
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
        url = f"http://127.0.0.1:{binding['HostPort']}"
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                if (
                    httpx.get(url + "/minio/health/ready", timeout=2, trust_env=False).status_code
                    == 200
                ):
                    resource["url"] = url
                    break
            except httpx.TransportError:
                pass
            Event().wait(0.3)
        else:
            raise AssertionError("Owned MinIO fixture did not become ready")

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
        httpx.put(
            f"{resource['url']}/{TEST_BUCKET}", headers=headers, timeout=5, trust_env=False
        ).raise_for_status()
        yield resource
    finally:
        if "container_id" in resource:
            verify_owned(resource)
            docker("rm", "--force", resource["container_id"])
        verify_owned(resource, volume=True)
        docker("volume", "rm", resource["volume"])


@contextlib.contextmanager
def owned_redis_cluster() -> Generator[dict[str, Any], None, None]:
    try:
        docker("info")
    except RuntimeError as exc:
        pytest.skip(f"Docker daemon unavailable for Redis fixture: {exc}")

    run_id = uuid4().hex
    resource: dict[str, Any] = {"run_id": run_id}
    try:
        resource["container_id"] = docker(
            "create",
            "--label",
            f"{LABEL}={run_id}",
            "--publish",
            "127.0.0.1::6379",
            REDIS_IMAGE,
        )
        verify_owned(resource)
        docker("start", resource["container_id"])
        ports = json.loads(
            docker(
                "inspect", "--format", "{{json .NetworkSettings.Ports}}", resource["container_id"]
            )
        )
        binding = ports["6379/tcp"][0]
        redis_url = f"redis://127.0.0.1:{binding['HostPort']}/0"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                client = redis_lib.Redis.from_url(redis_url, socket_timeout=1.0)
                if client.ping():
                    client.close()
                    resource["url"] = redis_url
                    break
                client.close()
            except redis_lib.RedisError:
                pass
            Event().wait(0.2)
        else:
            raise AssertionError("Owned Redis fixture did not become ready")
        yield resource
    finally:
        if "container_id" in resource:
            verify_owned(resource)
            docker("rm", "--force", resource["container_id"])


@pytest.fixture(scope="module")
def minio_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_minio_cluster() as cluster:
        yield cluster


@pytest.fixture(scope="module")
def redis_cluster() -> Generator[dict[str, Any], None, None]:
    with owned_redis_cluster() as cluster:
        yield cluster


def _to_psycopg_url(conninfo: str) -> str:
    parts = conninfo_to_dict(conninfo)
    return URL.create(
        "postgresql+psycopg",
        username=str(parts["user"]),
        password=str(parts["password"]),
        host=str(parts["host"]),
        port=int(parts["port"]),
        database=str(parts["dbname"]),
    ).render_as_string(hide_password=False)


def _to_async_pg_url(conninfo: str) -> str:
    return _to_async_pg_url_obj(conninfo).render_as_string(hide_password=False)


def _to_async_pg_url_obj(conninfo: str) -> URL:
    parts = conninfo_to_dict(conninfo)
    return URL.create(
        "postgresql+asyncpg",
        username=str(parts["user"]),
        password=str(parts["password"]),
        host=str(parts["host"]),
        port=int(parts["port"]),
        database=str(parts["dbname"]),
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint_runtime_jwt(
    *,
    user_id: str,
    tenant_id: str,
    workspace_id: str,
    session_id: str,
    roles: list[str],
    scopes: list[str],
    secret: str = RUNTIME_JWT_SECRET,
) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": RUNTIME_JWT_ISSUER,
                "aud": RUNTIME_JWT_AUDIENCE,
                "sub": f"sub-{user_id}",
                "user_id": user_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "session_id": session_id,
                "roles": roles,
                "scopes": scopes,
                "iat": now,
                "nbf": now - 5,
                "exp": now + 600,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = _b64url(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def _register_redis_session(redis_url: str, *, session_id: str, user_id: str) -> None:
    now = int(time.time())
    client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    try:
        client.set(
            f"auth:session:{session_id}",
            json.dumps(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "rotation_version": 1,
                    "created_at": now - 10,
                    "idle_expires_at": now + 1800,
                    "absolute_expires_at": now + 3600,
                }
            ),
            ex=3600,
        )
    finally:
        client.close()


def _revoke_redis_session(redis_url: str, *, session_id: str) -> None:
    now = int(time.time())
    client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    try:
        client.set(f"revocation:session:{session_id}", str(now + 3600), ex=3600)
    finally:
        client.close()


def _seed_admission_fixture(owner_url: str, redis_url: str) -> dict[str, Any]:
    t_a, t_b = str(uuid4()), str(uuid4())
    w_a1, w_a2, w_b1 = str(uuid4()), str(uuid4()), str(uuid4())
    dev_a, other_dev_a, viewer_a, user_b = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    sess_dev, sess_other, sess_viewer, sess_revoked, sess_b = (str(uuid4()) for _ in range(5))

    model_ok, model_disabled = str(uuid4()), str(uuid4())
    model_a2 = str(uuid4())
    skill_ok, skill_ver_ok = str(uuid4()), str(uuid4())
    skill_disabled, skill_ver_disabled = str(uuid4()), str(uuid4())
    tool_ok, tool_ver_ok = str(uuid4()), str(uuid4())
    kb_restricted, kb_denied = str(uuid4()), str(uuid4())

    agent_ok, av_ok = str(uuid4()), str(uuid4())
    agent_archived, av_archived = str(uuid4()), str(uuid4())
    av_disabled_model = str(uuid4())
    av_disabled_skill = str(uuid4())
    av_denied_kb = str(uuid4())
    av_no_grants = str(uuid4())
    agent_a2, av_a2 = str(uuid4()), str(uuid4())
    agent_b1, av_b1 = str(uuid4()), str(uuid4())
    conv_dev, conv_other = str(uuid4()), str(uuid4())

    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        for tid, slug in ((t_a, f"ta-{t_a[:8]}"), (t_b, f"tb-{t_b[:8]}")):
            cur.execute(
                "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)", (tid, slug, slug)
            )
        for tid, wid, slug in (
            (t_a, w_a1, f"wa1-{w_a1[:8]}"),
            (t_a, w_a2, f"wa2-{w_a2[:8]}"),
            (t_b, w_b1, f"wb1-{w_b1[:8]}"),
        ):
            cur.execute(
                "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
                (tid, wid, slug, slug),
            )

        for uid, subj in (
            (dev_a, f"dev-{dev_a[:8]}"),
            (other_dev_a, f"other-{other_dev_a[:8]}"),
            (viewer_a, f"view-{viewer_a[:8]}"),
            (user_b, f"userb-{user_b[:8]}"),
        ):
            cur.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) VALUES (%s, %s, %s, %s, %s)",
                (uid, RUNTIME_JWT_ISSUER, subj, subj, f"{subj}@test.local"),
            )

        for tid, wid, uid, role in (
            (t_a, w_a1, dev_a, "developer"),
            (t_a, w_a1, other_dev_a, "developer"),
            (t_a, w_a1, viewer_a, "viewer"),
            (t_a, w_a2, dev_a, "developer"),
            (t_b, w_b1, user_b, "developer"),
        ):
            cur.execute(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) VALUES (%s, %s, %s, %s, 'active')",
                (tid, wid, uid, role),
            )

        # Models
        cur.execute(
            "INSERT INTO catalog.model_configs (tenant_id, workspace_id, id, name, provider, provider_model_id, status) VALUES (%s, %s, %s, 'm-ok', 'openai', 'gpt-4o-mini', 'active')",
            (t_a, w_a1, model_ok),
        )
        cur.execute(
            "INSERT INTO catalog.model_configs (tenant_id, workspace_id, id, name, provider, provider_model_id, status) VALUES (%s, %s, %s, 'm-dis', 'openai', 'gpt-4o-mini', 'disabled')",
            (t_a, w_a1, model_disabled),
        )
        cur.execute(
            "INSERT INTO catalog.model_configs (tenant_id, workspace_id, id, name, provider, provider_model_id, status) VALUES (%s, %s, %s, 'm-a2', 'openai', 'gpt-4o-mini', 'active')",
            (t_a, w_a2, model_a2),
        )

        # Tools & grants
        cur.execute(
            "INSERT INTO catalog.tools (tenant_id, workspace_id, id, tool_key, display_name, kind) VALUES (%s, %s, %s, 'echo', 'Echo', 'builtin')",
            (t_a, w_a1, tool_ok),
        )
        cur.execute(
            "INSERT INTO catalog.tool_versions (tenant_id, workspace_id, id, tool_id, version, input_schema, output_schema, risk, sha256) VALUES (%s, %s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb, 'read', %s)",
            (t_a, w_a1, tool_ver_ok, tool_ok, "c" * 64),
        )
        cur.execute(
            "INSERT INTO catalog.tool_grants (tenant_id, workspace_id, tool_id, principal_kind, principal_key, allowed_operation) VALUES (%s, %s, %s, 'role', 'developer', 'invoke')",
            (t_a, w_a1, tool_ok),
        )

        # Skills
        cur.execute(
            "INSERT INTO catalog.skills (tenant_id, workspace_id, id, slug, display_name, status) VALUES (%s, %s, %s, 'skill-ok', 'Skill OK', 'active')",
            (t_a, w_a1, skill_ok),
        )
        cur.execute(
            "INSERT INTO catalog.skill_versions (tenant_id, workspace_id, id, skill_id, version, manifest, skill_md, bundle_ref, bundle_sha256) VALUES (%s, %s, %s, %s, 1, '{}'::jsonb, '# Skill', 's3://b/k', %s)",
            (t_a, w_a1, skill_ver_ok, skill_ok, "d" * 64),
        )
        cur.execute(
            "INSERT INTO catalog.skills (tenant_id, workspace_id, id, slug, display_name, status) VALUES (%s, %s, %s, 'skill-dis', 'Skill Disabled', 'disabled')",
            (t_a, w_a1, skill_disabled),
        )
        cur.execute(
            "INSERT INTO catalog.skill_versions (tenant_id, workspace_id, id, skill_id, version, manifest, skill_md, bundle_ref, bundle_sha256) VALUES (%s, %s, %s, %s, 1, '{}'::jsonb, '# Skill', 's3://b/k2', %s)",
            (t_a, w_a1, skill_ver_disabled, skill_disabled, "e" * 64),
        )

        # Knowledge bases
        cur.execute(
            "INSERT INTO knowledge.bases (tenant_id, workspace_id, id, slug, name, visibility) VALUES (%s, %s, %s, 'kb-ok', 'KB OK', 'restricted')",
            (t_a, w_a1, kb_restricted),
        )
        cur.execute(
            "INSERT INTO knowledge.base_grants (tenant_id, workspace_id, base_id, principal_kind, principal_key, permission) VALUES (%s, %s, %s, 'user', %s, 'read')",
            (t_a, w_a1, kb_restricted, dev_a),
        )
        cur.execute(
            "INSERT INTO knowledge.base_grants (tenant_id, workspace_id, base_id, principal_kind, principal_key, permission) VALUES (%s, %s, %s, 'user', %s, 'read')",
            (t_a, w_a1, kb_restricted, other_dev_a),
        )
        cur.execute(
            "INSERT INTO knowledge.bases (tenant_id, workspace_id, id, slug, name, visibility) VALUES (%s, %s, %s, 'kb-denied', 'KB Denied', 'restricted')",
            (t_a, w_a1, kb_denied),
        )

        # Agents & versions
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, 'agent-ok', 'Agent OK')",
            (t_a, w_a1, agent_ok),
        )
        # 1. Sealed valid version
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 1, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a1, av_ok, agent_ok, "1" * 64, model_ok, dev_a),
        )
        cur.execute(
            "INSERT INTO catalog.agent_tool_bindings (tenant_id, workspace_id, agent_version_id, tool_version_id) VALUES (%s, %s, %s, %s)",
            (t_a, w_a1, av_ok, tool_ver_ok),
        )
        cur.execute(
            "INSERT INTO catalog.agent_skill_bindings (tenant_id, workspace_id, agent_version_id, skill_id, skill_version_id) VALUES (%s, %s, %s, %s, %s)",
            (t_a, w_a1, av_ok, skill_ok, skill_ver_ok),
        )
        cur.execute(
            "INSERT INTO catalog.agent_knowledge_bindings (tenant_id, workspace_id, agent_version_id, base_id) VALUES (%s, %s, %s, %s)",
            (t_a, w_a1, av_ok, kb_restricted),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s", (av_ok,)
        )

        # 2. Unsealed version (bypass check trigger using session_replication_role? No, disable constraint trigger? Even simpler: insert inside same conn? Wait: check_agent_version_sealed_trigger is DEFERRABLE INITIALLY DEFERRED! So we test unsealed by checking NOT sealed vs archived/disabled, OR wait: check_agent_version_sealed_trigger forbids committing unsealed! Let's check: can we create an unsealed version? In 0007, check_agent_version_sealed_trigger raises at COMMIT if bindings_sealed_at IS NULL! So in DB `bindings_sealed_at` is guaranteed non-null on committed rows — instead we test archived agent, disabled model, disabled skill, denied KB, cross-workspace version!)

        # 3. Archived agent with sealed version
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name, archived_at) VALUES (%s, %s, %s, 'agent-arch', 'Archived', now())",
            (t_a, w_a1, agent_archived),
        )
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 1, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a1, av_archived, agent_archived, "2" * 64, model_ok, dev_a),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_archived,),
        )

        # 4. Version with disabled model
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 2, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a1, av_disabled_model, agent_ok, "3" * 64, model_disabled, dev_a),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_disabled_model,),
        )

        # 5. Version with disabled skill
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 3, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a1, av_disabled_skill, agent_ok, "4" * 64, model_ok, dev_a),
        )
        cur.execute(
            "INSERT INTO catalog.agent_skill_bindings (tenant_id, workspace_id, agent_version_id, skill_id, skill_version_id) VALUES (%s, %s, %s, %s, %s)",
            (t_a, w_a1, av_disabled_skill, skill_disabled, skill_ver_disabled),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_disabled_skill,),
        )

        # 6. Version with denied restricted KB
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 4, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a1, av_denied_kb, agent_ok, "5" * 64, model_ok, dev_a),
        )
        cur.execute(
            "INSERT INTO catalog.agent_knowledge_bindings (tenant_id, workspace_id, agent_version_id, base_id) VALUES (%s, %s, %s, %s)",
            (t_a, w_a1, av_denied_kb, kb_denied),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_denied_kb,),
        )

        # 6b. Sealed version with zero tool/skill/KB dependency grants (R06B-01 regression)
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 5, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a1, av_no_grants, agent_ok, "8" * 64, model_ok, dev_a),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (av_no_grants,),
        )

        # 7. Workspace A2 & B1 sealed versions
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, 'agent-a2', 'Agent A2')",
            (t_a, w_a2, agent_a2),
        )
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 1, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, %s, %s)",
            (t_a, w_a2, av_a2, agent_a2, "6" * 64, model_a2, dev_a),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s", (av_a2,)
        )

        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, 'agent-b1', 'Agent B1')",
            (t_b, w_b1, agent_b1),
        )
        cur.execute(
            "INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by) VALUES (%s, %s, %s, %s, 1, '1.0', '{\"mode\":\"fixed_plan\"}'::jsonb, %s, '{}'::jsonb, NULL, %s)",
            (t_b, w_b1, av_b1, agent_b1, "7" * 64, user_b),
        )
        cur.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s", (av_b1,)
        )

        # Conversations
        cur.execute(
            "INSERT INTO runtime.conversations (tenant_id, workspace_id, id, external_subject, status) VALUES (%s, %s, %s, %s, 'active')",
            (t_a, w_a1, conv_dev, dev_a),
        )
        cur.execute(
            "INSERT INTO runtime.conversations (tenant_id, workspace_id, id, external_subject, status) VALUES (%s, %s, %s, %s, 'active')",
            (t_a, w_a1, conv_other, other_dev_a),
        )

    _register_redis_session(redis_url, session_id=sess_dev, user_id=dev_a)
    _register_redis_session(redis_url, session_id=sess_other, user_id=other_dev_a)
    _register_redis_session(redis_url, session_id=sess_viewer, user_id=viewer_a)
    _register_redis_session(redis_url, session_id=sess_revoked, user_id=dev_a)
    _revoke_redis_session(redis_url, session_id=sess_revoked)
    _register_redis_session(redis_url, session_id=sess_b, user_id=user_b)

    return {
        "t_a": t_a,
        "t_b": t_b,
        "w_a1": w_a1,
        "w_a2": w_a2,
        "w_b1": w_b1,
        "dev_a": dev_a,
        "other_dev_a": other_dev_a,
        "viewer_a": viewer_a,
        "user_b": user_b,
        "av_ok": av_ok,
        "av_archived": av_archived,
        "av_disabled_model": av_disabled_model,
        "av_disabled_skill": av_disabled_skill,
        "av_denied_kb": av_denied_kb,
        "av_no_grants": av_no_grants,
        "av_a2": av_a2,
        "av_b1": av_b1,
        "conv_dev": conv_dev,
        "conv_other": conv_other,
        "tok_dev_a": _mint_runtime_jwt(
            user_id=dev_a,
            tenant_id=t_a,
            workspace_id=w_a1,
            session_id=sess_dev,
            roles=["developer"],
            scopes=["run:start", "run:read"],
        ),
        "tok_dev_a_w2": _mint_runtime_jwt(
            user_id=dev_a,
            tenant_id=t_a,
            workspace_id=w_a2,
            session_id=sess_dev,
            roles=["developer"],
            scopes=["run:start", "run:read"],
        ),
        "tok_other_a": _mint_runtime_jwt(
            user_id=other_dev_a,
            tenant_id=t_a,
            workspace_id=w_a1,
            session_id=sess_other,
            roles=["developer"],
            scopes=["run:start", "run:read"],
        ),
        "tok_viewer_a": _mint_runtime_jwt(
            user_id=viewer_a,
            tenant_id=t_a,
            workspace_id=w_a1,
            session_id=sess_viewer,
            roles=["viewer"],
            scopes=["run:start", "run:read"],
        ),
        "tok_revoked": _mint_runtime_jwt(
            user_id=dev_a,
            tenant_id=t_a,
            workspace_id=w_a1,
            session_id=sess_revoked,
            roles=["developer"],
            scopes=["run:start", "run:read"],
        ),
    }


def _spawn_runtime_uvicorn(
    *,
    app_url: str,
    redis_url: str,
    minio: dict[str, Any],
    log_path: Path,
) -> tuple[subprocess.Popen[Any], int, Any]:
    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": "development",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "DATABASE_URL": _to_async_pg_url(app_url),
            "REDIS_URL": redis_url,
            "AUTH_STORE_TYPE": "redis",
            "RUNTIME_JWT_SECRET": RUNTIME_JWT_SECRET,
            "RUNTIME_JWT_ISSUER": RUNTIME_JWT_ISSUER,
            "RUNTIME_JWT_AUDIENCE": RUNTIME_JWT_AUDIENCE,
            "MODEL_GATEWAY_CONFIG_PATH": "",
            "S3_ENDPOINT": minio["url"],
            "S3_ACCESS_KEY": minio["app_user"],
            "S3_SECRET_KEY": minio["app_secret"],
            "S3_BUCKET": TEST_BUCKET,
            "RUN_ADMISSION_EXECUTION_ENABLED": "false",
        }
    )
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agent_runtime.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    _wait_http_ready(f"http://127.0.0.1:{port}/health/live", proc=proc, log_path=log_path)
    return proc, port, log_file


def _stop_proc(proc: subprocess.Popen[Any] | None, log_file: Any | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if log_file is not None:
        log_file.close()


def test_06b_happy_path_no_executor_worker_guard_and_api_restart(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-B01, G6-B07: Real HTTP + PG (platform_app RLS) + MinIO + Redis + Worker guard + Restart."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    proc_a, port_a, log_a = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "runtime_a.log",
    )
    proc_b = None
    log_b = None
    try:
        # 1. Model gateway readiness stays fail-closed (503) while admission_ready is True (D6-03)
        ready_resp = httpx.get(
            f"http://127.0.0.1:{port_a}/health/ready", timeout=5, trust_env=False
        )
        assert ready_resp.status_code == 503
        ready_body = ready_resp.json()
        assert ready_body["capabilities"]["execution_ready"] is False
        assert ready_body["capabilities"]["storage_configured"] is True

        # 2. Happy path POST /v1/runs
        idem_key = f"b01-happy-{uuid4().hex[:8]}"
        start_resp = httpx.post(
            f"http://127.0.0.1:{port_a}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": idem_key,
            },
            json={
                "workspace_id": fx["w_a1"],
                "agent_version_id": fx["av_ok"],
                "conversation_id": fx["conv_dev"],
                "inputs": {"prompt": "Tóm tắt tài liệu A bằng tiếng Việt", "optional": None},
                "attachments": [{"id": "att-1", "sha256": "a" * 64, "size": 64}],
            },
            timeout=10,
            trust_env=False,
        )
        assert start_resp.status_code == 202, start_resp.text
        _validate_api_response(start_resp, "/v1/runs", "post")
        body = start_resp.json()
        run_id = body["run_id"]
        assert body["status"] == "queued"
        assert body["replayed"] is False
        assert body["execution_available"] is False
        assert body["availability_reason"] == "no_executor_configured"
        assert body["agent_version_id"] == fx["av_ok"]
        assert body["conversation_id"] == fx["conv_dev"]

        # 3. Verify authoritative rows under platform_app role + RLS (and zero rows without RLS context)
        with psycopg.connect(app_url) as pconn:
            assert (
                pconn.execute("SELECT * FROM runtime.runs WHERE id = %s", (run_id,)).fetchall()
                == []
            )
            pconn.rollback()
            with pconn.transaction():
                pconn.execute(
                    "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                    (fx["t_a"], fx["w_a1"]),
                )
                run_row = pconn.execute(
                    "SELECT status, agent_version_id, conversation_id, initiated_by, next_event_seq FROM runtime.runs WHERE id = %s",
                    (run_id,),
                ).fetchone()
                assert run_row is not None
                assert run_row[0] == "queued"
                assert str(run_row[1]) == fx["av_ok"]
                assert str(run_row[2]) == fx["conv_dev"]
                assert str(run_row[3]) == fx["dev_a"]
                assert run_row[4] == 2

                job_row = pconn.execute(
                    "SELECT id, job_type, status, payload_ref, attempt FROM runtime.jobs WHERE run_id = %s",
                    (run_id,),
                ).fetchone()
                assert job_row is not None
                assert job_row[1] == "run"
                assert job_row[2] == "queued"
                payload_ref = str(job_row[3])
                assert payload_ref.startswith(
                    f"s3://{TEST_BUCKET}/tenants/{fx['t_a']}/workspaces/{fx['w_a1']}/runs/{run_id}/input/"
                )

                msg_row = pconn.execute(
                    "SELECT role, body_ref, body_ciphertext FROM runtime.messages WHERE conversation_id = %s ORDER BY created_at DESC LIMIT 1",
                    (fx["conv_dev"],),
                ).fetchone()
                assert msg_row is not None
                assert msg_row[0] == "user"
                assert msg_row[1] == payload_ref
                assert msg_row[2] is None

                evt_row = pconn.execute(
                    "SELECT sequence, event_type, redacted_payload FROM runtime.run_events WHERE run_id = %s",
                    (run_id,),
                ).fetchone()
                assert evt_row is not None
                assert evt_row[0] == 1
                event_row = pconn.execute(
                    "SELECT id, created_at FROM runtime.run_events WHERE run_id = %s", (run_id,)
                ).fetchone()
                queued_schema = json.loads(
                    (ROOT / "contracts/jsonschema/run-event.v1.2.schema.json").read_text(
                        encoding="utf-8"
                    )
                )
                Draft202012Validator(queued_schema).validate(
                    {
                        "schemaVersion": "1.2",
                        "eventId": str(event_row[0]),
                        "runId": run_id,
                        "workspaceId": fx["w_a1"],
                        "sequence": evt_row[0],
                        "type": evt_row[1],
                        "timestamp": event_row[1].isoformat(),
                        "data": evt_row[2],
                    }
                )
                assert "Tóm tắt tài liệu A" not in json.dumps(evt_row[2], ensure_ascii=False)

                audit_row = pconn.execute(
                    "SELECT action, outcome, details_redacted FROM governance.audit_events WHERE resource_type = 'run' AND resource_id = %s",
                    (run_id,),
                ).fetchone()
                assert audit_row is not None
                assert audit_row[0] == "run.admitted"
                assert audit_row[1] == "succeeded"
                assert audit_row[2]["input_ref"] == payload_ref
                assert audit_row[2]["execution_available"] is False

                outbox_row = pconn.execute(
                    "SELECT aggregate_type, event_type, state FROM integration.outbox_events WHERE aggregate_id = %s",
                    (run_id,),
                ).fetchone()
                assert outbox_row == ("run", "run.queued", "pending")
                outbox_payload = pconn.execute(
                    "SELECT payload FROM integration.outbox_events WHERE aggregate_id=%s", (run_id,)
                ).fetchone()[0]["data"]
                Draft202012Validator(queued_schema).validate(outbox_payload)
                assert outbox_payload["eventId"] == str(event_row[0])
                assert datetime.fromisoformat(outbox_payload["timestamp"]) == event_row[1]
                assert outbox_payload["sequence"] == 1

        # 4. Worker without executor leaves queued job untouched and validates payload_ref contract
        worker = JobWorker(_to_psycopg_url(app_url), fx["t_a"], fx["w_a1"])
        assert worker.has_run_executor is False
        assert worker.lease_next_job() is None
        assert worker.reclaim_expired_leases() == 0
        now_utc = datetime.now(UTC)
        valid_job = JobRecord(
            id=str(job_row[0]),
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            job_type=JobType.RUN,
            run_id=run_id,
            payload_ref=payload_ref,
            status=JobStatus.QUEUED,
            available_at=now_utc,
            leased_until=None,
            leased_by=None,
            attempt=0,
            max_attempts=5,
            created_at=now_utc,
        )
        bucket, _key, _sha, _sz = JobWorker.validate_run_payload_ref(
            valid_job, expected_bucket=TEST_BUCKET
        )
        assert bucket == TEST_BUCKET

        # 5. Restart API (kill A, start B) -> read input via RunDataService + replay + fail-closed resume
        _stop_proc(proc_a, log_a)
        proc_a = None

        async def _verify_input_read_after_restart() -> None:
            engine = create_async_engine(_to_async_pg_url_obj(app_url))
            factory = async_sessionmaker(engine, expire_on_commit=False)
            store = S3SigV4ObjectStore(
                endpoint=minio_cluster["url"],
                access_key=minio_cluster["app_user"],
                secret_key=minio_cluster["app_secret"],
                bucket=TEST_BUCKET,
            )
            svc = RunDataService(object_store=store, bucket=TEST_BUCKET)
            principal = RuntimePrincipal(
                subject="dev",
                user_id=fx["dev_a"],
                tenant_id=fx["t_a"],
                workspace_id=fx["w_a1"],
                roles=("developer",),
                scopes=frozenset({"run:read"}),
            )
            try:
                async with factory() as session, session.begin():
                    await set_rls_context(
                        session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], user_id=fx["dev_a"]
                    )
                    auth_ref = await svc.authorize_run_input_read(
                        session, principal=principal, run_id=run_id
                    )
                manifest = await svc.load_authorized_manifest(auth_ref)
                assert manifest["inputs"]["prompt"] == "Tóm tắt tài liệu A bằng tiếng Việt"
                assert manifest["inputs"]["optional"] is None
                assert manifest["agentVersionId"] == fx["av_ok"]
            finally:
                await store.aclose()
                await engine.dispose()

        asyncio.run(_verify_input_read_after_restart())

        proc_b, port_b, log_b = _spawn_runtime_uvicorn(
            app_url=app_url,
            redis_url=redis_cluster["url"],
            minio=minio_cluster,
            log_path=tmp_path / "runtime_b.log",
        )
        meta_resp = httpx.get(
            f"http://127.0.0.1:{port_b}/v1/runs/{run_id}",
            headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
            timeout=5,
            trust_env=False,
        )
        assert meta_resp.status_code == 200
        meta_body = meta_resp.json()
        assert meta_body["run_id"] == run_id
        assert meta_body["status"] == "queued"
        assert "inputs" not in meta_body

        replay_resp = httpx.post(
            f"http://127.0.0.1:{port_b}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": idem_key,
            },
            json={
                "workspace_id": fx["w_a1"],
                "agent_version_id": fx["av_ok"],
                "conversation_id": fx["conv_dev"],
                "inputs": {"prompt": "Tóm tắt tài liệu A bằng tiếng Việt", "optional": None},
                "attachments": [{"id": "att-1", "sha256": "a" * 64, "size": 64}],
            },
            timeout=10,
            trust_env=False,
        )
        assert replay_resp.status_code == 202
        assert replay_resp.json()["run_id"] == run_id
        assert replay_resp.json()["replayed"] is True

        resume_resp = httpx.post(
            f"http://127.0.0.1:{port_b}/internal/v1/runs/{run_id}/resume",
            headers={"Authorization": f"Bearer {fx['tok_dev_a']}"},
            timeout=5,
            trust_env=False,
        )
        assert resume_resp.status_code == 503
        assert resume_resp.json()["detail"]["code"] == "resume_disabled"
    finally:
        _stop_proc(proc_a, log_a)
        _stop_proc(proc_b, log_b)


def test_06b_denials_leave_zero_partial_rows(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-B02: Revoked session, scope, archived agent, disabled deps, cross-scope leave 0 rows."""
    owner_url = migrated_database["owner_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    proc, port, log_f = _spawn_runtime_uvicorn(
        app_url=migrated_database["app_url"],
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "runtime_denial.log",
    )
    try:
        denial_cases = [
            ("revoked_session", fx["tok_revoked"], fx["w_a1"], fx["av_ok"], None, 401),
            ("viewer_scope_denied", fx["tok_viewer_a"], fx["w_a1"], fx["av_ok"], None, 403),
            ("workspace_mismatch", fx["tok_dev_a"], fx["w_a2"], fx["av_a2"], None, 403),
            ("cross_workspace_version", fx["tok_dev_a"], fx["w_a1"], fx["av_b1"], None, 404),
            ("archived_agent", fx["tok_dev_a"], fx["w_a1"], fx["av_archived"], None, 409),
            ("disabled_model", fx["tok_dev_a"], fx["w_a1"], fx["av_disabled_model"], None, 409),
            ("disabled_skill", fx["tok_dev_a"], fx["w_a1"], fx["av_disabled_skill"], None, 409),
            ("denied_kb", fx["tok_dev_a"], fx["w_a1"], fx["av_denied_kb"], None, 403),
            (
                "other_user_conversation",
                fx["tok_dev_a"],
                fx["w_a1"],
                fx["av_ok"],
                fx["conv_other"],
                403,
            ),
        ]
        for idx, (label, token, wid, avid, conv_id, expected_status) in enumerate(denial_cases):
            resp = httpx.post(
                f"http://127.0.0.1:{port}/v1/runs",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": f"deny-{idx}-{label}",
                },
                json={
                    "workspace_id": wid,
                    "agent_version_id": avid,
                    "conversation_id": conv_id,
                    "inputs": {"prompt": f"denied-{label}"},
                },
                timeout=5,
                trust_env=False,
            )
            assert resp.status_code == expected_status, (
                f"{label}: got {resp.status_code} {resp.text}"
            )
            _validate_api_response(resp, "/v1/runs", "post")

        with psycopg.connect(owner_url) as conn:
            run_count = conn.execute(
                "SELECT count(*) FROM runtime.runs WHERE external_request_key LIKE 'deny-%'"
            ).fetchone()[0]
            assert run_count == 0
    finally:
        _stop_proc(proc, log_f)


def test_06b_two_replica_barrier_conflicts_and_pruning_independence(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-B03, G6-B04, G6-B05: 2 uvicorn replicas race barrier, cross-actor 409, terminal replay after event pruning."""
    owner_url = migrated_database["owner_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    proc_a, port_a, log_a = _spawn_runtime_uvicorn(
        app_url=migrated_database["app_url"],
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "replica_a.log",
    )
    proc_b, port_b, log_b = _spawn_runtime_uvicorn(
        app_url=migrated_database["app_url"],
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "replica_b.log",
    )
    try:
        race_key = f"barrier-{uuid4().hex[:8]}"
        payload = {
            "workspace_id": fx["w_a1"],
            "agent_version_id": fx["av_ok"],
            "inputs": {"prompt": "Concurrent barrier admission"},
        }

        async def _fire_barrier() -> tuple[httpx.Response, httpx.Response]:
            barrier = asyncio.Barrier(2)
            async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:

                async def _send(port: int) -> httpx.Response:
                    await barrier.wait()
                    return await client.post(
                        f"http://127.0.0.1:{port}/v1/runs",
                        headers={
                            "Authorization": f"Bearer {fx['tok_dev_a']}",
                            "Idempotency-Key": race_key,
                        },
                        json=payload,
                    )

                return await asyncio.gather(_send(port_a), _send(port_b))

        r1, r2 = asyncio.run(_fire_barrier())
        assert r1.status_code == 202 and r2.status_code == 202
        b1, b2 = r1.json(), r2.json()
        assert b1["run_id"] == b2["run_id"]
        assert {b1["replayed"], b2["replayed"]} == {False, True}
        admitted_run_id = b1["run_id"]

        with psycopg.connect(owner_url) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.runs WHERE id = %s", (admitted_run_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.jobs WHERE run_id = %s", (admitted_run_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.run_events WHERE run_id = %s", (admitted_run_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM governance.audit_events WHERE resource_type = 'run' AND resource_id = %s",
                    (admitted_run_id,),
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM integration.outbox_events WHERE aggregate_id = %s",
                    (admitted_run_id,),
                ).fetchone()[0]
                == 1
            )

        # G6-B04: Same key + different actor in same workspace -> 409 without leaking run_id
        other_actor_resp = httpx.post(
            f"http://127.0.0.1:{port_b}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_other_a']}",
                "Idempotency-Key": race_key,
            },
            json=payload,
            timeout=5,
            trust_env=False,
        )
        assert other_actor_resp.status_code == 409
        assert admitted_run_id not in other_actor_resp.text

        # G6-B04: Same key + different payload -> 409 without leaking run_id
        diff_payload_resp = httpx.post(
            f"http://127.0.0.1:{port_a}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": race_key,
            },
            json={
                "workspace_id": fx["w_a1"],
                "agent_version_id": fx["av_ok"],
                "inputs": {"prompt": "Different intent"},
            },
            timeout=5,
            trust_env=False,
        )
        assert diff_payload_resp.status_code == 409
        assert admitted_run_id not in diff_payload_resp.text

        # G6-B04: Same key in different workspace -> accepted independently
        ws2_resp = httpx.post(
            f"http://127.0.0.1:{port_b}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a_w2']}",
                "Idempotency-Key": race_key,
            },
            json={
                "workspace_id": fx["w_a2"],
                "agent_version_id": fx["av_a2"],
                "inputs": {"prompt": "Concurrent barrier admission"},
            },
            timeout=10,
            trust_env=False,
        )
        assert ws2_resp.status_code == 202
        assert ws2_resp.json()["run_id"] != admitted_run_id

        # G6-B05: Transition to terminal 'succeeded' AND prune runtime.run_events -> replay remains stable!
        with psycopg.connect(owner_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE runtime.runs SET status = 'succeeded', finished_at = now() WHERE id = %s",
                (admitted_run_id,),
            )
            conn.execute("DELETE FROM runtime.run_events WHERE run_id = %s", (admitted_run_id,))

        term_replay = httpx.post(
            f"http://127.0.0.1:{port_a}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": race_key,
            },
            json=payload,
            timeout=5,
            trust_env=False,
        )
        assert term_replay.status_code == 202
        assert term_replay.json()["run_id"] == admitted_run_id
        assert term_replay.json()["status"] == "succeeded"
        assert term_replay.json()["replayed"] is True

        # Different payload after pruning still fails with 409 (immutable audit receipt survives pruning!)
        post_prune_conflict = httpx.post(
            f"http://127.0.0.1:{port_b}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": race_key,
            },
            json={
                "workspace_id": fx["w_a1"],
                "agent_version_id": fx["av_ok"],
                "inputs": {"prompt": "Post-prune tamper attempt"},
            },
            timeout=5,
            trust_env=False,
        )
        assert post_prune_conflict.status_code == 409
    finally:
        _stop_proc(proc_a, log_a)
        _stop_proc(proc_b, log_b)


@pytest.mark.parametrize(
    "hook_stage",
    [
        "after_run_insert",
        "after_job_insert",
        "after_event_insert",
        "after_audit_insert",
        "after_outbox_insert",
    ],
)
def test_06b_write_stage_fault_rollback_real_postgres(
    hook_stage: str,
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """G6-B06 / R06B-05: Fault at every Phase-3 PG write stage rolls back all 6 tables cleanly."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    async def _scenario() -> None:
        engine = create_async_engine(_to_async_pg_url_obj(app_url), pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def _scoped(tenant_id: str, workspace_id: str, user_id: str | None = None):
            async with factory() as session, session.begin():
                await set_rls_context(
                    session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id
                )
                yield session

        store = S3SigV4ObjectStore(
            endpoint=minio_cluster["url"],
            access_key=minio_cluster["app_user"],
            secret_key=minio_cluster["app_secret"],
            bucket=TEST_BUCKET,
        )
        data_repo = RunDataRepository()
        data_service = RunDataService(object_store=store, bucket=TEST_BUCKET, repository=data_repo)
        svc = RunAdmissionService(
            session_factory=_scoped,
            run_data_service=data_service,
            admission_repository=RunAdmissionRepository(),
            outbox_publisher=PostgresOutboxEventBus(factory),
            run_data_repository=data_repo,
        )
        principal = RuntimePrincipal(
            subject="dev",
            user_id=fx["dev_a"],
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            roles=("developer",),
            scopes=frozenset({"run:start", "run:read"}),
            token_scopes=frozenset({"run:start", "run:read"}),
            session_id=str(uuid4()),
        )
        fault_key = f"fault-{hook_stage}-{uuid4().hex[:6]}"
        try:

            async def _fail_at_stage(stage: str, _sess: Any) -> None:
                if stage == hook_stage:
                    raise OperationalError("WRITE", {}, RuntimeError(f"injected-{hook_stage}"))

            with pytest.raises(RunAdmissionUnavailableError):
                await svc.admit_run(
                    principal=principal,
                    workspace_id=fx["w_a1"],
                    agent_version_id=fx["av_ok"],
                    conversation_id=fx["conv_dev"],
                    inputs={"prompt": f"Rollback test {hook_stage}"},
                    idempotency_key=fault_key,
                    after_write_hook=_fail_at_stage,
                )

            with psycopg.connect(owner_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.runs WHERE workspace_id = %s", (fx["w_a1"],)
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.jobs WHERE workspace_id = %s", (fx["w_a1"],)
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.messages WHERE workspace_id = %s",
                        (fx["w_a1"],),
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.run_events WHERE workspace_id = %s",
                        (fx["w_a1"],),
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM governance.audit_events WHERE workspace_id = %s",
                        (fx["w_a1"],),
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    conn.execute(
                        "SELECT count(*) FROM integration.outbox_events WHERE workspace_id = %s",
                        (fx["w_a1"],),
                    ).fetchone()[0]
                    == 0
                )

            admitted = await svc.admit_run(
                principal=principal,
                workspace_id=fx["w_a1"],
                agent_version_id=fx["av_ok"],
                conversation_id=fx["conv_dev"],
                inputs={"prompt": f"Rollback test {hook_stage}"},
                idempotency_key=fault_key,
            )
            assert admitted.status == "queued"
            assert admitted.replayed is False
        finally:
            await store.aclose()
            await engine.dispose()

    asyncio.run(_scenario())


def test_06b_post_commit_os_kill_and_fresh_process_recovery(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-B06 / R06B-05: OS SIGKILL after PG COMMIT before HTTP response; fresh uvicorn recovers exact run_id."""
    import queue
    import threading

    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    crash_port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": "development",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "CRASH_HELPER_PORT": str(crash_port),
            "DATABASE_URL": _to_async_pg_url(app_url),
            "REDIS_URL": redis_cluster["url"],
            "AUTH_STORE_TYPE": "redis",
            "RUNTIME_JWT_SECRET": RUNTIME_JWT_SECRET,
            "RUNTIME_JWT_ISSUER": RUNTIME_JWT_ISSUER,
            "RUNTIME_JWT_AUDIENCE": RUNTIME_JWT_AUDIENCE,
            "MODEL_GATEWAY_CONFIG_PATH": "",
            "S3_ENDPOINT": minio_cluster["url"],
            "S3_ACCESS_KEY": minio_cluster["app_user"],
            "S3_SECRET_KEY": minio_cluster["app_secret"],
            "S3_BUCKET": TEST_BUCKET,
            "RUN_ADMISSION_EXECUTION_ENABLED": "false",
        }
    )
    crash_key = f"crash-{uuid4().hex[:8]}"
    payload = {
        "workspace_id": fx["w_a1"],
        "agent_version_id": fx["av_ok"],
        "conversation_id": fx["conv_dev"],
        "inputs": {"prompt": "Kill after commit before HTTP 202"},
    }

    crash_proc = subprocess.Popen(
        [sys.executable, "-u", str(TESTS_INTEGRATION_DIR / "run_admission_crash_helper.py")],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc_recovery = None
    log_recovery = None
    try:
        assert crash_proc.stdout is not None
        out_q: queue.Queue[str | None] = queue.Queue()
        helper_logs: list[str] = []

        def _drain_stdout() -> None:
            assert crash_proc.stdout is not None
            for raw_line in crash_proc.stdout:
                cleaned = raw_line.rstrip()
                helper_logs.append(cleaned)
                out_q.put(cleaned)
            out_q.put(None)

        threading.Thread(target=_drain_stdout, daemon=True).start()

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                line = out_q.get(timeout=remaining)
            except queue.Empty as exc:
                raise AssertionError(
                    f"crash helper did not emit BARRIER:listening: logs={helper_logs}"
                ) from exc
            if line is None:
                raise AssertionError(
                    f"crash helper exited early ({crash_proc.poll()}): logs={helper_logs}"
                )
            if line.strip() == f"BARRIER:listening:{crash_port}":
                break
        else:
            raise AssertionError(f"crash helper did not emit BARRIER:listening: logs={helper_logs}")
        _wait_http_ready(
            f"http://127.0.0.1:{crash_port}/health/live",
            proc=crash_proc,
            log_path=tmp_path / "crash_noop.log",
        )

        client_errors: list[Exception] = []
        client_responses: list[httpx.Response] = []

        def _send_request() -> None:
            try:
                resp = httpx.post(
                    f"http://127.0.0.1:{crash_port}/v1/runs",
                    headers={
                        "Authorization": f"Bearer {fx['tok_dev_a']}",
                        "Idempotency-Key": crash_key,
                    },
                    json=payload,
                    timeout=15,
                    trust_env=False,
                )
                client_responses.append(resp)
            except httpx.TransportError as exc:
                client_errors.append(exc)

        req_thread = threading.Thread(target=_send_request, daemon=True)
        req_thread.start()

        commit_deadline = time.monotonic() + 20
        while time.monotonic() < commit_deadline:
            remaining = max(0.1, commit_deadline - time.monotonic())
            try:
                line = out_q.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if not req_thread.is_alive():
                    raise AssertionError(
                        f"HTTP request ended before BARRIER:committed; responses={[(r.status_code, r.text) for r in client_responses]}, errors={client_errors}, logs={helper_logs}"
                    ) from None
                continue
            if line is None:
                raise AssertionError(
                    f"crash helper died before BARRIER:committed ({crash_proc.poll()}): logs={helper_logs}"
                )
            if line.strip() == "BARRIER:committed":
                break
        else:
            raise AssertionError(
                f"crash helper did not reach BARRIER:committed: logs={helper_logs}, responses={[(r.status_code, r.text) for r in client_responses]}, errors={client_errors}"
            )

        # Hard-kill OS process AFTER PG COMMIT and BEFORE FastAPI sends HTTP 202
        crash_proc.kill()
        crash_proc.wait(timeout=10)
        req_thread.join(timeout=10)

        assert client_responses == [], (
            f"Killed process unexpectedly returned HTTP response: {client_responses}"
        )
        assert len(client_errors) == 1 and isinstance(client_errors[0], httpx.TransportError)

        with psycopg.connect(owner_url) as conn:
            committed_run_id = conn.execute(
                "SELECT id::text FROM runtime.runs WHERE workspace_id = %s AND external_request_key = %s",
                (fx["w_a1"], crash_key),
            ).fetchone()[0]

        proc_recovery, recovery_port, log_recovery = _spawn_runtime_uvicorn(
            app_url=app_url,
            redis_url=redis_cluster["url"],
            minio=minio_cluster,
            log_path=tmp_path / "recovery.log",
        )
        replay = httpx.post(
            f"http://127.0.0.1:{recovery_port}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": crash_key,
            },
            json=payload,
            timeout=10,
            trust_env=False,
        )
        assert replay.status_code == 202
        replay_body = replay.json()
        assert replay_body["run_id"] == committed_run_id
        assert replay_body["status"] == "queued"
        assert replay_body["replayed"] is True

        with psycopg.connect(owner_url) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.runs WHERE workspace_id = %s", (fx["w_a1"],)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.jobs WHERE run_id = %s", (committed_run_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.messages WHERE workspace_id = %s", (fx["w_a1"],)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM runtime.run_events WHERE run_id = %s", (committed_run_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM governance.audit_events WHERE resource_id = %s",
                    (committed_run_id,),
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM integration.outbox_events WHERE aggregate_id = %s",
                    (committed_run_id,),
                ).fetchone()[0]
                == 1
            )
    finally:
        if crash_proc.poll() is None:
            crash_proc.kill()
            crash_proc.wait(timeout=5)
        _stop_proc(proc_recovery, log_recovery)


def test_06b_r01_role_demotion_during_staging_denies_even_without_grants(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """R06B-01: Live developer->viewer demotion during MinIO staging fails closed even with zero dependency grants."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    class _DemotingObjectStore(S3SigV4ObjectStore):
        async def put_immutable_object(self, *args: Any, **kwargs: Any) -> Any:
            staged = await super().put_immutable_object(*args, **kwargs)
            with psycopg.connect(owner_url, autocommit=True) as conn:
                conn.execute(
                    "UPDATE iam.memberships SET role = 'viewer' WHERE tenant_id = %s AND workspace_id = %s AND user_id = %s",
                    (fx["t_a"], fx["w_a1"], fx["dev_a"]),
                )
            return staged

    async def _scenario() -> None:
        engine = create_async_engine(_to_async_pg_url_obj(app_url), pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def _scoped(tenant_id: str, workspace_id: str, user_id: str | None = None):
            async with factory() as session, session.begin():
                await set_rls_context(
                    session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id
                )
                yield session

        store = _DemotingObjectStore(
            endpoint=minio_cluster["url"],
            access_key=minio_cluster["app_user"],
            secret_key=minio_cluster["app_secret"],
            bucket=TEST_BUCKET,
        )
        data_repo = RunDataRepository()
        svc = RunAdmissionService(
            session_factory=_scoped,
            run_data_service=RunDataService(
                object_store=store, bucket=TEST_BUCKET, repository=data_repo
            ),
            admission_repository=RunAdmissionRepository(),
            outbox_publisher=PostgresOutboxEventBus(factory),
            run_data_repository=data_repo,
        )
        principal = RuntimePrincipal(
            subject=f"sub-{fx['dev_a']}",
            user_id=fx["dev_a"],
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            roles=("developer",),
            scopes=frozenset({"run:start", "run:read"}),
            token_scopes=frozenset({"run:start", "run:read"}),
            session_id=str(uuid4()),
        )
        key = f"r01-{uuid4().hex[:8]}"
        try:
            with pytest.raises(RunAdmissionAccessDeniedError):
                await svc.admit_run(
                    principal=principal,
                    workspace_id=fx["w_a1"],
                    agent_version_id=fx["av_no_grants"],
                    inputs={"prompt": "Demoted during staging"},
                    idempotency_key=key,
                )
            with psycopg.connect(owner_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.runs WHERE workspace_id = %s", (fx["w_a1"],)
                    ).fetchone()[0]
                    == 0
                )
        finally:
            await store.aclose()
            await engine.dispose()

    asyncio.run(_scenario())

    # Stale JWT claiming developer scopes still returns 403 over HTTP after role demotion
    proc_a, port_a, log_a = _spawn_runtime_uvicorn(
        app_url=app_url,
        redis_url=redis_cluster["url"],
        minio=minio_cluster,
        log_path=tmp_path / "r01_http.log",
    )
    try:
        http_resp = httpx.post(
            f"http://127.0.0.1:{port_a}/v1/runs",
            headers={
                "Authorization": f"Bearer {fx['tok_dev_a']}",
                "Idempotency-Key": f"r01-http-{uuid4().hex[:6]}",
            },
            json={
                "workspace_id": fx["w_a1"],
                "agent_version_id": fx["av_no_grants"],
                "inputs": {"prompt": "Post-demotion HTTP attempt"},
            },
            timeout=10,
            trust_env=False,
        )
        assert http_resp.status_code == 403
    finally:
        _stop_proc(proc_a, log_a)


def test_06b_r03_different_key_quota_race_serializes_at_capacity(
    migrated_database: dict[str, Any],
    minio_cluster: dict[str, Any],
    redis_cluster: dict[str, Any],
) -> None:
    """R06B-03: Concurrent different-key admissions in one workspace serialize quota checks."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    class _BarrierRepo(RunAdmissionRepository):
        def __init__(self) -> None:
            self._barrier = asyncio.Barrier(2)

        async def verify_active_workspace_membership(
            self,
            session: Any,
            *,
            tenant_id: str,
            workspace_id: str,
            user_id: str,
            for_share: bool = False,
        ) -> str:
            role = await super().verify_active_workspace_membership(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                for_share=for_share,
            )
            if for_share:
                # Synchronize both Phase-3 transactions right before acquiring workspace admission lock.
                await self._barrier.wait()
            return role

    async def _scenario() -> None:
        engine = create_async_engine(_to_async_pg_url_obj(app_url), pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def _scoped(tenant_id: str, workspace_id: str, user_id: str | None = None):
            async with factory() as session, session.begin():
                await set_rls_context(
                    session, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id
                )
                yield session

        store = S3SigV4ObjectStore(
            endpoint=minio_cluster["url"],
            access_key=minio_cluster["app_user"],
            secret_key=minio_cluster["app_secret"],
            bucket=TEST_BUCKET,
        )
        data_repo = RunDataRepository()
        svc = RunAdmissionService(
            session_factory=_scoped,
            run_data_service=RunDataService(
                object_store=store, bucket=TEST_BUCKET, repository=data_repo
            ),
            admission_repository=_BarrierRepo(),
            outbox_publisher=PostgresOutboxEventBus(factory),
            run_data_repository=data_repo,
            max_queued_per_workspace=1,
        )
        principal = RuntimePrincipal(
            subject=f"sub-{fx['dev_a']}",
            user_id=fx["dev_a"],
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            roles=("developer",),
            scopes=frozenset({"run:start", "run:read"}),
            token_scopes=frozenset({"run:start", "run:read"}),
            session_id=str(uuid4()),
        )
        try:
            k1 = f"quota-a-{uuid4().hex[:8]}"
            k2 = f"quota-b-{uuid4().hex[:8]}"
            results = await asyncio.gather(
                svc.admit_run(
                    principal=principal,
                    workspace_id=fx["w_a1"],
                    agent_version_id=fx["av_no_grants"],
                    inputs={"prompt": "Race slot 1"},
                    idempotency_key=k1,
                ),
                svc.admit_run(
                    principal=principal,
                    workspace_id=fx["w_a1"],
                    agent_version_id=fx["av_no_grants"],
                    inputs={"prompt": "Race slot 2"},
                    idempotency_key=k2,
                ),
                return_exceptions=True,
            )
            accepted = [r for r in results if not isinstance(r, BaseException)]
            rejected = [r for r in results if isinstance(r, BaseException)]
            assert len(accepted) == 1, f"Expected 1 accepted, got results={results}"
            assert len(rejected) == 1 and isinstance(rejected[0], RunAdmissionQuotaExceededError)

            with psycopg.connect(owner_url) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM runtime.runs WHERE workspace_id = %s", (fx["w_a1"],)
                    ).fetchone()[0]
                    == 1
                )
        finally:
            await store.aclose()
            await engine.dispose()

    asyncio.run(_scenario())


def test_06b_r04_storage_outage_updates_readiness_and_fails_closed(
    migrated_database: dict[str, Any],
    redis_cluster: dict[str, Any],
    tmp_path: Path,
) -> None:
    """R06B-04: Stopping MinIO flips admission_ready/storage_ready to False and POST /v1/runs returns 503."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    fx = _seed_admission_fixture(owner_url, redis_cluster["url"])

    with owned_minio_cluster() as isolated_minio:
        proc_a, port_a, log_a = _spawn_runtime_uvicorn(
            app_url=app_url,
            redis_url=redis_cluster["url"],
            minio=isolated_minio,
            log_path=tmp_path / "r04_outage.log",
        )
        try:
            up_ready = httpx.get(
                f"http://127.0.0.1:{port_a}/health/ready", timeout=5, trust_env=False
            )
            assert up_ready.status_code == 503  # No model gateway configured
            up_caps = up_ready.json()["capabilities"]
            assert up_caps["storage_configured"] is True
            assert up_caps["storage_ready"] is True
            assert up_caps["admission_ready"] is True

            docker("stop", isolated_minio["container_id"])

            down_live = httpx.get(
                f"http://127.0.0.1:{port_a}/health/live", timeout=5, trust_env=False
            )
            assert down_live.status_code == 200

            down_ready = httpx.get(
                f"http://127.0.0.1:{port_a}/health/ready", timeout=5, trust_env=False
            )
            assert down_ready.status_code == 503
            down_caps = down_ready.json()["capabilities"]
            assert down_caps["storage_configured"] is True
            assert down_caps["storage_ready"] is False
            assert down_caps["admission_ready"] is False

            outage_post = httpx.post(
                f"http://127.0.0.1:{port_a}/v1/runs",
                headers={
                    "Authorization": f"Bearer {fx['tok_dev_a']}",
                    "Idempotency-Key": f"r04-down-{uuid4().hex[:6]}",
                },
                json={
                    "workspace_id": fx["w_a1"],
                    "agent_version_id": fx["av_ok"],
                    "inputs": {"prompt": "Storage down"},
                },
                timeout=10,
                trust_env=False,
            )
            assert outage_post.status_code == 503
        finally:
            _stop_proc(proc_a, log_a)
