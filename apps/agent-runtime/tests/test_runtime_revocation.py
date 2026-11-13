"""Unit and Security Tests for Runtime Live Authorization & Bounded SSE Revocation (Step 03-D)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from agent_runtime.auth import (
    RuntimePrincipal,
    close_shared_parent_session_verifier,
    get_parent_session_verifier,
    get_runtime_principal,
    set_parent_session_verifier,
)
from agent_runtime.main import app
from agent_runtime.persistence.database import get_db_session
from agent_runtime.settings import RuntimeSettings
from agent_runtime.tools import ToolDispatcher, ToolInvocation, ToolPolicyDeniedError
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy.exc import OperationalError


class _Mappings:
    def __init__(self, item: Any = None, items: list[Any] | None = None) -> None:
        self._item = item
        self._items = items or []

    def one_or_none(self) -> Any:
        return self._item

    def all(self) -> list[Any]:
        return self._items


def test_sse_stream_closes_promptly_on_midstream_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_run_sse_midstream_revocation(monkeypatch))


async def _run_sse_midstream_revocation(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    user_id = str(uuid4())
    run_id = str(uuid4())
    session_id = str(uuid4())

    state: dict[str, Any] = {
        "session_status": "OK",
        "membership_active": True,
        "role": "viewer",
        "events": [
            {
                "sequence": 1,
                "event_type": "run.started",
                "redacted_payload": {"step": 1},
                "created_at": datetime.now(UTC),
            }
        ],
        "sessions_opened": 0,
        "sessions_closed": 0,
        "raise_db": False,
        "hang_db": False,
    }

    class FakeVerifier:
        def verify_parent_session(self, session_id: str, user_id: str, now: int | None = None):
            _ = (session_id, user_id, now)
            return state["session_status"], {"session_id": session_id}

    set_parent_session_verifier(FakeVerifier())

    @asynccontextmanager
    async def fake_scoped_session(t_id: str, w_id: str, u_id: str | None = None):
        _ = (t_id, w_id, u_id)
        state["sessions_opened"] += 1
        session = AsyncMock()

        async def execute(stmt: Any, params: dict[str, Any] | None = None):
            sql = " ".join(str(stmt).split())
            params = params or {}
            if state["hang_db"] and "resolve_user_workspace_membership" in sql:
                await asyncio.sleep(5.0)
            if state["raise_db"]:
                raise OperationalError(sql, params, Exception("db offline"))
            res = MagicMock()
            if "iam.resolve_user_workspace_membership" in sql:
                if not state["membership_active"]:
                    res.mappings.return_value = _Mappings(item=None)
                else:
                    res.mappings.return_value = _Mappings(
                        item={
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "role": state["role"],
                            "tenant_status": "active",
                            "workspace_status": "active",
                        }
                    )
                return res
            if (
                "SELECT id, status FROM runtime.runs" in sql
                or "SELECT status FROM runtime.runs" in sql
            ):
                res.mappings.return_value = _Mappings(item={"id": run_id, "status": "running"})
                return res
            if "FROM runtime.run_events" in sql:
                cursor_seq = int(params.get("cursor_seq", 0))
                matching = [e for e in state["events"] if e["sequence"] > cursor_seq]
                res.mappings.return_value = _Mappings(items=matching)
                return res
            raise AssertionError(f"Unexpected SQL: {sql}")

        session.execute = AsyncMock(side_effect=execute)
        try:
            yield session
        finally:
            state["sessions_closed"] += 1

    import agent_runtime.api.events as events_mod

    monkeypatch.setattr(events_mod, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(
        events_mod,
        "get_settings",
        lambda: RuntimeSettings(
            environment="test",
            sse_revalidation_interval_seconds=0.1,
            sse_auth_lookup_timeout_seconds=0.2,
            sse_max_auth_age_seconds=0.5,
        ),
    )

    principal = RuntimePrincipal(
        subject="sub-1",
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        roles=("viewer",),
        scopes=frozenset({"run:read"}),
        session_id=session_id,
        token_scopes=frozenset({"run:read"}),
    )

    async def override_principal() -> RuntimePrincipal:
        return principal

    async def override_db():
        yield AsyncMock()

    app.dependency_overrides[get_runtime_principal] = override_principal
    app.dependency_overrides[get_db_session] = override_db

    try:
        # Inject event #2 + revoke membership concurrently while the SSE loop is open
        # (httpx.ASGITransport buffers ASGI body until generator completion).
        async def revoke_midstream() -> None:
            for _ in range(50):
                if (
                    state["sessions_opened"] >= 3
                    and state["sessions_opened"] == state["sessions_closed"]
                ):
                    break
                await asyncio.sleep(0.02)
            state["events"].append(
                {
                    "sequence": 2,
                    "event_type": "run.secret_step",
                    "redacted_payload": {"secret": "must-never-emit"},
                    "created_at": datetime.now(UTC),
                }
            )
            state["membership_active"] = False

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://runtime.test") as client:
            revoker = asyncio.create_task(revoke_midstream())
            try:
                async with asyncio.timeout(3.0):
                    async with client.stream("GET", f"/v1/runs/{run_id}/events") as response:
                        assert response.status_code == 200
                        text_body = (await response.aread()).decode("utf-8")
            finally:
                await revoker

        assert state["sessions_opened"] == state["sessions_closed"]
        # Revocation may win after the event page is read but before its per-event
        # authorization check. In that safe outcome even the pre-existing event is
        # withheld; the real TCP integration test separately proves pre-revoke delivery.
        assert f": connected to run {run_id}" in text_body
        assert "run.secret_step" not in text_body
        assert "event: error" in text_body
        assert "authorization_revoked" in text_body
    finally:
        app.dependency_overrides.clear()
        set_parent_session_verifier(None)


def test_sse_stream_fails_closed_on_auth_lookup_timeout_or_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_run_sse_timeout_or_db_error(monkeypatch))


async def _run_sse_timeout_or_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    user_id = str(uuid4())
    run_id = str(uuid4())
    session_id = str(uuid4())

    state = {"hang": False, "err": False, "polled": 0}

    class FakeVerifier:
        def verify_parent_session(self, session_id: str, user_id: str, now: int | None = None):
            _ = (session_id, user_id, now)
            return "OK", {"session_id": session_id}

    set_parent_session_verifier(FakeVerifier())

    @asynccontextmanager
    async def fake_scoped_session(t_id: str, w_id: str, u_id: str | None = None):
        _ = (t_id, w_id, u_id)
        session = AsyncMock()

        async def execute(stmt: Any, params: dict[str, Any] | None = None):
            sql = " ".join(str(stmt).split())
            res = MagicMock()
            if "iam.resolve_user_workspace_membership" in sql:
                state["polled"] += 1
                if state["hang"]:
                    await asyncio.sleep(2.0)
                if state["err"]:
                    raise OperationalError(sql, params or {}, Exception("down"))
                res.mappings.return_value = _Mappings(
                    item={
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "role": "viewer",
                        "tenant_status": "active",
                        "workspace_status": "active",
                    }
                )
                return res
            res.mappings.return_value = (
                _Mappings(item={"id": run_id, "status": "running"})
                if "runs" in sql
                else _Mappings(items=[])
            )
            return res

        session.execute = AsyncMock(side_effect=execute)
        yield session

    import agent_runtime.api.events as events_mod

    monkeypatch.setattr(events_mod, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(
        events_mod,
        "get_settings",
        lambda: RuntimeSettings(
            environment="test",
            sse_revalidation_interval_seconds=0.06,
            sse_auth_lookup_timeout_seconds=0.15,
            sse_max_auth_age_seconds=0.4,
        ),
    )

    principal = RuntimePrincipal(
        subject="sub-1",
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        roles=("viewer",),
        scopes=frozenset({"run:read"}),
        session_id=session_id,
        token_scopes=frozenset({"run:read"}),
    )
    app.dependency_overrides[get_runtime_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://runtime.test") as client:
            # 1. Timeout must abort stream quickly via fail-closed error event
            async def trigger_hang():
                await asyncio.sleep(0.12)
                state["hang"] = True

            task = asyncio.create_task(trigger_hang())
            async with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
                text_body = (await resp.aread()).decode("utf-8")
            await task
            assert "event: error" in text_body
            assert "authorization_unavailable" in text_body
    finally:
        app.dependency_overrides.clear()
        set_parent_session_verifier(None)


def test_tool_dispatcher_rechecks_live_membership_and_denies_revoked() -> None:
    asyncio.run(_run_tool_dispatcher_rechecks_live_membership())


async def _run_tool_dispatcher_rechecks_live_membership() -> None:
    session = AsyncMock()
    session.begin = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False)
        )
    )

    async def execute_side_effect(stmt: Any, params: dict[str, Any] | None = None):
        _ = params
        sql = " ".join(str(stmt).split())
        res = MagicMock()
        if "set_config(" in sql:
            return res
        if "iam.resolve_user_workspace_membership" in sql:
            # Membership revoked right before tool execution!
            res.mappings.return_value = _Mappings(item=None)
            return res
        raise AssertionError(f"Tool query should never run when membership is revoked: {sql}")

    session.execute = AsyncMock(side_effect=execute_side_effect)

    @asynccontextmanager
    async def session_factory():
        yield session

    dispatcher = ToolDispatcher(session_factory)
    invocation = ToolInvocation(
        principal=AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(uuid4()),
            workspace_id=str(uuid4()),
            roles=["owner"],
        ),
        run_id=str(uuid4()),
        agent_version_id=str(uuid4()),
        tool_version_id=str(uuid4()),
        operation_key="run-1:step-1",
        arguments={"q": "x"},
        step_key="step-1",
    )

    with pytest.raises(ToolPolicyDeniedError, match="Active workspace membership required"):
        await dispatcher.execute(invocation)


def test_tool_dispatcher_rechecks_again_immediately_before_adapter_invocation() -> None:
    asyncio.run(_run_tool_dispatcher_final_reauthorization_race())


async def _run_tool_dispatcher_final_reauthorization_race() -> None:
    final_check_started = asyncio.Event()
    allow_final_check = asyncio.Event()
    transitions: list[str] = []
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=({"ok": True}, None))

    session = AsyncMock()
    session.begin = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    @asynccontextmanager
    async def session_factory():
        yield session

    class BarrierDispatcher(ToolDispatcher):
        @staticmethod
        async def _scope(session: Any, principal: AuthenticatedPrincipal) -> None:
            _ = (session, principal)

        async def _resolve(self, session: Any, call: ToolInvocation) -> dict[str, Any]:
            _ = (session, call)
            return {
                "kind": "mcp",
                "tool_version_id": call.tool_version_id,
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "execution_config": {},
                "risk": "read",
                "usage_mode": "automatic",
            }

        async def _verify_approval(
            self,
            session: Any,
            call: ToolInvocation,
            tool: dict[str, Any],
        ) -> None:
            _ = (session, call, tool)

        async def _reserve(
            self,
            call: ToolInvocation,
            tool: dict[str, Any],
            request_hash: str,
        ) -> None:
            _ = (call, tool, request_hash)

        async def _transition(
            self,
            call: ToolInvocation,
            status: str,
            *,
            external_reference: str | None = None,
        ) -> None:
            _ = (call, external_reference)
            transitions.append(status)

        async def _reauthorize_before_execution(
            self,
            call: ToolInvocation,
        ) -> dict[str, Any]:
            _ = call
            final_check_started.set()
            await allow_final_check.wait()
            raise ToolPolicyDeniedError("Active workspace membership required for tool execution")

    invocation = ToolInvocation(
        principal=AuthenticatedPrincipal(
            user_id=str(uuid4()),
            tenant_id=str(uuid4()),
            workspace_id=str(uuid4()),
            roles=["owner"],
        ),
        run_id=str(uuid4()),
        agent_version_id=str(uuid4()),
        tool_version_id=str(uuid4()),
        operation_key="run-race:step-1",
        arguments={"q": "x"},
        step_key="step-1",
    )
    dispatcher = BarrierDispatcher(session_factory, adapters={"mcp": adapter})

    task = asyncio.create_task(dispatcher.execute(invocation))
    await final_check_started.wait()
    allow_final_check.set()
    with pytest.raises(ToolPolicyDeniedError, match="Active workspace membership required"):
        await task

    adapter.invoke.assert_not_awaited()
    assert transitions == ["executing", "failed"]


def test_runtime_reuses_and_closes_shared_redis_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_runtime.auth as auth_module

    close_shared_parent_session_verifier()
    created: list[Any] = []

    class FakeRedisVerifier:
        def __init__(self, settings: RuntimeSettings | None = None) -> None:
            self.settings = settings
            self.closed = False
            created.append(self)

        def verify_parent_session(
            self,
            session_id: str,
            user_id: str,
            now: int | None = None,
        ) -> tuple[str, Any]:
            _ = (session_id, user_id, now)
            return "OK", {}

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(auth_module, "RedisParentSessionVerifier", FakeRedisVerifier)
    settings = RuntimeSettings(
        environment="test",
        redis_url="redis://127.0.0.1:6399/12",
    )
    try:
        first = get_parent_session_verifier(settings)
        second = get_parent_session_verifier(settings)
        assert first is second
        assert len(created) == 1

        close_shared_parent_session_verifier()
        assert created[0].closed is True

        third = get_parent_session_verifier(settings)
        assert third is not first
        assert len(created) == 2
    finally:
        close_shared_parent_session_verifier()
