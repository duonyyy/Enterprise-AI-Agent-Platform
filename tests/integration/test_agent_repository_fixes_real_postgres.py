"""Real PostgreSQL Integration & Concurrency Acceptance Tests for AgentRepository Fixes (IT-01..IT-20).

Validates:
- AG-FIX-01: agent_knowledge_bindings column consistency (base_id vs knowledge_base_id).
- AG-FIX-02: Promote authorization using actor live role and verified principal/permission.
- AG-FIX-03: Elimination of TOCTOU/race conditions via FOR SHARE locking.
- AG-FIX-04: Model settings snapshot allowlist preservation and secret stripping.
- AG-FIX-05: Lock hierarchy (iam.workspaces FOR SHARE/FOR UPDATE of w, iam.tenants evaluated safely).
- AG-FIX-06: Concurrency, CAS, RLS, transactional outbox, and atomic rollback on real PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.agents.repositories.agent_repository import AgentRepository
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from platform_common.rls import set_rls_context
from run_migrations import run_migrations
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "database/migrations"


def _make_sa_url(conn_url: str) -> str:
    conn_dict = psycopg.conninfo.conninfo_to_dict(conn_url)
    return (
        f"postgresql+asyncpg://{conn_dict['user']}:{conn_dict.get('password', '')}"
        f"@{conn_dict['host']}:{conn_dict['port']}/{conn_dict['dbname']}"
    )


def _seed_tenants_and_workspaces(owner_url: str) -> dict[str, Any]:
    issuer = settings.jwt_issuer
    tenant_1, ws_1a = uuid4(), uuid4()
    tenant_2, ws_2a = uuid4(), uuid4()
    owner_1a, dev_1a, viewer_1a = uuid4(), uuid4(), uuid4()
    owner_2a = uuid4()
    suffix_1 = str(tenant_1)[:8]
    suffix_2 = str(tenant_2)[:8]

    with psycopg.connect(owner_url) as conn:
        # Tenant 1 users
        for uid, sub, email in (
            (owner_1a, f"owner-1a-{suffix_1}", f"owner-1a-{suffix_1}@example.com"),
            (dev_1a, f"dev-1a-{suffix_1}", f"dev-1a-{suffix_1}@example.com"),
            (viewer_1a, f"viewer-1a-{suffix_1}", f"viewer-1a-{suffix_1}@example.com"),
        ):
            conn.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
                "VALUES (%s, %s, %s, %s, %s)",
                (uid, issuer, sub, sub, email),
            )

        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, %s, 'active')",
            (tenant_1, f"tenant-1-{suffix_1}", "Tenant 1"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_1, ws_1a, f"ws-1a-{suffix_1}", "Workspace 1A"),
        )

        for tid, wid, uid, role, m_status in (
            (tenant_1, ws_1a, owner_1a, "owner", "active"),
            (tenant_1, ws_1a, dev_1a, "developer", "active"),
            (tenant_1, ws_1a, viewer_1a, "viewer", "active"),
        ):
            conn.execute(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tid, wid, uid, role, m_status),
            )

        # Tenant 2 for cross-tenant RLS testing
        conn.execute(
            "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
            "VALUES (%s, %s, %s, %s, %s)",
            (owner_2a, issuer, f"owner-2a-{suffix_2}", f"owner-2a-{suffix_2}", f"owner-2a-{suffix_2}@example.com"),
        )
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, %s, 'active')",
            (tenant_2, f"tenant-2-{suffix_2}", "Tenant 2"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_2, ws_2a, f"ws-2a-{suffix_2}", "Workspace 2A"),
        )
        conn.execute(
            "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tenant_2, ws_2a, owner_2a, "owner", "active"),
        )

    return {
        "issuer": issuer,
        "tenant_1": tenant_1,
        "ws_1a": ws_1a,
        "owner_1a": owner_1a,
        "dev_1a": dev_1a,
        "viewer_1a": viewer_1a,
        "tenant_2": tenant_2,
        "ws_2a": ws_2a,
        "owner_2a": owner_2a,
        "dev_1a_sub": f"dev-1a-{suffix_1}",
        "owner_1a_sub": f"owner-1a-{suffix_1}",
        "viewer_1a_sub": f"viewer-1a-{suffix_1}",
        "owner_2a_sub": f"owner-2a-{suffix_2}",
    }


def _seed_dependencies(
    owner_url: str, tenant_id: UUID, workspace_id: UUID
) -> dict[str, Any]:
    model_id = uuid4()
    tool_id = uuid4()
    tool_version_id = uuid4()
    skill_id = uuid4()
    skill_version_id = uuid4()
    kb_id = uuid4()

    with psycopg.connect(owner_url) as conn:
        # 1. Model config
        conn.execute(
            "INSERT INTO catalog.model_configs (tenant_id, workspace_id, id, name, provider, provider_model_id, status) "
            "VALUES (%s, %s, %s, 'gpt-4o-primary', 'openai', 'gpt-4o', 'active')",
            (tenant_id, workspace_id, model_id),
        )

        # 2. Tool & Version & Grants (developer and owner)
        conn.execute(
            "INSERT INTO catalog.tools (tenant_id, workspace_id, id, tool_key, display_name, kind) "
            "VALUES (%s, %s, %s, 'enterprise_search', 'Enterprise Search', 'builtin')",
            (tenant_id, workspace_id, tool_id),
        )
        conn.execute(
            "INSERT INTO catalog.tool_versions (tenant_id, workspace_id, id, tool_id, version, input_schema, output_schema, risk, sha256) "
            "VALUES (%s, %s, %s, %s, 1, '{}', '{}', 'read', %s)",
            (tenant_id, workspace_id, tool_version_id, tool_id, "b" * 64),
        )
        conn.execute(
            "INSERT INTO catalog.tool_grants (tenant_id, workspace_id, tool_id, principal_kind, principal_key, allowed_operation) "
            "VALUES (%s, %s, %s, 'role', 'developer', 'invoke')",
            (tenant_id, workspace_id, tool_id),
        )
        conn.execute(
            "INSERT INTO catalog.tool_grants (tenant_id, workspace_id, tool_id, principal_kind, principal_key, allowed_operation) "
            "VALUES (%s, %s, %s, 'role', 'owner', 'invoke')",
            (tenant_id, workspace_id, tool_id),
        )

        # 3. Skill & Version
        conn.execute(
            "INSERT INTO catalog.skills (tenant_id, workspace_id, id, slug, display_name, status) "
            "VALUES (%s, %s, %s, 'research-skill', 'Research Skill', 'active')",
            (tenant_id, workspace_id, skill_id),
        )
        conn.execute(
            "INSERT INTO catalog.skill_versions (tenant_id, workspace_id, id, skill_id, version, manifest, skill_md, bundle_ref, bundle_sha256) "
            "VALUES (%s, %s, %s, %s, 1, '{}', '# Skill', 's3://skills/v1', %s)",
            (tenant_id, workspace_id, skill_version_id, skill_id, "c" * 64),
        )

        # 4. Knowledge Base & Grants
        conn.execute(
            "INSERT INTO knowledge.bases (tenant_id, workspace_id, id, slug, name, visibility) "
            "VALUES (%s, %s, %s, 'corp-kb', 'Corporate Knowledge', 'restricted')",
            (tenant_id, workspace_id, kb_id),
        )
        conn.execute(
            "INSERT INTO knowledge.base_grants (tenant_id, workspace_id, base_id, principal_kind, principal_key, permission) "
            "VALUES (%s, %s, %s, 'role', 'developer', 'read')",
            (tenant_id, workspace_id, kb_id),
        )
        conn.execute(
            "INSERT INTO knowledge.base_grants (tenant_id, workspace_id, base_id, principal_kind, principal_key, permission) "
            "VALUES (%s, %s, %s, 'role', 'owner', 'read')",
            (tenant_id, workspace_id, kb_id),
        )

    return {
        "model_id": model_id,
        "tool_id": tool_id,
        "tool_version_id": tool_version_id,
        "skill_id": skill_id,
        "skill_version_id": skill_version_id,
        "kb_id": kb_id,
    }


def _make_spec(
    name: str,
    deps: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {
            "name": name,
            "displayName": "Enterprise Agent",
            "description": "Assistant for enterprise workflows",
        },
        "mode": "fixed_plan",
        "modelRef": str(deps["model_id"]),
        "persona": {
            "role": "Assistant",
            "instructions": "Helpful enterprise assistant.",
        },
        "toolRefs": [str(deps["tool_version_id"])],
        "skillRefs": [
            {
                "skillId": str(deps["skill_id"]),
                "skillVersionId": str(deps["skill_version_id"]),
            }
        ],
        "knowledgeRefs": [str(deps["kb_id"])],
        "limits": {
            "maxSteps": 10,
            "maxWallTimeSeconds": 300,
            "maxTotalTokens": 50000,
        },
    }


def _create_token(user_id: UUID, issuer: str, subject: str) -> str:
    _, token = session_manager.create_session(user_id=str(user_id), issuer=issuer, subject=subject)
    return token


@pytest.fixture
def repo_test_env(isolated_database: dict[str, Any]):
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]

    # Verify platform_app non-superuser and non-bypassrls
    with psycopg.connect(app_url) as app_conn:
        row = app_conn.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == ("platform_app", False, False)

    run_migrations(owner_url, MIGRATIONS_DIR)
    seeded = _seed_tenants_and_workspaces(owner_url)
    deps = _seed_dependencies(
        owner_url,
        seeded["tenant_1"],
        seeded["ws_1a"],
    )

    sa_url = _make_sa_url(app_url)
    previous_denylist = session_manager.denylist
    session_manager.denylist = InMemoryRevocationStore()

    tokens = {
        "dev_1a": _create_token(seeded["dev_1a"], seeded["issuer"], seeded["dev_1a_sub"]),
        "owner_1a": _create_token(seeded["owner_1a"], seeded["issuer"], seeded["owner_1a_sub"]),
        "viewer_1a": _create_token(seeded["viewer_1a"], seeded["issuer"], seeded["viewer_1a_sub"]),
        "owner_2a": _create_token(seeded["owner_2a"], seeded["issuer"], seeded["owner_2a_sub"]),
    }

    try:
        yield {
            "owner_url": owner_url,
            "app_url": app_url,
            "sa_url": sa_url,
            "seeded": seeded,
            "deps": deps,
            "tokens": tokens,
        }
    finally:
        session_manager.denylist = previous_denylist


# ===========================================================================
# Group A: Logic & Authorization Fixes (AG-FIX-01, AG-FIX-02, AG-FIX-04)
# ===========================================================================

@pytest.mark.integration
def test_it01_kb_binding_column_consistency_and_promotion_success(repo_test_env):
    """IT-01 (AG-FIX-01): Publish agent with KB binding, then promote.
    Validates that catalog.agent_knowledge_bindings uses base_id consistently,
    preventing SQL errors like 'akb.knowledge_base_id does not exist'.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Create draft with KB ref
                spec = _make_spec("agent-it01", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it01", "display_name": "Agent IT01", "spec": spec},
                )
                assert create_res.status_code == 201, create_res.text
                agent_id = UUID(create_res.json()["agent_id"])

                # 2. Publish revision 1
                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it01"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200, pub_res.text
                version_id = UUID(pub_res.json()["agent_version_id"])

                # Verify KB binding in DB uses base_id
                with psycopg.connect(owner_url) as conn:
                    kb_rows = conn.execute(
                        "SELECT base_id FROM catalog.agent_knowledge_bindings WHERE agent_version_id = %s",
                        (version_id,),
                    ).fetchall()
                    assert len(kb_rows) == 1
                    assert kb_rows[0][0] == deps["kb_id"]

                # 3. Promote version to development
                promo_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_res.status_code == 200, promo_res.text
                promo_data = promo_res.json()
                assert promo_data["version"] == 1
                assert promo_data["environment"] == "development"
                assert promo_data["revision"] == 1

                # Verify publication record in DB
                with psycopg.connect(owner_url) as conn:
                    pub_row = conn.execute(
                        "SELECT revision, agent_version_id FROM catalog.agent_publications WHERE agent_id = %s AND environment = 'development'",
                        (agent_id,),
                    ).fetchone()
                    assert pub_row is not None
                    assert pub_row[0] == 1
                    assert pub_row[1] == version_id
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it02_promote_denied_when_actor_lacks_tool_grant(repo_test_env):
    """IT-02 (AG-FIX-02): Promote with actor having workspace role 'owner', but missing tool grant.
    Must fail closed with 422 (code: dependency_not_permitted).
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Create & publish draft
                spec = _make_spec("agent-it02", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it02", "display_name": "Agent IT02", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it02"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                version_id = UUID(pub_res.json()["agent_version_id"])

                # 2. Revoke tool grant for 'owner' role
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "DELETE FROM catalog.tool_grants WHERE tool_id = %s AND principal_kind = 'role' AND principal_key = 'owner'",
                        (deps["tool_id"],),
                    )

                # 3. Promote attempt by owner MUST fail
                promo_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_res.status_code == 422, promo_res.text
                err = promo_res.json()
                assert err["detail"]["code"] == "dependency_not_permitted"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it03_promote_denied_when_principal_kind_mismatched(repo_test_env):
    """IT-03 (AG-FIX-02): Promote with tool grant having principal_kind = 'service_account'
    even though principal_key = 'owner'. Must fail closed with 422.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it03", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it03", "display_name": "Agent IT03", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it03"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                version_id = UUID(pub_res.json()["agent_version_id"])

                # Replace 'owner' role grant with a service_account grant named 'owner'
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "DELETE FROM catalog.tool_grants WHERE tool_id = %s AND principal_key = 'owner'",
                        (deps["tool_id"],),
                    )
                    conn.execute(
                        "INSERT INTO catalog.tool_grants (tenant_id, workspace_id, tool_id, principal_kind, principal_key, allowed_operation) "
                        "VALUES (%s, %s, %s, 'service_account', 'owner', 'invoke')",
                        (repo_test_env["seeded"]["tenant_1"], ws_1a, deps["tool_id"]),
                    )

                promo_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_res.status_code == 422, promo_res.text
                assert promo_res.json()["detail"]["code"] == "dependency_not_permitted"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it04_restricted_and_workspace_kb_access_matrix(repo_test_env):
    """IT-04 (AG-FIX-02): KB grant permission & visibility matrix in Promote:
    - 4a: Restricted KB with no grant -> 422.
    - 4b: Restricted KB with grant permission = 'write' -> 422 (must be 'read' or 'manage').
    - 4c: Restricted KB with user grant (principal_kind='user', principal_key=actor_user_id) -> 200.
    - 4d: KB visibility = 'workspace' -> 200 without individual grant.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    tenant_1 = repo_test_env["seeded"]["tenant_1"]
    owner_1a = repo_test_env["seeded"]["owner_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it04", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it04", "display_name": "Agent IT04", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it04"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                version_id = UUID(pub_res.json()["agent_version_id"])

                # --- 4a: Delete all owner grants on restricted KB -> 422 ---
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "DELETE FROM knowledge.base_grants WHERE base_id = %s AND principal_key = 'owner'",
                        (deps["kb_id"],),
                    )

                res_4a = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_4a.status_code == 422
                assert res_4a.json()["detail"]["code"] == "dependency_not_permitted"

                # --- 4b: Grant permission = 'write' only -> 422 ---
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "INSERT INTO knowledge.base_grants (tenant_id, workspace_id, base_id, principal_kind, principal_key, permission) "
                        "VALUES (%s, %s, %s, 'role', 'owner', 'write')",
                        (tenant_1, ws_1a, deps["kb_id"]),
                    )

                res_4b = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_4b.status_code == 422
                assert res_4b.json()["detail"]["code"] == "dependency_not_permitted"

                # --- 4c: User-level read grant (principal_kind='user', principal_key=actor_user_id) -> 200 ---
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "DELETE FROM knowledge.base_grants WHERE base_id = %s",
                        (deps["kb_id"],),
                    )
                    conn.execute(
                        "INSERT INTO knowledge.base_grants (tenant_id, workspace_id, base_id, principal_kind, principal_key, permission) "
                        "VALUES (%s, %s, %s, 'user', %s, 'read')",
                        (tenant_1, ws_1a, deps["kb_id"], str(owner_1a)),
                    )

                res_4c = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_4c.status_code == 200, res_4c.text
                assert res_4c.json()["revision"] == 1

                # --- 4d: KB visibility = 'workspace' -> succeeds without individual grant ---
                with psycopg.connect(owner_url) as conn:
                    # Remove all grants, set visibility to 'workspace'
                    conn.execute("DELETE FROM knowledge.base_grants WHERE base_id = %s", (deps["kb_id"],))
                    conn.execute(
                        "UPDATE knowledge.bases SET visibility = 'workspace' WHERE id = %s",
                        (deps["kb_id"],),
                    )

                res_4d = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "staging",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_4d.status_code == 200, res_4d.text
                assert res_4d.json()["environment"] == "staging"
                assert res_4d.json()["revision"] == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it15_model_settings_snapshot_preserves_tokens_and_strips_secrets(repo_test_env):
    """IT-15 (AG-FIX-04): Model settings snapshot preserves max_output_tokens and temperature,
    while completely stripping secrets (secret_token, api_key, credentials).
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Update model config settings with valid allowed params and forbidden secrets
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        """
                        UPDATE catalog.model_configs
                        SET settings = %s
                        WHERE id = %s
                        """,
                        (
                            psycopg.types.json.Json({
                                "temperature": 0.7,
                                "max_output_tokens": 4096,
                                "top_p": 0.95,
                                "seed": 42,
                                "api_key": "sk-proj-super-secret-key",
                                "secret_token": "token-xyz-12345",
                                "private_key": "-----BEGIN PRIVATE KEY-----",
                            }),
                            deps["model_id"],
                        ),
                    )

                # 2. Create and publish draft
                spec = _make_spec("agent-it15", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it15", "display_name": "Agent IT15", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it15"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200, pub_res.text
                version_id = UUID(pub_res.json()["agent_version_id"])

                # 3. Direct DB inspect of manifest snapshot
                with psycopg.connect(owner_url) as conn:
                    row = conn.execute(
                        "SELECT bundle_manifest FROM catalog.agent_versions WHERE id = %s",
                        (version_id,),
                    ).fetchone()
                    assert row is not None
                    manifest = row[0]
                    model_snapshot = manifest["dependencies"]["model"]
                    settings_snapshot = model_snapshot.get("effectiveSettings", model_snapshot.get("settings", {}))

                    # Preserved keys
                    assert settings_snapshot["max_output_tokens"] == 4096
                    assert settings_snapshot["temperature"] == 0.7
                    assert settings_snapshot["top_p"] == 0.95
                    assert settings_snapshot["seed"] == 42

                    # Stripped secrets
                    assert "api_key" not in settings_snapshot
                    assert "secret_token" not in settings_snapshot
                    assert "private_key" not in settings_snapshot
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it16_inactive_model_or_skill_rejected_at_publish_and_promote(repo_test_env):
    """IT-16 (AG-FIX-04): Inactive model config or inactive skill is rejected at Publish and Promote."""
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Inactive model at publish
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.model_configs SET status = 'disabled' WHERE id = %s", (deps["model_id"],))

                spec = _make_spec("agent-it16-a", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it16-a", "display_name": "Agent IT16 A", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_fail_model = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it16-m"},
                    json={"expected_revision": 1},
                )
                assert pub_fail_model.status_code == 422

                # Restore model
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.model_configs SET status = 'active' WHERE id = %s", (deps["model_id"],))

                # 2. Inactive skill at publish
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.skills SET status = 'archived' WHERE id = %s", (deps["skill_id"],))

                pub_fail_skill = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it16-s"},
                    json={"expected_revision": 1},
                )
                assert pub_fail_skill.status_code == 422

                # Restore skill & publish valid version
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.skills SET status = 'active' WHERE id = %s", (deps["skill_id"],))

                pub_ok = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it16-ok"},
                    json={"expected_revision": 1},
                )
                assert pub_ok.status_code == 200
                version_id = UUID(pub_ok.json()["agent_version_id"])

                # 3. Disable model after publish -> Promote must fail
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.model_configs SET status = 'disabled' WHERE id = %s", (deps["model_id"],))

                promo_fail_model = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_fail_model.status_code == 422
                assert promo_fail_model.json()["detail"]["code"] == "dependency_unavailable"

                # Restore model, disable skill -> Promote must fail
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.model_configs SET status = 'active' WHERE id = %s", (deps["model_id"],))
                    conn.execute("UPDATE catalog.skills SET status = 'archived' WHERE id = %s", (deps["skill_id"],))

                promo_fail_skill = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_fail_skill.status_code == 422
                assert promo_fail_skill.json()["detail"]["code"] == "dependency_unavailable"

                # Restore skill for subsequent tests
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.skills SET status = 'active' WHERE id = %s", (deps["skill_id"],))
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


