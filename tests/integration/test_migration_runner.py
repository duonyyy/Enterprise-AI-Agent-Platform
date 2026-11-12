"""Integration tests for DB-56 migration runner and technical history table.

Authority:
- DEC-DB-02 (APPROVED_BY_USER): Single technical migration history table (public.schema_migrations).
- Gate G1-B: Fresh migration bootstrap, 56 domain tables across 7 schemas, platform_app permissions.
- Gate G1-C: Upgrade from old version, idempotency, checksum drift, rollback on error, advisory lock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from run_migrations import (
    MigrationChecksumDriftError,
    MigrationExecutionError,
    MigrationHistoryError,
    MigrationLockError,
    ensure_history_table,
    run_migrations,
    write_bootstrap_sql,
)
from test_postgres_fixture import docker


@pytest.mark.integration
def test_fresh_migration_bootstrap_and_technical_table(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that 56 domain tables and 1 technical migration history table exist."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    # 1. Verify public.schema_migrations structure and contents
    with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT version, name, checksum, applied_by FROM public.schema_migrations ORDER BY version"
        )
        rows = cur.fetchall()
        assert len(rows) == 5
        assert rows[0][0] == "0001"
        assert rows[0][1] == "0001_baseline.sql"
        assert rows[1][0] == "0002"
        assert rows[1][1] == "0002_fail_closed_rls.sql"
        assert rows[2][0] == "0003"
        assert rows[2][1] == "0003_restore_workspaces_rls.sql"
        assert rows[3][0] == "0004"
        assert rows[3][1] == "0004_identity_discovery_projection.sql"
        assert rows[4][0] == "0005"
        assert rows[4][1] == "0005_workspace_invitations.sql"

        # 2. Verify exactly 57 domain tables (56 baseline + iam.workspace_invitations delta) across 7 schemas
        cur.execute(
            """
            SELECT table_schema, count(*)
            FROM information_schema.tables
            WHERE table_schema IN ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')
              AND table_type = 'BASE TABLE'
            GROUP BY table_schema
            ORDER BY table_schema
            """
        )
        schema_counts = dict(cur.fetchall())
        assert sum(schema_counts.values()) == 57
        assert schema_counts == {
            "iam": 6,
            "catalog": 16,
            "knowledge": 7,
            "runtime": 12,
            "integration": 6,
            "governance": 4,
            "ops": 6,
        }

        # 3. Verify platform_app role properties (NOSUPERUSER, NOBYPASSRLS)
        cur.execute(
            "SELECT rolsuper, rolbypassrls, rolcreatedb FROM pg_roles WHERE rolname = 'platform_app'"
        )
        role_props = cur.fetchone()
        assert role_props is not None
        assert role_props[0] is False  # rolsuper
        assert role_props[1] is False  # rolbypassrls
        assert role_props[2] is False  # rolcreatedb

    # 4. Verify platform_app permissions boundary: CANNOT modify technical migration table
    with psycopg.connect(app_url) as conn:
        with conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM public.schema_migrations")
        conn.rollback()

        with conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO public.schema_migrations (version, name, checksum, applied_by, execution_time_ms) VALUES ('9999', 'hack.sql', 'fake', 'hacker', 0)"
            )
        conn.rollback()

        # 5. Verify platform_app CANNOT run DDL
        with conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE iam.rogue_table (id uuid PRIMARY KEY)")
        conn.rollback()


@pytest.mark.integration
def test_migration_upgrade_preserves_data(isolated_database, tmp_path):
    db_url = isolated_database["owner_url"]
    migrations_dir = Path(__file__).resolve().parents[2] / "database/migrations"
    for name in ("0001_baseline.sql", "0002_fail_closed_rls.sql"):
        (tmp_path / name).write_bytes((migrations_dir / name).read_bytes())
    assert run_migrations(db_url, tmp_path) == ["0001", "0002"]
    tenant, workspace = uuid4(), uuid4()
    with psycopg.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO iam.tenants (id,slug,name) VALUES (%s,%s,%s)",
            (tenant, "upgrade-tenant", "Upgrade Tenant"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id,id,slug,name) VALUES (%s,%s,%s,%s)",
            (tenant, workspace, "upgrade-ws", "Upgrade Workspace"),
        )
    assert run_migrations(db_url) == ["0003", "0004", "0005"]
    with psycopg.connect(db_url) as conn:
        assert conn.execute(
            "SELECT name FROM iam.workspaces WHERE id=%s", (workspace,)
        ).fetchone() == ("Upgrade Workspace",)
        assert conn.execute("SELECT name FROM iam.tenants WHERE id=%s", (tenant,)).fetchone() == (
            "Upgrade Tenant",
        )
    assert run_migrations(db_url) == []


@pytest.mark.integration
def test_checksum_drift_fails_closed(isolated_database):
    db_url = isolated_database["owner_url"]
    run_migrations(db_url)
    with psycopg.connect(db_url) as conn:
        conn.execute(
            "UPDATE public.schema_migrations SET checksum='corrupted_hash' WHERE version='0002'"
        )
    with pytest.raises(MigrationChecksumDriftError, match="checksum drift"):
        run_migrations(db_url)


@pytest.mark.integration
def test_migration_failure_rolls_back_atomically(isolated_database, tmp_path):
    db_url = isolated_database["owner_url"]
    (tmp_path / "0001_valid.sql").write_text("CREATE TABLE test_tab (id int);")
    (tmp_path / "0002_broken.sql").write_text(
        "BEGIN; CREATE TABLE before_error (id int); INVALID SYNTAX; COMMIT;"
    )
    with pytest.raises(MigrationExecutionError):
        run_migrations(db_url, tmp_path)
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT version FROM public.schema_migrations").fetchall() == [
            ("0001",)
        ]
        assert conn.execute("SELECT to_regclass('public.before_error')").fetchone() == (None,)


@pytest.mark.integration
def test_history_failure_rolls_back_explicit_transaction_and_can_retry(isolated_database, tmp_path):
    db_url = isolated_database["owner_url"]
    with psycopg.connect(db_url, autocommit=True) as conn:
        ensure_history_table(conn)
        conn.execute(
            "ALTER TABLE public.schema_migrations ADD CONSTRAINT reject_probe CHECK(version <> '0001')"
        )
    (tmp_path / "0001_probe.sql").write_text(
        "BEGIN; CREATE TABLE atomic_probe (id int); COMMIT; GRANT SELECT ON atomic_probe TO platform_app;"
    )
    with pytest.raises(MigrationExecutionError, match="reject_probe"):
        run_migrations(db_url, tmp_path)
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT to_regclass('public.atomic_probe')").fetchone() == (None,)
        assert conn.execute("SELECT count(*) FROM public.schema_migrations").fetchone() == (0,)
        conn.execute("ALTER TABLE public.schema_migrations DROP CONSTRAINT reject_probe")
    assert run_migrations(db_url, tmp_path) == ["0001"]
    assert run_migrations(db_url, tmp_path) == []


@pytest.mark.integration
def test_migration_advisory_locking(isolated_database):
    db_url = isolated_database["owner_url"]
    with psycopg.connect(db_url) as conn:
        assert conn.execute(
            "SELECT pg_try_advisory_lock(hashtext('enterprise_platform_migration_lock'))"
        ).fetchone() == (True,)
        with pytest.raises(MigrationLockError):
            run_migrations(db_url)
    assert run_migrations(db_url) == ["0001", "0002", "0003", "0004", "0005"]


@pytest.mark.integration
def test_legacy_database_without_history_is_not_replayed(isolated_database):
    db_url = isolated_database["owner_url"]
    migrations = Path(__file__).resolve().parents[2] / "database/migrations"
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute((migrations / "0001_baseline.sql").read_text(encoding="utf-8-sig"))
        conn.execute((migrations / "0002_fail_closed_rls.sql").read_text(encoding="utf-8-sig"))
    with pytest.raises(MigrationHistoryError, match="reviewed adoption"):
        run_migrations(db_url)
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='iam'"
        ).fetchone() == (5,)


@pytest.mark.integration
def test_check_and_dry_run_do_not_create_history(isolated_database):
    db_url = isolated_database["owner_url"]
    assert run_migrations(db_url, dry_run=True) == ["0001", "0002", "0003", "0004", "0005"]
    from run_migrations import MigrationError

    with pytest.raises(MigrationError, match="pending"):
        run_migrations(db_url, check_only=True)
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (None,)


@pytest.mark.integration
def test_generated_docker_bootstrap_has_matching_history(isolated_database, tmp_path):
    path = tmp_path / "bootstrap.sql"
    write_bootstrap_sql(Path(__file__).resolve().parents[2] / "database/migrations", path)
    if isolated_database.get("local_mode"):
        import os
        import subprocess

        bin_dir = isolated_database["bin_dir"]
        psql_bin = str(bin_dir / ("psql.exe" if (bin_dir / "psql.exe").exists() else "psql"))
        env = {**os.environ, "PGPASSWORD": str(isolated_database.get("owner_password", ""))}
        subprocess.run(
            [
                psql_bin,
                "-h",
                str(isolated_database.get("host", "127.0.0.1")),
                "-p",
                str(isolated_database["port"]),
                "-U",
                "platform_owner",
                "-d",
                isolated_database["primary_db"],
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                str(path.resolve()),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    else:
        container = isolated_database["container_id"]
        docker("cp", str(path.resolve()), f"{container}:/tmp/bootstrap.sql")
        docker(
            "exec",
            container,
            "psql",
            "-U",
            "platform_owner",
            "-d",
            isolated_database["primary_db"],
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/tmp/bootstrap.sql",
        )
    assert run_migrations(isolated_database["owner_url"]) == []


@pytest.mark.integration
def test_sql_smoke_runs_as_restricted_app_role(migrated_database):
    smoke = Path(__file__).resolve().parents[2] / "database/tests/0002_smoke.sql"
    with psycopg.connect(migrated_database["app_url"], autocommit=True) as conn:
        conn.execute(smoke.read_text(encoding="utf-8-sig"))
