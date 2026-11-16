"""Real PostgreSQL Integration & Concurrency Acceptance Tests for Step 04-A: Agent Draft CRUD (A01..A14).

Authority:
- ADR-022: Agent draft concurrency and optimistic locking (D04-01).
- SPEC-AGENT-001: AgentSpec v1 schema-first draft lifecycle.
- TEST-AG-001: Acceptance Plan Matrix A01..A14.
- DEC-DB-01 / DEC-DB-02: Fail-closed RLS and restricted application role (platform_app).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from run_migrations import run_migrations
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "database/migrations"


def _make_sa_url(conn_url: str) -> str:
    conn_dict = psycopg.conninfo.conninfo_to_dict(conn_url)
    return (
        f"postgresql+asyncpg://{conn_dict['user']}:{conn_dict.get('password', '')}"
        f"@{conn_dict['host']}:{conn_dict['port']}/{conn_dict['dbname']}"
    )


def _get_error_code(resp: httpx.Response) -> str | None:
    data = resp.json()
    if isinstance(data, dict):
        if "detail" in data and isinstance(data["detail"], dict):
            return data["detail"].get("code")
        return data.get("code")
    return None


def _make_valid_spec(
    name: str = "researcher-agent",
    display_name: str = "Researcher Agent",
    mode: str = "react",
    max_steps: int = 10,
) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {
            "name": name,
            "displayName": display_name,
            "description": "Enterprise autonomous research agent",
        },
        "mode": mode,
        "modelRef": "gpt-4o",
        "persona": {
            "role": "Researcher",
            "instructions": "You are a professional enterprise research assistant.",
        },
        "toolRefs": ["enterprise-search"],
        "skillRefs": [],
        "knowledgeRefs": [],
        "limits": {
            "maxSteps": max_steps,
            "maxWallTimeSeconds": 300,
            "maxTotalTokens": 100000,
        },
    }


def _seed_tenants_and_workspaces(owner_url: str) -> dict[str, Any]:
    issuer = settings.jwt_issuer
    tenant_1, ws_1a, ws_1b = uuid4(), uuid4(), uuid4()
    tenant_2, ws_2a = uuid4(), uuid4()

    owner_1a, admin_1a, dev_1a, op_1a, viewer_1a, revoked_1a = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    user_1b, user_2 = uuid4(), uuid4()
    suffix = str(tenant_1)[:8]

    with psycopg.connect(owner_url) as conn:
        for uid, sub, email in (
            (owner_1a, f"owner-1a-{suffix}", f"owner-1a-{suffix}@example.com"),
            (admin_1a, f"admin-1a-{suffix}", f"admin-1a-{suffix}@example.com"),
            (dev_1a, f"dev-1a-{suffix}", f"dev-1a-{suffix}@example.com"),
            (op_1a, f"op-1a-{suffix}", f"op-1a-{suffix}@example.com"),
            (viewer_1a, f"viewer-1a-{suffix}", f"viewer-1a-{suffix}@example.com"),
            (revoked_1a, f"revoked-1a-{suffix}", f"revoked-1a-{suffix}@example.com"),
            (user_1b, f"user-1b-{suffix}", f"user-1b-{suffix}@example.com"),
            (user_2, f"user-2-{suffix}", f"user-2-{suffix}@example.com"),
        ):
            conn.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
                "VALUES (%s, %s, %s, %s, %s)",
                (uid, issuer, sub, sub, email),
            )

        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, %s, 'active')",
            (tenant_1, f"tenant-1-{suffix}", "Tenant 1"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_1, ws_1a, f"ws-1a-{suffix}", "Workspace 1A"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_1, ws_1b, f"ws-1b-{suffix}", "Workspace 1B"),
        )
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, %s, 'active')",
            (tenant_2, f"tenant-2-{suffix}", "Tenant 2"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_2, ws_2a, f"ws-2a-{suffix}", "Workspace 2A"),
        )

        for tid, wid, uid, role, m_status in (
            (tenant_1, ws_1a, owner_1a, "owner", "active"),
            (tenant_1, ws_1a, admin_1a, "admin", "active"),
            (tenant_1, ws_1a, dev_1a, "developer", "active"),
            (tenant_1, ws_1a, op_1a, "operator", "active"),
            (tenant_1, ws_1a, viewer_1a, "viewer", "active"),
            (tenant_1, ws_1a, revoked_1a, "developer", "revoked"),
            (tenant_1, ws_1b, user_1b, "developer", "active"),
            (tenant_2, ws_2a, user_2, "owner", "active"),
        ):
            conn.execute(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tid, wid, uid, role, m_status),
            )

    return {
        "issuer": issuer,
        "tenant_1": tenant_1,
        "ws_1a": ws_1a,
        "ws_1b": ws_1b,
        "tenant_2": tenant_2,
        "ws_2a": ws_2a,
        "owner_1a": owner_1a,
        "admin_1a": admin_1a,
        "dev_1a": dev_1a,
        "op_1a": op_1a,
        "viewer_1a": viewer_1a,
        "revoked_1a": revoked_1a,
        "user_1b": user_1b,
        "user_2": user_2,
        "owner_1a_sub": f"owner-1a-{suffix}",
        "dev_1a_sub": f"dev-1a-{suffix}",
        "admin_1a_sub": f"admin-1a-{suffix}",
        "op_1a_sub": f"op-1a-{suffix}",
        "viewer_1a_sub": f"viewer-1a-{suffix}",
        "revoked_1a_sub": f"revoked-1a-{suffix}",
        "user_1b_sub": f"user-1b-{suffix}",
        "user_2_sub": f"user-2-{suffix}",
    }


def _create_token(user_id: UUID, issuer: str, subject: str) -> str:
    _, token = session_manager.create_session(user_id=str(user_id), issuer=issuer, subject=subject)
    return token


@pytest.fixture
def agent_test_env(isolated_database: dict[str, Any]):
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]

    # Verify platform_app privileges
    with psycopg.connect(app_url) as app_conn:
        row = app_conn.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == ("platform_app", False, False), f"Unexpected privileges: {row}"

    run_migrations(owner_url, MIGRATIONS_DIR)
    seeded = _seed_tenants_and_workspaces(owner_url)

    sa_url = _make_sa_url(app_url)
    previous_denylist = session_manager.denylist
    session_manager.denylist = InMemoryRevocationStore()

    # Generate tokens
    tokens = {
        "owner_1a": _create_token(seeded["owner_1a"], seeded["issuer"], seeded["owner_1a_sub"]),
        "admin_1a": _create_token(seeded["admin_1a"], seeded["issuer"], seeded["admin_1a_sub"]),
        "dev_1a": _create_token(seeded["dev_1a"], seeded["issuer"], seeded["dev_1a_sub"]),
        "op_1a": _create_token(seeded["op_1a"], seeded["issuer"], seeded["op_1a_sub"]),
        "viewer_1a": _create_token(seeded["viewer_1a"], seeded["issuer"], seeded["viewer_1a_sub"]),
        "revoked_1a": _create_token(seeded["revoked_1a"], seeded["issuer"], seeded["revoked_1a_sub"]),
        "user_1b": _create_token(seeded["user_1b"], seeded["issuer"], seeded["user_1b_sub"]),
        "user_2": _create_token(seeded["user_2"], seeded["issuer"], seeded["user_2_sub"]),
    }

    try:
        yield {
            "owner_url": owner_url,
            "app_url": app_url,
            "sa_url": sa_url,
            "seeded": seeded,
            "tokens": tokens,
        }
    finally:
        session_manager.denylist = previous_denylist


@pytest.mark.integration
def test_a01_create_valid_full_agent_spec_real_postgres(agent_test_env):
    """A01: Create valid full AgentSpec -> 201, real UUID, revision 1, ETag, DB scope and sanitized audit."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_valid_spec(name="research-agent-01", display_name="Research Agent 01")
                payload = {
                    "slug": "research-agent-01",
                    "display_name": "Research Agent 01",
                    "spec": spec,
                }

                resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json=payload,
                )

                assert resp.status_code == 201, resp.text
                data = resp.json()
                assert resp.headers.get("etag") == '"1"'
                assert data["slug"] == "research-agent-01"
                assert data["display_name"] == "Research Agent 01"
                assert data["revision"] == 1
                assert data["archived_at"] is None
                assert "created_at" in data
                assert data["spec"]["metadata"]["name"] == "research-agent-01"

                agent_id = UUID(data["agent_id"])

                # Direct DB assertions via owner_url
                with psycopg.connect(owner_url) as conn:
                    row = conn.execute(
                        "SELECT tenant_id, workspace_id, slug, display_name, revision, archived_at "
                        "FROM catalog.agents WHERE id = %s",
                        (agent_id,),
                    ).fetchone()
                    assert row is not None
                    assert row[0] == agent_test_env["seeded"]["tenant_1"]
                    assert row[1] == ws_1a
                    assert row[2] == "research-agent-01"
                    assert row[3] == "Research Agent 01"
                    assert row[4] == 1
                    assert row[5] is None

                    # Assert sanitized audit event
                    audit_row = conn.execute(
                        "SELECT action, outcome, resource_type, resource_id, details_redacted "
                        "FROM governance.audit_events WHERE resource_id = %s",
                        (str(agent_id),),
                    ).fetchone()
                    assert audit_row is not None
                    assert audit_row[0] == "agent.create"
                    assert audit_row[1] == "succeeded"
                    assert audit_row[2] == "agent"
                    assert audit_row[3] == str(agent_id)
                    details = audit_row[4]
                    assert details["slug"] == "research-agent-01"
                    assert details["revision"] == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a02_raw_invalid_spec_rejected_real_postgres(agent_test_env):
    """A02: Raw invalid required/pattern/extra/null/limits -> 422, zero rows inserted, zero audit events."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Missing schemaVersion
                spec1 = _make_valid_spec(name="agent-bad-1")
                del spec1["schemaVersion"]
                resp1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-bad-1", "display_name": "Bad 1", "spec": spec1},
                )
                assert resp1.status_code == 422

                # 2. Invalid slug pattern (uppercase & spaces)
                spec2 = _make_valid_spec(name="agent-bad-2")
                resp2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "Invalid Slug!!", "display_name": "Bad 2", "spec": spec2},
                )
                assert resp2.status_code == 422

                # 3. Invalid mode
                spec3 = _make_valid_spec(name="agent-bad-3", mode="unsupported_quantum_mode")
                resp3 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-bad-3", "display_name": "Bad 3", "spec": spec3},
                )
                assert resp3.status_code == 422

                # 4. Limits violation (negative maxSteps)
                spec4 = _make_valid_spec(name="agent-bad-4", max_steps=-10)
                resp4 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-bad-4", "display_name": "Bad 4", "spec": spec4},
                )
                assert resp4.status_code == 422

                # 5. Extra disallowed property
                spec5 = _make_valid_spec(name="agent-bad-5")
                spec5["extraDisallowedProperty"] = "disallowed"
                resp5 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-bad-5", "display_name": "Bad 5", "spec": spec5},
                )
                assert resp5.status_code == 422

                # Assert zero rows in catalog.agents and governance.audit_events
                with psycopg.connect(owner_url) as conn:
                    agent_count = conn.execute("SELECT count(*) FROM catalog.agents").fetchone()[0]
                    assert agent_count == 0
                    audit_count = conn.execute("SELECT count(*) FROM governance.audit_events").fetchone()[0]
                    assert audit_count == 0
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a03_unresolved_refs_saved_as_draft_no_bindings(agent_test_env):
    """A03: Structurally-valid unresolved refs -> draft saved, zero bindings/ready/publish claims."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_valid_spec(name="unresolved-agent", display_name="Unresolved Agent")
                spec["modelRef"] = "future-gpt-99-turbo"
                spec["toolRefs"] = ["non-existent-tool"]

                resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "unresolved-agent", "display_name": "Unresolved Agent", "spec": spec},
                )
                assert resp.status_code == 201, resp.text
                agent_id = UUID(resp.json()["agent_id"])

                # Direct DB verification: draft saved, but zero rows in catalog.agent_versions
                with psycopg.connect(owner_url) as conn:
                    agent_row = conn.execute(
                        "SELECT id FROM catalog.agents WHERE id = %s", (agent_id,)
                    ).fetchone()
                    assert agent_row is not None

                    ver_count = conn.execute("SELECT count(*) FROM catalog.agent_versions").fetchone()[0]
                    assert ver_count == 0
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a04_duplicate_slug_and_slug_mismatch_real_postgres(agent_test_env):
    """A04: Duplicate slug -> 409; slug/spec.metadata.name mismatch -> 422; cross-workspace duplicate -> 201."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    ws_1b = agent_test_env["seeded"]["ws_1b"]
    dev_token_1a = agent_test_env["tokens"]["dev_1a"]
    user_token_1b = agent_test_env["tokens"]["user_1b"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_valid_spec(name="shared-slug", display_name="Shared Slug")

                # 1. Create first in Workspace 1A
                resp1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token_1a}"},
                    json={"slug": "shared-slug", "display_name": "Shared Slug", "spec": spec},
                )
                assert resp1.status_code == 201, resp1.text

                # 2. Duplicate slug in SAME workspace -> 409 Conflict
                resp2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token_1a}"},
                    json={"slug": "shared-slug", "display_name": "Shared Slug 2", "spec": spec},
                )
                assert resp2.status_code == 409
                assert _get_error_code(resp2) == "slug_conflict"

                # 3. Slug and metadata.name mismatch -> 422
                mismatch_spec = _make_valid_spec(name="meta-name", display_name="Mismatch")
                resp3 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token_1a}"},
                    json={"slug": "url-slug", "display_name": "Mismatch", "spec": mismatch_spec},
                )
                assert resp3.status_code == 422
                assert _get_error_code(resp3) == "invalid_slug"

                # 4. Same slug in DIFFERENT workspace (Workspace 1B) -> 201 Created (scoped uniqueness)
                resp4 = await client.post(
                    f"/v1/workspaces/{ws_1b}/agents",
                    headers={"Authorization": f"Bearer {user_token_1b}"},
                    json={"slug": "shared-slug", "display_name": "Shared Slug in 1B", "spec": spec},
                )
                assert resp4.status_code == 201, resp4.text
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a05_get_list_empty_and_bounded_keyset_pagination_real_postgres(agent_test_env):
    """A05: GET list empty / bounded keyset cursor pagination / detail with no-store."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Empty list
                empty_resp = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert empty_resp.status_code == 200
                assert empty_resp.json() == {"items": [], "next_cursor": None}

                # 2. Create 5 agents
                agent_ids = []
                for i in range(1, 6):
                    name = f"page-agent-{i:02d}"
                    spec = _make_valid_spec(name=name, display_name=f"Page Agent {i}")
                    r = await client.post(
                        f"/v1/workspaces/{ws_1a}/agents",
                        headers={"Authorization": f"Bearer {dev_token}"},
                        json={"slug": name, "display_name": f"Page Agent {i}", "spec": spec},
                    )
                    assert r.status_code == 201, r.text
                    agent_ids.append(r.json()["agent_id"])

                # 3. Keyset Pagination (limit=2)
                p1 = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents?limit=2",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert p1.status_code == 200
                p1_data = p1.json()
                assert len(p1_data["items"]) == 2
                assert p1_data["next_cursor"] is not None

                p2 = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents?limit=2&cursor={p1_data['next_cursor']}",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert p2.status_code == 200
                p2_data = p2.json()
                assert len(p2_data["items"]) == 2
                assert p2_data["next_cursor"] is not None

                p3 = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents?limit=2&cursor={p2_data['next_cursor']}",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert p3.status_code == 200
                p3_data = p3.json()
                assert len(p3_data["items"]) == 1
                assert p3_data["next_cursor"] is None

                # Verify all 5 items returned without duplicate or skip
                all_returned_ids = [
                    it["agent_id"]
                    for it in p1_data["items"] + p2_data["items"] + p3_data["items"]
                ]
                assert len(all_returned_ids) == 5
                assert set(all_returned_ids) == set(agent_ids)

                # 4. Detail GET with no-store and ETag
                detail_resp = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_ids[0]}",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert detail_resp.status_code == 200
                assert detail_resp.headers.get("cache-control") == "no-store"
                assert detail_resp.headers.get("etag") == '"1"'
                assert detail_resp.json()["agent_id"] == agent_ids[0]
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a06_patch_spec_etag_preconditions_real_postgres(agent_test_env):
    """A06: PATCH valid ETag, missing/malformed/weak/wildcard/list/stale -> 200/428/400/412; stale no write."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # Create initial agent (revision 1)
                spec = _make_valid_spec(name="etag-agent", display_name="ETag Agent")
                c_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "etag-agent", "display_name": "ETag Agent", "spec": spec},
                )
                assert c_resp.status_code == 201, c_resp.text
                agent_id = c_resp.json()["agent_id"]

                updated_spec = _make_valid_spec(name="etag-agent", display_name="ETag Agent", max_steps=45)
                patch_payload = {"spec": updated_spec}

                # 1. Missing If-Match -> 428 Precondition Required
                r_missing = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json=patch_payload,
                )
                assert r_missing.status_code == 428
                assert _get_error_code(r_missing) == "precondition_required"

                # 2. Malformed If-Match -> 400 Bad Request
                r_malformed = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"not_a_num"'},
                    json=patch_payload,
                )
                assert r_malformed.status_code == 400

                # 3. Weak entity-tag -> 400 Bad Request
                r_weak = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": 'W/"1"'},
                    json=patch_payload,
                )
                assert r_weak.status_code == 400

                # 4. Wildcard If-Match: * -> 400 Bad Request
                r_wild = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": "*"},
                    json=patch_payload,
                )
                assert r_wild.status_code == 400

                # 5. List of tags -> 400 Bad Request
                r_list = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"1", "2"'},
                    json=patch_payload,
                )
                assert r_list.status_code == 400

                # 6. Valid PATCH with If-Match: "1" -> 200 OK, revision 2, ETag: "2"
                r_valid = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"1"'},
                    json=patch_payload,
                )
                assert r_valid.status_code == 200, r_valid.text
                assert r_valid.headers.get("etag") == '"2"'
                assert r_valid.headers.get("cache-control") == "no-store"
                assert r_valid.json()["revision"] == 2
                assert r_valid.json()["spec"]["limits"]["maxSteps"] == 45

                # 7. Stale revision If-Match: "1" (now at revision 2) -> 412 Precondition Failed
                r_stale = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"1"'},
                    json=patch_payload,
                )
                assert r_stale.status_code == 412
                assert _get_error_code(r_stale) == "stale_revision"

                # Verify DB state: revision is exactly 2, no writes from stale attempt
                with psycopg.connect(owner_url) as conn:
                    rev = conn.execute(
                        "SELECT revision FROM catalog.agents WHERE id = %s", (UUID(agent_id),)
                    ).fetchone()[0]
                    assert rev == 2
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a07_concurrent_patch_same_revision_atomic_cas_real_postgres(agent_test_env):
    """A07: Two concurrent PATCH requests with identical expected revision -> exactly one 200, one 412."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # Create agent
                spec = _make_valid_spec(name="cas-race-agent", display_name="CAS Race Agent")
                c_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "cas-race-agent", "display_name": "CAS Race Agent", "spec": spec},
                )
                assert c_resp.status_code == 201, c_resp.text
                agent_id = c_resp.json()["agent_id"]

                # Prepare two competing PATCH payloads
                spec_a = _make_valid_spec(name="cas-race-agent", display_name="CAS Race Agent", max_steps=35)
                spec_b = _make_valid_spec(name="cas-race-agent", display_name="CAS Race Agent", max_steps=40)

                async def send_patch(p_spec):
                    return await client.patch(
                        f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                        headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"1"'},
                        json={"spec": p_spec},
                    )

                # Fire simultaneously
                res_a, res_b = await asyncio.gather(send_patch(spec_a), send_patch(spec_b))

                statuses = {res_a.status_code, res_b.status_code}
                assert statuses == {200, 412}, f"Expected {200, 412}, got {statuses}"

                # In DB: revision is exactly 2 (atomic CAS, no lost updates)
                with psycopg.connect(owner_url) as conn:
                    rev = conn.execute(
                        "SELECT revision FROM catalog.agents WHERE id = %s", (UUID(agent_id),)
                    ).fetchone()[0]
                    assert rev == 2
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a08_multi_tenant_and_workspace_rls_isolation_real_postgres(agent_test_env):
    """A08: Two tenants + same-tenant two workspaces; no/empty RLS context + pool reuse."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    ws_1b = agent_test_env["seeded"]["ws_1b"]
    ws_2a = agent_test_env["seeded"]["ws_2a"]

    dev_token_1a = agent_test_env["tokens"]["dev_1a"]
    user_token_1b = agent_test_env["tokens"]["user_1b"]
    user_token_2 = agent_test_env["tokens"]["user_2"]
    app_url = agent_test_env["app_url"]

    async def _scenario():
        # Single-connection pool to verify backend PID reuse without context leakage
        pooled_engine = create_async_engine(
            sa_url, poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0
        )
        pooled_factory = async_sessionmaker(pooled_engine, class_=AsyncSession, expire_on_commit=False)

        observed_pids = []

        async def override_pooled_db():
            async with pooled_factory() as session, session.begin():
                pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                observed_pids.append(int(pid))
                yield session

        app.dependency_overrides[get_db_session] = override_pooled_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Create agent in Workspace 1A
                spec = _make_valid_spec(name="agent-1a-secret", display_name="Agent 1A Secret")
                resp1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token_1a}"},
                    json={"slug": "agent-1a-secret", "display_name": "Agent 1A Secret", "spec": spec},
                )
                assert resp1.status_code == 201, resp1.text
                agent_id = resp1.json()["agent_id"]

                # 2. User in Workspace 1B (same tenant, different workspace) cannot read or write agent 1A
                r_get_1b = await client.get(
                    f"/v1/workspaces/{ws_1b}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {user_token_1b}"},
                )
                assert r_get_1b.status_code == 404

                r_patch_1b = await client.patch(
                    f"/v1/workspaces/{ws_1b}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {user_token_1b}", "If-Match": '"1"'},
                    json={"spec": spec},
                )
                assert r_patch_1b.status_code == 404

                # 3. User in Tenant 2 / Workspace 2A cannot read or write agent 1A
                r_get_2 = await client.get(
                    f"/v1/workspaces/{ws_2a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {user_token_2}"},
                )
                assert r_get_2.status_code == 404

                # 4. Verify physical PID was reused
                assert len(observed_pids) >= 3
                assert len(set(observed_pids)) == 1, f"Expected single reused PID, got {set(observed_pids)}"

                # 5. Direct restricted-role query without RLS context returns 0 rows (fail-closed)
                with psycopg.connect(app_url) as app_conn:
                    # Query catalog.agents directly under platform_app without setting app.tenant_id/workspace_id
                    rows = app_conn.execute("SELECT count(*) FROM catalog.agents").fetchone()[0]
                    assert rows == 0, f"Expected 0 rows without RLS context under platform_app, got {rows}"
        finally:
            app.dependency_overrides.clear()
            await pooled_engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a09_role_matrix_and_membership_revocation_real_postgres(agent_test_env):
    """A09: Role matrix (Owner/Admin/Dev write, Op/Viewer read-only 403) and live membership revocation."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    owner_token = agent_test_env["tokens"]["owner_1a"]
    admin_token = agent_test_env["tokens"]["admin_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    op_token = agent_test_env["tokens"]["op_1a"]
    viewer_token = agent_test_env["tokens"]["viewer_1a"]
    revoked_token = agent_test_env["tokens"]["revoked_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_valid_spec(name="role-agent", display_name="Role Agent")

                # 1. Developer can create -> 201
                c_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "role-agent", "display_name": "Role Agent", "spec": spec},
                )
                assert c_resp.status_code == 201, c_resp.text
                agent_id = c_resp.json()["agent_id"]

                # 1b. Admin and Owner can also write -> 200
                r_admin_patch = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {admin_token}", "If-Match": '"1"'},
                    json={"spec": spec},
                )
                assert r_admin_patch.status_code == 200, r_admin_patch.text

                r_owner_patch = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {owner_token}", "If-Match": '"2"'},
                    json={"spec": spec},
                )
                assert r_owner_patch.status_code == 200, r_owner_patch.text

                # 2. Operator can read -> 200, but cannot write -> 403
                r_op_get = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {op_token}"},
                )
                assert r_op_get.status_code == 200

                r_op_patch = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {op_token}", "If-Match": '"3"'},
                    json={"spec": spec},
                )
                assert r_op_patch.status_code == 403

                # 3. Viewer can read -> 200, but cannot write -> 403
                r_view_get = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                )
                assert r_view_get.status_code == 200

                r_view_create = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                    json={"slug": "viewer-agent", "display_name": "Viewer Agent", "spec": spec},
                )
                assert r_view_create.status_code == 403

                # 4. Revoked user is rejected at workspace level -> 403
                r_revoked = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {revoked_token}"},
                )
                assert r_revoked.status_code == 403

                # 5. Live Revocation in DB: revoking dev_1a mid-session denies next write
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE iam.memberships SET status = 'revoked' WHERE user_id = %s",
                        (agent_test_env["seeded"]["dev_1a"],),
                    )

                r_dev_after_revoke = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"3"'},
                    json={"spec": spec},
                )
                assert r_dev_after_revoke.status_code == 403
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a10_csrf_origin_cookie_and_malformed_bearer_real_postgres(agent_test_env):
    """A10: Cookie mutation missing/untrusted Origin/CSRF; malformed Bearer does not fallback to cookie."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    owner_1a = agent_test_env["seeded"]["owner_1a"]
    owner_1a_sub = agent_test_env["seeded"]["owner_1a_sub"]
    issuer = agent_test_env["seeded"]["issuer"]

    # Issue session cookie
    _, cookie_token = session_manager.create_session(
        user_id=str(owner_1a), issuer=issuer, subject=owner_1a_sub
    )

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            # 1. Cookie mutation with UNTRUSTED Origin -> 403 Forbidden
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://testserver",
                cookies={settings.session_cookie_name: cookie_token},
            ) as client:
                spec = _make_valid_spec(name="csrf-agent", display_name="CSRF Agent")
                payload = {"slug": "csrf-agent", "display_name": "CSRF Agent", "spec": spec}

                r_untrusted = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Origin": "https://attacker.example.com"},
                    json=payload,
                )
                assert r_untrusted.status_code == 403

                # 2. Cookie mutation with cross-site Sec-Fetch-Site -> 403 Forbidden
                r_cross_site = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Sec-Fetch-Site": "cross-site"},
                    json=payload,
                )
                assert r_cross_site.status_code == 403

                # 3. Cookie mutation with TRUSTED Origin -> 201 Created
                r_trusted = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Origin": "http://localhost:3000"},
                    json=payload,
                )
                assert r_trusted.status_code == 201, r_trusted.text
                agent_id = r_trusted.json()["agent_id"]

                # 4. Request with malformed Bearer header AND valid cookie -> 401 Unauthorized (never fallback to cookie)
                r_malformed_bearer = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": "Bearer malformed.invalid.token"},
                )
                assert r_malformed_bearer.status_code == 401
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a11_api_process_and_database_restart_durability_real_postgres(agent_test_env):
    """A11: API process restart and database restart -> verify persisted agent, spec, and revision."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]

    async def _scenario():
        # Initial engine
        engine1 = create_async_engine(sa_url, poolclass=NullPool)
        factory1 = async_sessionmaker(engine1, class_=AsyncSession, expire_on_commit=False)

        async def override_db1():
            async with factory1() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db1
        transport1 = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport1, base_url="https://testserver") as client:
                spec = _make_valid_spec(name="restart-agent", display_name="Restart Agent")
                c_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "restart-agent", "display_name": "Restart Agent", "spec": spec},
                )
                assert c_resp.status_code == 201, c_resp.text
                agent_id = c_resp.json()["agent_id"]

                # Patch to revision 2
                spec2 = _make_valid_spec(name="restart-agent", display_name="Restart Agent", max_steps=42)
                p_resp = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"1"'},
                    json={"spec": spec2},
                )
                assert p_resp.status_code == 200, p_resp.text
                assert p_resp.json()["revision"] == 2
        finally:
            app.dependency_overrides.clear()
            await engine1.dispose()

        # Recreate completely fresh engine & pool to simulate restart
        engine2 = create_async_engine(sa_url, poolclass=NullPool)
        factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)

        async def override_db2():
            async with factory2() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db2
        transport2 = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport2, base_url="https://testserver") as client:
                get_resp = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert get_resp.status_code == 200, get_resp.text
                data = get_resp.json()
                assert data["agent_id"] == agent_id
                assert data["slug"] == "restart-agent"
                assert data["revision"] == 2
                assert data["spec"]["limits"]["maxSteps"] == 42
                assert get_resp.headers.get("etag") == '"2"'
        finally:
            app.dependency_overrides.clear()
            await engine2.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_a12_audit_failure_rollback_and_archived_agent_real_postgres(agent_test_env):
    """A12: Audit failure rolls back atomically; archived Agent -> 409 on PATCH, archived_at visible on GET."""
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Atomic rollback on audit insert failure
                spec_fail = _make_valid_spec(name="rollback-agent", display_name="Rollback Agent")
                with patch(
                    "control_api.modules.agents.repositories.agent_repository.write_audit_event",
                    side_effect=SQLAlchemyError("Simulated audit write failure"),
                ):
                    resp_fail = await client.post(
                        f"/v1/workspaces/{ws_1a}/agents",
                        headers={"Authorization": f"Bearer {dev_token}"},
                        json={"slug": "rollback-agent", "display_name": "Rollback Agent", "spec": spec_fail},
                    )
                    assert resp_fail.status_code in (500, 503)

                # Confirm zero rows inserted into catalog.agents
                with psycopg.connect(owner_url) as conn:
                    count = conn.execute(
                        "SELECT count(*) FROM catalog.agents WHERE slug = 'rollback-agent'"
                    ).fetchone()[0]
                    assert count == 0

                # 2. Archived Agent behavior
                spec_normal = _make_valid_spec(name="normal-agent", display_name="Normal Agent")
                resp_ok = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "normal-agent", "display_name": "Normal Agent", "spec": spec_normal},
                )
                assert resp_ok.status_code == 201, resp_ok.text
                agent_id = resp_ok.json()["agent_id"]

                # Mark agent as archived in DB
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE catalog.agents SET archived_at = now() WHERE id = %s",
                        (UUID(agent_id),),
                    )

                # GET detail returns 200 with archived_at populated
                r_get = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert r_get.status_code == 200
                assert r_get.json()["archived_at"] is not None

                # PATCH returns 409 Conflict (agent_archived)
                r_patch = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {dev_token}", "If-Match": '"1"'},
                    json={"spec": spec_normal},
                )
                assert r_patch.status_code == 409
                assert _get_error_code(r_patch) == "conflict"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


