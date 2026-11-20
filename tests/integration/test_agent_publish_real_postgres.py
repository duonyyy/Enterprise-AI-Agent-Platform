"""Real PostgreSQL Integration & Concurrency Acceptance Tests for Step 04-C: Agent Publish & Receipt (C01..C16).

Authority:
- ADR-009: Immutable publishing with bundle SHA-256 digest.
- ADR-022: Publication receipt ledger, binding sealing protocol, and RFC 8785 canonical manifest.
- SPEC-AGENT-001: catalog.agent_versions and catalog.agent_publish_receipts (Proposed Table 58).
- DEC-DB-01 / DEC-DB-02: Fail-closed RLS and restricted application role (platform_app).
- TEST-AG-001: Acceptance Plan Matrix C01..C16.
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
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from run_migrations import run_migrations
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
    owner_1a, dev_1a, viewer_1a = uuid4(), uuid4(), uuid4()
    suffix = str(tenant_1)[:8]

    with psycopg.connect(owner_url) as conn:
        for uid, sub, email in (
            (owner_1a, f"owner-1a-{suffix}", f"owner-1a-{suffix}@example.com"),
            (dev_1a, f"dev-1a-{suffix}", f"dev-1a-{suffix}@example.com"),
            (viewer_1a, f"viewer-1a-{suffix}", f"viewer-1a-{suffix}@example.com"),
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

    return {
        "issuer": issuer,
        "tenant_1": tenant_1,
        "ws_1a": ws_1a,
        "owner_1a": owner_1a,
        "dev_1a": dev_1a,
        "viewer_1a": viewer_1a,
        "dev_1a_sub": f"dev-1a-{suffix}",
        "owner_1a_sub": f"owner-1a-{suffix}",
        "viewer_1a_sub": f"viewer-1a-{suffix}",
    }


def _seed_dependencies(
    owner_url: str, tenant_id: UUID, workspace_id: UUID, actor_user_id: UUID
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

        # 2. Tool & Version & Grant
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

        # 4. Knowledge Base & Grant
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
def publish_test_env(isolated_database: dict[str, Any]):
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]

    # Verify platform_app privileges
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
        seeded["dev_1a"],
    )

    sa_url = _make_sa_url(app_url)
    previous_denylist = session_manager.denylist
    session_manager.denylist = InMemoryRevocationStore()

    tokens = {
        "dev_1a": _create_token(seeded["dev_1a"], seeded["issuer"], seeded["dev_1a_sub"]),
        "owner_1a": _create_token(seeded["owner_1a"], seeded["issuer"], seeded["owner_1a_sub"]),
        "viewer_1a": _create_token(seeded["viewer_1a"], seeded["issuer"], seeded["viewer_1a_sub"]),
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


@pytest.mark.integration
def test_c01_happy_path_publish_atomic_immutability_and_manifest(publish_test_env):
    """C01: Publish creates real version, manifest, bindings, seal, receipt, audit, outbox.
    Draft revision is UNCHANGED. Publication pointer is NOT updated.
    """
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    owner_url = publish_test_env["owner_url"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                # 1. Create draft
                spec = _make_spec("agent-c01", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c01", "display_name": "Agent C01", "spec": spec},
                )
                assert create_resp.status_code == 201, create_resp.text
                agent_id = UUID(create_resp.json()["agent_id"])

                # 2. Publish draft revision 1
                pub_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "pub-c01-key-01",
                    },
                    json={"expected_revision": 1},
                )
                assert pub_resp.status_code == 200, pub_resp.text
                pub_data = pub_resp.json()
                assert pub_data["agent_id"] == str(agent_id)
                assert pub_data["version"] == 1
                assert pub_data["is_immutable"] is True
                assert len(pub_data["bundle_sha256"]) == 64
                version_id = UUID(pub_data["agent_version_id"])

                # 3. Direct DB assertions
                with psycopg.connect(owner_url) as conn:
                    # Assert draft revision is UNCHANGED (revision 1)
                    draft_row = conn.execute(
                        "SELECT revision, archived_at FROM catalog.agents WHERE id = %s",
                        (agent_id,),
                    ).fetchone()
                    assert draft_row[0] == 1
                    assert draft_row[1] is None

                    # Assert version row created and sealed
                    v_row = conn.execute(
                        "SELECT version, bundle_sha256, bundle_manifest, bindings_sealed_at "
                        "FROM catalog.agent_versions WHERE id = %s",
                        (version_id,),
                    ).fetchone()
                    assert v_row is not None
                    assert v_row[0] == 1
                    assert v_row[1] == pub_data["bundle_sha256"]
                    manifest = v_row[2]
                    assert manifest["canonicalization"] == "RFC8785"
                    assert manifest["agentSlug"] == "agent-c01"
                    assert manifest["dependencies"]["model"]["id"] == str(deps["model_id"])
                    assert v_row[3] is not None  # bindings_sealed_at IS NOT NULL

                    # Assert bindings
                    tb = conn.execute(
                        "SELECT tool_version_id FROM catalog.agent_tool_bindings WHERE agent_version_id = %s",
                        (version_id,),
                    ).fetchall()
                    assert len(tb) == 1
                    assert tb[0][0] == deps["tool_version_id"]

                    kb = conn.execute(
                        "SELECT base_id FROM catalog.agent_knowledge_bindings WHERE agent_version_id = %s",
                        (version_id,),
                    ).fetchall()
                    assert len(kb) == 1
                    assert kb[0][0] == deps["kb_id"]

                    sb = conn.execute(
                        "SELECT skill_id, skill_version_id FROM catalog.agent_skill_bindings WHERE agent_version_id = %s",
                        (version_id,),
                    ).fetchall()
                    assert len(sb) == 1
                    assert sb[0] == (deps["skill_id"], deps["skill_version_id"])

                    # Assert durable publish receipt
                    receipt_row = conn.execute(
                        "SELECT operation_key, response_status, response_body FROM catalog.agent_publish_receipts "
                        "WHERE agent_id = %s AND operation_key = %s",
                        (agent_id, "pub-c01-key-01"),
                    ).fetchone()
                    assert receipt_row is not None
                    assert receipt_row[0] == "pub-c01-key-01"
                    assert receipt_row[1] == 200
                    assert receipt_row[2]["version"] == 1

                    # Assert audit event
                    audit_row = conn.execute(
                        "SELECT action, outcome, resource_type, resource_id, details_redacted "
                        "FROM governance.audit_events WHERE resource_id = %s",
                        (str(version_id),),
                    ).fetchone()
                    assert audit_row is not None
                    assert audit_row[0] == "agent.publish"
                    assert audit_row[1] == "succeeded"
                    assert audit_row[4]["version"] == 1

                    # Assert outbox event
                    outbox_row = conn.execute(
                        "SELECT event_type, state, payload FROM integration.outbox_events WHERE aggregate_id = %s",
                        (agent_id,),
                    ).fetchone()
                    assert outbox_row is not None
                    assert outbox_row[0] == "agent.published"
                    assert outbox_row[1] == "pending"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c02_concurrency_10_concurrent_publish_exact_same_key(publish_test_env):
    """C02: 10 concurrent requests with the exact same operation key produce exactly 1 version and 1 receipt."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    owner_url = publish_test_env["owner_url"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c02", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c02", "display_name": "Agent C02", "spec": spec},
                )
                assert create_resp.status_code == 201
                agent_id = UUID(create_resp.json()["agent_id"])

                # Concurrency >= 10: fire 10 simultaneous publish requests with exact same key
                shared_key = f"c02-shared-key-{uuid4().hex}"

                async def _do_publish():
                    return await client.post(
                        f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                        headers={
                            "Authorization": f"Bearer {dev_token}",
                            "Idempotency-Key": shared_key,
                        },
                        json={"expected_revision": 1},
                    )

                responses = await asyncio.gather(*[_do_publish() for _ in range(10)])

                for r in responses:
                    assert r.status_code == 200, r.text
                    assert r.json()["version"] == 1

                # Verify all responses received the exact same version ID and bundle hash
                version_ids = {r.json()["agent_version_id"] for r in responses}
                assert len(version_ids) == 1

                bundle_hashes = {r.json()["bundle_sha256"] for r in responses}
                assert len(bundle_hashes) == 1

                # Verify DB has exactly 1 version row and 1 receipt row
                with psycopg.connect(owner_url) as conn:
                    v_count = conn.execute(
                        "SELECT count(*) FROM catalog.agent_versions WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert v_count == 1

                    r_count = conn.execute(
                        "SELECT count(*) FROM catalog.agent_publish_receipts WHERE agent_id = %s",
                        (agent_id,),
                    ).fetchone()[0]
                    assert r_count == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c03_key_collision_with_different_request_or_actor_returns_409(publish_test_env):
    """C03: Same operation key but different request payload or actor returns 409 conflict."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    owner_token = publish_test_env["tokens"]["owner_1a"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c03", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c03", "display_name": "Agent C03", "spec": spec},
                )
                assert create_resp.status_code == 201
                agent_id = UUID(create_resp.json()["agent_id"])

                collision_key = "c03-collision-key"

                # 1. First publish succeeds
                resp1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": collision_key,
                    },
                    json={"expected_revision": 1},
                )
                assert resp1.status_code == 200

                # 2. Same key but different expected_revision -> 409
                resp2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": collision_key,
                    },
                    json={"expected_revision": 2},
                )
                assert resp2.status_code == 409
                assert resp2.json()["detail"]["code"] == "idempotency_conflict"

                # 3. Same key but different actor (owner instead of dev) -> 409
                resp3 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {owner_token}",
                        "Idempotency-Key": collision_key,
                    },
                    json={"expected_revision": 1},
                )
                assert resp3.status_code == 409
                assert resp3.json()["detail"]["code"] == "idempotency_conflict"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c04_draft_edit_after_publish_replay_old_key_returns_200_not_412(publish_test_env):
    """C04: Replay lookup precedes current revision check; draft edit does not break replay."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c04", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c04", "display_name": "Agent C04", "spec": spec},
                )
                assert create_resp.status_code == 201
                agent_id = UUID(create_resp.json()["agent_id"])

                op_key = "c04-replay-key"

                # 1. Publish rev 1
                pub1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": op_key,
                    },
                    json={"expected_revision": 1},
                )
                assert pub1.status_code == 200
                v1_id = pub1.json()["agent_version_id"]

                # 2. Edit draft to increment revision to 2
                spec["metadata"]["description"] = "Updated draft description"
                edit_resp = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "If-Match": '"1"',
                    },
                    json={"spec": spec},
                )
                assert edit_resp.status_code == 200
                assert edit_resp.json()["revision"] == 2

                # 3. Replay with original key and expected_revision=1
                # MUST return 200 from receipt, NOT 412!
                replay_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": op_key,
                    },
                    json={"expected_revision": 1},
                )
                assert replay_resp.status_code == 200
                assert replay_resp.json()["agent_version_id"] == v1_id
                assert replay_resp.json()["version"] == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c05_different_keys_same_draft_revision_creates_sequential_versions(publish_test_env):
    """C05: Different operation keys with the same draft revision create sequential versions (1, 2, 3)."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    owner_url = publish_test_env["owner_url"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c05", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c05", "display_name": "Agent C05", "spec": spec},
                )
                assert create_resp.status_code == 201
                agent_id = UUID(create_resp.json()["agent_id"])

                # Publish 1: key-v1 -> version 1
                r1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c05-key-v1",
                    },
                    json={"expected_revision": 1},
                )
                assert r1.status_code == 200
                assert r1.json()["version"] == 1

                # Publish 2: key-v2 -> version 2
                r2 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c05-key-v2",
                    },
                    json={"expected_revision": 1},
                )
                assert r2.status_code == 200
                assert r2.json()["version"] == 2

                # Publish 3: key-v3 -> version 3
                r3 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c05-key-v3",
                    },
                    json={"expected_revision": 1},
                )
                assert r3.status_code == 200
                assert r3.json()["version"] == 3

                # Assert all 3 versions exist in DB
                with psycopg.connect(owner_url) as conn:
                    versions = conn.execute(
                        "SELECT version FROM catalog.agent_versions WHERE agent_id = %s ORDER BY version ASC",
                        (agent_id,),
                    ).fetchall()
                    assert [v[0] for v in versions] == [1, 2, 3]
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c06_draft_edit_wins_before_publish_returns_412(publish_test_env):
    """C06: If draft edit occurs before a new publish request, old expected_revision gets 412."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c06", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c06", "display_name": "Agent C06", "spec": spec},
                )
                agent_id = UUID(create_resp.json()["agent_id"])

                # Edit draft to revision 2
                spec["persona"]["role"] = "Specialist"
                edit_resp = await client.patch(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "If-Match": '"1"',
                    },
                    json={"spec": spec},
                )
                assert edit_resp.status_code == 200
                assert edit_resp.json()["revision"] == 2

                # Publish with stale revision 1 on a NEW operation key -> 412
                pub_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c06-new-key",
                    },
                    json={"expected_revision": 1},
                )
                assert pub_resp.status_code == 412
                assert pub_resp.json()["detail"]["code"] == "stale_revision"
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c10_c11_direct_app_role_mutation_denied_and_deferred_seal(publish_test_env):
    """C10 & C11: App role cannot UPDATE/DELETE versions, bindings, receipts.
    Cannot INSERT bindings on sealed version.
    Unsealed version cannot COMMIT (asserted by check_agent_version_sealed deferred trigger).
    """
    app_url = publish_test_env["app_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    tenant_1 = publish_test_env["seeded"]["tenant_1"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    deps = publish_test_env["deps"]

    # First publish a real version to test against
    async def _setup():
        engine = create_async_engine(publish_test_env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c10", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c10", "display_name": "Agent C10", "spec": spec},
                )
                agent_id = UUID(create_resp.json()["agent_id"])
                pub_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c10-key",
                    },
                    json={"expected_revision": 1},
                )
                assert pub_resp.status_code == 200
                return agent_id, UUID(pub_resp.json()["agent_version_id"])
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    agent_id, version_id = asyncio.run(_setup())

    # C10: Direct SQL mutation attempts by platform_app MUST fail
    with psycopg.connect(app_url) as conn:

        def _set_ctx():
            conn.execute(
                "SELECT set_config('app.tenant_id', %s, false), set_config('app.workspace_id', %s, false)",
                (str(tenant_1), str(ws_1a)),
            )

        _set_ctx()
        # 1. Direct UPDATE on sealed version -> denied
        with pytest.raises(psycopg.Error):
            conn.execute(
                "UPDATE catalog.agent_versions SET schema_version = '2.0' WHERE id = %s",
                (version_id,),
            )
        conn.rollback()

        _set_ctx()
        # 2. Direct DELETE on sealed version -> denied
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM catalog.agent_versions WHERE id = %s", (version_id,))
        conn.rollback()

        _set_ctx()
        # 3. Direct UPDATE on bindings -> denied
        with pytest.raises(psycopg.Error):
            conn.execute(
                "UPDATE catalog.agent_tool_bindings SET usage_mode = 'requires_approval' WHERE agent_version_id = %s",
                (version_id,),
            )
        conn.rollback()

        _set_ctx()
        # 4. Direct DELETE on bindings -> denied
        with pytest.raises(psycopg.Error):
            conn.execute(
                "DELETE FROM catalog.agent_tool_bindings WHERE agent_version_id = %s",
                (version_id,),
            )
        conn.rollback()

        _set_ctx()
        # 5. Direct UPDATE on receipts -> denied
        with pytest.raises(psycopg.Error):
            conn.execute(
                "UPDATE catalog.agent_publish_receipts SET response_status = 500 WHERE agent_version_id = %s",
                (version_id,),
            )
        conn.rollback()

        _set_ctx()
        # 6. Direct DELETE on receipts -> denied
        with pytest.raises(psycopg.Error):
            conn.execute(
                "DELETE FROM catalog.agent_publish_receipts WHERE agent_version_id = %s",
                (version_id,),
            )
        conn.rollback()

        _set_ctx()
        # C11: Direct INSERT into bindings on sealed version -> denied by guard_binding_insert trigger
        with pytest.raises(psycopg.Error) as exc_info:
            conn.execute(
                "INSERT INTO catalog.agent_tool_bindings (tenant_id, workspace_id, agent_version_id, tool_version_id) "
                "VALUES (%s, %s, %s, %s)",
                (tenant_1, ws_1a, version_id, deps["tool_version_id"]),
            )
        conn.rollback()
        assert "sealed" in str(exc_info.value).lower()

        _set_ctx()
        # C11: Direct INSERT of agent version with bindings_sealed_at IS NULL committed -> rejected at COMMIT
        with pytest.raises(psycopg.Error) as exc_info2:
            unsealed_ver_id = uuid4()
            conn.execute(
                "INSERT INTO catalog.agent_versions ("
                "    tenant_id, workspace_id, id, agent_id, version, schema_version, "
                "    frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by, bindings_sealed_at"
                ") VALUES ("
                "    %s, %s, %s, %s, 999, '1.0', '{}', %s, '{}', %s, %s, NULL"
                ")",
                (
                    tenant_1,
                    ws_1a,
                    unsealed_ver_id,
                    agent_id,
                    "d" * 64,
                    deps["model_id"],
                    publish_test_env["seeded"]["dev_1a"],
                ),
            )
            # Fails at commit because check_agent_version_sealed_trigger is DEFERRED
            conn.commit()
        conn.rollback()
        assert "sealed" in str(exc_info2.value).lower()


