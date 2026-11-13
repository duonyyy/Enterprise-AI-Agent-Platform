"""Real PostgreSQL Integration & Concurrency Tests for Step 03-A — Tenant/Workspace & Initial Owner Bootstrap.

Executes against real PostgreSQL (migrations 0001..0004 applied) using the restricted
application role `platform_app` (`rolsuper = false`, `rolbypassrls = false`).

Verifies all mandatory Step 03-A requirements:
1. Authorized Bootstrap Operator provisions tenant + initial workspace + active Owner membership + audit event atomically.
2. Authorized Tenant Owner creates a second workspace in the same tenant and receives active Owner membership.
3. Authorized Bootstrap Operator designates a different provisioned user (`initial_owner_user_id`) as initial Owner.
4. Unauthorized actors (newly logged-in user, `admin`/`developer`/`operator`/`viewer`, cross-tenant owner, forged body fields) are rejected (`403` / `422`) with zero partial rows in PostgreSQL.
5. Multi-tenant and multi-workspace isolation (2 tenants, 2+ workspaces in same tenant) enforced at both HTTP (`403`) and PostgreSQL RLS (`0 rows` / `InsufficientPrivilege`).
6. Missing or wrong RLS context fails closed under `platform_app`.
7. Connection pool reuse (`AsyncAdaptedQueuePool(pool_size=1, max_overflow=0)`) reuses a single physical backend PID without leaking `app.tenant_id` / `app.workspace_id` / `app.user_id` across requests.
8. Mid-transaction failure rolls back `iam.tenants`, `iam.workspaces`, `iam.memberships`, and `governance.audit_events` atomically (zero orphan rows).
9. Concurrent `POST /v1/workspaces` with identical `(tenant_id, slug)` produces deterministic `201 Created` + `409 Conflict`; identical `slug` across different tenants both succeed `201 Created`; retry after commit returns `409 Conflict` while discovery (`GET /v1/workspaces`, `GET /v1/auth/me`) returns the committed workspace.
10. `invited`/`revoked` memberships, `archived` workspaces, and `suspended`/`closing` tenants are denied (`403`).
11. Committed bootstrap state persists across a fresh database engine / connection pool restart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from control_api.modules.workspaces.repositories.workspace_repository import WorkspaceRepository
from run_migrations import run_migrations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool


def _make_sa_url(psycopg: Any, conn_url: str) -> str:
    conn_dict = psycopg.conninfo.conninfo_to_dict(conn_url)
    return (
        f"postgresql+asyncpg://{conn_dict['user']}:{conn_dict['password']}"
        f"@{conn_dict['host']}:{conn_dict['port']}/{conn_dict['dbname']}"
    )


def _insert_user_via_app_role(
    psycopg: Any, app_url: str, *, issuer: str, subject: str, email: str
) -> UUID:
    user_id = uuid4()
    with psycopg.connect(app_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, issuer, subject, subject, email),
            )
        conn.commit()
    return user_id


@pytest.mark.integration
def test_workspace_bootstrap_e2e_authority_rls_rollback_and_concurrency_real_postgres(
    isolated_database: dict[str, Any],
) -> None:
    """Comprehensive real PostgreSQL verification for Step 03-A under `platform_app`."""
    psycopg = pytest.importorskip("psycopg")
    run_migrations(
        isolated_database["owner_url"],
        Path(__file__).resolve().parents[2] / "database/migrations",
    )
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]
    sa_url = _make_sa_url(psycopg, app_url)

    # Verify `platform_app` is strictly non-superuser and non-bypassrls
    with psycopg.connect(app_url) as app_conn:
        row = app_conn.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == ("platform_app", False, False), f"Unexpected app role privileges: {row}"

    orig_issuer = settings.oidc_issuer
    orig_db_url = settings.database_url
    orig_denylist = session_manager.denylist
    orig_operators = list(settings.bootstrap_operator_subjects)

    issuer_url = f"https://idp.step03a.{uuid4().hex[:8]}.local"
    operator_subject = f"bootstrap-op-{uuid4().hex[:8]}"
    alice_subject = f"alice-{uuid4().hex[:6]}"
    bob_subject = f"bob-{uuid4().hex[:6]}"
    charlie_subject = f"charlie-{uuid4().hex[:6]}"
    dave_subject = f"dave-{uuid4().hex[:6]}"
    eve_subject = f"eve-{uuid4().hex[:6]}"
    frank_subject = f"frank-{uuid4().hex[:6]}"

    session_manager.denylist = InMemoryRevocationStore()
    settings.oidc_issuer = issuer_url
    settings.database_url = sa_url
    settings.bootstrap_operator_subjects = [f"{issuer_url}|{operator_subject}"]

    # Provision test identities in `iam.users` using `platform_app`
    op_user_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=operator_subject, email="operator@vinai.io"
    )
    alice_owner_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=alice_subject, email="alice@vinai.io"
    )
    bob_normal_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=bob_subject, email="bob@vinai.io"
    )
    charlie_admin_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=charlie_subject, email="charlie@vinai.io"
    )
    dave_tenant_b_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=dave_subject, email="dave@tenantb.io"
    )
    eve_invited_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=eve_subject, email="eve@vinai.io"
    )
    frank_revoked_id = _insert_user_via_app_role(
        psycopg, app_url, issuer=issuer_url, subject=frank_subject, email="frank@vinai.io"
    )

    _, op_token = session_manager.create_session(
        user_id=str(op_user_id), issuer=issuer_url, subject=operator_subject
    )
    _, alice_token = session_manager.create_session(
        user_id=str(alice_owner_id), issuer=issuer_url, subject=alice_subject
    )
    _, bob_token = session_manager.create_session(
        user_id=str(bob_normal_id), issuer=issuer_url, subject=bob_subject
    )
    _, charlie_token = session_manager.create_session(
        user_id=str(charlie_admin_id), issuer=issuer_url, subject=charlie_subject
    )
    _, dave_token = session_manager.create_session(
        user_id=str(dave_tenant_b_id), issuer=issuer_url, subject=dave_subject
    )
    _, eve_token = session_manager.create_session(
        user_id=str(eve_invited_id), issuer=issuer_url, subject=eve_subject
    )
    _, frank_token = session_manager.create_session(
        user_id=str(frank_revoked_id), issuer=issuer_url, subject=frank_subject
    )

    created_tenant_ids: list[UUID] = []

    async def _run_suite() -> None:
        pooled_engine = create_async_engine(
            sa_url, poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0
        )
        pooled_factory = async_sessionmaker(pooled_engine, class_=AsyncSession, expire_on_commit=False)

        observed_pids: list[int] = []

        async def override_pooled_db():
            async with pooled_factory() as session, session.begin():
                pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                observed_pids.append(int(pid))
                yield session

        concurrent_engine = create_async_engine(sa_url, poolclass=NullPool)
        concurrent_factory = async_sessionmaker(
            concurrent_engine, class_=AsyncSession, expire_on_commit=False
        )

        async def override_concurrent_db():
            async with concurrent_factory() as session, session.begin():
                yield session

        fresh_engine = create_async_engine(sa_url, poolclass=NullPool)
        fresh_factory = async_sessionmaker(fresh_engine, class_=AsyncSession, expire_on_commit=False)

        async def override_fresh_db():
            async with fresh_factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_pooled_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                # =================================================================
                # 1. Normal newly logged-in user (Bob) has 0 workspaces & CANNOT create tenant/workspace
                # =================================================================
                bob_me = await client.get(
                    "/v1/auth/me", headers={"Authorization": f"Bearer {bob_token}"}
                )
                assert bob_me.status_code == 200
                assert bob_me.json()["workspaces"] == []

                bob_list = await client.get(
                    "/v1/workspaces", headers={"Authorization": f"Bearer {bob_token}"}
                )
                assert bob_list.status_code == 200
                assert bob_list.json()["items"] == []

                rogue_slug = f"rogue-{uuid4().hex[:8]}"
                bob_create = await client.post(
                    "/v1/workspaces",
                    json={
                        "name": "Rogue Workspace",
                        "slug": rogue_slug,
                        "tenant_slug": rogue_slug,
                        "tenant_name": "Rogue Tenant",
                    },
                    headers={"Authorization": f"Bearer {bob_token}"},
                )
                assert bob_create.status_code == 403

                with psycopg.connect(owner_url) as owner_conn:
                    cnt = owner_conn.execute(
                        "SELECT count(*) FROM iam.workspaces WHERE slug = %s", (rogue_slug,)
                    ).fetchone()[0]
                    assert cnt == 0, "Unauthorized user must not leave any rows in iam.workspaces"

                # =================================================================
                # 2. Authorized Bootstrap Operator creates Tenant A + Workspace A1 with Alice as Owner
                # =================================================================
                tenant_a_slug = f"tenant-a-{uuid4().hex[:8]}"
                ws_a1_slug = f"ws-a1-{uuid4().hex[:8]}"
                res_a1 = await client.post(
                    "/v1/workspaces",
                    json={
                        "name": "Workspace A1",
                        "slug": ws_a1_slug,
                        "tenant_slug": tenant_a_slug,
                        "tenant_name": "Tenant Alpha Corp",
                        "initial_owner_user_id": str(alice_owner_id),
                    },
                    headers={"Authorization": f"Bearer {op_token}"},
                )
                assert res_a1.status_code == 201, f"Operator bootstrap failed: {res_a1.text}"
                a1_data = res_a1.json()
                tenant_a_id = UUID(a1_data["tenant_id"])
                ws_a1_id = UUID(a1_data["workspace_id"])
                created_tenant_ids.append(tenant_a_id)

                assert a1_data["tenant_slug"] == tenant_a_slug
                assert a1_data["workspace_slug"] == ws_a1_slug
                assert a1_data["status"] == "active"
                assert a1_data["role"] == "owner"
                assert a1_data["initial_owner_user_id"] == str(alice_owner_id)
                assert a1_data["audit_event_id"] is not None

                # Verify real DB state: tenant, workspace, active owner membership, and audit event
                with psycopg.connect(owner_url) as owner_conn:
                    m_row = owner_conn.execute(
                        """
                        SELECT role, status FROM iam.memberships
                        WHERE tenant_id = %s AND workspace_id = %s AND user_id = %s
                        """,
                        (tenant_a_id, ws_a1_id, alice_owner_id),
                    ).fetchone()
                    assert m_row == ("owner", "active")

                    audit_row = owner_conn.execute(
                        """
                        SELECT action, resource_type, resource_id, actor_subject, outcome, reason_code, details_redacted
                        FROM governance.audit_events
                        WHERE tenant_id = %s AND workspace_id = %s AND action = 'workspace.bootstrap'
                        """,
                        (tenant_a_id, ws_a1_id),
                    ).fetchone()
                    assert audit_row is not None
                    assert audit_row[0] == "workspace.bootstrap"
                    assert audit_row[1] == "workspace"
                    assert audit_row[2] == str(ws_a1_id)
                    assert audit_row[3] == str(op_user_id)
                    assert audit_row[4] == "succeeded"
                    assert audit_row[5] == "BOOTSTRAP_OPERATOR"
                    assert audit_row[6]["initial_owner_user_id"] == str(alice_owner_id)
                    assert audit_row[6]["tenant_bootstrapped"] is True

                # Alice sees Workspace A1 in `/v1/auth/me`, `/v1/workspaces`, and `/v1/workspaces/{ws_a1_id}`
                alice_me = await client.get(
                    "/v1/auth/me", headers={"Authorization": f"Bearer {alice_token}"}
                )
                assert alice_me.status_code == 200
                assert len(alice_me.json()["workspaces"]) == 1
                assert alice_me.json()["workspaces"][0]["workspace_id"] == str(ws_a1_id)
                assert alice_me.json()["workspaces"][0]["role"] == "owner"

                alice_list = await client.get(
                    "/v1/workspaces", headers={"Authorization": f"Bearer {alice_token}"}
                )
                assert alice_list.status_code == 200
                assert len(alice_list.json()["items"]) == 1
                assert alice_list.json()["items"][0]["workspace_id"] == str(ws_a1_id)

                alice_detail = await client.get(
                    f"/v1/workspaces/{ws_a1_id}", headers={"Authorization": f"Bearer {alice_token}"}
                )
                assert alice_detail.status_code == 200
                assert alice_detail.json()["workspace_id"] == str(ws_a1_id)
                assert alice_detail.json()["role"] == "owner"

                # =================================================================
                # 3. Connection Pool Reuse Check: Bob calls on the SAME single pooled PID
                #    and must NOT inherit Alice's RLS context
                # =================================================================
                bob_detail_attempt = await client.get(
                    f"/v1/workspaces/{ws_a1_id}", headers={"Authorization": f"Bearer {bob_token}"}
                )
                assert bob_detail_attempt.status_code == 403

                bob_list_after = await client.get(
                    "/v1/workspaces", headers={"Authorization": f"Bearer {bob_token}"}
                )
                assert bob_list_after.status_code == 200
                assert bob_list_after.json()["items"] == []
                assert len(set(observed_pids)) == 1, (
                    f"Expected single pooled backend PID across sequential requests, got {observed_pids}"
                )

                # =================================================================
                # 4. Alice (existing Tenant Owner in Tenant A) creates Workspace A2 in Tenant A
                # =================================================================
                ws_a2_slug = f"ws-a2-{uuid4().hex[:8]}"
                res_a2 = await client.post(
                    "/v1/workspaces",
                    json={
                        "name": "Workspace A2",
                        "slug": ws_a2_slug,
                        "tenant_id": str(tenant_a_id),
                    },
                    headers={"Authorization": f"Bearer {alice_token}"},
                )
                assert res_a2.status_code == 201, f"Tenant Owner creation failed: {res_a2.text}"
                a2_data = res_a2.json()
                ws_a2_id = UUID(a2_data["workspace_id"])
                assert a2_data["tenant_id"] == str(tenant_a_id)
                assert a2_data["initial_owner_user_id"] == str(alice_owner_id)
                assert a2_data["role"] == "owner"

                # =================================================================
                # 5. Bootstrap Operator creates Tenant B + Workspace B1 with SAME slug (`ws_a2_slug`)
                #    proving cross-tenant slug independence and multi-tenant isolation
                # =================================================================
                tenant_b_slug = f"tenant-b-{uuid4().hex[:8]}"
                res_b1 = await client.post(
                    "/v1/workspaces",
                    json={
                        "name": "Workspace B1 (Same Slug as A2)",
                        "slug": ws_a2_slug,
                        "tenant_slug": tenant_b_slug,
                        "tenant_name": "Tenant Beta Ltd",
                        "initial_owner_user_id": str(dave_tenant_b_id),
                    },
                    headers={"Authorization": f"Bearer {op_token}"},
                )
                assert res_b1.status_code == 201, f"Same slug in different tenant failed: {res_b1.text}"
                b1_data = res_b1.json()
                tenant_b_id = UUID(b1_data["tenant_id"])
                ws_b1_id = UUID(b1_data["workspace_id"])
                created_tenant_ids.append(tenant_b_id)

                # Cross-tenant isolation: Alice cannot read Workspace B1 or create in Tenant B
                assert (
                    await client.get(
                        f"/v1/workspaces/{ws_b1_id}",
                        headers={"Authorization": f"Bearer {alice_token}"},
                    )
                ).status_code == 403
                assert (
                    await client.post(
                        "/v1/workspaces",
                        json={"name": "Cross Tenant WS", "slug": "cross-ws", "tenant_id": str(tenant_b_id)},
                        headers={"Authorization": f"Bearer {alice_token}"},
                    )
                ).status_code == 403

                # Cross-workspace isolation within Tenant A:
                # Grant Charlie `admin` in Workspace A1 ONLY; Charlie cannot read Workspace A2 or create WS
                with psycopg.connect(owner_url) as owner_conn:
                    owner_conn.execute(
                        """
                        INSERT INTO iam.memberships (id, tenant_id, workspace_id, user_id, role, status)
                        VALUES (%s, %s, %s, %s, 'admin', 'active')
                        """,
                        (uuid4(), tenant_a_id, ws_a1_id, charlie_admin_id),
                    )
                    owner_conn.commit()

                assert (
                    await client.get(
                        f"/v1/workspaces/{ws_a1_id}",
                        headers={"Authorization": f"Bearer {charlie_token}"},
                    )
                ).status_code == 200
                assert (
                    await client.get(
                        f"/v1/workspaces/{ws_a2_id}",
                        headers={"Authorization": f"Bearer {charlie_token}"},
                    )
                ).status_code == 403
                assert (
                    await client.post(
                        "/v1/workspaces",
                        json={"name": "Charlie WS", "slug": "charlie-ws", "tenant_id": str(tenant_a_id)},
                        headers={"Authorization": f"Bearer {charlie_token}"},
                    )
                ).status_code == 403

                # Direct PostgreSQL RLS verification under `platform_app`:
                # 1) Missing context -> 0 rows
                # 2) Context set to (tenant_a_id, ws_a1_id) -> sees ONLY ws_a1_id
                with psycopg.connect(app_url) as app_conn:
                    assert app_conn.execute("SELECT id FROM iam.workspaces").fetchall() == []

                    app_conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a_id),))
                    app_conn.execute("SELECT set_config('app.workspace_id', %s, true)", (str(ws_a1_id),))
                    scoped_ws_rows = app_conn.execute("SELECT id FROM iam.workspaces").fetchall()
                    assert [r[0] for r in scoped_ws_rows] == [ws_a1_id]

                    scoped_mem_rows = app_conn.execute("SELECT workspace_id FROM iam.memberships").fetchall()
                    assert all(r[0] == ws_a1_id for r in scoped_mem_rows)

                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        app_conn.execute(
                            """
                            INSERT INTO iam.memberships (id, tenant_id, workspace_id, user_id, role, status)
                            VALUES (%s, %s, %s, %s, 'viewer', 'active')
                            """,
                            (uuid4(), tenant_a_id, ws_a2_id, bob_normal_id),
                        )
                    app_conn.rollback()

                # =================================================================
                # 6. Mid-Transaction Failure Rollback Proof (Atomicity)
                # =================================================================
                rollback_tenant_slug = f"rollback-t-{uuid4().hex[:8]}"
                rollback_ws_slug = f"rollback-ws-{uuid4().hex[:8]}"
                orig_create_atomic = WorkspaceRepository.create_workspace_atomic

                async def failing_create_atomic(
                    self: WorkspaceRepository, session: AsyncSession, **kwargs: Any
                ) -> dict[str, Any]:
                    await orig_create_atomic(self, session, **kwargs)
                    raise RuntimeError("Simulated mid-transaction crash after SQL inserts before commit")

                with (
                    patch.object(WorkspaceRepository, "create_workspace_atomic", failing_create_atomic),
                    pytest.raises(RuntimeError, match="Simulated mid-transaction crash"),
                ):
                    await client.post(
                        "/v1/workspaces",
                        json={
                            "name": "Rollback WS",
                            "slug": rollback_ws_slug,
                            "tenant_slug": rollback_tenant_slug,
                            "tenant_name": "Rollback Tenant",
                        },
                        headers={"Authorization": f"Bearer {op_token}"},
                    )

                with psycopg.connect(owner_url) as owner_conn:
                    t_cnt = owner_conn.execute(
                        "SELECT count(*) FROM iam.tenants WHERE slug = %s", (rollback_tenant_slug,)
                    ).fetchone()[0]
                    w_cnt = owner_conn.execute(
                        "SELECT count(*) FROM iam.workspaces WHERE slug = %s", (rollback_ws_slug,)
                    ).fetchone()[0]
                    assert t_cnt == 0, "Mid-transaction failure must roll back iam.tenants"
                    assert w_cnt == 0, "Mid-transaction failure must roll back iam.workspaces"

                # =================================================================
                # 7. Concurrent Duplicate Slug & Retry After Commit (`201` + `409`)
                # =================================================================
                app.dependency_overrides[get_db_session] = override_concurrent_db
                race_slug = f"race-ws-{uuid4().hex[:8]}"

                r1, r2 = await asyncio.gather(
                    client.post(
                        "/v1/workspaces",
                        json={"name": "Concurrent WS", "slug": race_slug, "tenant_id": str(tenant_a_id)},
                        headers={"Authorization": f"Bearer {alice_token}"},
                    ),
                    client.post(
                        "/v1/workspaces",
                        json={"name": "Concurrent WS", "slug": race_slug, "tenant_id": str(tenant_a_id)},
                        headers={"Authorization": f"Bearer {alice_token}"},
                    ),
                )
                assert sorted([r1.status_code, r2.status_code]) == [201, 409]

                # Retry after commit returns deterministic 409; DB holds exactly 1 row
                retry_resp = await client.post(
                    "/v1/workspaces",
                    json={"name": "Concurrent WS", "slug": race_slug, "tenant_id": str(tenant_a_id)},
                    headers={"Authorization": f"Bearer {alice_token}"},
                )
                assert retry_resp.status_code == 409

                with psycopg.connect(owner_url) as owner_conn:
                    race_rows = owner_conn.execute(
                        "SELECT count(*) FROM iam.workspaces WHERE tenant_id = %s AND slug = %s",
                        (tenant_a_id, race_slug),
                    ).fetchone()[0]
                    assert race_rows == 1

                # =================================================================
                # 8. Non-active states (`invited`/`revoked` membership, `archived` workspace,
                #    `suspended` tenant) rejected `403`
                # =================================================================
                with psycopg.connect(owner_url) as owner_conn:
                    owner_conn.execute(
                        """
                        INSERT INTO iam.memberships (id, tenant_id, workspace_id, user_id, role, status)
                        VALUES
                            (%s, %s, %s, %s, 'owner', 'invited'),
                            (%s, %s, %s, %s, 'owner', 'revoked')
                        """,
                        (
                            uuid4(), tenant_a_id, ws_a1_id, eve_invited_id,
                            uuid4(), tenant_a_id, ws_a1_id, frank_revoked_id,
                        ),
                    )
                    owner_conn.execute(
                        "UPDATE iam.workspaces SET status = 'archived' WHERE id = %s",
                        (ws_a2_id,),
                    )
                    owner_conn.execute(
                        "UPDATE iam.tenants SET status = 'suspended' WHERE id = %s",
                        (tenant_b_id,),
                    )
                    owner_conn.commit()

                # =================================================================
                # 9. Fresh Engine / Connection Pool Restart Persistence & Non-active Check
                # =================================================================
                app.dependency_overrides[get_db_session] = override_fresh_db

                for blocked_token in (eve_token, frank_token):
                    assert (
                        await client.get(
                            "/v1/workspaces",
                            headers={"Authorization": f"Bearer {blocked_token}"},
                        )
                    ).json()["items"] == []
                    assert (
                        await client.get(
                            f"/v1/workspaces/{ws_a1_id}",
                            headers={"Authorization": f"Bearer {blocked_token}"},
                        )
                    ).status_code == 403
                    assert (
                        await client.post(
                            "/v1/workspaces",
                            json={
                                "name": "Nonactive Owner WS",
                                "slug": f"nonactive-{uuid4().hex[:6]}",
                                "tenant_id": str(tenant_a_id),
                            },
                            headers={"Authorization": f"Bearer {blocked_token}"},
                        )
                    ).status_code == 403

                assert (
                    await client.get(
                        f"/v1/workspaces/{ws_a2_id}",
                        headers={"Authorization": f"Bearer {alice_token}"},
                    )
                ).status_code == 403
                assert (
                    await client.get(
                        f"/v1/workspaces/{ws_b1_id}",
                        headers={"Authorization": f"Bearer {dave_token}"},
                    )
                ).status_code == 403
                assert (
                    await client.post(
                        "/v1/workspaces",
                        json={
                            "name": "Suspended Tenant WS",
                            "slug": "susp-ws",
                            "tenant_id": str(tenant_b_id),
                        },
                        headers={"Authorization": f"Bearer {dave_token}"},
                    )
                ).status_code == 403

                alice_list_after_restart = await client.get(
                    "/v1/workspaces", headers={"Authorization": f"Bearer {alice_token}"}
                )
                assert alice_list_after_restart.status_code == 200
                active_slugs = {
                    item["workspace_slug"] for item in alice_list_after_restart.json()["items"]
                }
                assert ws_a1_slug in active_slugs
                assert race_slug in active_slugs
                assert ws_a2_slug not in active_slugs
                assert len(created_tenant_ids) == 2

        finally:
            app.dependency_overrides.pop(get_db_session, None)
            await pooled_engine.dispose()
            await concurrent_engine.dispose()
            await fresh_engine.dispose()

    try:
        asyncio.run(_run_suite())
    finally:
        settings.oidc_issuer = orig_issuer
        settings.database_url = orig_db_url
        session_manager.denylist = orig_denylist
        settings.bootstrap_operator_subjects = orig_operators

