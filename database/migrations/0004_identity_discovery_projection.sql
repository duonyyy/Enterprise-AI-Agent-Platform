-- DB-56 identity discovery and workspace membership projection functions (ADR-018 / D3-A).
-- Author: Platform Engineering
-- Authority: ADR-018, D3-A (APPROVED_BY_USER)
-- Invariant: Strictly preserves 56 domain tables across 7 PostgreSQL schemas (DB-56).
-- Security: Narrow read-only SECURITY DEFINER projections with pinned search_path
--           (pg_catalog, iam, pg_temp), revoked PUBLIC execution, and explicit
--           EXECUTE grant to platform_app only.
BEGIN;

CREATE OR REPLACE FUNCTION iam.get_user_workspaces(p_user_id uuid)
RETURNS TABLE (
    tenant_id uuid,
    tenant_slug text,
    tenant_name text,
    workspace_id uuid,
    workspace_slug text,
    workspace_name text,
    role text,
    membership_status text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, iam, pg_temp
AS $$
    SELECT
        t.id AS tenant_id,
        t.slug AS tenant_slug,
        t.name AS tenant_name,
        w.id AS workspace_id,
        w.slug AS workspace_slug,
        w.name AS workspace_name,
        m.role AS role,
        m.status AS membership_status
    FROM iam.memberships AS m
    JOIN iam.workspaces AS w
      ON w.tenant_id = m.tenant_id
     AND w.id = m.workspace_id
    JOIN iam.tenants AS t
      ON t.id = m.tenant_id
    WHERE m.user_id = p_user_id
      AND m.status = 'active'
      AND w.status = 'active'
      AND t.status = 'active'
    ORDER BY t.name ASC, w.name ASC, w.id ASC;
$$;

CREATE OR REPLACE FUNCTION iam.resolve_user_workspace_membership(
    p_user_id uuid,
    p_workspace_id uuid
)
RETURNS TABLE (
    tenant_id uuid,
    role text,
    tenant_status text,
    workspace_status text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, iam, pg_temp
AS $$
    SELECT
        m.tenant_id AS tenant_id,
        m.role AS role,
        t.status AS tenant_status,
        w.status AS workspace_status
    FROM iam.memberships AS m
    JOIN iam.workspaces AS w
      ON w.tenant_id = m.tenant_id
     AND w.id = m.workspace_id
    JOIN iam.tenants AS t
      ON t.id = m.tenant_id
    WHERE m.user_id = p_user_id
      AND m.workspace_id = p_workspace_id
      AND m.status = 'active'
    LIMIT 1;
$$;

REVOKE ALL ON FUNCTION iam.get_user_workspaces(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION iam.get_user_workspaces(uuid) TO platform_app;

REVOKE ALL ON FUNCTION iam.resolve_user_workspace_membership(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION iam.resolve_user_workspace_membership(uuid, uuid) TO platform_app;

COMMIT;