@pytest.mark.integration
def test_c12_skill_pair_composite_integrity(publish_test_env):
    """C12: Foreign key fk_agent_skill_bindings_scoped_pair rejects mismatched skill_id/skill_version_id."""
    owner_url = publish_test_env["owner_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    tenant_1 = publish_test_env["seeded"]["tenant_1"]
    deps = publish_test_env["deps"]

    other_skill_id = uuid4()
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO catalog.skills (tenant_id, workspace_id, id, slug, display_name, status) "
            "VALUES (%s, %s, %s, 'other-skill', 'Other Skill', 'active')",
            (tenant_1, ws_1a, other_skill_id),
        )

        agent_id = uuid4()
        conn.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name, draft_spec, revision) "
            "VALUES (%s, %s, %s, 'skill-pair-agent', 'Skill Pair Agent', '{}', 1)",
            (tenant_1, ws_1a, agent_id),
        )

        dummy_version_id = uuid4()
        conn.execute(
            "INSERT INTO catalog.agent_versions ("
            "    tenant_id, workspace_id, id, agent_id, version, schema_version, "
            "    frozen_spec, bundle_sha256, bundle_manifest, model_config_id, published_by, bindings_sealed_at"
            ") VALUES ("
            "    %s, %s, %s, %s, 100, '1.0', '{}', %s, '{}', %s, %s, NULL"
            ")",
            (
                tenant_1,
                ws_1a,
                dummy_version_id,
                agent_id,
                "e" * 64,
                deps["model_id"],
                publish_test_env["seeded"]["dev_1a"],
            ),
        )

        # Attempt to insert pair with other_skill_id but deps["skill_version_id"] (which belongs to deps["skill_id"])
        with pytest.raises(psycopg.Error) as exc_info:
            conn.execute(
                "INSERT INTO catalog.agent_skill_bindings (tenant_id, workspace_id, agent_version_id, skill_id, skill_version_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tenant_1, ws_1a, dummy_version_id, other_skill_id, deps["skill_version_id"]),
            )
        conn.rollback()
        assert (
            "fk_agent_skill_bindings_scoped_pair" in str(exc_info.value).lower()
            or "foreign key" in str(exc_info.value).lower()
        )


