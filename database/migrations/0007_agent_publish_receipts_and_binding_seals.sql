-- Forward Migration 0007: Agent Publish Receipts, Immutable Version Manifest, and Binding Seals
-- Authority: ADR-022, SPEC-AGENT-001, AG-IMPL-001 Section 5.
-- Adds catalog.agent_publish_receipts (Proposed 57 -> 58 canonical domain tables).
-- Adds bundle_manifest and bindings_sealed_at to catalog.agent_versions.
-- Enforces composite integrity on skill versions and atomic binding sealing invariants.

BEGIN;

-- 1. Extend catalog.agent_versions with durable manifest envelope and sealing timestamp
ALTER TABLE catalog.agent_versions
    ADD COLUMN bundle_manifest jsonb NOT NULL CHECK (jsonb_typeof(bundle_manifest) = 'object'),
    ADD COLUMN bindings_sealed_at timestamptz;

-- 2. Composite skill-pair integrity constraints
ALTER TABLE catalog.skill_versions
    ADD CONSTRAINT uq_skill_versions_scoped_pair
    UNIQUE (tenant_id, workspace_id, skill_id, id);

ALTER TABLE catalog.agent_skill_bindings
    ADD CONSTRAINT fk_agent_skill_bindings_scoped_pair
    FOREIGN KEY (tenant_id, workspace_id, skill_id, skill_version_id)
    REFERENCES catalog.skill_versions(tenant_id, workspace_id, skill_id, id);

-- 3. Create durable publish receipts table (Table 58)
CREATE TABLE catalog.agent_publish_receipts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL,
    operation_key text NOT NULL CHECK (
        char_length(btrim(operation_key)) > 0
        AND char_length(operation_key) <= 256
        AND operation_key !~ '[\r\n]'
    ),
    request_sha256 char(64) NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
    actor_user_id uuid NOT NULL REFERENCES iam.users(id),
    agent_version_id uuid NOT NULL,
    response_status integer NOT NULL DEFAULT 200 CHECK (response_status >= 200 AND response_status < 300),
    response_body jsonb NOT NULL CHECK (jsonb_typeof(response_body) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, agent_id, operation_key),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id),
    FOREIGN KEY (tenant_id, workspace_id, agent_id) REFERENCES catalog.agents(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, agent_id, agent_version_id)
        REFERENCES catalog.agent_versions(tenant_id, workspace_id, agent_id, id)
);

CREATE INDEX ix_agent_publish_receipts_lookup
    ON catalog.agent_publish_receipts (tenant_id, workspace_id, agent_id, operation_key);

CREATE INDEX ix_agent_publish_receipts_version
    ON catalog.agent_publish_receipts (tenant_id, workspace_id, agent_version_id);

-- 4. Row Level Security for catalog.agent_publish_receipts
ALTER TABLE catalog.agent_publish_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.agent_publish_receipts FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.agent_publish_receipts
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    );

-- 5. Trigger functions for sealing lifecycle and immutability protection

-- 5a. Initial version creation must be unsealed
CREATE OR REPLACE FUNCTION catalog.guard_agent_version_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.bindings_sealed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Agent version must be created in unsealed state; finalize bindings before commit';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER guard_agent_version_insert
    BEFORE INSERT ON catalog.agent_versions
    FOR EACH ROW EXECUTE FUNCTION catalog.guard_agent_version_insert();

-- 5b. Guard version mutation: forbid DELETE, allow single sealing transition on bindings_sealed_at
CREATE OR REPLACE FUNCTION catalog.guard_agent_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Immutable table %.% cannot be deleted', TG_TABLE_SCHEMA, TG_TABLE_NAME;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.bindings_sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Agent version % is already sealed and cannot be modified', OLD.id;
        END IF;

        IF NEW.bindings_sealed_at IS NULL THEN
            RAISE EXCEPTION 'Cannot update agent version % without setting bindings_sealed_at', OLD.id;
        END IF;

        IF (OLD.tenant_id, OLD.workspace_id, OLD.id, OLD.agent_id, OLD.version,
            OLD.schema_version, OLD.frozen_spec, OLD.bundle_sha256, OLD.bundle_manifest,
            OLD.workflow_version_id, OLD.model_config_id, OLD.published_by, OLD.created_at)
           IS DISTINCT FROM
           (NEW.tenant_id, NEW.workspace_id, NEW.id, NEW.agent_id, NEW.version,
            NEW.schema_version, NEW.frozen_spec, NEW.bundle_sha256, NEW.bundle_manifest,
            NEW.workflow_version_id, NEW.model_config_id, NEW.published_by, NEW.created_at)
        THEN
            RAISE EXCEPTION 'Agent version % content is immutable; only bindings_sealed_at can be finalized', OLD.id;
        END IF;

        RETURN NEW;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS immutable_row ON catalog.agent_versions;