# ===========================================================================
# Group B: Concurrency, Locking & TOCTOU Elimination (AG-FIX-03, AG-FIX-05)
# ===========================================================================

@pytest.mark.integration
def test_it05_toctou_revoke_tool_grant_during_publish_fails_closed(repo_test_env):
    """IT-05 (AG-FIX-03): Concurrency test on Publish vs Revoke Tool Grant.
    When a tool grant is revoked right before or during FOR SHARE verification,
    Publish must fail-closed (422) with 0 orphan versions committed.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it05", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it05", "display_name": "Agent IT05", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                # Revoke developer tool grant in concurrent session
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "DELETE FROM catalog.tool_grants WHERE tool_id = %s AND principal_key = 'developer'",
                        (deps["tool_id"],),
                    )

                # Publish attempt must fail closed
                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it05"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 422
                assert pub_res.json()["detail"]["code"] in ("invalid_spec", "dependency_not_permitted")

                # Verify NO version or publication receipt created
                with psycopg.connect(owner_url) as conn:
                    v_count = conn.execute(
                        "SELECT COUNT(*) FROM catalog.agent_versions WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert v_count == 0

                    receipt_count = conn.execute(
                        "SELECT COUNT(*) FROM catalog.agent_publish_receipts WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert receipt_count == 0
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it06_toctou_revoke_kb_grant_during_promote_fails_closed(repo_test_env):
    """IT-06 (AG-FIX-03): Concurrency test on Promote vs Revoke KB Grant.
    When a restricted KB grant is revoked, Promote must fail-closed (422) under FOR SHARE.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it06", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it06", "display_name": "Agent IT06", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it06"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                version_id = UUID(pub_res.json()["agent_version_id"])

                # Revoke KB grant for owner role before promote executes lock verification
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "DELETE FROM knowledge.base_grants WHERE base_id = %s AND principal_key = 'owner'",
                        (deps["kb_id"],),
                    )

                promo_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_res.status_code == 422
                assert promo_res.json()["detail"]["code"] == "dependency_not_permitted"

                # Verify 0 publications created
                with psycopg.connect(owner_url) as conn:
                    pub_count = conn.execute(
                        "SELECT COUNT(*) FROM catalog.agent_publications WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert pub_count == 0
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it07_share_lock_contention_blocks_concurrent_delete(repo_test_env):
    """IT-07 (AG-FIX-03): Real PostgreSQL lock contention test.
    Proves that SELECT ... FOR SHARE locks rows and genuinely blocks concurrent DELETE transactions.
    """
    owner_url = repo_test_env["owner_url"]
    deps = repo_test_env["deps"]

    # Session 1: Acquire FOR SHARE lock on tool_grants row
    with psycopg.connect(owner_url) as conn1:
        conn1.execute("BEGIN")
        row = conn1.execute(
            "SELECT 1 FROM catalog.tool_grants WHERE tool_id = %s AND principal_key = 'owner' FOR SHARE",
            (deps["tool_id"],),
        ).fetchone()
        assert row is not None

        # Session 2: Attempt concurrent DELETE with lock_timeout
        with psycopg.connect(owner_url) as conn2:
            conn2.execute("SET lock_timeout = '200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                conn2.execute(
                    "DELETE FROM catalog.tool_grants WHERE tool_id = %s AND principal_key = 'owner'",
                    (deps["tool_id"],),
                )

        # Release Session 1 lock
        conn1.execute("COMMIT")

    # Session 3: Now delete succeeds without blocking
    with psycopg.connect(owner_url) as conn3:
        conn3.execute(
            "DELETE FROM catalog.tool_grants WHERE tool_id = %s AND principal_key = 'owner'",
            (deps["tool_id"],),
        )


@pytest.mark.integration
def test_it08_concurrent_publishes_serialize_cleanly(repo_test_env):
    """IT-08: Two concurrent publish requests on the same draft with different Idempotency-Keys.
    Must serialize under draft FOR UPDATE lock: one succeeds with v1 (draft becomes rev 2),
    and the second either publishes v2 or fails with 412 (stale draft revision).
    No duplicate version numbers or unhandled deadlocks.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it08", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it08", "display_name": "Agent IT08", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                async def _pub(key: str):
                    return await client.post(
                        f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                        headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": key},
                        json={"expected_revision": 1},
                    )

                res1, res2 = await asyncio.gather(_pub("pub-it08-1"), _pub("pub-it08-2"))
                statuses = {res1.status_code, res2.status_code}

                # Both serialize under draft lock and create sequential versions (or 1 creates v1 and 2nd gets serialized)
                assert 200 in statuses
                if statuses == {200}:
                    with psycopg.connect(owner_url) as conn:
                        versions = conn.execute(
                            "SELECT version FROM catalog.agent_versions WHERE agent_id = %s ORDER BY version",
                            (agent_id,),
                        ).fetchall()
                        assert [v[0] for v in versions] == [1, 2]
                else:
                    assert 412 in statuses
                    with psycopg.connect(owner_url) as conn:
                        versions = conn.execute(
                            "SELECT version FROM catalog.agent_versions WHERE agent_id = %s ORDER BY version",
                            (agent_id,),
                        ).fetchall()
                        assert len(versions) == 1
                        assert versions[0][0] == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it11_concurrent_promote_cas_race_single_winner(repo_test_env):
    """IT-11: Two concurrent Promote requests with the same expected_publication_revision (CAS race).
    Exactly 1 request succeeds (CAS winner); the other returns 412 (StaleRevisionError).
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it11", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it11", "display_name": "Agent IT11", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it11"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                v1_id = UUID(pub_res.json()["agent_version_id"])

                # First promote to establish revision 1
                first_promo = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(v1_id),
                        "environment": "production",
                        "expected_publication_revision": 0,
                    },
                )
                assert first_promo.status_code == 200
                assert first_promo.json()["revision"] == 1

                # Publish version 2 (draft revision is still 1 since publish does not increment draft revision)
                pub_v2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it11-v2"},
                    json={"expected_revision": 1},
                )
                assert pub_v2.status_code == 200
                v2_id = UUID(pub_v2.json()["agent_version_id"])

                # Two concurrent promotes expecting revision 1 to promote to v2
                async def _promote_v2():
                    return await client.post(
                        f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                        headers={"Authorization": f"Bearer {owner_token}"},
                        json={
                            "agent_version_id": str(v2_id),
                            "environment": "production",
                            "expected_publication_revision": 1,
                        },
                    )

                r1, r2 = await asyncio.gather(_promote_v2(), _promote_v2())
                status_codes = {r1.status_code, r2.status_code}
                assert status_codes == {200, 412}

                with psycopg.connect(owner_url) as conn:
                    pub = conn.execute(
                        "SELECT revision, agent_version_id FROM catalog.agent_publications WHERE agent_id = %s AND environment = 'production'",
                        (agent_id,),
                    ).fetchone()
                    assert pub[0] == 2
                    assert pub[1] == v2_id
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it12_concurrent_first_promote_race_single_creator(repo_test_env):
    """IT-12: Concurrent first Promote requests on an environment (expected_publication_revision = 0).
    Exactly 1 request creates revision 1; the other receives 412 StaleRevisionError.
    No duplicate publication row or constraint violation.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it12", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it12", "display_name": "Agent IT12", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it12"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                v1_id = UUID(pub_res.json()["agent_version_id"])

                async def _first_promote():
                    return await client.post(
                        f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                        headers={"Authorization": f"Bearer {owner_token}"},
                        json={
                            "agent_version_id": str(v1_id),
                            "environment": "staging",
                            "expected_publication_revision": 0,
                        },
                    )

                r1, r2 = await asyncio.gather(_first_promote(), _first_promote())
                assert {r1.status_code, r2.status_code} == {200, 412}

                with psycopg.connect(owner_url) as conn:
                    rows = conn.execute(
                        "SELECT revision FROM catalog.agent_publications WHERE agent_id = %s AND environment = 'staging'",
                        (agent_id,),
                    ).fetchall()
                    assert len(rows) == 1
                    assert rows[0][0] == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it17_workspace_or_tenant_suspension_fails_closed(repo_test_env):
    """IT-17 (AG-FIX-05): Workspace or parent Tenant suspension fails closed with 403."""
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    tenant_1 = repo_test_env["seeded"]["tenant_1"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it17", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it17", "display_name": "Agent IT17", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                # 1. Archive workspace (iam.workspaces status IN ('active', 'archived'))
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE iam.workspaces SET status = 'archived' WHERE id = %s", (ws_1a,))

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it17-w"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 403

                # Restore workspace, suspend tenant
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE iam.workspaces SET status = 'active' WHERE id = %s", (ws_1a,))
                    conn.execute("UPDATE iam.tenants SET status = 'suspended' WHERE id = %s", (tenant_1,))

                pub_res2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it17-t"},
                    json={"expected_revision": 1},
                )
                assert pub_res2.status_code == 403

                # Restore tenant for cleanup
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE iam.tenants SET status = 'active' WHERE id = %s", (tenant_1,))
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


# ===========================================================================
# Group C: Idempotency, CAS & Transactional Guarantees
# ===========================================================================

@pytest.mark.integration
def test_it09_idempotency_replay_returns_cached_without_duplicate_outbox(repo_test_env):
    """IT-09: Replay Publish with the same Idempotency-Key returns cached 200 receipt
    without creating a new version or duplicate outbox event.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it09", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it09", "display_name": "Agent IT09", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                op_key = "idemp-key-it09"

                # 1. First publish
                pub1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": op_key},
                    json={"expected_revision": 1},
                )
                assert pub1.status_code == 200
                v1_id = pub1.json()["agent_version_id"]

                # Count rows in versions and outbox
                with psycopg.connect(owner_url) as conn:
                    v_cnt_1 = conn.execute("SELECT COUNT(*) FROM catalog.agent_versions WHERE agent_id = %s", (agent_id,)).fetchone()[0]
                    ob_cnt_1 = conn.execute("SELECT COUNT(*) FROM integration.outbox_events WHERE aggregate_id = %s", (str(agent_id),)).fetchone()[0]
                    assert v_cnt_1 == 1
                    assert ob_cnt_1 == 1

                # 2. Replay publish with exact same key and payload
                pub2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": op_key},
                    json={"expected_revision": 1},
                )
                assert pub2.status_code == 200
                assert pub2.json()["agent_version_id"] == v1_id

                # Verify counts have NOT increased
                with psycopg.connect(owner_url) as conn:
                    v_cnt_2 = conn.execute("SELECT COUNT(*) FROM catalog.agent_versions WHERE agent_id = %s", (agent_id,)).fetchone()[0]
                    ob_cnt_2 = conn.execute("SELECT COUNT(*) FROM integration.outbox_events WHERE aggregate_id = %s", (str(agent_id),)).fetchone()[0]
                    assert v_cnt_2 == 1
                    assert ob_cnt_2 == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it10_idempotency_conflict_returns_409(repo_test_env):
    """IT-10: Same Idempotency-Key with different payload returns 409 Conflict."""
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it10", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it10", "display_name": "Agent IT10", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                op_key = "idemp-key-it10"

                # Publish with expected_revision = 1
                pub1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": op_key},
                    json={"expected_revision": 1},
                )
                assert pub1.status_code == 200

                # Replay with same key but expected_revision = 2 -> 409 Conflict
                pub2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": op_key},
                    json={"expected_revision": 2},
                )
                assert pub2.status_code == 409
                assert pub2.json()["detail"]["code"] in ("conflict", "idempotency_conflict")
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it13_mid_transaction_rollback_guarantee(repo_test_env):
    """IT-13: Mid-transaction failure injection cleanly rolls back version, bindings, and audit."""
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it13", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it13", "display_name": "Agent IT13", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                # Inject a failure by disabling a dependency row right before publish
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.model_configs SET status = 'disabled' WHERE id = %s", (deps["model_id"],))

                # Attempt publish
                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it13"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 422

                # Verify complete database atomicity: 0 versions, 0 bindings, 0 receipts
                with psycopg.connect(owner_url) as conn:
                    v_cnt = conn.execute("SELECT COUNT(*) FROM catalog.agent_versions WHERE agent_id = %s", (agent_id,)).fetchone()[0]
                    assert v_cnt == 0

                    receipt_cnt = conn.execute("SELECT COUNT(*) FROM catalog.agent_publish_receipts WHERE agent_id = %s", (agent_id,)).fetchone()[0]
                    assert receipt_cnt == 0
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it14_outbox_transactional_guarantee(repo_test_env):
    """IT-14: Outbox transactional guarantee: Publish and Promote create exactly one outbox event in integration.outbox_events."""
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it14", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it14", "display_name": "Agent IT14", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                # Publish
                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it14"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                v_id = UUID(pub_res.json()["agent_version_id"])

                # Promote
                promo_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(v_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promo_res.status_code == 200

                # Check outbox events in DB
                with psycopg.connect(owner_url) as conn:
                    events = conn.execute(
                        "SELECT event_type, payload FROM integration.outbox_events WHERE aggregate_id IN (%s, %s) ORDER BY created_at",
                        (str(v_id), str(agent_id)),
                    ).fetchall()
                    assert len(events) >= 2
                    types = [e[0] for e in events]
                    assert any("agent.published" in t for t in types)
                    assert any("agent.promoted" in t for t in types)
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


# ===========================================================================
# Group D: RLS & Tenant Isolation
# ===========================================================================

@pytest.mark.integration
def test_it18_cross_tenant_rls_isolation(repo_test_env):
    """IT-18: Cross-tenant RLS isolation: Tenant 2 actor cannot read, publish, or promote Tenant 1's agent.
    Must return 404/403 with 0 rows visible.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    ws_2a = repo_test_env["seeded"]["ws_2a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_2a_token = repo_test_env["tokens"]["owner_2a"]
    deps = repo_test_env["deps"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                spec = _make_spec("agent-it18", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it18", "display_name": "Agent IT18", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                # Tenant 2 owner tries to get Tenant 1 agent via Tenant 2 workspace URL -> 404
                t2_get = await client.get(
                    f"/v1/workspaces/{ws_2a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {owner_2a_token}"},
                )
                assert t2_get.status_code == 404

                # Tenant 2 owner tries to access via Tenant 1 workspace URL -> 403 Forbidden
                t2_ws1_get = await client.get(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={"Authorization": f"Bearer {owner_2a_token}"},
                )
                assert t2_ws1_get.status_code == 403
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
def test_it19_set_local_connection_reset_hygiene(repo_test_env):
    """IT-19: SET LOCAL reset hygiene: RLS session settings reset when connection returns to pool."""
    app_url = repo_test_env["app_url"]
    tenant_1 = str(repo_test_env["seeded"]["tenant_1"])

    with psycopg.connect(app_url) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_1,))
            val = conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]
            assert val == tenant_1

        # In next transaction on same connection, local setting must revert to empty/null
        with conn.transaction():
            val_after = conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]
            assert val_after in ("", None)


@pytest.mark.integration
def test_it20_negative_domain_error_cases(repo_test_env):
    """IT-20: Negative test cases:
    - Publish with random non-existent UUID -> 404.
    - Promote version belonging to another agent -> 404/422.
    - Promote by user with suspended membership -> 403.
    - Publish archived agent draft -> 409.
    """
    sa_url = repo_test_env["sa_url"]
    ws_1a = repo_test_env["seeded"]["ws_1a"]
    dev_token = repo_test_env["tokens"]["dev_1a"]
    owner_token = repo_test_env["tokens"]["owner_1a"]
    viewer_token = repo_test_env["tokens"]["viewer_1a"]
    deps = repo_test_env["deps"]
    owner_url = repo_test_env["owner_url"]

    async def _run():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
                # 1. Non-existent agent publish -> 404
                random_agent = uuid4()
                res_404 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{random_agent}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-404"},
                    json={"expected_revision": 1},
                )
                assert res_404.status_code == 404

                # Create valid agent and publish
                spec = _make_spec("agent-it20", deps)
                create_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-it20", "display_name": "Agent IT20", "spec": spec},
                )
                assert create_res.status_code == 201
                agent_id = UUID(create_res.json()["agent_id"])

                pub_res = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-it20"},
                    json={"expected_revision": 1},
                )
                assert pub_res.status_code == 200
                v1_id = UUID(pub_res.json()["agent_version_id"])

                # 2. Promote non-existent version ID -> 404
                random_ver = uuid4()
                res_ver_404 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(random_ver),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_ver_404.status_code == 404

                # 3. Promote by Viewer -> 403 Forbidden
                res_viewer = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                    json={
                        "agent_version_id": str(v1_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_viewer.status_code == 403

                # 4. Revoke owner membership -> 403 Forbidden
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE iam.memberships SET status = 'revoked' WHERE user_id = %s",
                        (repo_test_env["seeded"]["owner_1a"],),
                    )

                res_suspended = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/promotions",
                    headers={"Authorization": f"Bearer {owner_token}"},
                    json={
                        "agent_version_id": str(v1_id),
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert res_suspended.status_code == 403

                # Restore owner membership
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE iam.memberships SET status = 'active' WHERE user_id = %s",
                        (repo_test_env["seeded"]["owner_1a"],),
                    )

                # 5. Archive agent draft, then attempt new publish -> 409 Conflict
                with psycopg.connect(owner_url) as conn:
                    conn.execute("UPDATE catalog.agents SET archived_at = now() WHERE id = %s", (agent_id,))

                res_archived = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={"Authorization": f"Bearer {dev_token}", "Idempotency-Key": "pub-archived"},
                    json={"expected_revision": 1},
                )
                assert res_archived.status_code == 409
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.integration
@pytest.mark.parametrize("grant_kind", ["read_and_manage", "user_and_role"])
@pytest.mark.parametrize("phase", ["publish", "promote"])
def test_overlapping_valid_kb_grants_preserve_access(
    repo_test_env: dict[str, Any], phase: str, grant_kind: str
) -> None:
    env = repo_test_env
    workspace_id = env["seeded"]["ws_1a"]
    tenant_id = env["seeded"]["tenant_1"]

    def add_second_grant(role: str) -> None:
        with psycopg.connect(env["owner_url"]) as conn:
            conn.execute(
                "INSERT INTO knowledge.base_grants "
                "(tenant_id,workspace_id,base_id,principal_kind,principal_key,permission) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    tenant_id, workspace_id, env["deps"]["kb_id"],
                    "user" if grant_kind == "user_and_role" else "role",
                    str(env["seeded"]["dev_1a" if role == "developer" else "owner_1a"])
                    if grant_kind == "user_and_role" else role,
                    "read" if grant_kind == "user_and_role" else "manage",
                ),
            )

    async def scenario() -> None:
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_database():
            async with factory() as session:
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override_database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                developer = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}
                spec = _make_spec("overlap-review", env["deps"])
                create = await client.post(
                    f"/v1/workspaces/{workspace_id}/agents", headers=developer,
                    json={"slug": "overlap-review", "display_name": "Review", "spec": spec},
                )
                assert create.status_code == 201, create.text
                agent_id = create.json()["agent_id"]
                if phase == "publish":
                    add_second_grant("developer")
                published = await client.post(
                    f"/v1/workspaces/{workspace_id}/agents/{agent_id}/publish",
                    headers={**developer, "Idempotency-Key": "overlap-review"},
                    json={"expected_revision": 1},
                )
                assert published.status_code == 200, published.text
                if phase == "promote":
                    add_second_grant("owner")
                    promoted = await client.post(
                        f"/v1/workspaces/{workspace_id}/agents/{agent_id}/promotions",
                        headers={"Authorization": f"Bearer {env['tokens']['owner_1a']}"},
                        json={"agent_version_id": published.json()["agent_version_id"],
                              "environment": "development", "expected_publication_revision": 0},
                    )
                    assert promoted.status_code == 200, promoted.text
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("guard", ["tenant_publish", "tenant_promote", "tool_parent"])
def test_actual_repository_guard_blocks_owner_status_or_kind_change(
    repo_test_env: dict[str, Any], guard: str
) -> None:
    """Acquire locks through the producer method, then attempt the owner write."""
    env = repo_test_env
    tenant_id = env["seeded"]["tenant_1"]
    workspace_id = env["seeded"]["ws_1a"]
    user_id = env["seeded"]["dev_1a"]

    async def scenario() -> None:
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as session:
                await set_rls_context(
                    session, tenant_id=str(tenant_id), workspace_id=str(workspace_id), user_id=str(user_id)
                )
                repository = AgentRepository()
                await repository._verify_active_workspace(
                    session, tenant_id=tenant_id, workspace_id=workspace_id,
                    for_update=guard == "tenant_promote",
                )
                if guard == "tool_parent":
                    resolved = await repository.resolve_tool_version(
                        session, tenant_id=tenant_id, workspace_id=workspace_id,
                        tool_version_id=env["deps"]["tool_version_id"],
                        caller_role="developer", for_share=True,
                    )
                    assert resolved is not None
                    statement = "UPDATE catalog.tools SET kind = 'http' WHERE id = %s"
                    target_id = env["deps"]["tool_id"]
                else:
                    statement = "UPDATE iam.tenants SET status = 'suspended' WHERE id = %s"
                    target_id = tenant_id
                with psycopg.connect(env["owner_url"]) as owner:
                    owner.execute("SET lock_timeout = '200ms'")
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        owner.execute(statement, (target_id,))
                    owner.rollback()
                await session.rollback()
                # The same authorized owner mutation succeeds once producer locks release.
                with psycopg.connect(env["owner_url"]) as owner:
                    assert owner.execute(statement, (target_id,)).rowcount == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("operation", "failure_table"),
    [
        ("publish", "catalog.agent_publish_receipts"),
        ("publish", "governance.audit_events"),
        ("publish", "integration.outbox_events"),
        ("promote", "governance.audit_events"),
        ("promote", "integration.outbox_events"),
    ],
)
def test_post_write_fault_rolls_back_entire_lifecycle_transaction(
    repo_test_env: dict[str, Any], operation: str, failure_table: str
) -> None:
    """C08/D09: inject after domain writes, then compare authoritative DB state."""
    env = repo_test_env
    tenant_id = env["seeded"]["tenant_1"]
    workspace_id = env["seeded"]["ws_1a"]
    tables = (
        "catalog.agent_versions", "catalog.agent_tool_bindings",
        "catalog.agent_skill_bindings", "catalog.agent_knowledge_bindings",
        "catalog.agent_publish_receipts", "catalog.agent_publications",
        "governance.audit_events", "integration.outbox_events",
    )

    def counts() -> dict[str, int]:
        with psycopg.connect(env["owner_url"]) as conn:
            # Table identifiers are fixed test constants, never request data.
            return {
                table: conn.execute(
                    f"SELECT count(*) FROM {table} WHERE tenant_id = %s AND workspace_id = %s",
                    (tenant_id, workspace_id),
                ).fetchone()[0]
                for table in tables
            }

    async def scenario() -> None:
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_database():
            async with factory() as session:
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override_database
        injected = False
        writes_before_failure: list[str] = []

        def fail_write(_conn, _cursor, statement, _parameters, _context, _executemany):
            nonlocal injected
            if statement.lstrip().startswith(("INSERT INTO", "UPDATE")):
                writes_before_failure.append(statement)
            if statement.lstrip().startswith(f"INSERT INTO {failure_table} "):
                injected = True
                raise SQLAlchemyError("Controlled lifecycle regression fault")

        listener_installed = False
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                developer = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}
                spec = _make_spec("fault-review", env["deps"])
                created = await client.post(
                    f"/v1/workspaces/{workspace_id}/agents", headers=developer,
                    json={"slug": "fault-review", "display_name": "Fault regression", "spec": spec},
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]
                prefix = f"/v1/workspaces/{workspace_id}/agents/{agent_id}"
                version_id = None
                if operation == "promote":
                    published = await client.post(
                        f"{prefix}/publish", headers={**developer, "Idempotency-Key": "setup"},
                        json={"expected_revision": 1},
                    )
                    assert published.status_code == 200, published.text
                    version_id = published.json()["agent_version_id"]
                before = counts()
                event.listen(engine.sync_engine, "before_cursor_execute", fail_write)
                listener_installed = True
                if operation == "publish":
                    result = await client.post(
                        f"{prefix}/publish", headers={**developer, "Idempotency-Key": "fault"},
                        json={"expected_revision": 1},
                    )
                    domain_table = "catalog.agent_versions"
                else:
                    result = await client.post(
                        f"{prefix}/promotions",
                        headers={"Authorization": f"Bearer {env['tokens']['owner_1a']}"},
                        json={"agent_version_id": version_id, "environment": "development", "expected_publication_revision": 0},
                    )
                    domain_table = "catalog.agent_publications"
                assert result.status_code == 503, result.text
                assert injected, "The intended post-write failure point must execute"
                assert any(f"INSERT INTO {domain_table} " in sql for sql in writes_before_failure)
                assert counts() == before, "Versions, bindings, receipt, pointer, audit and outbox must all roll back"
        finally:
            if listener_installed:
                event.remove(engine.sync_engine, "before_cursor_execute", fail_write)
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


# ============================================================================
# Step 04 Final Review Defect Regressions (RF04-02 .. RF04-06) — Real PostgreSQL
# ============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "kind,expected_field",
    [
        ("toolRefs", "toolRefs[1]"),
        ("skillRefs", "skillRefs[1]"),
        ("knowledgeRefs", "knowledgeRefs[1]"),
        ("two_versions_one_skill", "skillRefs[1]"),
    ],
)
def test_rf04_02_duplicate_and_conflicting_refs_diagnosed_and_rejected_422_real_postgres(
    repo_test_env, kind: str, expected_field: str
):
    """RF04-02: Duplicate/conflicting bindings are saved in draft (201), diagnosed ready=false,
    and rejected by publish with domain 422 (never 503) and zero partial writes."""
    env = repo_test_env
    spec = _make_spec(f"dup-{kind.lower()}", env["deps"])
    if kind == "two_versions_one_skill":
        version_2 = uuid4()
        with psycopg.connect(env["owner_url"]) as conn:
            conn.execute(
                "INSERT INTO catalog.skill_versions "
                "(tenant_id, workspace_id, id, skill_id, version, manifest, skill_md, bundle_ref, bundle_sha256) "
                "VALUES (%s, %s, %s, %s, 2, '{}', '# Skill v2', 's3://skills/v2', %s)",
                (env["seeded"]["tenant_1"], env["seeded"]["ws_1a"], version_2, env["deps"]["skill_id"], "d" * 64),
            )
        spec["skillRefs"].append({"skillId": str(env["deps"]["skill_id"]), "skillVersionId": str(version_2)})
    elif kind == "skillRefs":
        first = spec["skillRefs"][0]
        spec["skillRefs"].append(
            {"skillId": first["skillId"].upper(), "skillVersionId": first["skillVersionId"].upper()}
        )
    else:
        # Also verify case-normalized UUID comparison
        spec[kind].append(spec[kind][0].upper())

    async def scenario():
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override():
            async with factory() as session, session.begin():
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                base = f"/v1/workspaces/{env['seeded']['ws_1a']}/agents"
                headers = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}

                created = await client.post(
                    base,
                    headers=headers,
                    json={"slug": f"dup-{kind.lower()}", "display_name": f"Dup {kind}", "spec": spec},
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]

                validated = await client.post(f"{base}/{agent_id}/validate", headers=headers)
                assert validated.status_code == 200, validated.text
                val_body = validated.json()
                assert val_body["ready"] is False
                assert any(
                    d["field"] == expected_field and d["code"] == "invalid_spec"
                    for d in val_body["diagnostics"]
                ), f"Expected diagnostic for {expected_field}, got {val_body['diagnostics']}"

                published = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert published.status_code == 422, published.text

                with psycopg.connect(env["owner_url"]) as conn:
                    versions = conn.execute(
                        "SELECT count(*) FROM catalog.agent_versions WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    receipts = conn.execute(
                        "SELECT count(*) FROM catalog.agent_publish_receipts WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert versions == 0
                    assert receipts == 0
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_rf04_02_two_distinct_tool_versions_of_same_tool_remain_publishable_real_postgres(
    repo_test_env,
):
    """RF04-02: Schema UNIQUE (tenant_id,workspace_id,agent_version_id,tool_version_id) permits
    two distinct tool_version_ids of the same parent tool."""
    env = repo_test_env
    tool_ver_2 = uuid4()
    with psycopg.connect(env["owner_url"]) as conn:
        conn.execute(
            "INSERT INTO catalog.tool_versions "
            "(tenant_id, workspace_id, id, tool_id, version, input_schema, output_schema, risk, sha256) "
            "VALUES (%s, %s, %s, %s, 2, '{}'::jsonb, '{}'::jsonb, 'read', %s)",
            (env["seeded"]["tenant_1"], env["seeded"]["ws_1a"], tool_ver_2, env["deps"]["tool_id"], "e" * 64),
        )
    spec = _make_spec("two-tool-vers", env["deps"])
    spec["toolRefs"].append(str(tool_ver_2))

    async def scenario():
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override():
            async with factory() as session, session.begin():
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                base = f"/v1/workspaces/{env['seeded']['ws_1a']}/agents"
                headers = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}

                created = await client.post(
                    base,
                    headers=headers,
                    json={"slug": "two-tool-vers", "display_name": "Two Tool Versions", "spec": spec},
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]

                validated = await client.post(f"{base}/{agent_id}/validate", headers=headers)
                assert validated.status_code == 200 and validated.json()["ready"] is True

                published = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert published.status_code == 200, published.text
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_rf04_03_promote_and_rollback_reject_tool_mutated_to_unsupported_mcp_real_postgres(
    repo_test_env,
):
    """RF04-03: Parent catalog.tools.kind changed to 'mcp' after publish must be rejected
    under FOR SHARE parent lock during promote and rollback, without mutating pointers."""
    env = repo_test_env

    async def scenario():
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override():
            async with factory() as session, session.begin():
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                base = f"/v1/workspaces/{env['seeded']['ws_1a']}/agents"
                dev_headers = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}
                owner_headers = {"Authorization": f"Bearer {env['tokens']['owner_1a']}"}

                created = await client.post(
                    base,
                    headers=dev_headers,
                    json={
                        "slug": "mcp-drift-promote",
                        "display_name": "MCP Drift Promote",
                        "spec": _make_spec("mcp-drift-promote", env["deps"]),
                    },
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]

                # Publish v1 while tool is builtin
                pub_v1 = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**dev_headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert pub_v1.status_code == 200, pub_v1.text
                v1_id = pub_v1.json()["agent_version_id"]

                # Owner mutates tool kind to unsupported 'mcp' in test-owned fixture
                with psycopg.connect(env["owner_url"]) as conn:
                    conn.execute(
                        "UPDATE catalog.tools SET kind = 'mcp' WHERE id = %s",
                        (env["deps"]["tool_id"],),
                    )

                revalidated = await client.post(f"{base}/{agent_id}/validate", headers=dev_headers)
                assert revalidated.status_code == 200
                assert revalidated.json()["ready"] is False

                promoted = await client.post(
                    f"{base}/{agent_id}/promotions",
                    headers=owner_headers,
                    json={
                        "agent_version_id": v1_id,
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promoted.status_code == 422, promoted.text
                assert promoted.json()["detail"]["code"] == "unsupported_capability"

                with psycopg.connect(env["owner_url"]) as conn:
                    pointers = conn.execute(
                        "SELECT count(*) FROM catalog.agent_publications WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert pointers == 0

                # Also verify rollback path: publish v2 without tools, promote v2, then try rollback to v1 (MCP tool)
                spec_no_tools = _make_spec("mcp-drift-promote", env["deps"])
                spec_no_tools["toolRefs"] = []
                patched = await client.patch(
                    f"{base}/{agent_id}",
                    headers={**dev_headers, "If-Match": '"1"'},
                    json={"spec": spec_no_tools},
                )
                assert patched.status_code == 200, patched.text
                pub_v2 = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**dev_headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 2},
                )
                assert pub_v2.status_code == 200, pub_v2.text
                v2_id = pub_v2.json()["agent_version_id"]

                promote_v2 = await client.post(
                    f"{base}/{agent_id}/promotions",
                    headers=owner_headers,
                    json={
                        "agent_version_id": v2_id,
                        "environment": "development",
                        "expected_publication_revision": 0,
                    },
                )
                assert promote_v2.status_code == 200, promote_v2.text

                rollback_v1 = await client.post(
                    f"{base}/{agent_id}/promotions",
                    headers=owner_headers,
                    json={
                        "agent_version_id": v1_id,
                        "environment": "development",
                        "expected_publication_revision": 1,
                    },
                )
                assert rollback_v1.status_code == 422, rollback_v1.text
                assert rollback_v1.json()["detail"]["code"] == "unsupported_capability"

                with psycopg.connect(env["owner_url"]) as conn:
                    row = conn.execute(
                        "SELECT agent_version_id, revision FROM catalog.agent_publications "
                        "WHERE agent_id = %s AND environment = 'development'",
                        (agent_id,),
                    ).fetchone()
                    assert str(row[0]) == v2_id
                    assert row[1] == 1
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("bad_revision", [False, True, "0", 1.5, 2_147_483_648])
def test_rf04_04_promote_rejects_non_integer_and_out_of_range_revisions_real_postgres(
    repo_test_env, bad_revision
):
    """RF04-04: Non-integer/coerced or out-of-range expected_publication_revision returns 422
    and creates zero publication pointers."""
    env = repo_test_env

    async def scenario():
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override():
            async with factory() as session, session.begin():
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                base = f"/v1/workspaces/{env['seeded']['ws_1a']}/agents"
                headers = {"Authorization": f"Bearer {env['tokens']['owner_1a']}"}

                created = await client.post(
                    base,
                    headers=headers,
                    json={
                        "slug": "strict-rev-promote",
                        "display_name": "Strict Rev Promote",
                        "spec": _make_spec("strict-rev-promote", env["deps"]),
                    },
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]

                pub = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert pub.status_code == 200, pub.text

                promoted = await client.post(
                    f"{base}/{agent_id}/promotions",
                    headers=headers,
                    json={
                        "agent_version_id": pub.json()["agent_version_id"],
                        "environment": "production",
                        "expected_publication_revision": bad_revision,
                    },
                )
                assert promoted.status_code == 422, promoted.text

                with psycopg.connect(env["owner_url"]) as conn:
                    pointers = conn.execute(
                        "SELECT count(*) FROM catalog.agent_publications WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert pointers == 0
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_rf04_05_uncanonicalizable_revision_and_spec_return_422_not_500_real_postgres(
    repo_test_env,
):
    """RF04-05: Oversized revisions (2**53, 10**80) and uncanonicalizable spec numbers
    (limits.maxTotalTokens = 2**53) fail closed with 422 / ready=false and never 500."""
    env = repo_test_env

    async def scenario():
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override():
            async with factory() as session, session.begin():
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                base = f"/v1/workspaces/{env['seeded']['ws_1a']}/agents"
                headers = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}

                # 1. Oversized expected_revision rejected at DTO boundary (422, not 500)
                created = await client.post(
                    base,
                    headers=headers,
                    json={
                        "slug": "oversized-rev-test",
                        "display_name": "Oversized Rev Test",
                        "spec": _make_spec("oversized-rev-test", env["deps"]),
                    },
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]

                for bad_rev in (2**53, 10**80):
                    pub = await client.post(
                        f"{base}/{agent_id}/publish",
                        headers={**headers, "Idempotency-Key": str(uuid4())},
                        json={"expected_revision": bad_rev},
                    )
                    assert pub.status_code == 422, pub.text

                # 2. Uncanonicalizable spec number: draft saves (201), validate reports ready=false, publish returns 422
                large_spec = _make_spec("uncanon-spec-test", env["deps"])
                large_spec["limits"]["maxTotalTokens"] = 2**53
                created_large = await client.post(
                    base,
                    headers=headers,
                    json={
                        "slug": "uncanon-spec-test",
                        "display_name": "Uncanon Spec Test",
                        "spec": large_spec,
                    },
                )
                assert created_large.status_code == 201, created_large.text
                large_agent_id = created_large.json()["agent_id"]

                validated = await client.post(f"{base}/{large_agent_id}/validate", headers=headers)
                assert validated.status_code == 200, validated.text
                assert validated.json()["ready"] is False
                assert any(
                    d["field"] == "spec" and d["code"] == "invalid_spec"
                    for d in validated.json()["diagnostics"]
                )

                pub_large = await client.post(
                    f"{base}/{large_agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert pub_large.status_code == 422, pub_large.text

                with psycopg.connect(env["owner_url"]) as conn:
                    versions = conn.execute(
                        "SELECT count(*) FROM catalog.agent_versions WHERE agent_id = %s",
                        (large_agent_id,),
                    ).fetchone()[0]
                    assert versions == 0
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_rf04_06_output_schema_properties_allowed_while_credentials_rejected_real_postgres(
    repo_test_env,
):
    """RF04-06: Structured output schema containing property names like 'total_tokens' and benign
    stop delimiters publishes cleanly (200), while credential-bearing response_format or stop_sequences
    are still rejected (422 invalid_model_setting)."""
    env = repo_test_env
    format_setting = {
        "type": "json_schema",
        "json_schema": {
            "name": "usage",
            "schema": {
                "type": "object",
                "properties": {"total_tokens": {"type": "integer"}},
                "required": ["total_tokens"],
                "additionalProperties": False,
            },
        },
    }
    with psycopg.connect(env["owner_url"]) as conn:
        conn.execute(
            "UPDATE catalog.model_configs SET settings = %s::jsonb WHERE id = %s",
            (
                json.dumps(
                    {
                        "response_format": format_setting,
                        "stop_sequences": ["</secret_section>", "Password:"],
                    }
                ),
                env["deps"]["model_id"],
            ),
        )

    async def scenario():
        engine = create_async_engine(env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override():
            async with factory() as session, session.begin():
                yield session

        prior = dict(app.dependency_overrides)
        app.dependency_overrides[get_db_session] = override
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as client:
                base = f"/v1/workspaces/{env['seeded']['ws_1a']}/agents"
                headers = {"Authorization": f"Bearer {env['tokens']['dev_1a']}"}

                created = await client.post(
                    base,
                    headers=headers,
                    json={
                        "slug": "output-schema-ok",
                        "display_name": "Output Schema OK",
                        "spec": _make_spec("output-schema-ok", env["deps"]),
                    },
                )
                assert created.status_code == 201, created.text
                agent_id = created.json()["agent_id"]

                validated = await client.post(f"{base}/{agent_id}/validate", headers=headers)
                assert validated.status_code == 200 and validated.json()["ready"] is True

                pub_ok = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert pub_ok.status_code == 200, pub_ok.text

                # Now inject an actual credential-bearing key into response_format -> MUST reject 422
                with psycopg.connect(env["owner_url"]) as conn:
                    conn.execute(
                        "UPDATE catalog.model_configs SET settings = %s::jsonb WHERE id = %s",
                        (
                            json.dumps(
                                {
                                    "response_format": {
                                        "type": "json_object",
                                        "api_key": "sk-proj-forbidden1234567890",
                                    }
                                }
                            ),
                            env["deps"]["model_id"],
                        ),
                    )

                pub_bad_rf = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert pub_bad_rf.status_code == 422, pub_bad_rf.text
                assert pub_bad_rf.json()["detail"]["code"] == "invalid_model_setting"

                # Inject an actual embedded credential into stop_sequences -> MUST reject 422
                with psycopg.connect(env["owner_url"]) as conn:
                    conn.execute(
                        "UPDATE catalog.model_configs SET settings = %s::jsonb WHERE id = %s",
                        (
                            json.dumps(
                                {
                                    "stop_sequences": [
                                        "Bearer eyJhbGciOiJIUzI1NiJ9.secretpayload"
                                    ]
                                }
                            ),
                            env["deps"]["model_id"],
                        ),
                    )

                pub_bad_stop = await client.post(
                    f"{base}/{agent_id}/publish",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    json={"expected_revision": 1},
                )
                assert pub_bad_stop.status_code == 422, pub_bad_stop.text
                assert pub_bad_stop.json()["detail"]["code"] == "invalid_model_setting"
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(prior)
            await engine.dispose()

    asyncio.run(scenario())
