-- DB-56 security hardening. No domain tables are added by this migration.
-- Every tenant/workspace table requires both transaction-local settings; absent
-- context evaluates to NULL and denies the row by PostgreSQL RLS semantics.
BEGIN;

DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT DISTINCT schemaname, tablename
        FROM pg_policies
        WHERE policyname = 'tenant_isolation'
          AND schemaname IN ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I.%I', target.schemaname, target.tablename);
    END LOOP;

    FOR target IN
        SELECT c.table_schema, c.table_name
        FROM information_schema.columns AS c
        JOIN information_schema.columns AS workspace_column
          ON workspace_column.table_schema = c.table_schema
         AND workspace_column.table_name = c.table_name
         AND workspace_column.column_name = 'workspace_id'
        WHERE c.column_name = 'tenant_id'
          AND c.table_schema IN ('iam', 'catalog', 'knowledge', 'runtime', 'integration', 'governance', 'ops')
    LOOP
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I.%I USING '
            || '(tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid '
            || 'AND workspace_id = NULLIF(current_setting(''app.workspace_id'', true), '''')::uuid) '
            || 'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid '
            || 'AND workspace_id = NULLIF(current_setting(''app.workspace_id'', true), '''')::uuid)',
            target.table_schema,
            target.table_name
        );
    END LOOP;

    CREATE POLICY tenant_isolation ON iam.tenants
        USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
END $$;

COMMIT;
