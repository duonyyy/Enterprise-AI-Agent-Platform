-- Step 06-D: run queue deadline, job closure reason, and immutable receipt guards.
-- Forward delta after 0009. No new domain table, role, status or scope.
BEGIN;
SET LOCAL TIME ZONE 'UTC';

-- 1. Add queue_expires_at to runtime.runs and closed_reason to runtime.jobs
ALTER TABLE runtime.runs
    ADD COLUMN queue_expires_at timestamptz;

ALTER TABLE runtime.jobs
    ADD COLUMN closed_reason text,
    ADD CONSTRAINT job_closed_reason_check CHECK (
        closed_reason IS NULL
        OR (status = 'dead' AND closed_reason IN ('run_cancelled', 'run_timed_out'))
    );

-- 2. Backfill existing queued runs with default 24h queue lifetime from created_at
UPDATE runtime.runs
SET queue_expires_at = created_at + interval '24 hours'
WHERE status = 'queued' AND queue_expires_at IS NULL;

-- 3. Partial index for queued expiry sweep
CREATE INDEX idx_runs_queued_expiry
    ON runtime.runs (tenant_id, workspace_id, queue_expires_at, id)
    WHERE status = 'queued';

-- 4. Update receipt guard to enforce immutability of created_at and queue_expires_at
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
        IF OLD.status IN ('succeeded', 'failed', 'cancelled', 'timed_out') AND
           (NEW.status IS DISTINCT FROM OLD.status OR NEW.finished_at IS DISTINCT FROM OLD.finished_at) THEN
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
