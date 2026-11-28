-- Step 07-A: run execution authority, lease fencing, deadline and state owner guards.
-- Forward delta after 0010. No new domain table, role, status or scope.
BEGIN;
SET LOCAL TIME ZONE 'UTC';

-- 1. Preflight checks: ensure no duplicate run jobs and no undrained leased jobs exist
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM runtime.jobs
        WHERE job_type = 'run' AND run_id IS NOT NULL
        GROUP BY tenant_id, workspace_id, run_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Legacy duplicate run jobs exist; resolve before applying migration 0011'
            USING ERRCODE = '23505';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM runtime.jobs
        WHERE status = 'leased'
    ) THEN
        RAISE EXCEPTION 'Active leased jobs must be drained before applying migration 0011'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

-- 2. Add lease_token to runtime.jobs and enforce fenced lease invariants
ALTER TABLE runtime.jobs
    ADD COLUMN lease_token uuid,
    ADD CONSTRAINT job_lease_token_check CHECK (
        (status = 'leased' AND lease_token IS NOT NULL AND leased_by IS NOT NULL AND leased_until IS NOT NULL)
        OR
        (status != 'leased' AND lease_token IS NULL)
    );

-- 3. Expand runtime.jobs closed_reason check for execution terminal outcomes
ALTER TABLE runtime.jobs
    DROP CONSTRAINT job_closed_reason_check,
    ADD CONSTRAINT job_closed_reason_check CHECK (
        closed_reason IS NULL
        OR (status = 'dead' AND closed_reason IN (
            'run_cancelled',
            'run_timed_out',
            'execution_failed',
            'execution_timed_out',
            'authority_denied',
            'outcome_unknown',
            'checkpoint_incompatible',
            'attempts_exhausted'
        ))
    );

-- 4. Canonical run-job unique guard: at most one active/queued/leased/terminal run job per run
CREATE UNIQUE INDEX uq_jobs_canonical_run
    ON runtime.jobs (tenant_id, workspace_id, run_id)
    WHERE job_type = 'run' AND run_id IS NOT NULL;

-- 5. Add execution authority, deadline, and error_code to runtime.runs
ALTER TABLE runtime.runs
    ADD COLUMN execution_authority_ciphertext bytea,
    ADD COLUMN execution_authority_key_id text,
    ADD COLUMN authority_expires_at timestamptz,
    ADD COLUMN execution_deadline_at timestamptz,
    ADD COLUMN error_code text,
    ADD CONSTRAINT run_execution_authority_shape CHECK (
        (execution_authority_ciphertext IS NULL
         AND execution_authority_key_id IS NULL
         AND authority_expires_at IS NULL)
        OR
        (execution_authority_ciphertext IS NOT NULL
         AND octet_length(execution_authority_ciphertext) <= 16384
         AND execution_authority_key_id IS NOT NULL
         AND length(execution_authority_key_id) BETWEEN 1 AND 128
         AND authority_expires_at IS NOT NULL)
    ),
    ADD CONSTRAINT run_error_code_check CHECK (
        error_code IS NULL OR error_code IN (
            'execution_capability_unsupported',
            'invalid_execution_input',
            'execution_authority_denied',
            'model_policy_denied',
            'context_budget_exceeded',
            'token_budget_exceeded',
            'model_outcome_unknown',
            'model_response_invalid',
            'model_output_truncated',
            'model_tool_call_unsupported',
            'execution_backend_unavailable',
            'checkpoint_incompatible',
            'checkpoint_corrupt',
            'checkpoint_limit_exceeded',
            'attempts_exhausted',
            'execution_deadline_exceeded'
        )
    );

