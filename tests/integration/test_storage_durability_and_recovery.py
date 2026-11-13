"""Integration tests for storage persistence across server restart and recovery via backup/restore.

Authority:
- ADR-006 (Durable State): Database state is durable across process restarts, failures, and migrations.
- Gate G1-F: Restart server process, preserving volume/data dir, and verify all data and RLS intact.
- Gate G1-G: Backup database via pg_dump and restore via pg_restore to a separate database,
  verifying 56 domain tables, technical migration history, RLS policies, and exact row counts.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from test_postgres_fixture import (
    backup_database,
    database_url,
    restart_cluster,
    restore_database,
    temporary_database,
)

from scripts.validate_database_schema import EXPECTED_MIGRATIONS

CONSTRAINTS_SQL = """
SELECT n.nspname, c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid)
FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
ORDER BY n.nspname,c.relname,k.conname
"""


@pytest.mark.integration
@pytest.mark.parametrize("recreate", [False, True], ids=["restart", "recreate"])
def test_persistence_across_server_restart(
    recreate: bool,
    postgres_cluster: dict[str, Any],
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that data, tables, and RLS survive PostgreSQL server process restart (G1-F)."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    tenant_id = uuid4()
    workspace_id = uuid4()
    agent_id = uuid4()

    # Step 1: Insert test records
    with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (tenant_id, f"tp-{tenant_id.hex[:8]}", "Tenant Persistence"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (tenant_id, workspace_id, f"wsp-{workspace_id.hex[:8]}", "Workspace Persistence"),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (
                tenant_id,
                workspace_id,
                agent_id,
                "agent-persist",
                "Agent Persisted Across Restart",
            ),
        )

    try:
        # Step 2: Restart the PostgreSQL server process (preserving data directory)
        restart_cluster(postgres_cluster, recreate=recreate)
        owner_url = postgres_cluster["owner_url"]
        app_url = postgres_cluster["app_url"]

        # Step 3: Reconnect with platform_app and verify data is intact under proper context
        with psycopg.connect(app_url) as app, app.cursor() as cur:
            # Without context -> 0 rows
            cur.execute("SELECT count(*) FROM catalog.agents WHERE id = %s", (agent_id,))
            assert cur.fetchone()[0] == 0

            # With context -> exactly 1 row, display_name preserved
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
            cur.execute("SELECT display_name FROM catalog.agents WHERE id = %s", (agent_id,))
            res = cur.fetchone()
            assert res is not None
            assert res[0] == "Agent Persisted Across Restart"

        # Step 4: Verify 57 domain tables (56 baseline + iam.workspace_invitations ADR-019) remain intact
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema IN ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')
                  AND table_type = 'BASE TABLE'
                """
            )
            assert cur.fetchone()[0] == 57

    finally:
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute("DELETE FROM catalog.agents WHERE id = %s", (agent_id,))
            cur.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
            cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))


