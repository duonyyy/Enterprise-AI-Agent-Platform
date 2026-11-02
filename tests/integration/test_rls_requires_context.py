"""PostgreSQL integration proof for fail-closed tenant/workspace RLS.

The test intentionally requires real database URLs. It skips locally when no
ephemeral PostgreSQL instance was supplied; CI provisions one before execution.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.mark.integration
def test_application_role_requires_both_tenant_and_workspace_context() -> None:
    psycopg = pytest.importorskip("psycopg")
    owner_url = os.getenv("MIGRATION_DATABASE_URL")
    app_url = os.getenv("RLS_TEST_DATABASE_URL")
    if not owner_url or not app_url:
        pytest.skip("real PostgreSQL URLs are required for RLS integration coverage")

    tenant_id = uuid4()
    workspace_id = uuid4()
    wrong_workspace_id = uuid4()
    tenant_slug = f"tenant-{tenant_id.hex[:12]}"
    workspace_slug = f"ws-{workspace_id.hex[:12]}"

    with psycopg.connect(owner_url) as owner:
        with owner.cursor() as cursor:
            cursor.execute(
                "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
                (tenant_id, tenant_slug, "RLS integration tenant"),
            )
            cursor.execute(
                "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
                (tenant_id, workspace_id, workspace_slug, "RLS integration workspace"),
            )

    try:
        with psycopg.connect(app_url) as app:
            with app.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
                assert cursor.fetchone()[0] == 0

                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cursor.execute("SELECT set_config('app.workspace_id', %s, true)", (str(wrong_workspace_id),))
                cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
                assert cursor.fetchone()[0] == 0

                cursor.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))
                cursor.execute("SELECT count(*) FROM iam.workspaces WHERE id = %s", (workspace_id,))
                assert cursor.fetchone()[0] == 1
    finally:
        with psycopg.connect(owner_url) as owner:
            with owner.cursor() as cursor:
                cursor.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
                cursor.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))
