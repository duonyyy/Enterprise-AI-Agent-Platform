"""Integration tests for DB-56 immutability triggers and guards.

Authority:
- ADR-009 (Immutable Publishing & Content Hashes): Published artifacts, versions, and audit
  ledgers are append-only; updates and deletions must fail closed.
- Gate G1-E: Immutability triggers on audit events, published versions, and document version guards.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest


@pytest.mark.integration
def test_immutable_audit_events_rejects_mutation(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that governance.audit_events cannot be updated or deleted."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    tenant_id = uuid4()
    workspace_id = uuid4()
    event_id = uuid4()

    with psycopg.connect(owner_url) as owner:
        with owner.cursor() as cur:
            cur.execute(
                "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)",
                (tenant_id, f"t-{tenant_id.hex[:8]}", "Tenant Audit"),
            )
            cur.execute(
                "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)",
                (tenant_id, workspace_id, f"ws-{workspace_id.hex[:8]}", "Workspace Audit"),
            )
            cur.execute(
                """
                INSERT INTO governance.audit_events (
                    tenant_id, workspace_id, id, actor_subject, action,
                    resource_type, resource_id, outcome
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id, workspace_id, event_id, "user:admin", "publish",
                    "catalog.agent", "ag-001", "succeeded",
                ),
            )

    try:
        # Test under application role
        with psycopg.connect(app_url) as app:
            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                # Attempting UPDATE on audit event must fail closed via trigger
                with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                    cur.execute(
                        "UPDATE governance.audit_events SET outcome = 'failed' WHERE id = %s",
                        (event_id,),
                    )
                assert "Immutable table governance.audit_events cannot be changed via UPDATE" in str(exc_info.value)
            app.rollback()

            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                # Attempting DELETE on audit event must fail closed via trigger
                with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                    cur.execute(
                        "DELETE FROM governance.audit_events WHERE id = %s",
                        (event_id,),
                    )
                assert "Immutable table governance.audit_events cannot be changed via DELETE" in str(exc_info.value)
            app.rollback()

    finally:
        with psycopg.connect(owner_url) as owner:
            with owner.cursor() as cur:
                # Cleanup: disable trigger temporarily on fixture cluster to clean up
                cur.execute("ALTER TABLE governance.audit_events DISABLE TRIGGER immutable_row")
                cur.execute("DELETE FROM governance.audit_events WHERE id = %s", (event_id,))
                cur.execute("ALTER TABLE governance.audit_events ENABLE TRIGGER immutable_row")
                cur.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
                cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))


@pytest.mark.integration
def test_immutable_agent_versions_rejects_mutation(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that catalog.agent_versions cannot be updated or deleted."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    tenant_id = uuid4()
    workspace_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    sha256 = "a" * 64

    with psycopg.connect(owner_url) as owner:
        with owner.cursor() as cur:
            cur.execute("INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)", (tenant_id, f"t-{tenant_id.hex[:8]}", "Tenant AG"))
            cur.execute("INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)", (tenant_id, workspace_id, f"ws-{workspace_id.hex[:8]}", "WS AG"))
            cur.execute("INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, %s, %s)", (tenant_id, workspace_id, agent_id, "ag-v", "Agent V"))
            cur.execute(
                """
                INSERT INTO catalog.agent_versions (
                    tenant_id, workspace_id, id, agent_id, version,
                    schema_version, frozen_spec, bundle_sha256
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tenant_id, workspace_id, version_id, agent_id, 1, "1.0", '{"name":"agent"}' , sha256),
            )

    try:
        with psycopg.connect(app_url) as app:
            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                    cur.execute(
                        "UPDATE catalog.agent_versions SET schema_version = '2.0' WHERE id = %s",
                        (version_id,),
                    )
                assert "Immutable table catalog.agent_versions cannot be changed via UPDATE" in str(exc_info.value)
            app.rollback()

            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                    cur.execute(
                        "DELETE FROM catalog.agent_versions WHERE id = %s",
                        (version_id,),
                    )
                assert "Immutable table catalog.agent_versions cannot be changed via DELETE" in str(exc_info.value)
            app.rollback()

    finally:
        with psycopg.connect(owner_url) as owner:
            with owner.cursor() as cur:
                cur.execute("ALTER TABLE catalog.agent_versions DISABLE TRIGGER immutable_row")
                cur.execute("DELETE FROM catalog.agent_versions WHERE id = %s", (version_id,))
                cur.execute("ALTER TABLE catalog.agent_versions ENABLE TRIGGER immutable_row")
                cur.execute("DELETE FROM catalog.agents WHERE id = %s", (agent_id,))
                cur.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
                cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))


@pytest.mark.integration
def test_guard_document_version_identity_and_reversion(
    migrated_database: dict[str, Any],
) -> None:
    """Verifies that knowledge.document_versions preserves identity and prevents status reversion."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    tenant_id = uuid4()
    workspace_id = uuid4()
    base_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    sha256 = "b" * 64

    with psycopg.connect(owner_url) as owner:
        with owner.cursor() as cur:
            cur.execute("INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, %s)", (tenant_id, f"t-{tenant_id.hex[:8]}", "Tenant Doc"))
            cur.execute("INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, %s)", (tenant_id, workspace_id, f"ws-{workspace_id.hex[:8]}", "WS Doc"))
            cur.execute("INSERT INTO knowledge.bases (tenant_id, workspace_id, id, slug, name) VALUES (%s, %s, %s, %s, %s)", (tenant_id, workspace_id, base_id, "kb-1", "Base 1"))
            cur.execute("INSERT INTO knowledge.documents (tenant_id, workspace_id, id, base_id, title) VALUES (%s, %s, %s, %s, %s)", (tenant_id, workspace_id, doc_id, base_id, "Doc 1"))
            cur.execute(
                """
                INSERT INTO knowledge.document_versions (
                    tenant_id, workspace_id, id, document_id, version,
                    object_key, checksum_sha256, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft')
                """,
                (tenant_id, workspace_id, ver_id, doc_id, 1, "docs/v1.pdf", sha256),
            )

    try:
        with psycopg.connect(app_url) as app:
            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                # 1. Attempting to change content hash fails closed via guard trigger
                with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                    cur.execute(
                        "UPDATE knowledge.document_versions SET checksum_sha256 = %s WHERE id = %s",
                        ("c" * 64, ver_id),
                    )
                assert "Document version identity/content cannot be changed" in str(exc_info.value)
            app.rollback()

            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                # 2. Transition from draft to published (with published_at) succeeds
                cur.execute(
                    "UPDATE knowledge.document_versions SET status = 'published', published_at = now() WHERE id = %s",
                    (ver_id,),
                )
                assert cur.rowcount == 1
            app.commit()

            with app.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
                cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id),))

                # 3. Attempting to revert from published back to draft must fail
                with pytest.raises(psycopg.errors.RaiseException) as exc_info:
                    cur.execute(
                        "UPDATE knowledge.document_versions SET status = 'draft' WHERE id = %s",
                        (ver_id,),
                    )
                assert "Published/superseded document version cannot revert" in str(exc_info.value)
            app.rollback()

    finally:
        with psycopg.connect(owner_url) as owner:
            with owner.cursor() as cur:
                cur.execute("DELETE FROM knowledge.document_versions WHERE id = %s", (ver_id,))
                cur.execute("DELETE FROM knowledge.documents WHERE id = %s", (doc_id,))
                cur.execute("DELETE FROM knowledge.bases WHERE id = %s", (base_id,))
                cur.execute("DELETE FROM iam.workspaces WHERE id = %s", (workspace_id,))
                cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))