@pytest.mark.integration
def test_backup_and_restore_recovery(
    postgres_cluster: dict[str, Any],
    migrated_database: dict[str, Any],
) -> None:
    """Verifies full pg_dump backup and pg_restore recovery to a separate database (G1-G)."""
    owner_url = migrated_database["owner_url"]
    Path(__file__).resolve().parents[2]
    evidence_dir = postgres_cluster["evidence_dir"]
    evidence_dir.mkdir(parents=True, exist_ok=True)
    backup_file = evidence_dir / "database_backup.dump"

    tenant_id = uuid4()
    workspace_id = uuid4()
    agent_id = uuid4()

    # Seed test data in primary database
    with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
        cur.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (tenant_id, f"tr-{tenant_id.hex[:8]}", "Tenant Recovery"),
        )
        cur.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
            (tenant_id, workspace_id, f"wsr-{workspace_id.hex[:8]}", "Workspace Recovery"),
        )
        cur.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (tenant_id, workspace_id, agent_id, "agent-recover", "Agent For Recovery Test"),
        )

    try:
        # Step 1: Perform pg_dump backup
        with psycopg.connect(owner_url) as owner:
            expected_constraints = owner.execute(CONSTRAINTS_SQL).fetchall()
        assert expected_constraints
        t0 = time.perf_counter()
        backup_database(postgres_cluster, backup_file)
        backup_duration = time.perf_counter() - t0

        assert backup_file.exists()
        backup_size = backup_file.stat().st_size
        assert backup_size > 0

        t1 = time.perf_counter()
        # The context owns a fresh target; restore never overwrites an existing DB.
        with temporary_database(postgres_cluster, "restore") as restore_db:
            restore_database(postgres_cluster, backup_file, restore_db)
            restore_duration = time.perf_counter() - t1
            restore_owner_url = database_url(postgres_cluster, restore_db)
            restore_app_url = database_url(postgres_cluster, restore_db, app=True)

            # Step 3: Verify restored database as owner
            with psycopg.connect(restore_owner_url) as conn, conn.cursor() as cur:
                assert conn.execute(CONSTRAINTS_SQL).fetchall() == expected_constraints
                # 57 domain tables (56 baseline + iam.workspace_invitations ADR-019)
                cur.execute(
                    """
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema IN ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')
                      AND table_type = 'BASE TABLE'
                    """
                )
                assert cur.fetchone()[0] == 57

                # 1 technical migration history table with the reviewed canonical migration inventory
                cur.execute("SELECT count(*) FROM public.schema_migrations")
                assert cur.fetchone()[0] == len(EXPECTED_MIGRATIONS)

                # Verify RLS is enabled and forced on all 56 non-global domain tables (iam.users is global)
                cur.execute(
                    """
                    SELECT count(*)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')
                      AND c.relkind = 'r'
                      AND c.relrowsecurity = true
                      AND c.relforcerowsecurity = true
                    """
                )
                tables_with_forced_rls = cur.fetchone()[0]
                assert tables_with_forced_rls == 56

                # Verify iam.users is the single global domain table without RLS
                cur.execute(
                    """
                    SELECT count(*)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'iam' AND c.relname = 'users'
                      AND c.relrowsecurity = false
                    """
                )
                assert cur.fetchone()[0] == 1

            # Step 4: Verify restored database as platform_app with RLS enforcement
            with psycopg.connect(restore_app_url) as app, app.cursor() as cur:
                # Without context -> 0 rows
                cur.execute("SELECT count(*) FROM catalog.agents WHERE id = %s", (agent_id,))
                assert cur.fetchone()[0] == 0

                # With matching context -> 1 row, display_name verified
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
                cur.execute("SELECT display_name FROM catalog.agents WHERE id = %s", (agent_id,))
                res = cur.fetchone()
                assert res is not None
                assert res[0] == "Agent For Recovery Test"
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(uuid4()),))
                cur.execute("SELECT count(*) FROM catalog.agents WHERE id=%s", (agent_id,))
                assert cur.fetchone()[0] == 0
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(uuid4()),))
                cur.execute("SELECT count(*) FROM catalog.agents WHERE id=%s", (agent_id,))
                assert cur.fetchone()[0] == 0

        # Record metrics file in evidence directory
        metrics_file = evidence_dir / "backup_restore_metrics.txt"
        metrics_file.write_text(
            f"Backup duration: {backup_duration:.3f}s\n"
            f"Backup size: {backup_size} bytes\n"
            f"Restore duration: {restore_duration:.3f}s\n"
            f"Target database: {restore_db}\n"
            f"Tables verified: 57 domain tables + 1 technical migration history table\n"
            f"Forced RLS verified: 56 tables\n",
            encoding="utf-8",
        )

    finally:
        with psycopg.connect(owner_url) as owner, owner.cursor() as cur:
            cur.execute("DELETE FROM catalog.agents WHERE id = %s", (agent_id,))
            cur.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
            cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))


@pytest.mark.integration
def test_corrupt_backup_fails_closed(postgres_cluster, tmp_path):
    bad = tmp_path / "corrupt.dump"
    bad.write_bytes(b"not a PostgreSQL dump")
    with temporary_database(postgres_cluster, "badrestore") as target:
        with pytest.raises(RuntimeError, match="exit 1"):
            restore_database(postgres_cluster, bad, target)
        with psycopg.connect(database_url(postgres_cluster, target)) as conn:
            assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (
                None,
            )