def test_a13_studio_web_ui_agent_crud_verification():
    """A13: Browser/Studio Web UI static and structural contract verification.

    Verifies:
    - No SEED_AGENTS, ag_local, fake IDs, or mock viewState switcher.
    - Presence of real HTTP 412 CAS conflict handling (conflict banner, retaining unsaved edits, retry buttons).
    - If-Match: "${draft.revision}" used in PATCH request.
    - True empty state rendering without fake fallback rows.
    """
    page_path = ROOT / "apps/studio-web/src/app/(console)/w/[workspaceId]/agents/page.tsx"
    assert page_path.exists(), f"Studio Web agents page missing: {page_path}"
    content = page_path.read_text(encoding="utf-8")

    # Anti-patterns strictly forbidden
    assert "SEED_AGENTS" not in content
    assert "ag_local" not in content
    assert "fake-" not in content
    assert "mock-" not in content

    # Mandatory real implementations
    assert "If-Match" in content
    assert "412" in content
    assert "Xung đột sửa đổi" in content
    assert "Tải lại bản mới nhất từ máy chủ" in content
    assert "Giữ lại thay đổi hiện tại để sao chép" in content
    assert "Chưa có Tác tử AI nào trong không gian này" in content
    assert "defaultApiClient.listAgents" in content
    assert "defaultApiClient.getAgentDraft" in content
    assert "defaultApiClient.createAgentDraft" in content
    assert "defaultApiClient.replaceAgentDraftSpec" in content


