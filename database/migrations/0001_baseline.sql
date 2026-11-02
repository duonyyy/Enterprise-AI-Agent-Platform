
-- ``platform_app`` is provisioned outside this schema migration by the deployment
-- secret mechanism. Schema migrations must never contain a database password.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'platform_app') THEN
        RAISE EXCEPTION 'platform_app role must be provisioned before the DB-56 migration';
    END IF;
END $$;

-- Enterprise AI Agent Platform | Baseline DDL Unified v2.0 (56 Tables) | PostgreSQL >= 16
-- Apply as one first-install migration with a migration owner, NOT an application role.
-- Set an authenticated, trusted tenant context per DB transaction for tenant-scoped operations:
--   BEGIN; SELECT set_config('app.tenant_id', '<uuid>', true); ... COMMIT;
-- Do not treat custom GUC alone as protection against arbitrary SQL injection.
-- IMPORTANT: This DDL has NOT been executed against a live PostgreSQL instance.

BEGIN;

SET LOCAL lock_timeout = '10s';

CREATE SCHEMA IF NOT EXISTS iam;

CREATE SCHEMA IF NOT EXISTS catalog;

CREATE SCHEMA IF NOT EXISTS knowledge;

CREATE SCHEMA IF NOT EXISTS runtime;

CREATE SCHEMA IF NOT EXISTS integration;

CREATE SCHEMA IF NOT EXISTS governance;

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE iam.tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{2,62}$'),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closing')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE iam.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer text NOT NULL,
    subject text NOT NULL,
    display_name text,
    email_normalized text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE iam.workspaces (
    tenant_id uuid NOT NULL REFERENCES iam.tenants(id),
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, slug)
);