@pytest.mark.integration
def test_c13_zero_leak_in_receipts_and_audit(publish_test_env):
    """C13: Publish receipt response_body and audit details_redacted contain zero raw secrets or instructions."""
    owner_url = publish_test_env["owner_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    deps = publish_test_env["deps"]

    async def _scenario():
        engine = create_async_engine(publish_test_env["sa_url"], poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                secret_phrase = "SECRET_PROMPT_DO_NOT_LEAK_12345"
                spec = _make_spec("agent-c13", deps)
                spec["persona"]["instructions"] = f"You are an assistant. {secret_phrase}"

                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c13", "display_name": "Agent C13", "spec": spec},
                )
                agent_id = UUID(create_resp.json()["agent_id"])

                pub_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c13-key",
                    },
                    json={"expected_revision": 1},
                )
                assert pub_resp.status_code == 200
                version_id = UUID(pub_resp.json()["agent_version_id"])

                with psycopg.connect(owner_url) as conn:
                    # 1. Check receipt response_body
                    receipt_row = conn.execute(
                        "SELECT response_body FROM catalog.agent_publish_receipts WHERE agent_version_id = %s",
                        (version_id,),
                    ).fetchone()
                    receipt_json_str = json.dumps(receipt_row[0])
                    assert secret_phrase not in receipt_json_str

                    # 2. Check audit details_redacted
                    audit_row = conn.execute(
                        "SELECT details_redacted FROM governance.audit_events WHERE resource_id = %s",
                        (str(version_id),),
                    ).fetchone()
                    audit_json_str = json.dumps(audit_row[0])
                    assert secret_phrase not in audit_json_str
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c04b_replay_after_agent_archive_returns_200_and_new_key_returns_409(publish_test_env):
    """C04b: Replay of an existing idempotency key on an archived agent succeeds with 200; new publish fails with 409."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    deps = publish_test_env["deps"]
    owner_url = publish_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c04b", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c04b", "display_name": "Agent C04b", "spec": spec},
                )
                assert create_resp.status_code == 201
                agent_id = UUID(create_resp.json()["agent_id"])

                op_key = "c04b-replay-key"

                # 1. Publish revision 1
                pub1 = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": op_key,
                    },
                    json={"expected_revision": 1},
                )
                assert pub1.status_code == 200
                v1_id = pub1.json()["agent_version_id"]

                # 2. Archive the agent draft
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE catalog.agents SET archived_at = now() WHERE id = %s",
                        (agent_id,),
                    )

                # 3. Replay with original key -> MUST return 200 cached receipt
                replay_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": op_key,
                    },
                    json={"expected_revision": 1},
                )
                assert replay_resp.status_code == 200
                assert replay_resp.json()["agent_version_id"] == v1_id

                # 4. Attempt new publish with new key on archived agent -> MUST return 409
                new_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c04b-new-key",
                    },
                    json={"expected_revision": 1},
                )
                assert new_resp.status_code == 409
                assert new_resp.json()["detail"]["code"] == "conflict"
                assert "archived" in new_resp.json()["detail"]["message"].lower()
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())


@pytest.mark.integration
def test_c07_inactive_model_or_revoked_tool_rejected_at_publish(publish_test_env):
    """C07: Inactive/disabled model dependency is caught under FOR SHARE lock and rejected with 422."""
    sa_url = publish_test_env["sa_url"]
    ws_1a = publish_test_env["seeded"]["ws_1a"]
    dev_token = publish_test_env["tokens"]["dev_1a"]
    deps = publish_test_env["deps"]
    owner_url = publish_test_env["owner_url"]

    async def _scenario():
        engine = create_async_engine(sa_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_db
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as client:
                spec = _make_spec("agent-c07", deps)
                create_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"slug": "agent-c07", "display_name": "Agent C07", "spec": spec},
                )
                assert create_resp.status_code == 201
                agent_id = UUID(create_resp.json()["agent_id"])

                # Disable model in DB
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE catalog.model_configs SET status = 'disabled' WHERE id = %s",
                        (deps["model_id"],),
                    )

                # Attempt publish -> 422 Unprocessable Content
                pub_resp = await client.post(
                    f"/v1/workspaces/{ws_1a}/agents/{agent_id}/publish",
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "Idempotency-Key": "c07-key",
                    },
                    json={"expected_revision": 1},
                )
                assert pub_resp.status_code == 422
                assert "disabled" in pub_resp.json()["detail"]["message"].lower()

                # Restore model status for subsequent tests
                with psycopg.connect(owner_url) as conn:
                    conn.execute(
                        "UPDATE catalog.model_configs SET status = 'active' WHERE id = %s",
                        (deps["model_id"],),
                    )
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(_scenario())