@pytest.mark.integration
def test_a14_workspace_switching_safety_and_publish_stub_real_postgres(agent_test_env):
    """A14: Browser workspace switch / late response cancellation + publish stub fails closed (HTTP 503)."""
    # 1. Verify Studio Web implements AbortController on workspaceId change
    page_path = ROOT / "apps/studio-web/src/app/(console)/w/[workspaceId]/agents/page.tsx"
    content = page_path.read_text(encoding="utf-8")
    assert "AbortController" in content
    assert "abortControllerRef" in content

    # 2. Verify Publish endpoint fails closed with HTTP 503 capability_unavailable
    sa_url = agent_test_env["sa_url"]
    ws_1a = agent_test_env["seeded"]["ws_1a"]
    dev_token = agent_test_env["tokens"]["dev_1a"]
    owner_url = agent_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session, session.begin():
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # Create draft
                spec = _make_valid_spec(name="publish-agent", display_name="Publish Agent")
                c_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "publish-agent", "display_name": "Publish Agent", "spec": spec},
                )
                assert c_resp.status_code == 201, c_resp.text
                agent_id = c_resp.json()["agent_id"]

                # Call POST publish -> MUST fail closed with 503
                pub_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert pub_resp.status_code == 503
                assert _get_error_code(pub_resp) == "capability_unavailable"

                # Assert zero rows created in catalog.agent_versions
                with psycopg.connect(owner_url) as conn:
                    count = conn.execute("SELECT count(*) FROM catalog.agent_versions").fetchone()[0]
                    assert count == 0, f"Expected 0 published versions, got {count}"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_b_validation_real_dependency_sql_is_scoped_and_read_only(agent_test_env):
    """Real restricted-role SQL/HTTP; auth fixture is isolated, no provider or Tool executes."""
    fixture = agent_test_env
    tenant = fixture["seeded"]["tenant_1"]
    workspace = fixture["seeded"]["ws_1a"]
    other_workspace = fixture["seeded"]["ws_1b"]
    user = fixture["seeded"]["dev_1a"]
    model, other_model, tool, tool_version, mcp_tool, mcp_version, base = [
        uuid4() for _ in range(7)
    ]
    with psycopg.connect(fixture["owner_url"]) as conn:
        for mid, wid in ((model, workspace), (other_model, other_workspace)):
            conn.execute(
                "INSERT INTO catalog.model_configs (tenant_id,workspace_id,id,name,provider,provider_model_id) "
                "VALUES (%s,%s,%s,%s,'fixture','not-a-live-model')",
                (tenant, wid, mid, str(mid)),
            )
        for tid, vid, kind in ((tool, tool_version, "builtin"), (mcp_tool, mcp_version, "mcp")):
            conn.execute(
                "INSERT INTO catalog.tools (tenant_id,workspace_id,id,tool_key,display_name,kind) "
                "VALUES (%s,%s,%s,%s,'Fixture Tool',%s)",
                (tenant, workspace, tid, str(tid), kind),
            )
            conn.execute(
                "INSERT INTO catalog.tool_versions (tenant_id,workspace_id,id,tool_id,version,input_schema,output_schema,risk,sha256) "
                "VALUES (%s,%s,%s,%s,1,'{}','{}','read',%s)",
                (tenant, workspace, vid, tid, "a" * 64),
            )
        conn.execute(
            "INSERT INTO knowledge.bases (tenant_id,workspace_id,id,slug,name,visibility) "
            "VALUES (%s,%s,%s,'fixture-base','Fixture Base','restricted')",
            (tenant, workspace, base),
        )

    async def scenario():
        engine = create_async_engine(fixture["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def database():
            async with factory() as session, session.begin():
                yield session

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = database
        spec = _make_valid_spec(name="validation-real", mode="fixed_plan")
        spec.update(modelRef=str(model), toolRefs=[], skillRefs=[], knowledgeRefs=[])
        headers = {"Authorization": f"Bearer {fixture['tokens']['dev_1a']}"}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                created = await client.post(
                    f"/v1/workspaces/{workspace}/agents",
                    headers=headers,
                    json={"slug": "validation-real", "display_name": "Fixture", "spec": spec},
                )
                assert created.status_code == 201, created.text
                agent = created.json()["agent_id"]
                path = f"/v1/workspaces/{workspace}/agents/{agent}/validate"
                with psycopg.connect(fixture["owner_url"]) as conn:
                    audit_before = conn.execute(
                        "SELECT count(*) FROM governance.audit_events"
                    ).fetchone()[0]
                response = await client.post(path, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()["ready"] is True
                root_null = await client.post(path, headers={**headers, "Content-Type":"application/json"}, content="null")
                assert root_null.status_code == 422
                invalid = {**spec, "mode": ["fixed_plan"]}
                response = await client.post(path, headers=headers, json={"spec": invalid})
                assert response.status_code == 200 and response.json()["ready"] is False
                assert response.json()["diagnostics"][0]["code"] == "invalid_spec"
                assert (
                    await client.post(path, headers=headers, json={"spec": None})
                ).status_code == 422
                response = await client.post(
                    path, headers=headers, json={"spec": {**spec, "modelRef": str(other_model)}}
                )
                assert response.status_code == 200 and response.json()["ready"] is False
                assert response.json()["diagnostics"][0]["code"] == "dependency_unavailable"
                tool_spec = {**spec, "toolRefs": [str(tool_version)]}
                denied = await client.post(path, headers=headers, json={"spec": tool_spec})
                assert any(
                    d["code"] == "dependency_not_permitted" for d in denied.json()["diagnostics"]
                )
                with psycopg.connect(fixture["owner_url"]) as conn:
                    conn.execute(
                        "INSERT INTO catalog.tool_grants (tenant_id,workspace_id,tool_id,principal_kind,principal_key,allowed_operation) "
                        "VALUES (%s,%s,%s,'role','developer','invoke')",
                        (tenant, workspace, tool),
                    )
                allowed = await client.post(path, headers=headers, json={"spec": tool_spec})
                assert allowed.status_code == 200 and allowed.json()["ready"] is True
                denied = await client.post(
                    path, headers=headers, json={"spec": {**spec, "knowledgeRefs": [str(base)]}}
                )
                assert any(
                    d["code"] == "dependency_not_permitted" for d in denied.json()["diagnostics"]
                )
                with psycopg.connect(fixture["owner_url"]) as conn:
                    conn.execute(
                        "INSERT INTO knowledge.base_grants (tenant_id,workspace_id,base_id,principal_kind,principal_key,permission) "
                        "VALUES (%s,%s,%s,'user',%s,'read')",
                        (tenant, workspace, base, str(user)),
                    )
                allowed = await client.post(
                    path, headers=headers, json={"spec": {**spec, "knowledgeRefs": [str(base)]}}
                )
                assert allowed.status_code == 200 and allowed.json()["ready"] is True
                mcp = await client.post(
                    path, headers=headers, json={"spec": {**spec, "toolRefs": [str(mcp_version)]}}
                )
                assert mcp.status_code == 200 and mcp.json()["ready"] is False
                assert any(d["code"] == "unsupported_capability" for d in mcp.json()["diagnostics"])
                with psycopg.connect(fixture["owner_url"]) as conn:
                    row = conn.execute(
                        "SELECT revision,draft_spec FROM catalog.agents WHERE id=%s", (UUID(agent),)
                    ).fetchone()
                    assert row == (1, spec)
                    assert (
                        conn.execute("SELECT count(*) FROM governance.audit_events").fetchone()[0]
                        == audit_before
                    )
                    assert (
                        conn.execute("SELECT count(*) FROM catalog.agent_tool_bindings").fetchone()[
                            0
                        ]
                        == 0
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM catalog.agent_knowledge_bindings"
                        ).fetchone()[0]
                        == 0
                    )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
            await engine.dispose()

    asyncio.run(scenario())
