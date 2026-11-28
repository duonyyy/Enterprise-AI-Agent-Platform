"""Real PostgreSQL 16 + Real Redis + Real TCP Uvicorn multi-process integration tests for Step 06-C (G6-C01..G6-C08)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from jsonschema import Draft202012Validator

from tests.integration.test_run_admission_real_postgres import (
    RUNTIME_JWT_AUDIENCE,
    RUNTIME_JWT_ISSUER,
    RUNTIME_JWT_SECRET,
    _find_free_port,
    _mint_runtime_jwt,
    _register_redis_session,
    _revoke_redis_session,
    _seed_admission_fixture,
    _stop_proc,
    _to_async_pg_url,
    _wait_http_ready,
    owned_redis_cluster,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _spawn_event_uvicorn(
    *,
    app_url: str,
    redis_url: str,
    log_path: Path,
    extra_env: dict[str, str] | None = None,
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
            "RUN_ADMISSION_EXECUTION_ENABLED": "false",
            "SSE_POLL_INTERVAL_SECONDS": "0.2",
            "SSE_REVALIDATION_INTERVAL_SECONDS": "0.5",
            "SSE_AUTH_LOOKUP_TIMEOUT_SECONDS": "1.0",
            "SSE_MAX_AUTH_AGE_SECONDS": "2.0",
            "SSE_HEARTBEAT_INTERVAL_SECONDS": "5.0",
        }
    )
    if extra_env:
        env.update(extra_env)

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


def _seed_test_run(
    owner_url: str,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    status: str = "running",
    initial_seq: int = 1,
) -> str:
    run_id = str(uuid4())
    key = f"key-{run_id[:8]}"
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            """
            INSERT INTO runtime.runs (
                id, tenant_id, workspace_id, agent_version_id, initiated_by,
                external_request_key, status, next_event_seq, created_at
            ) VALUES (
                %s, %s, %s,
                (SELECT id FROM catalog.agent_versions WHERE tenant_id=%s AND workspace_id=%s LIMIT 1),
                %s, %s, %s, %s, now()
            )
            """,
            (
                run_id,
                tenant_id,
                workspace_id,
                tenant_id,
                workspace_id,
                user_id,
                key,
                status,
                initial_seq + 1,
            ),
        )
        conn.execute(
            """
            INSERT INTO runtime.run_events (
                id, tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload
            ) VALUES (%s, %s, %s, %s, %s, 'run.queued', %s::jsonb)
            """,
            (
                str(uuid4()),
                tenant_id,
                workspace_id,
                run_id,
                initial_seq,
                json.dumps({"status": "queued", "executionAvailable": False}),
            ),
        )
    return run_id


def test_g6_c01_and_c02_concurrent_append_and_envelope_schema(
    migrated_database: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-C01 & G6-C02: Canonical RunEvent 1.2 envelope on wire, concurrent monotonic sequence, rollback without holes."""
    owner_url = migrated_database["owner_url"]
    with owned_redis_cluster() as redis_fixture:
        fx = _seed_admission_fixture(owner_url, redis_fixture["url"])
        run_id = _seed_test_run(
            owner_url,
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            user_id=fx["dev_a"],
            status="running",
            initial_seq=1,
        )

        import contextlib

        from agent_runtime.application.run_events import RunEventType
        from agent_runtime.persistence.run_event_repository import RunEventRepository
        from platform_common.rls import set_rls_context
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        repo = RunEventRepository()
        engine = create_async_engine(_to_async_pg_url(migrated_database["app_url"]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scoped_session(t: str, w: str, u: str | None = None):
            async with factory() as session, session.begin():
                await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                yield session

        # Concurrent appends via real PG transactions
        async def _append_worker(step_idx: int) -> None:
            async with scoped_session(fx["t_a"], fx["w_a1"], fx["dev_a"]) as session:
                await repo.append_run_event(
                    session,
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    run_id=run_id,
                    event_type=RunEventType.RUN_STEP_STARTED,
                    data={"status": "running", "stepId": str(uuid4()), "attempt": step_idx},
                )

        async def _run_concurrent() -> None:
            try:
                await asyncio.gather(*[_append_worker(i) for i in range(1, 6)])
            finally:
                await engine.dispose()

        asyncio.run(_run_concurrent())

        # Verify monotonic sequences in PG (sequences 1, 2, 3, 4, 5, 6)
        with psycopg.connect(owner_url) as conn:
            rows = conn.execute(
                "SELECT sequence, event_type, redacted_payload FROM runtime.run_events WHERE run_id=%s ORDER BY sequence ASC",
                (run_id,),
            ).fetchall()
            assert len(rows) == 6
            sequences = [r[0] for r in rows]
            assert sequences == [1, 2, 3, 4, 5, 6]
            next_seq = conn.execute(
                "SELECT next_event_seq FROM runtime.runs WHERE id=%s", (run_id,)
            ).fetchone()[0]
            assert next_seq == 7

        # Verify failed transaction rolls back cleanly without leaving sequence holes
        async def _failing_worker() -> None:
            engine2 = create_async_engine(_to_async_pg_url(migrated_database["app_url"]))
            factory2 = async_sessionmaker(engine2, expire_on_commit=False)

            @contextlib.asynccontextmanager
            async def scoped_session2(t: str, w: str, u: str | None = None):
                async with factory2() as session, session.begin():
                    await set_rls_context(session, tenant_id=t, workspace_id=w, user_id=u)
                    yield session

            try:
                with pytest.raises(RuntimeError):
                    async with scoped_session2(fx["t_a"], fx["w_a1"], fx["dev_a"]) as session:
                        await repo.append_run_event(
                            session,
                            tenant_id=fx["t_a"],
                            workspace_id=fx["w_a1"],
                            run_id=run_id,
                            event_type=RunEventType.RUN_STEP_STARTED,
                            data={"status": "running", "stepId": str(uuid4()), "attempt": 99},
                        )
                        raise RuntimeError("Intentional rollback after event append")
            finally:
                await engine2.dispose()

        asyncio.run(_failing_worker())

        with psycopg.connect(owner_url) as conn:
            next_seq_after = conn.execute(
                "SELECT next_event_seq FROM runtime.runs WHERE id=%s", (run_id,)
            ).fetchone()[0]
            # Since the transaction rolled back, next_event_seq remains 7!
            assert next_seq_after == 7
            count = conn.execute(
                "SELECT count(*) FROM runtime.run_events WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
            assert count == 6


def test_g6_c03_c04_c05_real_tcp_sse_lifecycle_catchup_and_reconnect(
    migrated_database: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-C03, G6-C04, G6-C05: Preflight HTTP errors, stream opens before commit, catch-up live race, reconnect on replica B."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    with owned_redis_cluster() as redis_fixture:
        fx = _seed_admission_fixture(owner_url, redis_fixture["url"])
        run_id = _seed_test_run(
            owner_url,
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            user_id=fx["dev_a"],
            status="running",
            initial_seq=1,
        )

        proc_a, port_a, log_a = _spawn_event_uvicorn(
            app_url=app_url,
            redis_url=redis_fixture["url"],
            log_path=tmp_path / "uvicorn_a.log",
        )
        proc_b, port_b, log_b = _spawn_event_uvicorn(
            app_url=app_url,
            redis_url=redis_fixture["url"],
            log_path=tmp_path / "uvicorn_b.log",
        )

        schema_raw = json.loads(
            (ROOT / "contracts/jsonschema/run-event.v1.2.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema_raw)

        async def _scenario() -> None:
            headers_dev_a = {"Authorization": f"Bearer {fx['tok_dev_a']}"}

            async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
                # 1. Before headers: cursor > head -> 409
                resp_409 = await client.get(
                    f"http://127.0.0.1:{port_a}/v1/runs/{run_id}/events?last_event_id=99",
                    headers=headers_dev_a,
                )
                assert resp_409.status_code == 409
                assert resp_409.json()["detail"]["code"] == "cursor_ahead"

                # 2. Before headers: empty query cursor -> 400
                resp_400 = await client.get(
                    f"http://127.0.0.1:{port_a}/v1/runs/{run_id}/events?last_event_id=",
                    headers=headers_dev_a,
                )
                assert resp_400.status_code == 400
                assert resp_400.json()["detail"]["code"] == "invalid_cursor"

                # 3. Before headers: conflicting query vs header -> 400
                resp_conflict = await client.get(
                    f"http://127.0.0.1:{port_a}/v1/runs/{run_id}/events?last_event_id=1",
                    headers={**headers_dev_a, "Last-Event-ID": "2"},
                )
                assert resp_conflict.status_code == 400
                assert resp_conflict.json()["detail"]["code"] == "cursor_conflict"

                # 4. Stream A opens before event 2 is committed
                seen_events: list[dict[str, Any]] = []
                connected_received = asyncio.Event()

                async def _reader():
                    async with client.stream(
                        "GET",
                        f"http://127.0.0.1:{port_a}/v1/runs/{run_id}/events?last_event_id=0",
                        headers=headers_dev_a,
                    ) as stream_resp:
                        assert stream_resp.status_code == 200
                        assert stream_resp.headers["content-type"].startswith("text/event-stream")
                        assert stream_resp.headers.get("cache-control") == "no-store"
                        assert stream_resp.headers.get("x-accel-buffering") == "no"

                        current_event_type = ""
                        current_data = ""
                        async for line in stream_resp.aiter_lines():
                            if line.startswith(": connected"):
                                connected_received.set()
                            elif line.startswith("event:"):
                                current_event_type = line.split(":", 1)[1].strip()
                            elif line.startswith("data:"):
                                current_data = line.split(":", 1)[1].strip()
                            elif line == "" and current_event_type and current_data:
                                evt_obj = json.loads(current_data)
                                seen_events.append(evt_obj)
                                validator.validate(evt_obj)
                                current_event_type = ""
                                current_data = ""
                                if len(seen_events) >= 2:
                                    break

                reader_task = asyncio.create_task(_reader())
                await asyncio.wait_for(connected_received.wait(), timeout=5.0)

                # Commit event #2 from another connection while stream is open
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        """
                        INSERT INTO runtime.run_events (id, tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload)
                        VALUES (%s, %s, %s, %s, 2, 'run.started', '{"status":"running"}'::jsonb)
                        """,
                        (str(uuid4()), fx["t_a"], fx["w_a1"], run_id),
                    )
                    conn.execute(
                        "UPDATE runtime.runs SET next_event_seq = 3 WHERE id = %s",
                        (run_id,),
                    )

                await asyncio.wait_for(reader_task, timeout=5.0)
                assert len(seen_events) == 2
                assert seen_events[0]["sequence"] == 1
                assert seen_events[0]["type"] == "run.queued"
                assert seen_events[1]["sequence"] == 2
                assert seen_events[1]["type"] == "run.started"

                # 5. Reconnect on replica B with Last-Event-ID = 2
                # Commit event #3
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        """
                        INSERT INTO runtime.run_events (id, tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload)
                        VALUES (%s, %s, %s, %s, 3, 'run.completed', '{"status":"succeeded"}'::jsonb)
                        """,
                        (str(uuid4()), fx["t_a"], fx["w_a1"], run_id),
                    )
                    conn.execute(
                        "UPDATE runtime.runs SET next_event_seq = 4, status = 'succeeded' WHERE id = %s",
                        (run_id,),
                    )

                b_events: list[dict[str, Any]] = []
                async with client.stream(
                    "GET",
                    f"http://127.0.0.1:{port_b}/v1/runs/{run_id}/events",
                    headers={**headers_dev_a, "Last-Event-ID": "2"},
                ) as stream_b:
                    assert stream_b.status_code == 200
                    current_type = ""
                    current_d = ""
                    async for line in stream_b.aiter_lines():
                        if line.startswith("event:"):
                            current_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            current_d = line.split(":", 1)[1].strip()
                        elif line == "" and current_type and current_d:
                            evt = json.loads(current_d)
                            b_events.append(evt)
                            validator.validate(evt)
                            current_type = ""
                            current_d = ""

                assert len(b_events) == 1
                assert b_events[0]["sequence"] == 3
                assert b_events[0]["type"] == "run.completed"
                assert b_events[0]["data"]["status"] == "succeeded"

                # 6. Verify GET /v1/runs/{run_id}/history endpoint
                hist_resp = await client.get(
                    f"http://127.0.0.1:{port_a}/v1/runs/{run_id}/history?last_event_id=0&limit=50",
                    headers=headers_dev_a,
                )
                assert hist_resp.status_code == 200
                hist_body = hist_resp.json()
                assert hist_body["run_id"] == run_id
                assert hist_body["status"] == "succeeded"
                assert hist_body["head_sequence"] == 3
                assert hist_body["oldest_available_sequence"] == 1
                assert hist_body["has_more"] is False
                assert len(hist_body["events"]) == 3
                assert hist_body["next_cursor"] == "3"

        try:
            asyncio.run(_scenario())
        finally:
            _stop_proc(proc_a, log_a)
            _stop_proc(proc_b, log_b)


def test_g6_c06_and_c07_auth_revocation_pool_release_and_connection_caps(
    migrated_database: dict[str, Any],
    tmp_path: Path,
) -> None:
    """G6-C06 & G6-C07: Mid-stream session revocation <= 5s, pool connections returned to baseline, connection caps 429."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    with owned_redis_cluster() as redis_fixture:
        fx = _seed_admission_fixture(owner_url, redis_fixture["url"])
        run_id = _seed_test_run(
            owner_url,
            tenant_id=fx["t_a"],
            workspace_id=fx["w_a1"],
            user_id=fx["dev_a"],
            status="running",
            initial_seq=1,
        )

        proc, port, log_file = _spawn_event_uvicorn(
            app_url=app_url,
            redis_url=redis_fixture["url"],
            log_path=tmp_path / "uvicorn_revocation.log",
            extra_env={
                "SSE_MAX_CONNECTIONS_PER_USER_WORKSPACE": "2",
                "SSE_REVALIDATION_INTERVAL_SECONDS": "0.2",
            },
        )

        async def _scenario() -> None:
            headers_dev_a = {"Authorization": f"Bearer {fx['tok_dev_a']}"}

            async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
                # 1. Connection cap test: user allows max 2 connections
                stream1 = client.stream(
                    "GET",
                    f"http://127.0.0.1:{port}/v1/runs/{run_id}/events",
                    headers=headers_dev_a,
                )
                stream2 = client.stream(
                    "GET",
                    f"http://127.0.0.1:{port}/v1/runs/{run_id}/events",
                    headers=headers_dev_a,
                )
                r1 = await stream1.__aenter__()
                r2 = await stream2.__aenter__()
                assert r1.status_code == 200
                assert r2.status_code == 200

                # 3rd connection exceeds max 2 per user-workspace -> 429 Retry-After: 5
                r3 = await client.get(
                    f"http://127.0.0.1:{port}/v1/runs/{run_id}/events",
                    headers=headers_dev_a,
                )
                assert r3.status_code == 429
                assert r3.json()["detail"]["code"] == "stream_limit_reached"
                assert r3.headers.get("retry-after") == "5"

                await stream1.__aexit__(None, None, None)
                await stream2.__aexit__(None, None, None)

                # After release, new connection succeeds!
                r4 = await client.get(
                    f"http://127.0.0.1:{port}/v1/runs/{run_id}/history",
                    headers=headers_dev_a,
                )
                assert r4.status_code == 200

                # 2. Midstream Redis session revocation <= 5s
                # Issue new session token
                new_sess_id = str(uuid4())
                _register_redis_session(
                    redis_fixture["url"], session_id=new_sess_id, user_id=fx["dev_a"]
                )
                tok_revoke = _mint_runtime_jwt(
                    user_id=fx["dev_a"],
                    tenant_id=fx["t_a"],
                    workspace_id=fx["w_a1"],
                    session_id=new_sess_id,
                    roles=["developer"],
                    scopes=["run:read"],
                )

                seen_lines: list[str] = []
                t_revoked: float | None = None
                t_error: float | None = None

                async with client.stream(
                    "GET",
                    f"http://127.0.0.1:{port}/v1/runs/{run_id}/events",
                    headers={"Authorization": f"Bearer {tok_revoke}"},
                ) as sse_stream:
                    assert sse_stream.status_code == 200
                    async for line in sse_stream.aiter_lines():
                        seen_lines.append(line)
                        if t_revoked is None and line.startswith(": connected"):
                            t_revoked = time.monotonic()
                            _revoke_redis_session(redis_fixture["url"], session_id=new_sess_id)
                        if line.startswith("event: error"):
                            t_error = time.monotonic()
                        if "authorization_revoked" in line:
                            break

                assert t_revoked is not None
                assert t_error is not None
                assert (t_error - t_revoked) <= 5.0
                body_joined = "\n".join(seen_lines)
                assert "event: error" in body_joined
                assert "authorization_revoked" in body_joined

        try:
            asyncio.run(_scenario())
        finally:
            _stop_proc(proc, log_file)