CREATE TABLE iam.memberships (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES iam.users(id),
    role text NOT NULL CHECK (role IN ('owner','admin','developer','operator','viewer')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','invited','revoked')),
    joined_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, user_id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE iam.service_accounts (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    auth_subject text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, auth_subject), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.model_configs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    provider text NOT NULL,
    provider_model_id text NOT NULL,
    secret_ref text,
    settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, name), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.business_processes (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    process_key text NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    goal text NOT NULL DEFAULT '',
    guidance jsonb NOT NULL DEFAULT '{}'::jsonb,
    constraints_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    examples jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, process_key), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.workflows (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL,
    name text NOT NULL,
    draft_spec jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(draft_spec) = 'object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, slug), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.workflow_versions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    schema_version text NOT NULL,
    spec jsonb NOT NULL CHECK (jsonb_typeof(spec) = 'object'),
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    published_by uuid REFERENCES iam.users(id),
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, workflow_id, version),
    FOREIGN KEY (tenant_id,workspace_id,workflow_id) REFERENCES catalog.workflows(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.tools (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_key text NOT NULL,
    display_name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('http','mcp','builtin')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,tool_key), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.tool_versions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    input_schema jsonb NOT NULL,
    output_schema jsonb NOT NULL,
    execution_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    risk text NOT NULL CHECK (risk IN ('read','write','high_impact')),
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,tool_id,version),
    FOREIGN KEY (tenant_id,workspace_id,tool_id) REFERENCES catalog.tools(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.agents (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL,
    display_name text NOT NULL,
    draft_spec jsonb NOT NULL DEFAULT '{}'::jsonb,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,slug), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.agent_versions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    schema_version text NOT NULL,
    frozen_spec jsonb NOT NULL CHECK (jsonb_typeof(frozen_spec) = 'object'),
    bundle_sha256 char(64) NOT NULL CHECK (bundle_sha256 ~ '^[a-f0-9]{64}$'),
    workflow_version_id uuid,
    model_config_id uuid,
    published_by uuid REFERENCES iam.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,agent_id,version),
    UNIQUE (tenant_id,workspace_id,agent_id,id),
    FOREIGN KEY (tenant_id,workspace_id,agent_id) REFERENCES catalog.agents(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,workflow_version_id) REFERENCES catalog.workflow_versions(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,model_config_id) REFERENCES catalog.model_configs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.agent_publications (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL,
    agent_version_id uuid NOT NULL,
    environment text NOT NULL CHECK (environment IN ('development','staging','production')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,agent_id,environment),
    FOREIGN KEY (tenant_id,workspace_id,agent_id) REFERENCES catalog.agents(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,agent_id,agent_version_id)
        REFERENCES catalog.agent_versions(tenant_id,workspace_id,agent_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.tool_grants (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id uuid NOT NULL,
    principal_kind text NOT NULL CHECK (principal_kind IN ('role','service_account')),
    principal_key text NOT NULL,
    allowed_operation text NOT NULL CHECK (allowed_operation IN ('invoke','manage')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,tool_id,principal_kind,principal_key,allowed_operation),
    FOREIGN KEY (tenant_id,workspace_id,tool_id) REFERENCES catalog.tools(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.digital_workers (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    agent_id uuid NOT NULL,
    default_agent_version_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,name),
    FOREIGN KEY (tenant_id,workspace_id,agent_id) REFERENCES catalog.agents(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,agent_id,default_agent_version_id)
        REFERENCES catalog.agent_versions(tenant_id,workspace_id,agent_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.agent_tool_bindings (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_version_id uuid NOT NULL,
    tool_version_id uuid NOT NULL,
    usage_mode text NOT NULL DEFAULT 'allowed' CHECK (usage_mode IN ('allowed','requires_approval')),
    UNIQUE (tenant_id,workspace_id,agent_version_id,tool_version_id),
    FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,tool_version_id) REFERENCES catalog.tool_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.bases (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL,
    name text NOT NULL,
    visibility text NOT NULL DEFAULT 'restricted' CHECK (visibility IN ('restricted','workspace')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,slug), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.base_grants (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    base_id uuid NOT NULL,
    principal_kind text NOT NULL CHECK (principal_kind IN ('role','user','service_account')),
    principal_key text NOT NULL,
    permission text NOT NULL CHECK (permission IN ('read','write','manage')),
    UNIQUE (tenant_id,workspace_id,base_id,principal_kind,principal_key,permission),
    FOREIGN KEY (tenant_id,workspace_id,base_id) REFERENCES knowledge.bases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.documents (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    base_id uuid NOT NULL,
    title text NOT NULL,
    source_uri text,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,workspace_id,base_id) REFERENCES knowledge.bases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.document_versions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    object_key text NOT NULL,
    checksum_sha256 char(64) NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ingesting','ready','published','superseded','failed')),
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,document_id,version),
    CHECK ((status = 'published' AND published_at IS NOT NULL) OR status <> 'published'),
    FOREIGN KEY (tenant_id,workspace_id,document_id) REFERENCES knowledge.documents(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.chunks (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    token_count integer CHECK (token_count >= 0),
    UNIQUE (tenant_id,workspace_id,document_version_id,ordinal),
    FOREIGN KEY (tenant_id,workspace_id,document_version_id) REFERENCES knowledge.document_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.vector_refs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id uuid NOT NULL,
    collection_name text NOT NULL,
    point_id uuid NOT NULL,
    embedding_model text NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','indexed','delete_pending','deleted','failed')),
    indexed_at timestamptz,
    UNIQUE (tenant_id,workspace_id,chunk_id,embedding_model),
    UNIQUE (collection_name,point_id),
    FOREIGN KEY (tenant_id,workspace_id,chunk_id) REFERENCES knowledge.chunks(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE knowledge.ingestion_jobs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_version_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    FOREIGN KEY (tenant_id,workspace_id,document_version_id) REFERENCES knowledge.document_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.agent_knowledge_bindings (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_version_id uuid NOT NULL,
    base_id uuid NOT NULL,
    retrieval_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id,workspace_id,agent_version_id,base_id),
    FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,base_id) REFERENCES knowledge.bases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.skills (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    display_name text NOT NULL,
    draft_skill_md text NOT NULL DEFAULT '',
    draft_manifest jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(draft_manifest) = 'object'),
    draft_bundle_ref text,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'archived')),
    created_by uuid REFERENCES iam.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, slug),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.skill_versions (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id uuid NOT NULL,
    version integer NOT NULL CHECK (version >= 1),
    schema_version text NOT NULL DEFAULT 'agentskills/v1',
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    skill_md text NOT NULL,
    bundle_ref text NOT NULL,
    bundle_sha256 char(64) NOT NULL CHECK (bundle_sha256 ~ '^[a-f0-9]{64}$'),
    published_by uuid REFERENCES iam.users(id),
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, skill_id, version),
    FOREIGN KEY (tenant_id, workspace_id, skill_id) REFERENCES catalog.skills(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE catalog.agent_skill_bindings (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_version_id uuid NOT NULL,
    skill_id uuid NOT NULL,
    skill_version_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, agent_version_id, skill_id),
    FOREIGN KEY (tenant_id, workspace_id, agent_version_id) REFERENCES catalog.agent_versions(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, skill_id) REFERENCES catalog.skills(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, skill_version_id) REFERENCES catalog.skill_versions(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.conversations (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_subject text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.messages (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    body_ref text,
    body_ciphertext bytea,
    token_count integer CHECK (token_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((body_ref IS NOT NULL) <> (body_ciphertext IS NOT NULL)),
    FOREIGN KEY (tenant_id,workspace_id,conversation_id) REFERENCES runtime.conversations(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.runs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_version_id uuid NOT NULL,
    conversation_id uuid,
    workflow_version_id uuid,
    initiated_by uuid REFERENCES iam.users(id),
    external_request_key text,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','waiting_approval','succeeded','failed','cancelled','timed_out')),
    trace_id text,
    next_event_seq bigint NOT NULL DEFAULT 1 CHECK (next_event_seq > 0),
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,external_request_key),
    FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,conversation_id) REFERENCES runtime.conversations(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,workflow_version_id) REFERENCES catalog.workflow_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.work_items (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    digital_worker_id uuid,
    run_id uuid,
    title text NOT NULL,
    assigned_to text,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','waiting_human','completed','failed','cancelled')),
    due_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    FOREIGN KEY (tenant_id,workspace_id,digital_worker_id) REFERENCES catalog.digital_workers(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.run_steps (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    step_key text NOT NULL,
    attempt integer NOT NULL DEFAULT 1 CHECK (attempt > 0),
    status text NOT NULL CHECK (status IN ('pending','running','waiting','succeeded','failed','skipped')),
    started_at timestamptz,
    finished_at timestamptz,
    error_code text,
    UNIQUE (tenant_id,workspace_id,run_id,step_key,attempt),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.run_events (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_type text NOT NULL,
    redacted_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,run_id,sequence),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.checkpoint_refs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    thread_id text NOT NULL,
    checkpoint_namespace text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    backend text NOT NULL DEFAULT 'langgraph_postgres',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,run_id,checkpoint_namespace,checkpoint_id),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.memory_records (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_key text NOT NULL,
    memory_kind text NOT NULL CHECK (memory_kind IN ('semantic','episodic','profile')),
    ciphertext bytea NOT NULL,
    consent_ref text,
    valid_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.approval_requests (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    step_key text NOT NULL,
    action_hash char(64) NOT NULL CHECK (action_hash ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
    approver_role text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    expires_at timestamptz NOT NULL,
    decided_by uuid REFERENCES iam.users(id),
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,run_id,step_key,action_hash),
    CHECK ((status IN ('approved','rejected') AND decided_at IS NOT NULL AND decided_by IS NOT NULL) OR status NOT IN ('approved','rejected')),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.approval_decisions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_request_id uuid NOT NULL,
    actor_id uuid NOT NULL REFERENCES iam.users(id),
    decision text NOT NULL CHECK (decision IN ('approved','rejected')),
    decision_note_ref text,
    decided_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,approval_request_id),
    FOREIGN KEY (tenant_id,workspace_id,approval_request_id) REFERENCES runtime.approval_requests(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.tool_operations (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    tool_version_id uuid NOT NULL,
    operation_key text NOT NULL,
    request_hash char(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','executing','succeeded','failed','unknown')),
    result_ref text,
    external_reference text,
    last_attempt_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,operation_key),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,tool_version_id) REFERENCES catalog.tool_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE runtime.jobs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid,
    job_type text NOT NULL CHECK (job_type IN ('run','resume','ingest','reconcile')),
    payload_ref text,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','leased','done','dead')),
    available_at timestamptz NOT NULL DEFAULT now(),
    leased_until timestamptz,
    leased_by text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE integration.connections (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('http','mcp','webhook')),
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    secret_ref text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    UNIQUE (tenant_id,workspace_id,name), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE integration.outbox_events (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','delivering','delivered','dead')),
    available_at timestamptz NOT NULL DEFAULT now(),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz, UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE integration.inbox_receipts (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    consumer_key text NOT NULL,
    event_id uuid NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,consumer_key,event_id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE integration.mcp_discovery_snapshots (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL,
    protocol_version text,
    protocol_era text NOT NULL CHECK (protocol_era IN ('modern', 'legacy', 'unknown')),
    server_capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    catalog_sha256 char(64) CHECK (catalog_sha256 ~ '^[a-f0-9]{64}$'),
    status text NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
    error_code text,
    captured_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, id, connection_id),
    FOREIGN KEY (tenant_id, workspace_id, connection_id) REFERENCES integration.connections(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE integration.mcp_discovered_items (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    item_kind text NOT NULL CHECK (item_kind IN ('tool', 'resource', 'resource_template', 'prompt')),
    remote_key text NOT NULL,
    descriptor jsonb NOT NULL CHECK (jsonb_typeof(descriptor) = 'object'),
    descriptor_sha256 char(64) NOT NULL CHECK (descriptor_sha256 ~ '^[a-f0-9]{64}$'),
    schema_sha256 char(64) CHECK (schema_sha256 ~ '^[a-f0-9]{64}$'),
    captured_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, snapshot_id, item_kind, remote_key),
    UNIQUE (tenant_id, workspace_id, connection_id, id),
    FOREIGN KEY (tenant_id, workspace_id, snapshot_id, connection_id) REFERENCES integration.mcp_discovery_snapshots(tenant_id, workspace_id, id, connection_id),
    FOREIGN KEY (tenant_id, workspace_id, connection_id) REFERENCES integration.connections(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE integration.mcp_tool_mappings (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL,
    remote_tool_name text NOT NULL,
    discovery_item_id uuid NOT NULL,
    tool_version_id uuid NOT NULL,
    approved_schema_sha256 char(64) NOT NULL CHECK (approved_schema_sha256 ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'stale', 'revoked')),
    reviewed_by uuid REFERENCES iam.users(id),
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, connection_id, remote_tool_name, tool_version_id),
    FOREIGN KEY (tenant_id, workspace_id, connection_id) REFERENCES integration.connections(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, discovery_item_id) REFERENCES integration.mcp_discovered_items(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, tool_version_id) REFERENCES catalog.tool_versions(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE governance.policy_sets (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    resource_type text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,name), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE governance.policy_versions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_set_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    policy_body jsonb NOT NULL,
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,policy_set_id,version),
    FOREIGN KEY (tenant_id,workspace_id,policy_set_id) REFERENCES governance.policy_sets(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE governance.audit_events (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_subject text NOT NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('allowed','denied','succeeded','failed')),
    request_id text,
    trace_id text,
    reason_code text,
    details_redacted jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE governance.retention_policies (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    data_class text NOT NULL,
    retention_days integer NOT NULL CHECK (retention_days BETWEEN 1 AND 36500),
    legal_hold boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,data_class), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.model_usage (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    step_id uuid,
    provider text NOT NULL,
    model_id text NOT NULL,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    usage_source text NOT NULL CHECK (usage_source IN ('provider','estimated','unknown')),
    cost_usd numeric(18,8) CHECK (cost_usd >= 0),
    pricing_version text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,workspace_id,run_id) REFERENCES runtime.runs(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,step_id) REFERENCES runtime.run_steps(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.eval_datasets (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,name), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.eval_dataset_versions (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    checksum_sha256 char(64) NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,workspace_id,dataset_id,version),
    FOREIGN KEY (tenant_id,workspace_id,dataset_id) REFERENCES ops.eval_datasets(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.eval_cases (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL,
    case_key text NOT NULL,
    input_ref text NOT NULL,
    assertions jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id,workspace_id,dataset_version_id,case_key),
    FOREIGN KEY (tenant_id,workspace_id,dataset_version_id) REFERENCES ops.eval_dataset_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.eval_runs (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_version_id uuid NOT NULL,
    dataset_version_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')),
    started_at timestamptz,
    finished_at timestamptz,
    model_settings_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,workspace_id,agent_version_id) REFERENCES catalog.agent_versions(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,dataset_version_id) REFERENCES ops.eval_dataset_versions(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.eval_results (
    tenant_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_id uuid NOT NULL,
    case_id uuid NOT NULL,
    metric_name text NOT NULL,
    score numeric(12,6),
    passed boolean,
    trace_id text,
    explanation_ref text,
    UNIQUE (tenant_id,workspace_id,eval_run_id,case_id,metric_name),
    FOREIGN KEY (tenant_id,workspace_id,eval_run_id) REFERENCES ops.eval_runs(tenant_id,workspace_id,id),
    FOREIGN KEY (tenant_id,workspace_id,case_id) REFERENCES ops.eval_cases(tenant_id,workspace_id,id), UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE INDEX ix_memberships_active ON iam.memberships (tenant_id,workspace_id,role) WHERE status = 'active';

CREATE INDEX ix_agents_name ON catalog.agents (tenant_id,workspace_id,display_name);

CREATE INDEX ix_document_versions_published ON knowledge.document_versions (tenant_id,workspace_id,document_id,published_at DESC) WHERE status = 'published';

CREATE UNIQUE INDEX uq_document_one_published ON knowledge.document_versions (tenant_id,workspace_id,document_id) WHERE status = 'published';

CREATE INDEX ix_chunks_document ON knowledge.chunks (tenant_id,workspace_id,document_version_id,ordinal);

CREATE INDEX ix_work_items_status ON runtime.work_items (tenant_id,workspace_id,status,due_at);

CREATE INDEX ix_runs_recent ON runtime.runs (tenant_id,workspace_id,created_at DESC);

CREATE INDEX ix_runs_active ON runtime.runs (tenant_id,workspace_id,status,created_at) WHERE status IN ('queued','running','waiting_approval');

CREATE INDEX ix_run_events_replay ON runtime.run_events (tenant_id,workspace_id,run_id,sequence);

CREATE INDEX ix_messages_conversation ON runtime.messages (tenant_id,workspace_id,conversation_id,created_at);

CREATE INDEX ix_approvals_pending ON runtime.approval_requests (tenant_id,workspace_id,expires_at) WHERE status = 'pending';

CREATE INDEX ix_jobs_claim ON runtime.jobs (available_at,created_at) WHERE status = 'queued';

CREATE INDEX ix_outbox_claim ON integration.outbox_events (available_at,created_at) WHERE state = 'pending';

CREATE INDEX ix_audit_time ON governance.audit_events (tenant_id,workspace_id,occurred_at DESC);

CREATE INDEX ix_audit_resource ON governance.audit_events (tenant_id,workspace_id,resource_type,resource_id,occurred_at DESC);

CREATE INDEX ix_usage_run ON ops.model_usage (tenant_id,workspace_id,run_id);

CREATE INDEX ix_memory_subject ON runtime.memory_records (tenant_id,workspace_id,subject_key,memory_kind);

CREATE INDEX ix_vector_pending ON knowledge.vector_refs (tenant_id,workspace_id,state) WHERE state IN ('pending','failed');

CREATE INDEX ix_ingestion_status ON knowledge.ingestion_jobs (tenant_id,workspace_id,status,created_at);

CREATE UNIQUE INDEX uq_mcp_approved_active_tool ON integration.mcp_tool_mappings (tenant_id, workspace_id, connection_id, remote_tool_name) WHERE status = 'approved';
CREATE INDEX idx_skills_workspace ON catalog.skills (tenant_id, workspace_id, status, created_at DESC);
CREATE INDEX idx_skill_versions_lookup ON catalog.skill_versions (tenant_id, workspace_id, skill_id, version);
CREATE INDEX idx_agent_skill_bindings_agent ON catalog.agent_skill_bindings (tenant_id, workspace_id, agent_version_id);
CREATE INDEX idx_mcp_snapshot_latest ON integration.mcp_discovery_snapshots (tenant_id, workspace_id, connection_id, captured_at DESC);
CREATE INDEX idx_mcp_items_by_kind ON integration.mcp_discovered_items (tenant_id, workspace_id, snapshot_id, item_kind);
CREATE INDEX idx_mcp_mapping_tool_lookup ON integration.mcp_tool_mappings (tenant_id, workspace_id, tool_version_id, status);


CREATE FUNCTION governance.reject_immutable_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'Immutable table %.% cannot be changed via %', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END $$;

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON catalog.workflow_versions FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON catalog.tool_versions FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON catalog.agent_versions FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON governance.policy_versions FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON governance.audit_events FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON runtime.approval_decisions FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();

CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON catalog.skill_versions FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();
CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON catalog.agent_skill_bindings FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();
CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON integration.mcp_discovery_snapshots FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();
CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON integration.mcp_discovered_items FOR EACH ROW EXECUTE FUNCTION governance.reject_immutable_mutation();


CREATE FUNCTION governance.guard_document_version() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    IF (OLD.tenant_id, OLD.workspace_id, OLD.id, OLD.document_id, OLD.version,
        OLD.object_key, OLD.checksum_sha256, OLD.created_at)
       IS DISTINCT FROM
       (NEW.tenant_id, NEW.workspace_id, NEW.id, NEW.document_id, NEW.version,
        NEW.object_key, NEW.checksum_sha256, NEW.created_at) THEN
        RAISE EXCEPTION 'Document version identity/content cannot be changed';
    END IF;
    IF OLD.status IN ('published','superseded') AND NEW.status NOT IN ('superseded',OLD.status) THEN
        RAISE EXCEPTION 'Published/superseded document version cannot revert';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_document_version_before_update BEFORE UPDATE ON knowledge.document_versions FOR EACH ROW EXECUTE FUNCTION governance.guard_document_version();

ALTER TABLE iam.tenants ENABLE ROW LEVEL SECURITY;

ALTER TABLE iam.tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON iam.tenants USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE iam.workspaces ENABLE ROW LEVEL SECURITY;

ALTER TABLE iam.workspaces FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON iam.workspaces USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE iam.memberships ENABLE ROW LEVEL SECURITY;

ALTER TABLE iam.memberships FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON iam.memberships USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE iam.service_accounts ENABLE ROW LEVEL SECURITY;

ALTER TABLE iam.service_accounts FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON iam.service_accounts USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.model_configs ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.model_configs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.model_configs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.business_processes ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.business_processes FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.business_processes USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.workflows ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.workflows FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.workflows USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.workflow_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.workflow_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.workflow_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.tools ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.tools FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.tools USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.tool_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.tool_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.tool_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.agents ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.agents FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.agents USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.agent_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.agent_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.agent_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.agent_publications ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.agent_publications FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.agent_publications USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.tool_grants ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.tool_grants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.tool_grants USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.digital_workers ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.digital_workers FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.digital_workers USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.agent_tool_bindings ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.agent_tool_bindings FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.agent_tool_bindings USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.bases ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.bases FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.bases USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.base_grants ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.base_grants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.base_grants USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.documents ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.documents FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.documents USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.document_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.document_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.document_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.chunks ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.chunks USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.vector_refs ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.vector_refs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.vector_refs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE knowledge.ingestion_jobs ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge.ingestion_jobs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge.ingestion_jobs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.agent_knowledge_bindings ENABLE ROW LEVEL SECURITY;

ALTER TABLE catalog.agent_knowledge_bindings FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON catalog.agent_knowledge_bindings USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.conversations ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.conversations FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.conversations USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.messages ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.messages FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.messages USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.runs ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.runs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.runs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.work_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.work_items FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.work_items USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.run_steps ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.run_steps FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.run_steps USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.run_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.run_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.run_events USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.checkpoint_refs ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.checkpoint_refs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.checkpoint_refs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.memory_records ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.memory_records FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.memory_records USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.approval_requests ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.approval_requests FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.approval_requests USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.approval_decisions ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.approval_decisions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.approval_decisions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.tool_operations ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.tool_operations FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.tool_operations USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE runtime.jobs ENABLE ROW LEVEL SECURITY;

ALTER TABLE runtime.jobs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime.jobs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE integration.connections ENABLE ROW LEVEL SECURITY;

ALTER TABLE integration.connections FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON integration.connections USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE integration.outbox_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE integration.outbox_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON integration.outbox_events USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE integration.inbox_receipts ENABLE ROW LEVEL SECURITY;

ALTER TABLE integration.inbox_receipts FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON integration.inbox_receipts USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE governance.policy_sets ENABLE ROW LEVEL SECURITY;

ALTER TABLE governance.policy_sets FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON governance.policy_sets USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE governance.policy_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE governance.policy_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON governance.policy_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE governance.audit_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE governance.audit_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON governance.audit_events USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE governance.retention_policies ENABLE ROW LEVEL SECURITY;

ALTER TABLE governance.retention_policies FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON governance.retention_policies USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE ops.model_usage ENABLE ROW LEVEL SECURITY;

ALTER TABLE ops.model_usage FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ops.model_usage USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE ops.eval_datasets ENABLE ROW LEVEL SECURITY;

ALTER TABLE ops.eval_datasets FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ops.eval_datasets USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE ops.eval_dataset_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE ops.eval_dataset_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ops.eval_dataset_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE ops.eval_cases ENABLE ROW LEVEL SECURITY;

ALTER TABLE ops.eval_cases FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ops.eval_cases USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE ops.eval_runs ENABLE ROW LEVEL SECURITY;

ALTER TABLE ops.eval_runs FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ops.eval_runs USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE ops.eval_results ENABLE ROW LEVEL SECURITY;

ALTER TABLE ops.eval_results FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON ops.eval_results USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));


ALTER TABLE catalog.skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.skills FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalog.skills USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.skill_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.skill_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalog.skill_versions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE catalog.agent_skill_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.agent_skill_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalog.agent_skill_bindings USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE integration.mcp_discovery_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration.mcp_discovery_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON integration.mcp_discovery_snapshots USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE integration.mcp_discovered_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration.mcp_discovered_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON integration.mcp_discovered_items USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

ALTER TABLE integration.mcp_tool_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration.mcp_tool_mappings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON integration.mcp_tool_mappings USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL)) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid AND (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid OR NULLIF(current_setting('app.workspace_id', true), '') IS NULL));

COMMIT;


-- Grant application permissions to platform_app
GRANT USAGE ON SCHEMA iam, catalog, knowledge, runtime, integration, governance, ops TO platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA iam, catalog, knowledge, runtime, integration, governance, ops TO platform_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA iam, catalog, knowledge, runtime, integration, governance, ops GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO platform_app;