CREATE TRIGGER guard_agent_version_mutation
    BEFORE UPDATE OR DELETE ON catalog.agent_versions
    FOR EACH ROW EXECUTE FUNCTION catalog.guard_agent_version_mutation();

-- 5c. Deferred constraint trigger to guarantee agent version is sealed at COMMIT
CREATE OR REPLACE FUNCTION catalog.check_agent_version_sealed()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_sealed_at timestamptz;
BEGIN
    SELECT bindings_sealed_at INTO v_sealed_at
    FROM catalog.agent_versions
    WHERE tenant_id = NEW.tenant_id
      AND workspace_id = NEW.workspace_id
      AND id = NEW.id;

    IF NOT FOUND THEN
        -- Row was deleted in the same transaction
        RETURN NEW;
    END IF;

    IF v_sealed_at IS NULL THEN
        RAISE EXCEPTION 'Agent version % must be sealed before transaction commit', NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER check_agent_version_sealed_trigger
    AFTER INSERT OR UPDATE ON catalog.agent_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION catalog.check_agent_version_sealed();

-- 5d. Binding insert guard: cannot insert bindings if parent version is already sealed
CREATE OR REPLACE FUNCTION catalog.guard_agent_binding_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_sealed_at timestamptz;
BEGIN
    SELECT bindings_sealed_at INTO v_sealed_at
    FROM catalog.agent_versions
    WHERE tenant_id = NEW.tenant_id
      AND workspace_id = NEW.workspace_id
      AND id = NEW.agent_version_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Parent agent version % not found in workspace', NEW.agent_version_id;
    END IF;

    IF v_sealed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Cannot add bindings to sealed agent version %', NEW.agent_version_id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER guard_binding_insert
    BEFORE INSERT ON catalog.agent_tool_bindings
    FOR EACH ROW EXECUTE FUNCTION catalog.guard_agent_binding_insert();

CREATE TRIGGER guard_binding_insert
    BEFORE INSERT ON catalog.agent_knowledge_bindings
    FOR EACH ROW EXECUTE FUNCTION catalog.guard_agent_binding_insert();

CREATE TRIGGER guard_binding_insert
    BEFORE INSERT ON catalog.agent_skill_bindings
    FOR EACH ROW EXECUTE FUNCTION catalog.guard_agent_binding_insert();

-- 5e. Immutable triggers on tool and knowledge bindings
CREATE TRIGGER immutable_row
    BEFORE UPDATE OR DELETE ON catalog.agent_tool_bindings
    FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row
    BEFORE UPDATE OR DELETE ON catalog.agent_knowledge_bindings
    FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

-- 6. Strict application role permissions
REVOKE ALL ON TABLE catalog.agent_publish_receipts FROM PUBLIC;
REVOKE ALL ON TABLE catalog.agent_publish_receipts FROM platform_app;
GRANT SELECT, INSERT ON TABLE catalog.agent_publish_receipts TO platform_app;

REVOKE DELETE, TRUNCATE ON TABLE catalog.agent_versions FROM platform_app;
GRANT SELECT, INSERT, UPDATE ON TABLE catalog.agent_versions TO platform_app;

REVOKE UPDATE, DELETE, TRUNCATE ON TABLE catalog.agent_tool_bindings FROM platform_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE catalog.agent_knowledge_bindings FROM platform_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE catalog.agent_skill_bindings FROM platform_app;

COMMIT;
