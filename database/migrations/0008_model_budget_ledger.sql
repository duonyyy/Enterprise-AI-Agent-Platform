-- ADR-023 / D05-04 / D05-05: durable model-budget admission and accounting.
-- This migration is intentionally forward-only.  It preserves historical
-- model_usage rows while adding reservations and attempts for new model calls.
BEGIN;

CREATE TABLE ops.workspace_budgets (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_limit_usd numeric(18,8) NOT NULL CHECK (budget_limit_usd >= 0),
    spent_usd numeric(18,8) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
    reserved_usd numeric(18,8) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end > period_start),
    CHECK (spent_usd + reserved_usd <= budget_limit_usd),
    UNIQUE (tenant_id, workspace_id, period_start),
    UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id)
);

CREATE TABLE ops.model_reservations (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_key text NOT NULL CHECK (length(operation_key) BETWEEN 1 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    caller_kind text NOT NULL CHECK (caller_kind IN ('user', 'service_account', 'run', 'job', 'eval', 'standalone')),
    caller_id uuid NOT NULL,
    model_config_id uuid NOT NULL,
    budget_id uuid NOT NULL,
    run_id uuid,
    job_id uuid,
    status text NOT NULL DEFAULT 'reserved'
        CHECK (status IN ('reserved', 'dispatched', 'settled', 'released', 'unknown')),
    reserved_tokens bigint NOT NULL CHECK (reserved_tokens >= 0),
    reserved_cost_usd numeric(18,8) NOT NULL CHECK (reserved_cost_usd >= 0),
    pricing_snapshot jsonb NOT NULL,
    pricing_version text NOT NULL CHECK (length(pricing_version) BETWEEN 1 AND 128),
    expires_at timestamptz NOT NULL,
    settled_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, operation_key),
    UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id),
    FOREIGN KEY (tenant_id, workspace_id, model_config_id)
        REFERENCES catalog.model_configs(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, budget_id)
        REFERENCES ops.workspace_budgets(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, run_id)
        REFERENCES runtime.runs(tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id, job_id)
        REFERENCES runtime.jobs(tenant_id, workspace_id, id),
    CHECK (run_id IS NOT NULL OR job_id IS NOT NULL)
);

CREATE TABLE ops.model_attempts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    status text NOT NULL DEFAULT 'reserved'
        CHECK (status IN ('reserved', 'dispatched', 'response_received', 'settled', 'unknown', 'reconciled_released')),
    dispatch_token uuid NOT NULL DEFAULT gen_random_uuid(),
    provider_request_id text,
    dispatched_at timestamptz,
    response_received_at timestamptz,
    reconciliation_lease_id uuid,
    reconciliation_lease_until timestamptz,
    reconciliation_fence bigint NOT NULL DEFAULT 0 CHECK (reconciliation_fence >= 0),
    reconciliation_worker_id text,
    reconciliation_evidence_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, id),
    UNIQUE (tenant_id, workspace_id, reservation_id, attempt_number),
    UNIQUE (tenant_id, workspace_id, reservation_id, id),
    FOREIGN KEY (tenant_id, workspace_id, reservation_id)
        REFERENCES ops.model_reservations(tenant_id, workspace_id, id)
);

ALTER TABLE ops.model_usage ADD COLUMN job_id uuid;
ALTER TABLE ops.model_usage ADD COLUMN reservation_id uuid;
ALTER TABLE ops.model_usage ADD COLUMN attempt_id uuid;
ALTER TABLE ops.model_usage ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE ops.model_usage ADD CONSTRAINT ck_model_usage_anchor
    CHECK (run_id IS NOT NULL OR job_id IS NOT NULL OR reservation_id IS NOT NULL);
ALTER TABLE ops.model_usage ADD CONSTRAINT ck_model_usage_receipt_pair
    CHECK (
        (reservation_id IS NULL AND attempt_id IS NULL)
        OR (reservation_id IS NOT NULL AND attempt_id IS NOT NULL)
    );
ALTER TABLE ops.model_usage ADD CONSTRAINT fk_model_usage_job
    FOREIGN KEY (tenant_id, workspace_id, job_id)
    REFERENCES runtime.jobs(tenant_id, workspace_id, id);
ALTER TABLE ops.model_usage ADD CONSTRAINT fk_model_usage_attempt_reservation
    FOREIGN KEY (tenant_id, workspace_id, reservation_id, attempt_id)
    REFERENCES ops.model_attempts(tenant_id, workspace_id, reservation_id, id);
ALTER TABLE ops.model_usage ADD CONSTRAINT uq_model_usage_reservation
    UNIQUE (tenant_id, workspace_id, reservation_id);

CREATE INDEX ix_model_reservations_reconcile
    ON ops.model_reservations (tenant_id, workspace_id, created_at, id)
    WHERE status = 'unknown';
CREATE INDEX ix_model_reservations_run
    ON ops.model_reservations (tenant_id, workspace_id, run_id);
CREATE INDEX ix_model_reservations_job
    ON ops.model_reservations (tenant_id, workspace_id, job_id);
CREATE INDEX ix_workspace_budgets_active_period
    ON ops.workspace_budgets (tenant_id, workspace_id, period_end, period_start);
CREATE INDEX ix_model_attempts_reservation
    ON ops.model_attempts (tenant_id, workspace_id, reservation_id, attempt_number);
CREATE INDEX ix_model_attempts_reconcile
    ON ops.model_attempts (tenant_id, workspace_id, reconciliation_lease_until)
    WHERE status = 'unknown';
CREATE INDEX ix_model_usage_reservation
    ON ops.model_usage (tenant_id, workspace_id, reservation_id);
CREATE INDEX ix_model_usage_job
    ON ops.model_usage (tenant_id, workspace_id, job_id);

ALTER TABLE ops.workspace_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.workspace_budgets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops.workspace_budgets
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    );

ALTER TABLE ops.model_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.model_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops.model_reservations
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    );

ALTER TABLE ops.model_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.model_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops.model_attempts
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    );

REVOKE ALL ON TABLE ops.workspace_budgets, ops.model_reservations, ops.model_attempts FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.workspace_budgets, ops.model_reservations, ops.model_attempts TO platform_app;

COMMIT;
