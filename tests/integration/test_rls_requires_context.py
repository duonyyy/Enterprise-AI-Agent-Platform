"""PostgreSQL integration proof for fail-closed tenant and workspace RLS.

Authority:
- DEC-DB-01 (APPROVED_BY_USER): Restore fail-closed tenant & workspace isolation on iam.workspaces.
- ADR-005 (Trust boundaries): Authenticated principal and tenant/workspace scope come from
  trusted server-side state, strictly enforced by PostgreSQL RLS.
- Gate G1-D: Full RLS isolation matrix (missing, empty, cross-tenant, cross-workspace, DML denial, pool reuse).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.integration
def test_real_pool_reuses_connection_without_transaction_scope_leak(migrated_database):
    import psycopg

    tenant, ws_a, ws_b = uuid4(), uuid4(), uuid4()
    with psycopg.connect(migrated_database["owner_url"]) as conn:
        conn.execute(
            "INSERT INTO iam.tenants(id,slug,name) VALUES (%s,%s,'Pool fixture')",
            (tenant, str(tenant)),
        )
        for workspace in (ws_a, ws_b):
            conn.execute(
                "INSERT INTO iam.workspaces(tenant_id,id,slug,name) VALUES (%s,%s,%s,'Pool fixture')",
                (tenant, workspace, str(workspace)),
            )
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(migrated_database["app_url"]),
        pool_size=1,
        max_overflow=0,
        pool_reset_on_return="rollback",
    )
    try:
        with engine.begin() as conn:
            pid = conn.scalar(text("SELECT pg_backend_pid()"))
            conn.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant)}
            )
            conn.execute(
                text("SELECT set_config('app.workspace_id',:workspace,true)"),
                {"workspace": str(ws_a)},
            )
            assert conn.scalar(text("SELECT count(*) FROM iam.workspaces")) == 1
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT pg_backend_pid()")) == pid
            assert conn.scalar(text("SELECT count(*) FROM iam.workspaces")) == 0
            conn.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant)}
            )
            conn.execute(
                text("SELECT set_config('app.workspace_id',:workspace,true)"),
                {"workspace": str(ws_b)},
            )
            assert conn.scalar(text("SELECT id FROM iam.workspaces")) == ws_b
        with pytest.raises(RuntimeError, match="abort request"), engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant)}
            )
            conn.execute(
                text("SELECT set_config('app.workspace_id',:workspace,true)"),
                {"workspace": str(ws_a)},
            )
            raise RuntimeError("abort request")
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT pg_backend_pid()")) == pid
            assert conn.scalar(text("SELECT count(*) FROM iam.workspaces")) == 0
    finally:
        engine.dispose()
        with psycopg.connect(migrated_database["owner_url"]) as conn:
            conn.execute("DELETE FROM iam.workspaces WHERE tenant_id=%s", (tenant,))
            conn.execute("DELETE FROM iam.tenants WHERE id=%s", (tenant,))


@pytest.mark.integration
def test_application_role_requires_both_tenant_and_workspace_context(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that iam.workspaces requires BOTH app.tenant_id AND app.workspace_id.

    This test directly proves the fix for CI Error #3 (DEC-DB-01 / 0003_restore_workspaces_rls.sql).
    """
    psycopg = pytest.importorskip("psycopg")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    tenant_id = uuid4()
    workspace_id = uuid4()
    wrong_workspace_id = uuid4()
    wrong_tenant_id = uuid4()
    tenant_slug = f"tenant-{tenant_id.hex[:12]}"
    workspace_slug = f"ws-{workspace_id.hex[:12]}"

    with psycopg.connect(owner_url) as owner, owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (tenant_id, tenant_slug, "RLS integration tenant"),
        )
        cursor.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (tenant_id, workspace_id, workspace_slug, "RLS integration workspace"),
        )

    try:
        with psycopg.connect(app_url) as app, app.cursor() as cursor:
            # 1. No context set at all -> 0 rows
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 0

            # 2. Empty string context -> 0 rows
            cursor.execute("SELECT set_config('app.tenant_id', '', true)")
            cursor.execute("SELECT set_config('app.workspace_id', '', true)")
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 0

            # 3. Only tenant set, workspace missing/empty -> 0 rows
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cursor.execute("SELECT set_config('app.workspace_id', '', true)")
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 0

            # 4. Only workspace set, tenant missing/empty -> 0 rows
            cursor.execute("SELECT set_config('app.tenant_id', '', true)")
            cursor.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 0

            # 5. Correct tenant, WRONG workspace -> 0 rows
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cursor.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (str(wrong_workspace_id),)
            )
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 0

            # 6. WRONG tenant, correct workspace -> 0 rows
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(wrong_tenant_id),))
            cursor.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 0

            # 7. Both correct tenant and workspace -> EXACTLY 1 row
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cursor.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
            cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cursor.fetchone()[0] == 1
    finally:
        with psycopg.connect(owner_url) as owner, owner.cursor() as cursor:
            cursor.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
            cursor.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))


