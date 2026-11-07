-- DB-56 security repair: Restore workspace-scoped RLS on iam.workspaces (DEC-DB-01).
-- Author: Platform Engineering
-- Authority: DEC-DB-01 (APPROVED_BY_USER)
-- Invariant: iam.workspaces uses 'id' as workspace identity (not 'workspace_id').
-- Requires both valid 'app.tenant_id' and 'app.workspace_id' transaction-local settings.
BEGIN;

DROP POLICY IF EXISTS tenant_isolation ON iam.workspaces;

CREATE POLICY tenant_isolation ON iam.workspaces
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    );

COMMIT;