-- 6. Update receipt guard to enforce immutability of authority, deadline, and terminal error_code
CREATE OR REPLACE FUNCTION runtime.guard_run_admission_receipt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, runtime, pg_temp
SET TimeZone = 'UTC'
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.request_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'Admission key tombstone must be retained' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.request_sha256 IS NOT NULL THEN
        IF ROW(NEW.tenant_id, NEW.workspace_id, NEW.id, NEW.agent_version_id,
               NEW.conversation_id, NEW.initiated_by, NEW.external_request_key,
               NEW.request_sha256, NEW.input_ref, NEW.input_sha256, NEW.input_size_bytes,
               NEW.created_at, NEW.queue_expires_at)
           IS DISTINCT FROM
           ROW(OLD.tenant_id, OLD.workspace_id, OLD.id, OLD.agent_version_id,
               OLD.conversation_id, OLD.initiated_by, OLD.external_request_key,
               OLD.request_sha256, OLD.input_ref, OLD.input_sha256, OLD.input_size_bytes,
               OLD.created_at, OLD.queue_expires_at) THEN
            RAISE EXCEPTION 'Admission receipt identity is immutable' USING ERRCODE = '23514';
        END IF;

        -- Authority immutability once set
        IF OLD.execution_authority_ciphertext IS NOT NULL THEN
            IF ROW(NEW.execution_authority_ciphertext, NEW.execution_authority_key_id, NEW.authority_expires_at)
               IS DISTINCT FROM
               ROW(OLD.execution_authority_ciphertext, OLD.execution_authority_key_id, OLD.authority_expires_at) THEN
                RAISE EXCEPTION 'Execution authority grant is immutable once assigned' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.execution_authority_ciphertext IS NOT NULL AND OLD.status != 'queued' THEN
            RAISE EXCEPTION 'Execution authority grant cannot be added to non-queued run' USING ERRCODE = '23514';
        END IF;

        -- started_at and execution_deadline_at immutability once set
        IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION 'Run started_at timestamp is immutable once set' USING ERRCODE = '23514';
        END IF;
        IF OLD.execution_deadline_at IS NOT NULL AND NEW.execution_deadline_at IS DISTINCT FROM OLD.execution_deadline_at THEN
            RAISE EXCEPTION 'Run execution_deadline_at is immutable once set' USING ERRCODE = '23514';
        END IF;

        -- Terminal anchor immutability
        IF OLD.status IN ('succeeded', 'failed', 'cancelled', 'timed_out') AND
           (NEW.status IS DISTINCT FROM OLD.status
            OR NEW.finished_at IS DISTINCT FROM OLD.finished_at
            OR NEW.error_code IS DISTINCT FROM OLD.error_code) THEN
            RAISE EXCEPTION 'Terminal admission receipt anchor is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.request_sha256 IS NOT NULL THEN
        IF NEW.queue_expires_at IS NOT NULL AND NEW.queue_expires_at < NEW.created_at THEN
            RAISE EXCEPTION 'Queue deadline cannot precede creation time' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' AND NEW.queue_expires_at IS NULL THEN
            RAISE EXCEPTION 'Canonical admission receipt requires queue deadline' USING ERRCODE = '23514';
        END IF;
        IF NEW.execution_deadline_at IS NOT NULL AND NEW.started_at IS NOT NULL AND NEW.execution_deadline_at < NEW.started_at THEN
            RAISE EXCEPTION 'Execution deadline cannot precede start time' USING ERRCODE = '23514';
        END IF;
        IF NEW.status NOT IN ('failed', 'timed_out', 'cancelled') AND NEW.error_code IS NOT NULL THEN
            RAISE EXCEPTION 'Non-terminal run must not set error_code' USING ERRCODE = '23514';
        END IF;
        IF NEW.status IN ('succeeded', 'failed', 'cancelled', 'timed_out') THEN
            IF NEW.finished_at IS NULL THEN
                RAISE EXCEPTION 'Terminal receipt requires finished_at' USING ERRCODE = '23514';
            END IF;
            NEW.receipt_expires_at := NEW.finished_at + interval '30 days';
        ELSE
            NEW.receipt_expires_at := NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION runtime.guard_run_admission_receipt() FROM PUBLIC;

COMMIT;
