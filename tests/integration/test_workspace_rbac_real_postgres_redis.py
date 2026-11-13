"""Real PostgreSQL 16 + Real Redis + Real TCP Uvicorn SSE integration tests for Step 03-D."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import redis as redis_lib
from control_api.core import database as control_db
from control_api.core.settings import settings
from control_api.main import app as control_app
from control_api.modules.identity.providers.oidc_client import oidc_client
from control_api.modules.identity.repositories.auth_transaction_repository import (
    RedisAuthTransactionStore,
)
from control_api.modules.identity.repositories.session_repository import (
    RedisRevocationStore,
)
from control_api.modules.identity.services.session_service import session_manager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.test_invitation_acceptance_real_postgres_redis import (
    _configure_real_stack,
)
from tests.integration.test_workspace_invitations_real_postgres_smtp import (
    _issue_session_cookie_header,
    _seed_workspace_and_users,
    _to_async_pg_url,
)

ROOT = Path(__file__).resolve().parents[2]
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:16379/0")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
    accepted_statuses: tuple[int, ...] = (200,),
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
            raise AssertionError(f"Process exited early ({proc.returncode}):\n{log_text}")
        try:
            req = urllib.request.Request(url, method="GET")
            with _NO_PROXY_OPENER.open(req, timeout=1.5) as resp:
                if resp.status in accepted_statuses:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code in accepted_statuses:
                return
            last_err = f"HTTP {exc.code}"
        except (OSError, urllib.error.URLError) as exc:  # pragma: no cover - polling helper
            last_err = repr(exc)
        time.sleep(0.15)
    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path and log_path.exists()
        else ""
    )
    raise AssertionError(f"Timed out waiting for {url} ({last_err}):\n{log_text}")


def _flush_test_redis() -> redis_lib.Redis:
    client = redis_lib.Redis.from_url(REDIS_TEST_URL, socket_timeout=2.0)
    try:
        assert client.ping(), f"Cannot ping isolated test Redis at {REDIS_TEST_URL}"
        client.flushdb()
    except Exception:
        client.close()
        raise
    return client


def _configure_rbac_control_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_url: str,
    pool_size: int = 8,
    max_overflow: int = 8,
):
    rev_store = RedisRevocationStore(redis_url=REDIS_TEST_URL)
    tx_store = RedisAuthTransactionStore(redis_url=REDIS_TEST_URL)
    monkeypatch.setattr(session_manager, "denylist", rev_store)
    monkeypatch.setattr(oidc_client, "store", tx_store)
    monkeypatch.setattr(settings, "invitations_enabled", True)
    monkeypatch.setattr(settings, "invitation_public_app_origin", "http://localhost:3000")

    async_engine = create_async_engine(
        _to_async_pg_url(app_url),
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    async_session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(control_db, "engine", async_engine)
    monkeypatch.setattr(control_db, "AsyncSessionLocal", async_session_factory)
    return async_engine, async_session_factory, rev_store


def _seed_extra_members_and_dual_ws_user(owner_url: str, seeded: dict[str, Any]) -> dict[str, Any]:
    """Seed operator, viewer, second owner in ws_a, and grant dev_a access in ws_b too."""
    issuer = seeded["issuer"]
    tenant_a = seeded["tenant_a"]
    ws_a = seeded["ws_a"]
    tenant_b = seeded["tenant_b"]
    ws_b = seeded["ws_b"]

    operator_a = uuid4()
    viewer_a = uuid4()
    owner2_a = uuid4()
    suffix = uuid4().hex[:8]

    with psycopg.connect(owner_url, autocommit=True) as conn:
        for uid, name, subj in [
            (operator_a, "Operator A", f"op-a-{suffix}"),
            (viewer_a, "Viewer A", f"view-a-{suffix}"),
            (owner2_a, "Second Owner A", f"owner2-a-{suffix}"),
        ]:
            conn.execute(
                """
                INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (uid, issuer, subj, name, f"{subj}@example.com"),
            )

        conn.execute(
            """
            INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status)
            VALUES (%s, %s, %s, 'operator', 'active'),
                   (%s, %s, %s, 'viewer', 'active'),
                   (%s, %s, %s, 'owner', 'active'),
                   (%s, %s, %s, 'developer', 'active')
            """,
            (
                tenant_a,
                ws_a,
                operator_a,
                tenant_a,
                ws_a,
                viewer_a,
                tenant_a,
                ws_a,
                owner2_a,
                tenant_b,
                ws_b,
                seeded["dev_a"],
            ),
        )

        # Resolve user subjects for seeded accounts too
        rows = conn.execute(
            """
            SELECT u.id, u.subject, m.id AS membership_id, m.workspace_id
            FROM iam.users u
            LEFT JOIN iam.memberships m ON m.user_id = u.id
            WHERE u.id = ANY(%s)
            """,
            (
                [
                    seeded["owner_a"],
                    seeded["admin_a"],
                    seeded["dev_a"],
                    seeded["owner_b"],
                    operator_a,
                    viewer_a,
                    owner2_a,
                ],
            ),
        ).fetchall()

    subjects: dict[UUID, str] = {}
    memberships: dict[tuple[UUID, UUID], UUID] = {}
    for uid, subj, mid, wid in rows:
        subjects[uid] = subj
        if mid is not None and wid is not None:
            memberships[(wid, uid)] = mid

    return {
        "operator_a": operator_a,
        "viewer_a": viewer_a,
        "owner2_a": owner2_a,
        "subjects": subjects,
        "memberships": memberships,
    }