@pytest.mark.integration
def test_workspace_scoped_table_isolation_matrix(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies cross-tenant and same-tenant cross-workspace isolation on catalog.agents.

    Setup:
      - Tenant 1: Workspace 1A (Agent 1A), Workspace 1B (Agent 1B)
      - Tenant 2: Workspace 2A (Agent 2A)
    """
    psycopg = pytest.importorskip("psycopg")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    t1, t2 = uuid4(), uuid4()
    ws1a, ws1b, ws2a = uuid4(), uuid4(), uuid4()
    ag1a, ag1b, ag2a = uuid4(), uuid4(), uuid4()

    with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
        # Create tenants
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (t1, f"t1-{t1.hex[:8]}", "T1"),
        )
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (t2, f"t2-{t2.hex[:8]}", "T2"),
        )
        # Create workspaces
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t1, ws1a, f"ws1a-{ws1a.hex[:8]}", "WS1A"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t1, ws1b, f"ws1b-{ws1b.hex[:8]}", "WS1B"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t2, ws2a, f"ws2a-{ws2a.hex[:8]}", "WS2A"),
        )
        # Create agents
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t1, ws1a, ag1a, "agent-1a", "Agent 1A"),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t1, ws1b, ag1b, "agent-1b", "Agent 1B"),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t2, ws2a, ag2a, "agent-2a", "Agent 2A"),
        )

    try:
        with psycopg.connect(app_url) as app, app.cursor() as cur:
            # Query under T1 / WS1A
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t1),))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(ws1a),))
            cur.execute("SELECT id, display_name FROM catalog.agents ORDER BY slug")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == ag1a

            # Query under T1 / WS1B (same tenant, different workspace)
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t1),))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(ws1b),))
            cur.execute("SELECT id, display_name FROM catalog.agents ORDER BY slug")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == ag1b

            # Query under T2 / WS2A (different tenant)
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t2),))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(ws2a),))
            cur.execute("SELECT id, display_name FROM catalog.agents ORDER BY slug")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == ag2a
    finally:
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute("DELETE FROM catalog.agents WHERE id IN (%s, %s, %s)", (ag1a, ag1b, ag2a))
            cur.execute("DELETE FROM iam.workspaces WHERE id IN (%s, %s, %s)", (ws1a, ws1b, ws2a))
            cur.execute("DELETE FROM iam.tenants WHERE id IN (%s, %s)", (t1, t2))


@pytest.mark.integration
def test_dml_write_denial_and_silent_noop(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that platform_app cannot modify or delete rows outside its workspace scope."""
    psycopg = pytest.importorskip("psycopg")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    t1, t2 = uuid4(), uuid4()
    ws1, ws2 = uuid4(), uuid4()
    ag1, ag2 = uuid4(), uuid4()

    with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (t1, f"t1-{t1.hex[:8]}", "T1"),
        )
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (t2, f"t2-{t2.hex[:8]}", "T2"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t1, ws1, f"ws1-{ws1.hex[:8]}", "WS1"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (t2, ws2, f"ws2-{ws2.hex[:8]}", "WS2"),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t1, ws1, ag1, "agent-1", "Agent 1"),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (t2, ws2, ag2, "agent-2", "Agent 2"),
        )

    try:
        with psycopg.connect(app_url) as app, app.cursor() as cur:
            # Set context to T1 / WS1
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t1),))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(ws1),))

            # 1. INSERT with mismatched tenant_id in payload must fail WITH CHECK
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
                    (t2, ws1, uuid4(), "illegal-agent", "Illegal Agent"),
                )
            app.rollback()

            # Re-set context after rollback
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(t1),))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(ws1),))

            # 2. UPDATE out-of-scope row (Agent 2 in T2) is invisible -> affected rows = 0
            cur.execute(
                "UPDATE catalog.agents SET display_name = 'Hacked Agent' WHERE id = %s",
                (ag2,),
            )
            assert cur.rowcount == 0

            # 3. DELETE out-of-scope row (Agent 2 in T2) is invisible -> affected rows = 0
            cur.execute(
                "DELETE FROM catalog.agents WHERE id = %s",
                (ag2,),
            )
            assert cur.rowcount == 0

        # Verify on owner connection that Agent 2 was NOT modified or deleted
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute("SELECT display_name FROM catalog.agents WHERE id = %s", (ag2,))
            res = cur.fetchone()
            assert res is not None
            assert res[0] == "Agent 2"
    finally:
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute("DELETE FROM catalog.agents WHERE id IN (%s, %s)", (ag1, ag2))
            cur.execute("DELETE FROM iam.workspaces WHERE id IN (%s, %s)", (ws1, ws2))
            cur.execute("DELETE FROM iam.tenants WHERE id IN (%s, %s)", (t1, t2))


@pytest.mark.integration
def test_connection_pool_reuse_resets_context(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that pooling context reset (DISCARD ALL) closes all isolation leakage."""
    psycopg = pytest.importorskip("psycopg")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    tenant_id = uuid4()
    workspace_id = uuid4()

    with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (tenant_id, f"t-{tenant_id.hex[:8]}", "T"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (tenant_id, workspace_id, f"ws-{workspace_id.hex[:8]}", "WS"),
        )

    try:
        with psycopg.connect(app_url, autocommit=True) as app, app.cursor() as cur:
            # Request 1 sets context
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            cur.execute("SELECT set_config('app.workspace_id', %s, false)", (str(workspace_id),))
            cur.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cur.fetchone()[0] == 1

            # Connection returned to pool -> DISCARD ALL
            cur.execute("DISCARD ALL")

            # Request 2 (borrowing same connection from pool without context)
            cur.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
            assert cur.fetchone()[0] == 0
    finally:
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
            cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))
