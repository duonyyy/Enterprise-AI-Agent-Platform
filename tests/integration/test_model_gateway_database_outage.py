"""Owned loopback PostgreSQL outage after durable dispatch; no shared service is stopped."""

from __future__ import annotations

import asyncio
import socket
import threading
from uuid import uuid4

import httpx
import psycopg
import pytest
from agent_contracts.model_contracts import ModelGenerationRequest, ModelMessage
from agent_runtime.llm.adapters.http_provider import HttpModelAdapter
from agent_runtime.llm.budget_ledger import PostgresModelBudgetLedger
from agent_runtime.llm.errors import ModelTimeoutError
from agent_runtime.llm.gateway import ModelGateway
from agent_runtime.llm.profile import TokenProfiler
from agent_runtime.llm.secrets import ScopedSecretResolver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_model_gateway_crash_recovery import claim, expire, row
from tests.integration.test_model_gateway_pipeline import (
    _async_url,
    _authority,
    _principal,
    _seed_scope,
    gateway_redis,
    real_parent_sessions,
)

__all__ = ["gateway_redis", "real_parent_sessions"]
pytestmark = pytest.mark.integration


class OwnedRelay:
    """Drop only this test's sockets, retaining the fixture database for recovery."""

    def __init__(self, host, port):
        self.target = (host, port)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []
        self.pipes = []
        self.acceptor = threading.Thread(target=self.accept, daemon=True)
        self.acceptor.start()

    def pipe(self, source, target):
        try:
            while not self.stopped.is_set():
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        finally:
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def accept(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                upstream = socket.create_connection(self.target, timeout=1)
                upstream.settimeout(None)
            except OSError:
                client.close()
                continue
            with self.lock:
                if self.stopped.is_set():
                    client.close()
                    upstream.close()
                    break
                self.sockets.extend((client, upstream))
                for source, target in ((client, upstream), (upstream, client)):
                    thread = threading.Thread(target=self.pipe, args=(source, target), daemon=True)
                    self.pipes.append(thread)
                    thread.start()

    def cut(self):
        self.stopped.set()
        self.listener.close()
        with self.lock:
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            self.sockets.clear()

    def close(self):
        self.cut()
        self.acceptor.join(timeout=2)
        for thread in self.pipes:
            thread.join(timeout=2)
        assert not self.acceptor.is_alive()
        assert all(not thread.is_alive() for thread in self.pipes)


def test_post_dispatch_database_disconnect_preserves_hold_and_recovers(
    migrated_database,
    gateway_redis,
):
    scope = _seed_scope(migrated_database["owner_url"])
    parts = psycopg.conninfo.conninfo_to_dict(migrated_database["app_url"])
    relay = OwnedRelay(parts["host"], int(parts["port"]))
    relay_dsn = psycopg.conninfo.make_conninfo(
        migrated_database["app_url"], host="127.0.0.1", port=relay.port
    )
    request_id = uuid4()
    calls = []

    async def check():
        engine = create_async_engine(
            _async_url(relay_dsn),
            poolclass=NullPool,
            connect_args={"timeout": 1, "command_timeout": 1},
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def provider(request):
            calls.append(request)
            assert row(migrated_database, str(request_id))[1] == "dispatched"
            relay.cut()
            raise httpx.ReadTimeout("Owned provider timeout during database outage")

        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
                gateway = ModelGateway(
                    budget_ledger=PostgresModelBudgetLedger(),
                    secret_resolver=ScopedSecretResolver(),
                    token_profiler=TokenProfiler(
                        validated_utf8_models=frozenset({("test-provider", "test-model")})
                    ),
                    generation_adapter=HttpModelAdapter(
                        client, default_base_url="https://api.testprovider.com/v1"
                    ),
                    authority=_authority(scope, factory),
                    timeout_seconds=3,
                )
                request = ModelGenerationRequest(
                    requestId=request_id,
                    tenantId=scope["tenant_id"],
                    workspaceId=scope["workspace_id"],
                    modelConfigId=scope["model_id"],
                    requestedProvider="test-provider",
                    requestedModel="test-model",
                    messages=[ModelMessage(role="user", content="outage")],
                )
                async with factory() as session:
                    with pytest.raises(ModelTimeoutError):
                        await asyncio.wait_for(
                            gateway.generate(
                                session,
                                request,
                                principal=_principal(scope),
                                run_id=scope["run_id"],
                            ),
                            timeout=10,
                        )
        finally:
            await engine.dispose()

    try:
        asyncio.run(check())
    finally:
        relay.close()
    before = row(migrated_database, str(request_id))
    assert len(calls) == 1
    assert before[1] == "dispatched" and before[5] == "dispatched"
    assert before[6] == before[2] and before[7] == 0 and before[8] == 0
    expire(migrated_database, before[0])
    leases = claim(migrated_database, scope)
    assert [lease.reservation_id for lease in leases] == [before[0]]
    after = row(migrated_database, str(request_id))
    assert after[1] == "unknown" and after[6] == before[2] and after[8] == 0