def _seed_runtime_run(owner_url: str, *, tenant_id: UUID, workspace_id: UUID, user_id: UUID) -> UUID:
    agent_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name)
            VALUES (%s, %s, %s, %s, 'SSE Test Agent')
            """,
            (tenant_id, workspace_id, agent_id, f"agent-{agent_id.hex[:8]}"),
        )
        conn.execute(
            """
            INSERT INTO catalog.agent_versions (
                tenant_id, workspace_id, id, agent_id, version,
                schema_version, frozen_spec, bundle_sha256
            ) VALUES (%s, %s, %s, %s, 1, '1.0', '{"name":"sse-agent"}', %s)
            """,
            (tenant_id, workspace_id, version_id, agent_id, "a" * 64),
        )
        conn.execute(
            """
            INSERT INTO runtime.runs (
                id, tenant_id, workspace_id, agent_version_id, initiated_by,
                external_request_key, status, next_event_seq, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 'running', 2, now())
            """,
            (run_id, tenant_id, workspace_id, version_id, user_id, f"req-{run_id.hex[:8]}"),
        )
        conn.execute(
            """
            INSERT INTO runtime.run_events (
                tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload
            ) VALUES (%s, %s, %s, 1, 'run.started', '{"step":"init"}'::jsonb)
            """,
            (tenant_id, workspace_id, run_id),
        )
    return run_id


class _TcpRelay:
    """Loopback TCP relay to deterministically simulate mid-stream Redis outage."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self._target_host = target_host
        self._target_port = target_port
        self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_sock.bind(("127.0.0.1", 0))
        self._listen_sock.listen(16)
        self.port = int(self._listen_sock.getsockname()[1])
        self._stop = threading.Event()
        self._active_conns: list[socket.socket] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _pipe(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                data = src.recv(4096)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            self._listen_sock.settimeout(0.2)
            try:
                client_sock, _ = self._listen_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                upstream = socket.create_connection((self._target_host, self._target_port), timeout=2.0)
            except OSError:
                client_sock.close()
                continue
            with self._lock:
                self._active_conns.extend([client_sock, upstream])
            threading.Thread(target=self._pipe, args=(client_sock, upstream), daemon=True).start()
            threading.Thread(target=self._pipe, args=(upstream, client_sock), daemon=True).start()

    def cut_traffic(self) -> None:
        self._stop.set()
        try:
            self._listen_sock.close()
        except OSError:
            pass
        with self._lock:
            for sock in self._active_conns:
                try:
                    sock.close()
                except OSError:
                    pass
            self._active_conns.clear()

    def close(self) -> None:
        self.cut_traffic()
        self._thread.join(timeout=2.0)


@pytest.mark.integration
def test_rbac_matrix_pagination_privacy_audit_and_rls_real_pg_redis(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _flush_test_redis()
    try:
        owner_url = migrated_database["owner_url"]
        app_url = migrated_database["app_url"]
        seeded = _seed_workspace_and_users(owner_url)
        extra = _seed_extra_members_and_dual_ws_user(owner_url, seeded)

        async def _scenario() -> None:
            async_engine, _sf, _rev = _configure_rbac_control_api(
                monkeypatch,
                app_url=app_url,
                pool_size=2,
                max_overflow=0,
            )
            try:
                ws_a = seeded["ws_a"]
                ws_b = seeded["ws_b"]
                issuer = seeded["issuer"]
                subjs = extra["subjects"]
                mids = extra["memberships"]

                owner_h = _issue_session_cookie_header(seeded["owner_a"], issuer, subjs[seeded["owner_a"]])
                admin_h = _issue_session_cookie_header(seeded["admin_a"], issuer, subjs[seeded["admin_a"]])
                dev_h = _issue_session_cookie_header(seeded["dev_a"], issuer, subjs[seeded["dev_a"]])
                op_h = _issue_session_cookie_header(extra["operator_a"], issuer, subjs[extra["operator_a"]])
                view_h = _issue_session_cookie_header(extra["viewer_a"], issuer, subjs[extra["viewer_a"]])
                owner_b_h = _issue_session_cookie_header(seeded["owner_b"], issuer, subjs[seeded["owner_b"]])

                admin_mid = mids[(ws_a, seeded["admin_a"])]
                dev_mid = mids[(ws_a, seeded["dev_a"])]
                op_mid = mids[(ws_a, extra["operator_a"])]
                view_mid = mids[(ws_a, extra["viewer_a"])]
                owner2_mid = mids[(ws_a, extra["owner2_a"])]
                owner1_mid = mids[(ws_a, seeded["owner_a"])]

                transport = ASGITransport(app=control_app)
                async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                    # 1. Non-admin roles cannot view or mutate memberships
                    for role_h in (dev_h, op_h, view_h):
                        r = await client.get(f"/v1/workspaces/{ws_a}/memberships", headers=role_h)
                        assert r.status_code == 403
                        r2 = await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{op_mid}/role",
                            json={"role": "viewer", "expected_role": "operator", "expected_status": "active"},
                            headers=role_h,
                        )
                        assert r2.status_code == 403
                        r3 = await client.post(
                            f"/v1/workspaces/{ws_a}/memberships/{op_mid}/revoke",
                            json={"expected_role": "operator", "expected_status": "active"},
                            headers=role_h,
                        )
                        assert r3.status_code == 403

                    # 2. Scoped runtime Bearer token rejected on member endpoints (active_session_required)
                    tok_resp = await client.post(
                        "/v1/auth/token",
                        json={"workspace_id": str(ws_a)},
                        headers=owner_h,
                    )
                    assert tok_resp.status_code == 200
                    runtime_jwt = tok_resp.json()["access_token"]
                    scoped_list = await client.get(
                        f"/v1/workspaces/{ws_a}/memberships",
                        headers={"Authorization": f"Bearer {runtime_jwt}"},
                    )
                    assert scoped_list.status_code == 403
                    assert scoped_list.json()["detail"]["code"] == "active_session_required"

                    # 3. Cursor pagination + minimal privacy projection + status filter
                    all_ids: list[str] = []
                    cursor: str | None = None
                    while True:
                        params: dict[str, Any] = {"limit": 2, "status": "active"}
                        if cursor:
                            params["cursor"] = cursor
                        page = await client.get(
                            f"/v1/workspaces/{ws_a}/memberships",
                            params=params,
                            headers=owner_h,
                        )
                        assert page.status_code == 200
                        assert page.headers.get("cache-control") == "no-store"
                        body = page.json()
                        assert len(body["items"]) <= 2
                        for item in body["items"]:
                            assert set(item.keys()) == {
                                "membership_id",
                                "user_id",
                                "display_name",
                                "role",
                                "status",
                                "joined_at",
                            }
                            all_ids.append(item["membership_id"])
                        cursor = body["next_cursor"]
                        if cursor is None:
                            break
                    assert len(all_ids) == 6  # owner1, owner2, admin, dev, op, viewer
                    assert len(set(all_ids)) == 6

                    rev_list = await client.get(
                        f"/v1/workspaces/{ws_a}/memberships",
                        params={"status": "revoked"},
                        headers=admin_h,
                    )
                    assert rev_list.status_code == 200
                    assert len(rev_list.json()["items"]) == 1
                    assert rev_list.json()["items"][0]["status"] == "revoked"

                    bad_status = await client.get(
                        f"/v1/workspaces/{ws_a}/memberships",
                        params={"status": "bogus"},
                        headers=owner_h,
                    )
                    assert bad_status.status_code == 422

                    # 4. Admin boundaries: cannot touch owner/admin or promote to admin/owner or self-promote
                    assert (
                        await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{owner1_mid}/role",
                            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                            headers=admin_h,
                        )
                    ).status_code == 403
                    assert (
                        await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/role",
                            json={"role": "owner", "expected_role": "admin", "expected_status": "active"},
                            headers=admin_h,
                        )
                    ).status_code == 403
                    assert (
                        await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/role",
                            json={"role": "admin", "expected_role": "developer", "expected_status": "active"},
                            headers=admin_h,
                        )
                    ).status_code == 403
                    assert (
                        await client.post(
                            f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/revoke",
                            json={"expected_role": "admin", "expected_status": "active"},
                            headers=admin_h,
                        )
                    ).status_code == 403

                    # Admin CAN update developer -> operator -> developer
                    adm_upd = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/role",
                        json={"role": "operator", "expected_role": "developer", "expected_status": "active"},
                        headers=admin_h,
                    )
                    assert adm_upd.status_code == 200
                    assert adm_upd.json()["outcome"] == "updated"
                    assert adm_upd.json()["audit_event_id"] is not None

                    adm_back = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/role",
                        json={"role": "developer", "expected_role": "operator", "expected_status": "active"},
                        headers=admin_h,
                    )
                    assert adm_back.status_code == 200

                    # 5. Stale expected_role returns 409 & writes 0 audit rows; no-op writes 0 audit rows
                    stale = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/role",
                        json={"role": "operator", "expected_role": "operator", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert stale.status_code == 409
                    assert stale.json()["detail"]["code"] == "stale_state"

                    noop = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/role",
                        json={"role": "developer", "expected_role": "developer", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert noop.status_code == 200
                    assert noop.json()["outcome"] == "no_op"
                    assert noop.json()["audit_event_id"] is None

                    # 6. Admin can revoke viewer; replay revoke returns already_revoked with zero duplicate audit
                    rev_view = await client.post(
                        f"/v1/workspaces/{ws_a}/memberships/{view_mid}/revoke",
                        json={"expected_role": "viewer", "expected_status": "active"},
                        headers=admin_h,
                    )
                    assert rev_view.status_code == 200
                    assert rev_view.json()["outcome"] == "revoked"
                    assert rev_view.json()["audit_event_id"] is not None

                    rev_replay = await client.post(
                        f"/v1/workspaces/{ws_a}/memberships/{view_mid}/revoke",
                        json={"expected_role": "viewer", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert rev_replay.status_code == 200
                    assert rev_replay.json()["outcome"] == "already_revoked"
                    assert rev_replay.json()["audit_event_id"] is None

                    # 7. Owner promotion & last-owner protection
                    prom = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/role",
                        json={"role": "owner", "expected_role": "admin", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert prom.status_code == 200
                    assert prom.json()["role"] == "owner"

                    # Demote extra owners until only owner1 remains, then last-owner block must trigger
                    assert (
                        await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{owner2_mid}/role",
                            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                            headers=owner_h,
                        )
                    ).status_code == 200
                    assert (
                        await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/role",
                            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                            headers=owner_h,
                        )
                    ).status_code == 200

                    last_demote = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{owner1_mid}/role",
                        json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert last_demote.status_code == 409
                    assert last_demote.json()["detail"]["code"] == "last_owner_protected"

                    last_revoke = await client.post(
                        f"/v1/workspaces/{ws_a}/memberships/{owner1_mid}/revoke",
                        json={"expected_role": "owner", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert last_revoke.status_code == 409
                    assert last_revoke.json()["detail"]["code"] == "last_owner_protected"

                    # 8. Cross-tenant / cross-workspace isolation even with pool_size=2 reuse
                    cross_list = await client.get(
                        f"/v1/workspaces/{ws_b}/memberships",
                        headers=owner_h,
                    )
                    assert cross_list.status_code == 403
                    cross_patch = await client.patch(
                        f"/v1/workspaces/{ws_b}/memberships/{dev_mid}/role",
                        json={"role": "viewer", "expected_role": "developer", "expected_status": "active"},
                        headers=owner_b_h,
                    )
                    assert cross_patch.status_code == 404

                    # 9. Verify owner invitation remains forbidden via POST /invitations
                    inv_owner = await client.post(
                        f"/v1/workspaces/{ws_a}/invitations",
                        json={"email": "boss@example.com", "role": "owner"},
                        headers=owner_h,
                    )
                    assert inv_owner.status_code == 422

                # 10. Verify zero-context platform_app RLS enforcement and audit_events count
                with psycopg.connect(app_url) as pconn:
                    assert pconn.execute("SELECT * FROM iam.memberships").fetchall() == []
                    pconn.rollback()
                    assert pconn.execute("SELECT * FROM governance.audit_events").fetchall() == []
                    pconn.rollback()
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        pconn.execute(
                            "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                            "VALUES (%s, %s, %s, 'viewer', 'active')",
                            (seeded["tenant_a"], ws_a, extra["operator_a"]),
                        )
                    pconn.rollback()

                with psycopg.connect(owner_url) as oconn, oconn.transaction():
                    oconn.execute(
                        "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                        (str(seeded["tenant_a"]), str(ws_a)),
                    )
                    audit_rows = oconn.execute(
                        """
                        SELECT action, resource_id, details_redacted
                        FROM governance.audit_events
                        WHERE workspace_id = %s
                          AND action IN ('workspace.membership.role_update', 'workspace.membership.revoke')
                        ORDER BY occurred_at ASC
                        """,
                        (ws_a,),
                    ).fetchall()
                    # Exact mutations: dev->op, op->dev, view revoke, admin->owner, owner2->admin, admin->admin = 6
                    assert len(audit_rows) == 6
                    actions = [r[0] for r in audit_rows]
                    assert actions.count("workspace.membership.role_update") == 5
                    assert actions.count("workspace.membership.revoke") == 1
            finally:
                await async_engine.dispose()

        asyncio.run(_scenario())
    finally:
        redis_client.close()


@pytest.mark.integration
def test_concurrent_last_owner_and_revoke_serialization_real_postgres(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _flush_test_redis()
    try:
        owner_url = migrated_database["owner_url"]
        app_url = migrated_database["app_url"]
        seeded = _seed_workspace_and_users(owner_url)
        extra = _seed_extra_members_and_dual_ws_user(owner_url, seeded)

        async def _scenario() -> None:
            async_engine, _sf, _rev = _configure_rbac_control_api(
                monkeypatch,
                app_url=app_url,
                pool_size=8,
                max_overflow=8,
            )
            try:
                ws_a = seeded["ws_a"]
                issuer = seeded["issuer"]
                subjs = extra["subjects"]
                mids = extra["memberships"]

                owner1_h = _issue_session_cookie_header(seeded["owner_a"], issuer, subjs[seeded["owner_a"]])
                owner2_h = _issue_session_cookie_header(extra["owner2_a"], issuer, subjs[extra["owner2_a"]])
                admin_h = _issue_session_cookie_header(seeded["admin_a"], issuer, subjs[seeded["admin_a"]])

                owner1_mid = mids[(ws_a, seeded["owner_a"])]
                owner2_mid = mids[(ws_a, extra["owner2_a"])]
                admin_mid = mids[(ws_a, seeded["admin_a"])]
                dev_mid = mids[(ws_a, seeded["dev_a"])]

                transport = ASGITransport(app=control_app)
                async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                    # 1. Race A: Owner1 demotes Owner2 while Owner2 concurrently demotes Owner1
                    r1, r2 = await asyncio.gather(
                        client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{owner2_mid}/role",
                            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                            headers=owner1_h,
                        ),
                        client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{owner1_mid}/role",
                            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                            headers=owner2_h,
                        ),
                    )
                    statuses = sorted([r1.status_code, r2.status_code])
                    assert statuses in ([200, 403], [200, 409])
                    winner = r1 if r1.status_code == 200 else r2

                    # Restore promoted winner target back to dual owners for Race B
                    winner_target = owner2_mid if winner is r1 else owner1_mid
                    active_owner_h = owner1_h if winner is r1 else owner2_h
                    assert (
                        await client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{winner_target}/role",
                            json={"role": "owner", "expected_role": "admin", "expected_status": "active"},
                            headers=active_owner_h,
                        )
                    ).status_code == 200

                    # 2. Race B: Both active owners concurrently revoke each other -> exactly 1 revoked, 1 blocked
                    rv1, rv2 = await asyncio.gather(
                        client.post(
                            f"/v1/workspaces/{ws_a}/memberships/{owner2_mid}/revoke",
                            json={"expected_role": "owner", "expected_status": "active"},
                            headers=owner1_h,
                        ),
                        client.post(
                            f"/v1/workspaces/{ws_a}/memberships/{owner1_mid}/revoke",
                            json={"expected_role": "owner", "expected_status": "active"},
                            headers=owner2_h,
                        ),
                    )
                    assert sorted([rv1.status_code, rv2.status_code]) in ([200, 403], [200, 409])
                    survivor_h = owner1_h if rv1.status_code == 200 else owner2_h
                    survivor_mid = owner1_mid if rv1.status_code == 200 else owner2_mid

                    # Concurrent last-owner demote + revoke against sole surviving owner both return 409 last_owner_protected
                    lo1, lo2 = await asyncio.gather(
                        client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{survivor_mid}/role",
                            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
                            headers=survivor_h,
                        ),
                        client.post(
                            f"/v1/workspaces/{ws_a}/memberships/{survivor_mid}/revoke",
                            json={"expected_role": "owner", "expected_status": "active"},
                            headers=survivor_h,
                        ),
                    )
                    assert lo1.status_code == 409 and lo1.json()["detail"]["code"] == "last_owner_protected"
                    assert lo2.status_code == 409 and lo2.json()["detail"]["code"] == "last_owner_protected"

                    # 3. Race C: Remaining owner revokes Admin while Admin mutates Developer
                    ra, rb = await asyncio.gather(
                        client.post(
                            f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/revoke",
                            json={"expected_role": "admin", "expected_status": "active"},
                            headers=survivor_h,
                        ),
                        client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/role",
                            json={"role": "operator", "expected_role": "developer", "expected_status": "active"},
                            headers=admin_h,
                        ),
                    )
                    assert ra.status_code == 200
                    assert rb.status_code in (200, 403)

                with psycopg.connect(owner_url) as oconn:
                    active_owners = oconn.execute(
                        "SELECT count(*) FROM iam.memberships "
                        "WHERE workspace_id = %s AND role = 'owner' AND status = 'active'",
                        (ws_a,),
                    ).fetchone()[0]
                    assert active_owners == 1
            finally:
                await async_engine.dispose()

        asyncio.run(_scenario())
    finally:
        redis_client.close()


@pytest.mark.integration
def test_promoted_owner_accepted_invitation_replay_compatibility(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted admin invitation promoted to owner replays cleanly with already_accepted receipt."""
    redis_client = _flush_test_redis()
    try:
        owner_url = migrated_database["owner_url"]
        app_url = migrated_database["app_url"]
        seeded = _seed_workspace_and_users(owner_url)
        extra = _seed_extra_members_and_dual_ws_user(owner_url, seeded)

        from cryptography.fernet import Fernet

        proof_key = Fernet.generate_key().decode()
        handoff_key = Fernet.generate_key().decode()

        async def _scenario() -> None:
            async_engine, _sf, _rev, _tx, _hd = _configure_real_stack(
                monkeypatch,
                app_url=app_url,
                proof_key=proof_key,
                handoff_key=handoff_key,
                issuer_url=seeded["issuer"],
                redis_url=REDIS_TEST_URL,
            )
            try:
                ws_a = seeded["ws_a"]
                tenant_a = seeded["tenant_a"]
                admin_a = seeded["admin_a"]
                issuer = seeded["issuer"]
                subjs = extra["subjects"]
                admin_mid = extra["memberships"][(ws_a, admin_a)]

                # Insert an already-accepted invitation row for admin_a
                inv_id = uuid4()
                raw_token = secrets.token_urlsafe(32)
                import hashlib

                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                with psycopg.connect(owner_url, autocommit=True) as conn:
                    conn.execute(
                        """
                        INSERT INTO iam.workspace_invitations (
                            id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer,
                            role, status, created_by_user_id, accepted_by_user_id, accepted_at,
                            token_hash, proof_ciphertext, proof_key_id, delivery_requested_by_user_id,
                            expires_at
                        ) VALUES (%s, %s, %s, %s, %s, 'admin', 'accepted', %s, %s, now(), %s, NULL, NULL, %s, now() + interval '1 day')
                        """,
                        (
                            inv_id,
                            tenant_a,
                            ws_a,
                            f"{subjs[admin_a]}@example.com",
                            issuer,
                            seeded["owner_a"],
                            admin_a,
                            token_hash,
                            seeded["owner_a"],
                        ),
                    )

                from control_api.modules.identity.models.session import (
                    VerifiedIdentityEvidence,
                )

                now_ts = int(time.time())
                admin_ev = VerifiedIdentityEvidence(
                    issuer=issuer,
                    subject=subjs[admin_a],
                    user_id=str(admin_a),
                    email_normalized=f"{subjs[admin_a]}@example.com",
                    email_verified=True,
                    id_token_issued_at=now_ts,
                    observed_at=now_ts,
                )
                _admin_sess, admin_tok = session_manager.create_session(
                    user_id=str(admin_a),
                    issuer=issuer,
                    subject=subjs[admin_a],
                    identity_evidence=admin_ev,
                )
                owner_h = _issue_session_cookie_header(seeded["owner_a"], issuer, subjs[seeded["owner_a"]])

                transport = ASGITransport(app=control_app)
                async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                    # Promote admin_a to owner via Step 03-D API
                    prom = await client.patch(
                        f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/role",
                        json={"role": "owner", "expected_role": "admin", "expected_status": "active"},
                        headers=owner_h,
                    )
                    assert prom.status_code == 200
                    assert prom.json()["role"] == "owner"

                    # Create browser handoff cookie for the already-accepted invitation, then replay preview & accept
                    ho = await client.post(
                        f"/v1/invitations/{inv_id}/handoff",
                        json={"token": raw_token},
                        headers={"Origin": "http://localhost:3000"},
                    )
                    assert ho.status_code == 201
                    handoff_val = ho.cookies.get(settings.invitation_handoff_cookie_name)
                    assert handoff_val
                    replay_headers = {
                        "Cookie": f"{settings.session_cookie_name}={admin_tok}; {settings.invitation_handoff_cookie_name}={handoff_val}",
                        "Origin": "http://localhost:3000",
                    }

                    prev = await client.post(
                        f"/v1/invitations/{inv_id}/preview",
                        headers=replay_headers,
                    )
                    assert prev.status_code == 200
                    assert prev.json()["status"] == "accepted"
                    assert prev.json()["acceptance_state"] == "already_accepted"
                    assert prev.json()["role"] == "owner"

                    # Replay POST /v1/invitations/{inv_id}/accept with active admin_a session & empty body
                    acc = await client.post(
                        f"/v1/invitations/{inv_id}/accept",
                        headers=replay_headers,
                    )
                    assert acc.status_code == 200
                    body = acc.json()
                    assert body["acceptance_state"] == "already_accepted"
                    assert body["role"] == "owner"
            finally:
                await async_engine.dispose()

        asyncio.run(_scenario())
    finally:
        redis_client.close()


@pytest.mark.integration
def test_real_tcp_runtime_sse_live_revocation_redis_outage_and_workspace_isolation(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Real uvicorn Agent Runtime over TCP proves <=5s SSE revocation, session outage fail-closed, and WS-B survival."""
    redis_client = _flush_test_redis()
    # Parse host/port from REDIS_TEST_URL for loopback outage relay
    from urllib.parse import urlparse

    parsed_redis = urlparse(REDIS_TEST_URL)
    redis_host = parsed_redis.hostname or "127.0.0.1"
    redis_port = int(parsed_redis.port or 6379)
    redis_db = (parsed_redis.path or "/0").lstrip("/") or "0"

    relay = _TcpRelay(redis_host, redis_port)
    proc_main: subprocess.Popen[Any] | None = None
    proc_relay: subprocess.Popen[Any] | None = None
    main_log_f = None
    relay_log_f = None

    try:
        owner_url = migrated_database["owner_url"]
        app_url = migrated_database["app_url"]
        seeded = _seed_workspace_and_users(owner_url)
        extra = _seed_extra_members_and_dual_ws_user(owner_url, seeded)

        ws_a = seeded["ws_a"]
        tenant_a = seeded["tenant_a"]
        ws_b = seeded["ws_b"]
        tenant_b = seeded["tenant_b"]
        issuer = seeded["issuer"]
        subjs = extra["subjects"]
        mids = extra["memberships"]

        run_a_id = _seed_runtime_run(owner_url, tenant_id=tenant_a, workspace_id=ws_a, user_id=seeded["dev_a"])
        run_b_id = _seed_runtime_run(owner_url, tenant_id=tenant_b, workspace_id=ws_b, user_id=seeded["dev_a"])

        runtime_secret = settings.runtime_jwt_secret.get_secret_value()
        if len(runtime_secret) < 32:
            runtime_secret = settings.jwt_secret_key.get_secret_value()
        runtime_issuer = settings.runtime_jwt_issuer or settings.jwt_issuer

        port_main = _find_free_port()
        port_relay = _find_free_port()
        base_env = os.environ.copy()
        base_env.update(
            {
                "ENVIRONMENT": "development",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "DATABASE_URL": _to_async_pg_url(app_url),
                "AUTH_STORE_TYPE": "redis",
                "RUNTIME_JWT_SECRET": runtime_secret,
                "RUNTIME_JWT_ISSUER": runtime_issuer,
                "RUNTIME_JWT_AUDIENCE": settings.runtime_jwt_audience,
                "SSE_REVALIDATION_INTERVAL_SECONDS": "0.25",
                "SSE_AUTH_LOOKUP_TIMEOUT_SECONDS": "0.8",
                "SSE_MAX_AUTH_AGE_SECONDS": "1.5",
                "REDIS_SOCKET_TIMEOUT_SECONDS": "0.5",
                "REDIS_CONNECT_TIMEOUT_SECONDS": "0.5",
            }
        )

        env_main = base_env.copy()
        env_main["REDIS_URL"] = REDIS_TEST_URL
        env_relay = base_env.copy()
        env_relay["REDIS_URL"] = f"redis://127.0.0.1:{relay.port}/{redis_db}"

        main_log = tmp_path / "runtime_main.log"
        relay_log = tmp_path / "runtime_relay.log"
        main_log_f = main_log.open("w", encoding="utf-8")
        relay_log_f = relay_log.open("w", encoding="utf-8")

        proc_main = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent_runtime.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port_main),
            ],
            cwd=str(ROOT),
            env=env_main,
            stdout=main_log_f,
            stderr=subprocess.STDOUT,
        )
        proc_relay = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent_runtime.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port_relay),
            ],
            cwd=str(ROOT),
            env=env_relay,
            stdout=relay_log_f,
            stderr=subprocess.STDOUT,
        )

        _wait_http_ready(f"http://127.0.0.1:{port_main}/health/live", proc=proc_main, log_path=main_log)
        _wait_http_ready(f"http://127.0.0.1:{port_relay}/health/live", proc=proc_relay, log_path=relay_log)

        async def _scenario() -> None:
            async_engine, _sf, rev_store = _configure_rbac_control_api(
                monkeypatch,
                app_url=app_url,
                pool_size=6,
                max_overflow=6,
            )
            try:
                owner_h = _issue_session_cookie_header(seeded["owner_a"], issuer, subjs[seeded["owner_a"]])
                dev_h = _issue_session_cookie_header(seeded["dev_a"], issuer, subjs[seeded["dev_a"]])
                admin_h = _issue_session_cookie_header(seeded["admin_a"], issuer, subjs[seeded["admin_a"]])

                dev_mid = mids[(ws_a, seeded["dev_a"])]
                admin_mid = mids[(ws_a, seeded["admin_a"])]

                # Mint runtime JWTs BEFORE demotion/revocation
                async with AsyncClient(
                    transport=ASGITransport(app=control_app),
                    base_url="http://localhost:8000",
                ) as api_client:
                    tok_dev_a = (
                        await api_client.post("/v1/auth/token", json={"workspace_id": str(ws_a)}, headers=dev_h)
                    ).json()["access_token"]
                    tok_dev_b = (
                        await api_client.post("/v1/auth/token", json={"workspace_id": str(ws_b)}, headers=dev_h)
                    ).json()["access_token"]
                    tok_admin_a = (
                        await api_client.post("/v1/auth/token", json={"workspace_id": str(ws_a)}, headers=admin_h)
                    ).json()["access_token"]

                    async with httpx.AsyncClient(trust_env=False, timeout=15.0) as http_client:
                        # 1. Open SSE on run_a_id as dev_a BEFORE revocation
                        seen_lines: list[str] = []
                        t_revoked: float | None = None
                        t_error_event: float | None = None
                        async with http_client.stream(
                            "GET",
                            f"http://127.0.0.1:{port_main}/v1/runs/{run_a_id}/events",
                            headers={"Authorization": f"Bearer {tok_dev_a}"},
                        ) as sse_resp:
                            assert sse_resp.status_code == 200
                            async for line in sse_resp.aiter_lines():
                                seen_lines.append(line)
                                if (
                                    t_revoked is None
                                    and ('"step":"init"' in line or '"step": "init"' in line or "event: run.started" in line)
                                    and any('"step"' in seen_line for seen_line in seen_lines)
                                ):
                                    # Revoke dev_a in Workspace A while SSE stream is open!
                                    t_revoked = time.monotonic()
                                    rev_resp = await api_client.post(
                                        f"/v1/workspaces/{ws_a}/memberships/{dev_mid}/revoke",
                                        json={"expected_role": "developer", "expected_status": "active"},
                                        headers=owner_h,
                                    )
                                    assert rev_resp.status_code == 200
                                    # Insert post-revoke event sequence=2; must NEVER reach client!
                                    with psycopg.connect(owner_url, autocommit=True) as pconn:
                                        pconn.execute(
                                            """
                                            INSERT INTO runtime.run_events (
                                                tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload
                                            ) VALUES (%s, %s, %s, 2, 'run.post_revoke_secret', '{"leak":"forbidden"}'::jsonb)
                                            """,
                                            (tenant_a, ws_a, run_a_id),
                                        )
                                if line.startswith("event: error"):
                                    t_error_event = time.monotonic()
                                if "authorization_revoked" in line:
                                    break

                        assert t_revoked is not None
                        assert t_error_event is not None, f"Missing SSE error termination; lines={seen_lines}"
                        assert (t_error_event - t_revoked) <= 5.0
                        joined_output = "\n".join(seen_lines)
                        assert "run.post_revoke_secret" not in joined_output
                        assert "forbidden" not in joined_output

                        # 2. Immediate Last-Event-ID reconnect fails with 403
                        reconn = await http_client.get(
                            f"http://127.0.0.1:{port_main}/v1/runs/{run_a_id}/events",
                            headers={
                                "Authorization": f"Bearer {tok_dev_a}",
                                "Last-Event-ID": "1",
                            },
                        )
                        assert reconn.status_code == 403

                        # 3. Demoted admin's stale JWT (missing write scope) rejected immediately; WS-B survives WS-A revoke
                        dem_resp = await api_client.patch(
                            f"/v1/workspaces/{ws_a}/memberships/{admin_mid}/role",
                            json={"role": "viewer", "expected_role": "admin", "expected_status": "active"},
                            headers=owner_h,
                        )
                        assert dem_resp.status_code == 200

                        stale_run = await http_client.post(
                            f"http://127.0.0.1:{port_main}/v1/runs",
                            json={"workspace_id": str(ws_a), "agent_version_id": str(uuid4())},
                            headers={
                                "Authorization": f"Bearer {tok_admin_a}",
                                "Idempotency-Key": "stale-admin-write",
                            },
                        )
                        assert stale_run.status_code == 403

                        # Control API /auth/me reflects WS-A missing and WS-B still active for dev_a
                        me_dev = await api_client.get("/v1/auth/me", headers=dev_h)
                        assert me_dev.status_code == 200
                        ws_ids_for_dev = [w["workspace_id"] for w in me_dev.json()["workspaces"]]
                        assert str(ws_a) not in ws_ids_for_dev
                        assert str(ws_b) in ws_ids_for_dev

                        # Dev_a stream on Workspace B still succeeds until session/Redis outage
                        wsb_lines: list[str] = []
                        t_cut: float | None = None
                        t_cut_err: float | None = None
                        async with http_client.stream(
                            "GET",
                            f"http://127.0.0.1:{port_relay}/v1/runs/{run_b_id}/events",
                            headers={"Authorization": f"Bearer {tok_dev_b}"},
                        ) as sse_relay_resp:
                            assert sse_relay_resp.status_code == 200
                            async for line in sse_relay_resp.aiter_lines():
                                wsb_lines.append(line)
                                if t_cut is None and '"step"' in line:
                                    t_cut = time.monotonic()
                                    relay.cut_traffic()
                                if line.startswith("event: error"):
                                    t_cut_err = time.monotonic()
                                if "authorization_revalidation_failed" in line or "authorization_revoked" in line:
                                    break

                        assert t_cut is not None
                        assert t_cut_err is not None, f"Redis outage did not fail closed; lines={wsb_lines}"
                        assert (t_cut_err - t_cut) <= 5.0

                        # 4. Parent-session revocation via Redis kills open owner SSE in <= 5s
                        run_owner_b = _seed_runtime_run(
                            owner_url,
                            tenant_id=tenant_b,
                            workspace_id=ws_b,
                            user_id=seeded["owner_b"],
                        )
                        owner_b_h = _issue_session_cookie_header(
                            seeded["owner_b"], issuer, subjs[seeded["owner_b"]]
                        )
                        tok_owner_b = (
                            await api_client.post(
                                "/v1/auth/token", json={"workspace_id": str(ws_b)}, headers=owner_b_h
                            )
                        ).json()["access_token"]
                        from agent_runtime.auth import RuntimeSettings, verify_runtime_token

                        rt_parsed = verify_runtime_token(
                            tok_owner_b,
                            settings=RuntimeSettings(
                                runtime_jwt_secret=runtime_secret,
                                runtime_jwt_issuer=runtime_issuer,
                                runtime_jwt_audience=settings.runtime_jwt_audience,
                            ),
                        )
                        sess_lines: list[str] = []
                        t_sess_rev: float | None = None
                        t_sess_err: float | None = None
                        async with http_client.stream(
                            "GET",
                            f"http://127.0.0.1:{port_main}/v1/runs/{run_owner_b}/events",
                            headers={"Authorization": f"Bearer {tok_owner_b}"},
                        ) as sse_sess_resp:
                            assert sse_sess_resp.status_code == 200
                            async for line in sse_sess_resp.aiter_lines():
                                sess_lines.append(line)
                                if t_sess_rev is None and '"step"' in line:
                                    t_sess_rev = time.monotonic()
                                    rev_store.revoke_session(rt_parsed.session_id, int(time.time()) + 600)
                                if line.startswith("event: error"):
                                    t_sess_err = time.monotonic()
                                if "authorization_revoked" in line:
                                    break

                        assert t_sess_rev is not None
                        assert t_sess_err is not None
                        assert (t_sess_err - t_sess_rev) <= 5.0
            finally:
                await async_engine.dispose()

        asyncio.run(_scenario())
    finally:
        relay.close()
        for proc in (proc_main, proc_relay):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if main_log_f:
            main_log_f.close()
        if relay_log_f:
            relay_log_f.close()
        redis_client.close()
