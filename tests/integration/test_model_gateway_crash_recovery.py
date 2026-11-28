"""R05D-03/04: real Gateway subprocess crashes, provider acceptance and pg_dump/pg_restore."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from agent_runtime.llm.budget_ledger import PostgresModelBudgetLedger
from agent_runtime.llm.errors import SettlementConflictError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_model_gateway_pipeline import (
    _async_url,
    _principal,
    _profile,
    _seed_scope,
    gateway_redis,
    real_parent_sessions,
)

__all__ = ["gateway_redis", "real_parent_sessions"]

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


class Provider:
    """A scripted provider, never live LLM evidence. Receipt is retained in the parent."""

    def __init__(self, hold=False):
        self.received = threading.Event()
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.calls = []
        self.receipts = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                request_id = self.headers["X-Request-ID"]
                outer.calls.append(request_id)
                receipt = {"prompt_tokens": 5, "completion_tokens": 2}
                outer.receipts[request_id] = receipt
                outer.received.set()
                if not outer.release.wait(20):
                    return
                payload = json.dumps(
                    {
                        "model": body["model"],
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Synthetic provider response.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": receipt,
                    }
                ).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected only after the owned client process is killed.

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def packet(database, redis_client, tmp_path, provider, scope=None):
    profile = _profile(max_output=64)
    scope = scope or _seed_scope(database["owner_url"], profile=profile)
    identity = _principal(scope)
    request_id = str(uuid4())
    route = tmp_path / f"{request_id}.routes.json"
    route.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "model_config_id": str(scope["model_id"]),
                        "profile": profile.model_dump(mode="json"),
                        "adapter": "openai-chat",
                        "endpoint_url": provider.endpoint,
                        "utf8_bound_verified": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    content = {
        "scope": {k: str(v) for k, v in scope.items()},
        "request_id": request_id,
        "routes_path": str(route),
        "principal": {
            "subject": identity.subject,
            "user_id": identity.user_id,
            "tenant_id": identity.tenant_id,
            "workspace_id": identity.workspace_id,
            "roles": list(identity.roles),
            "scopes": list(identity.scopes),
            "token_scopes": list(identity.token_scopes),
            "session_id": identity.session_id,
        },
    }
    path = tmp_path / f"{request_id}.packet.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path, scope, request_id


class Child:
    def __init__(self, path, database, redis_client, *, stop="", action="generate"):
        redis_kwargs = redis_client.connection_pool.connection_kwargs
        env = {
            **os.environ,
            "MODEL_CRASH_DATABASE_URL": _async_url(database["app_url"]),
            "MODEL_CRASH_REDIS_URL": f"redis://{redis_kwargs['host']}:{redis_kwargs['port']}/0",
            "PYTHONPATH": os.pathsep.join(
                [
                    str(ROOT),
                    str(ROOT / "apps/agent-runtime/src"),
                    str(ROOT / "libs/python/agent-contracts/src"),
                    str(ROOT / "libs/python/platform-common/src"),
                ]
            ),
        }
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(Path(__file__).with_name("model_crash_worker.py")),
                "--packet",
                str(path),
                "--stop",
                stop,
                "--action",
                action,
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.lines = queue.Queue()

        def read():
            for line in self.proc.stdout:
                self.lines.put(line.strip())
            self.lines.put(None)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def wait(self, prefix):
        import time

        end = time.monotonic() + 20
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise AssertionError("Owned Gateway child did not reach the bounded stage")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError("Owned Gateway child timed out") from None
            if line is None:
                raise AssertionError("Gateway child exited before the expected stage")
            if line.startswith(prefix):
                return line[len(prefix) :]

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)

    def close(self):
        self.kill()
        self.reader.join(timeout=5)
        self.proc.stdout.close()


def row(database, request_id):
    with psycopg.connect(database["owner_url"]) as conn:
        return conn.execute(
            """SELECT r.id,r.status,r.reserved_cost_usd,r.pricing_snapshot,
            a.id,a.status,b.reserved_usd,b.spent_usd,
            (SELECT count(*) FROM ops.model_usage u WHERE u.reservation_id=r.id)
            FROM ops.model_reservations r JOIN ops.model_attempts a ON a.reservation_id=r.id
            JOIN ops.workspace_budgets b ON b.id=r.budget_id WHERE r.operation_key=%s""",
            (request_id,),
        ).fetchone()


def expire(database, reservation_id):
    with psycopg.connect(database["owner_url"]) as conn:
        conn.execute(
            "UPDATE ops.model_reservations SET expires_at=now()-interval '1 second' WHERE id=%s",
            (reservation_id,),
        )


def claim(database, scope):
    async def call():
        engine = create_async_engine(_async_url(database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                return await PostgresModelBudgetLedger().claim_unknown_attempts(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    worker_id="fixture-recovery",
                    limit=100,
                    lease_for=timedelta(minutes=1),
                )
        finally:
            await engine.dispose()

    return asyncio.run(call())


@contextmanager
def crashed(database, redis_client, tmp_path, stage, scope=None):
    provider = Provider(hold=stage == "accepted")
    path, scope, request_id = packet(database, redis_client, tmp_path, provider, scope)
    child = Child(path, database, redis_client, stop="" if stage == "accepted" else stage)
    try:
        if stage == "accepted":
            assert provider.received.wait(20), "Provider did not accept the real TCP request"
        else:
            child.wait("BARRIER:" + stage)
        child.kill()
        expected = 0 if stage in {"reserved", "dispatched"} else 1
        assert len(provider.calls) == expected
        if expected:
            assert provider.calls == [request_id]
        yield provider, path, scope, request_id
    finally:
        child.close()
        provider.close()


@pytest.mark.parametrize("stage", ["reserved", "dispatched", "accepted", "response", "settled"])
def test_gateway_process_crash_preserves_dispatch_and_budget(
    migrated_database, gateway_redis, tmp_path, stage
):
    with crashed(migrated_database, gateway_redis, tmp_path, stage) as (provider, path, scope, key):
        before = row(migrated_database, key)
        expected = (
            "reserved"
            if stage == "reserved"
            else ("settled" if stage == "settled" else "dispatched")
        )
        assert before[1] == expected and before[5] == expected
        assert before[8] == (1 if stage == "settled" else 0)
        if stage == "settled":
            assert before[6] == 0 and before[7] > 0
        else:
            assert before[6] == before[2] and before[7] == 0
        if stage == "reserved":

            async def release():
                engine = create_async_engine(
                    _async_url(migrated_database["app_url"]), poolclass=NullPool
                )
                factory = async_sessionmaker(engine, expire_on_commit=False)
                try:
                    async with factory() as session, session.begin():
                        await PostgresModelBudgetLedger().release_before_dispatch(
                            session,
                            tenant_id=scope["tenant_id"],
                            workspace_id=scope["workspace_id"],
                            reservation_id=before[0],
                        )
                finally:
                    await engine.dispose()

            asyncio.run(release())
            assert row(migrated_database, key)[1] == "released"
        elif stage != "settled":
            expire(migrated_database, before[0])
            leases = claim(migrated_database, scope)
            assert [x.reservation_id for x in leases] == [before[0]]
            recovered = row(migrated_database, key)
            assert recovered[1] == "unknown" and recovered[6] == before[2] and recovered[8] == 0
        calls = len(provider.calls)
        replay = Child(path, migrated_database, gateway_redis, action="replay")
        try:
            assert json.loads(replay.wait("RESULT:"))["outcome"] == "replay_conflict"
        finally:
            replay.close()
        assert len(provider.calls) == calls
        assert row(migrated_database, key)[8] == before[8]


def test_competing_reconcilers_claim_one_attempt_once(migrated_database, gateway_redis, tmp_path):
    with crashed(migrated_database, gateway_redis, tmp_path, "response") as (
        provider,
        _,
        scope,
        key,
    ):
        before = row(migrated_database, key)
        expire(migrated_database, before[0])

        async def compete():
            engine = create_async_engine(
                _async_url(migrated_database["app_url"]), poolclass=NullPool
            )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            barrier = asyncio.Barrier(2)

            async def worker(worker_id):
                async with factory() as session, session.begin():
                    await barrier.wait()
                    return await PostgresModelBudgetLedger().claim_unknown_attempts(
                        session,
                        tenant_id=scope["tenant_id"],
                        workspace_id=scope["workspace_id"],
                        worker_id=worker_id,
                        limit=1,
                        lease_for=timedelta(minutes=1),
                    )

            try:
                results = await asyncio.wait_for(
                    asyncio.gather(worker("race-a"), worker("race-b")), 10
                )
                assert sorted(len(result) for result in results) == [0, 1]
                assert [lease.reservation_id for result in results for lease in result] == [
                    before[0]
                ]
            finally:
                await engine.dispose()

        asyncio.run(compete())
        assert row(migrated_database, key)[1] == "unknown"
        assert row(migrated_database, key)[6] == before[2]
        assert provider.calls == [key]


def test_receipt_reconciliation_fences_both_stale_settle_and_release(
    migrated_database, gateway_redis, tmp_path
):
    with crashed(migrated_database, gateway_redis, tmp_path, "response") as (
        provider,
        _path,
        scope,
        key,
    ):
        before = row(migrated_database, key)
        expire(migrated_database, before[0])
        first = claim(migrated_database, scope)[0]
        with psycopg.connect(migrated_database["owner_url"]) as conn:
            conn.execute(
                "UPDATE ops.model_attempts SET reconciliation_lease_until=now()-interval '1 second' WHERE id=%s",
                (first.attempt_id,),
            )
        second = claim(migrated_database, scope)[0]
        assert second.fence > first.fence
        receipt = provider.receipts[key]
        # Evidence is explicitly the owned scripted provider's retained receipt.
        cost = (
            Decimal(before[3]["cost_per_prompt_token_usd"]) * receipt["prompt_tokens"]
            + Decimal(before[3]["cost_per_completion_token_usd"]) * receipt["completion_tokens"]
        )

        async def reconcile():
            engine = create_async_engine(
                _async_url(migrated_database["app_url"]), poolclass=NullPool
            )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            ledger = PostgresModelBudgetLedger()
            common = {
                "tenant_id": scope["tenant_id"],
                "workspace_id": scope["workspace_id"],
                "evidence_ref": "fixture://retained-provider-receipt",
            }
            settlement = {
                "provider": "test-provider",
                "model_id": "test-model",
                "input_tokens": receipt["prompt_tokens"],
                "output_tokens": receipt["completion_tokens"],
                "usage_source": "provider",
                "cost_usd": cost,
            }
            try:
                for method in ("release", "settle"):
                    async with factory() as session:
                        with pytest.raises(SettlementConflictError):
                            if method == "release":
                                await ledger.release_unknown_after_reconciliation(
                                    session, lease=first, worker_id="fixture-recovery", **common
                                )
                            else:
                                await ledger.settle_unknown_after_reconciliation(
                                    session,
                                    lease=first,
                                    worker_id="fixture-recovery",
                                    **common,
                                    **settlement,
                                )
                        await session.rollback()
                async with factory() as session, session.begin():
                    await ledger.settle_unknown_after_reconciliation(
                        session, lease=second, worker_id="fixture-recovery", **common, **settlement
                    )
                async with factory() as session:
                    with pytest.raises(SettlementConflictError):
                        await ledger.settle_unknown_after_reconciliation(
                            session,
                            lease=first,
                            worker_id="fixture-recovery",
                            **common,
                            **settlement,
                        )
            finally:
                await engine.dispose()

        asyncio.run(reconcile())
        after = row(migrated_database, key)
        assert after[1] == "settled" and after[8] == 1 and after[6] == 0 and after[7] == cost
        assert provider.calls == [key]


def test_pg_dump_restore_and_new_gateway_process_preserve_all_states(
    migrated_database, postgres_cluster, gateway_redis, tmp_path
):
    from scripts.test_postgres_fixture import (
        backup_database,
        database_url,
        restore_database,
        temporary_database,
    )
    from scripts.validate_database_schema import EXPECTED_MIGRATIONS

    provider = Provider()
    scope = _seed_scope(migrated_database["owner_url"], profile=_profile(max_output=64))
    packets = {}
    try:
        for state, stage in [
            ("reserved", "reserved"),
            ("dispatched", "dispatched"),
            ("unknown", "accepted"),
            ("settled", "settled"),
        ]:
            with crashed(migrated_database, gateway_redis, tmp_path, stage, scope) as (
                _provider,
                path,
                _scope,
                key,
            ):
                if state == "unknown":
                    expire(migrated_database, row(migrated_database, key)[0])
                    claim(migrated_database, scope)
                    with psycopg.connect(migrated_database["owner_url"]) as conn:
                        conn.execute(
                            "UPDATE ops.model_attempts SET reconciliation_lease_until=now()-interval '1 second' WHERE reservation_id=%s",
                            (row(migrated_database, key)[0],),
                        )
                # Use a live owned endpoint for replay; initial endpoints are now
                # stopped, so accidental re-dispatch is observable and fails counts.
                route = path.with_name(path.stem.replace(".packet", "") + ".routes.json")
                config = json.loads(route.read_text())
                config["routes"][0]["endpoint_url"] = provider.endpoint
                route.write_text(json.dumps(config))
                packets[state] = (path, key, row(migrated_database, key))
        # Budget totals are shared by all four operations. Snapshot only after
        # all seed operations have reached their durable states.
        packets = {
            state: (path, key, row(migrated_database, key))
            for state, (path, key, _) in packets.items()
        }
        backup = tmp_path / "model-ledger.dump"
        backup_database(postgres_cluster, backup)
        assert backup.stat().st_size > 0
        with temporary_database(postgres_cluster, "modelrestore") as target:
            restore_database(postgres_cluster, backup, target)
            restored = {
                **migrated_database,
                "owner_url": database_url(postgres_cluster, target),
                "app_url": database_url(postgres_cluster, target, app=True),
            }
            for state, (path, key, before) in packets.items():
                after = row(restored, key)
                assert after == before
                child = Child(path, restored, gateway_redis, action="replay")
                try:
                    assert json.loads(child.wait("RESULT:"))["outcome"] == "replay_conflict"
                finally:
                    child.close()
            assert provider.calls == []
            # Recover restored unknown + expired dispatched in a new OS process.
            expire(restored, packets["dispatched"][2][0])
            child = Child(packets["unknown"][0], restored, gateway_redis, action="claim")
            try:
                leases = json.loads(child.wait("RESULT:"))["leases"]
                assert set(leases) == {str(packets[s][2][0]) for s in ("unknown", "dispatched")}
            finally:
                child.close()
            with psycopg.connect(restored["app_url"]) as conn:
                conn.execute(
                    "SELECT set_config('app.tenant_id',%s,true),set_config('app.workspace_id',%s,true)",
                    (str(scope["tenant_id"]), str(scope["workspace_id"])),
                )
                for table, count in [
                    ("model_reservations", 4),
                    ("model_attempts", 4),
                    ("model_usage", 1),
                ]:
                    assert conn.execute(f"SELECT count(*) FROM ops.{table}").fetchone()[0] == count
                conn.execute(
                    "SELECT set_config('app.tenant_id',%s,true),set_config('app.workspace_id',%s,true)",
                    (str(uuid4()), str(uuid4())),
                )
                for table in (
                    "model_reservations",
                    "model_attempts",
                    "model_usage",
                    "workspace_budgets",
                ):
                    assert conn.execute(f"SELECT count(*) FROM ops.{table}").fetchone()[0] == 0
            with psycopg.connect(restored["owner_url"]) as conn:
                restored_history = conn.execute(
                    "SELECT name FROM public.schema_migrations ORDER BY version"
                ).fetchall()
                assert [record[0] for record in restored_history] == list(EXPECTED_MIGRATIONS)
    finally:
        provider.close()
