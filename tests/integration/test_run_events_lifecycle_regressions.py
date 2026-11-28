"""Regressions for C/D authority, event contracts, replay and bounded maintenance."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import psycopg
import pytest
from agent_runtime.application.run_event_service import RunEventService
from agent_runtime.application.run_lifecycle import RunLifecycleAccessDeniedError
from agent_runtime.application.run_lifecycle_service import RunLifecycleService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.persistence.database import set_rls_context
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_event_repository import RunEventRepository
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.run_migrations import run_migrations
from scripts.test_postgres_fixture import postgres_fixture_cluster
from tests.integration.test_run_admission_real_postgres import (
    RUNTIME_JWT_SECRET,
    _seed_admission_fixture,
    _stop_proc,
    _to_async_pg_url,
    owned_redis_cluster,
)
from tests.integration.test_run_events_sse_real_tcp import _seed_test_run, _spawn_event_uvicorn

ROOT = Path.cwd()


def test_stream_stops_when_scoped_token_expires_with_parent_session_active(env):
    _pg, _fx, base, rid, headers = seed(env, status="running")
    claims = jwt.decode(
        headers["Authorization"].removeprefix("Bearer "), options={"verify_signature": False}
    )
    claims["exp"] = int(time.time()) + 4
    token = jwt.encode(claims, RUNTIME_JWT_SECRET, algorithm="HS256")

    async def scenario():
        frames = []
        async with httpx.AsyncClient(trust_env=False, timeout=9) as client:
            async with client.stream(
                "GET",
                f"{base}/v1/runs/{rid}/events?last_event_id=1",
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    frames.append(line)
            assert any("authorization_expired" in line for line in frames)
            assert not any(line.startswith("id:") for line in frames)
            response = await client.get(
                f"{base}/v1/runs/{rid}/history", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 401

    asyncio.run(scenario())


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    with postgres_fixture_cluster() as pg, owned_redis_cluster() as redis:
        run_migrations(pg["owner_url"], ROOT / "database/migrations")
        proc, port, log = _spawn_event_uvicorn(
            app_url=pg["app_url"],
            redis_url=redis["url"],
            log_path=tmp_path_factory.mktemp("review06cd") / "runtime.log",
        )
        try:
            yield pg, redis, f"http://127.0.0.1:{port}"
        finally:
            _stop_proc(proc, log)


def seed(env, *, status="succeeded", creator_key="dev_a"):
    pg, redis, base = env
    fx = _seed_admission_fixture(pg["owner_url"], redis["url"])
    rid = _seed_test_run(
        pg["owner_url"],
        tenant_id=fx["t_a"],
        workspace_id=fx["w_a1"],
        user_id=fx[creator_key],
        status=status,
    )
    return pg, fx, base, rid, {"Authorization": f"Bearer {fx['tok_dev_a']}"}


def test_peer_stream_stops_after_owner_demotion(env):
    pg, fx, base, rid, headers = seed(env, status="running", creator_key="other_dev_a")
    with psycopg.connect(pg["owner_url"]) as conn:
        conn.execute(
            "UPDATE iam.memberships SET role='owner' WHERE workspace_id=%s AND user_id=%s",
            (fx["w_a1"], fx["dev_a"]),
        )

    async def scenario():
        leaked = []
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            async with client.stream(
                "GET", f"{base}/v1/runs/{rid}/events", headers=headers
            ) as resp:
                assert resp.status_code == 200
                demoted = False
                async for line in resp.aiter_lines():
                    if line.startswith("id: 1") and not demoted:
                        with psycopg.connect(pg["owner_url"]) as conn:
                            conn.execute(
                                "UPDATE iam.memberships SET role='developer' WHERE workspace_id=%s AND user_id=%s",
                                (fx["w_a1"], fx["dev_a"]),
                            )
                        demoted = True
                        await asyncio.sleep(2.5)
                        with psycopg.connect(pg["owner_url"]) as conn:
                            conn.execute(
                                "INSERT INTO runtime.run_events (id,tenant_id,workspace_id,run_id,sequence,event_type,redacted_payload) VALUES (%s,%s,%s,%s,2,'run.completed','{\"status\":\"succeeded\"}')",
                                (str(uuid4()), fx["t_a"], fx["w_a1"], rid),
                            )
                            conn.execute(
                                "UPDATE runtime.runs SET next_event_seq=3,status='succeeded' WHERE id=%s",
                                (rid,),
                            )
                    elif line.startswith("id: 2"):
                        leaked.append(line)
            denied = await client.get(f"{base}/v1/runs/{rid}/history", headers=headers)
            assert denied.status_code == 404, denied.text
        assert not leaked, f"Peer event emitted after demotion: {leaked}"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "query,extra_headers",
    [
        ("?last_event_id=0&last_event_id=0", {}),
        ("", [("Last-Event-ID", "0"), ("Last-Event-ID", "0")]),
    ],
)
def test_sse_rejects_duplicate_cursor(env, query, extra_headers):
    _, _, base, rid, headers = seed(env)
    merged = list(headers.items()) + list(
        extra_headers.items() if isinstance(extra_headers, dict) else extra_headers
    )
    resp = httpx.get(f"{base}/v1/runs/{rid}/events{query}", headers=merged, trust_env=False)
    assert resp.status_code == 400, f"Duplicate cursor accepted: HTTP {resp.status_code}"


def test_history_invalid_limit_uses_400(env):
    _, _, base, rid, headers = seed(env)
    resp = httpx.get(f"{base}/v1/runs/{rid}/history?limit=abc", headers=headers, trust_env=False)
    assert resp.status_code == 400, f"Expected 400 invalid_limit; got {resp.status_code}"


@pytest.mark.parametrize(
    "payload",
    [
        {"agentVersionId": "synthetic-sensitive-value", "executionAvailable": "not-a-boolean"},
        {"agentVersionId": "s" * 20000},
    ],
)
def test_legacy_projected_metadata_is_validated(env, payload):
    pg, _, base, rid, headers = seed(env)
    with psycopg.connect(pg["owner_url"]) as conn:
        conn.execute(
            "UPDATE runtime.run_events SET redacted_payload=%s::jsonb WHERE run_id=%s",
            (json.dumps(payload), rid),
        )
    resp = httpx.get(f"{base}/v1/runs/{rid}/history", headers=headers, trust_env=False)
    assert resp.status_code == 200, (
        f"Invalid optional metadata should be omitted before transfer: HTTP {resp.status_code}"
    )
    body = resp.json()
    assert "agentVersionId" not in body["events"][0]["data"], (
        "Invalid UUID reached the public projection"
    )
    assert "executionAvailable" not in body["events"][0]["data"], (
        "Non-boolean reached public projection"
    )
    schema = json.loads((ROOT / "contracts/jsonschema/run-event.v1.2.schema.json").read_text())
    for evt in body["events"]:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(evt)
    assert "synthetic-sensitive-value" not in resp.text


def test_normal_history_omits_null_trace_id(env):
    _, _, base, rid, headers = seed(env)
    resp = httpx.get(f"{base}/v1/runs/{rid}/history", headers=headers, trust_env=False)
    assert resp.status_code == 200
    schema = json.loads((ROOT / "contracts/jsonschema/run-event.v1.2.schema.json").read_text())
    for evt in resp.json()["events"]:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(evt)


def test_history_detects_missing_tail_before_headers(env):
    pg, fx, base, rid, headers = seed(env)
    with psycopg.connect(pg["owner_url"]) as conn:
        conn.execute("UPDATE runtime.runs SET next_event_seq=3 WHERE id=%s", (rid,))
    resp = httpx.get(f"{base}/v1/runs/{rid}/history", headers=headers, trust_env=False)
    assert resp.status_code == 503, f"Missing tail accepted: {resp.status_code} {resp.text}"
    peer = httpx.get(
        f"{base}/v1/runs/{rid}/history",
        headers={"Authorization": f"Bearer {fx['tok_other_a']}"},
        trust_env=False,
    )
    assert peer.status_code == 404  # Timeline corruption must not disclose a peer run.


def test_live_stream_detects_missing_tail(env):
    pg, _fx, base, rid, headers = seed(env, status="running")

    async def scenario():
        lines = []
        async with httpx.AsyncClient(trust_env=False, timeout=8) as client:
            async with client.stream(
                "GET", f"{base}/v1/runs/{rid}/events?last_event_id=1", headers=headers
            ) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    lines.append(line)
                    if line.startswith(": connected"):
                        with psycopg.connect(pg["owner_url"]) as conn:
                            conn.execute(
                                "UPDATE runtime.runs SET next_event_seq=3,status='succeeded' WHERE id=%s",
                                (rid,),
                            )
            assert any("event_history_inconsistent" in x for x in lines), (
                f"Missing event silently EOF: {lines}"
            )

    asyncio.run(scenario())


def test_history_batch_and_head_share_one_snapshot(env):
    from agent_runtime.persistence.run_event_repository import RunEventRepository

    pg, fx, _, rid, _ = seed(env, status="running")

    class AppendAfterSnapshotRepository(RunEventRepository):
        async def get_run_snapshot(self, *args, **kwargs):
            snapshot = await super().get_run_snapshot(*args, **kwargs)
            with psycopg.connect(pg["owner_url"]) as conn:
                conn.execute(
                    "INSERT INTO runtime.run_events (id,tenant_id,workspace_id,run_id,sequence,event_type,redacted_payload) VALUES (%s,%s,%s,%s,2,'run.started','{\"status\":\"running\"}')",
                    (str(uuid4()), fx["t_a"], fx["w_a1"], rid),
                )
                conn.execute("UPDATE runtime.runs SET next_event_seq=3 WHERE id=%s", (rid,))
            return snapshot

    async def scenario():
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def scoped(t, w, u):
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                yield session

        principal = RuntimePrincipal(
            subject="probe",
            user_id=fx["dev_a"],
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            roles=("developer",),
            scopes=frozenset({"run:read"}),
        )
        try:
            result = await RunEventService(
                session_factory=scoped, event_repository=AppendAfterSnapshotRepository()
            ).get_history(principal=principal, run_id=rid)
            assert max(e.sequence for e in result.events) <= result.head_sequence, (
                f"events={[e.sequence for e in result.events]}, snapshot head={result.head_sequence}"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def canonical_run(pg, fx, *, expired=False):
    rid = str(uuid4())
    digest = "b" * 64
    ref = (
        f"s3://review/tenants/{fx['t_a']}/workspaces/{fx['w_a1']}/runs/{rid}/input/{digest}-10.json"
    )
    with psycopg.connect(pg["owner_url"]) as conn:
        conn.execute(
            """INSERT INTO runtime.runs
            (id,tenant_id,workspace_id,agent_version_id,initiated_by,external_request_key,status,
             created_at,queue_expires_at,request_sha256,input_ref,input_sha256,input_size_bytes)
            VALUES (%s,%s,%s,%s,%s,%s,'queued',clock_timestamp()-interval '2 days',
                    clock_timestamp()+make_interval(secs => %s),%s,%s,%s,10)""",
            (
                rid,
                fx["t_a"],
                fx["w_a1"],
                fx["av_no_grants"],
                fx["dev_a"],
                str(uuid4()),
                -86400 if expired else 86400,
                "a" * 64,
                ref,
                digest,
            ),
        )
        conn.execute(
            "INSERT INTO runtime.jobs (id,tenant_id,workspace_id,run_id,job_type,status,payload_ref) VALUES (%s,%s,%s,%s,'run','queued',%s)",
            (str(uuid4()), fx["t_a"], fx["w_a1"], rid, ref),
        )
    return rid


def test_cancel_cannot_use_revoked_membership_after_precheck(env):
    pg, fx, _, _, _ = seed(env)
    rid = canonical_run(pg, fx)

    async def scenario():
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ready, proceed = asyncio.Event(), asyncio.Event()

        class BarrierAdmissionRepository(RunAdmissionRepository):
            async def verify_active_workspace_membership(self, *args, **kwargs):
                role = await super().verify_active_workspace_membership(*args, **kwargs)
                ready.set()
                await proceed.wait()
                return role

        @asynccontextmanager
        async def scoped(t, w, u):
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                yield session

        principal = RuntimePrincipal(
            subject="probe",
            user_id=fx["dev_a"],
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            roles=("developer",),
            scopes=frozenset({"run:start", "run:read"}),
        )
        service = RunLifecycleService(
            session_factory=scoped, admission_repository=BarrierAdmissionRepository()
        )
        task = asyncio.create_task(service.cancel_run(principal=principal, run_id=rid))
        await asyncio.wait_for(ready.wait(), 3)
        with psycopg.connect(pg["owner_url"]) as conn:
            conn.execute(
                "UPDATE iam.memberships SET status='revoked' WHERE workspace_id=%s AND user_id=%s",
                (fx["w_a1"], fx["dev_a"]),
            )
        proceed.set()
        try:
            result = await asyncio.wait_for(task, 5)
        except RunLifecycleAccessDeniedError as exc:
            assert getattr(exc, "code", None) in {
                "membership_inactive",
                "access_denied",
                "insufficient_scope",
            }
        else:
            pytest.fail(f"Revoked principal still committed cancellation: {result}")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_event_writer_returns_database_timestamp(env):
    pg, fx, _, rid, _ = seed(env, status="running")

    async def scenario():
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                await set_rls_context(
                    session, tenant_id=fx["t_a"], workspace_id=fx["w_a1"], user_id=fx["dev_a"]
                )
                await session.execute(text("SELECT now()"))
                await asyncio.sleep(0.15)
                dto = await RunEventRepository().append_run_event(
                    session,
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    run_id=rid,
                    event_type="run.started",
                    data={"status": "running"},
                )
                stored = (
                    await session.execute(
                        text(
                            "SELECT created_at FROM runtime.run_events WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": dto.eventId},
                    )
                ).scalar_one()
                assert dto.timestamp == stored.isoformat().replace("+00:00", "Z"), (
                    f"writer/outbox={dto.timestamp}; row={stored}"
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_blocked_asgi_send_is_bounded_and_releases_slot(monkeypatch):
    """Controlled ASGI transport fault, NOT a live network backpressure test."""
    import agent_runtime.api.events as api
    from agent_runtime.settings import RuntimeSettings

    caps = api.ConnectionCapManager()
    settings = RuntimeSettings(_env_file=None, sse_send_timeout_seconds=0.1)

    class PreflightService:
        async def get_stream_preflight(self, **kwargs):
            return 0, 0, 1, "running", None, []

    uid, tid, wid, rid = [str(uuid4()) for _ in range(4)]
    principal = RuntimePrincipal(
        subject="probe",
        user_id=uid,
        tenant_id=tid,
        workspace_id=wid,
        roles=("developer",),
        scopes=frozenset({"run:read"}),
        session_id=str(uuid4()),
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_connection_caps", lambda: caps)

    async def scenario():
        blocked, entered = asyncio.Event(), asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body":
                entered.set()
                await blocked.wait()

        async def receive():
            await blocked.wait()
            return {"type": "http.disconnect"}

        response = await api.stream_events(rid, principal, PreflightService(), None, None)
        task = asyncio.create_task(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        )
        await asyncio.wait_for(entered.wait(), 1)
        try:
            done, _ = await asyncio.wait({task}, timeout=0.5)
            assert task in done, "Blocked send outlived configured timeout; slot remains occupied"
            assert caps.counts()["total"] == 0
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_expiry_sweep_skips_locked_row(env, monkeypatch):
    from agent_runtime import maintenance

    pg, fx, _, _, _ = seed(env)
    rid = canonical_run(pg, fx, expired=True)

    async def scenario():
        engine = create_async_engine(_to_async_pg_url(pg["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def scoped(t, w, u=None):
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                yield session

        monkeypatch.setattr(maintenance, "scoped_session", scoped)
        monkeypatch.setattr(maintenance, "get_session_factory", lambda: factory)
        # Shorten only the chosen overall budget, not the database locking behavior.
        monkeypatch.setattr(maintenance, "MAX_MAINTENANCE_TIMEOUT_SECONDS", 2.0)
        with psycopg.connect(pg["owner_url"]) as lock:
            lock.execute("SELECT id FROM runtime.runs WHERE id=%s FOR UPDATE", (rid,))
            task = asyncio.create_task(
                maintenance._run_expire_queued(
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    limit=100,
                    apply=True,
                    start_time=time.monotonic(),
                )
            )
            try:
                done, _ = await asyncio.wait({task}, timeout=1.0)
                assert task in done, (
                    "Sweep waits on row lock beyond overall budget instead of skipping it"
                )
                result = task.result()
                assert result["changed"] == 0
                assert result["skipped_locked"] == 1
                assert result["failed"] == 0
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                lock.rollback()
        await engine.dispose()

    asyncio.run(scenario())
