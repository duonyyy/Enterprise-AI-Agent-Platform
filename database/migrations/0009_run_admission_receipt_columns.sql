-- Step 06-B: maintainer chose receipt columns, workspace keys and 30-day terminal replay.
-- No new table, role, policy or scope. Existing runtime.runs FORCE RLS remains authoritative.
BEGIN;
SET LOCAL TIME ZONE 'UTC';

ALTER TABLE runtime.runs
    ADD COLUMN request_sha256 char(64),
    ADD COLUMN input_ref text,
    ADD COLUMN input_sha256 char(64),
    ADD COLUMN input_size_bytes bigint,
    ADD COLUMN receipt_expires_at timestamptz,
    ADD CONSTRAINT run_admission_receipt_shape CHECK (
        (request_sha256 IS NULL AND input_ref IS NULL AND input_sha256 IS NULL
            AND input_size_bytes IS NULL AND receipt_expires_at IS NULL)
        OR
        (request_sha256 IS NOT NULL AND request_sha256 ~ '^[a-f0-9]{64}$'
            AND input_ref IS NOT NULL AND length(input_ref) > 0
            AND input_sha256 IS NOT NULL AND input_sha256 ~ '^[a-f0-9]{64}$'
            AND input_size_bytes IS NOT NULL AND input_size_bytes > 0
            AND initiated_by IS NOT NULL AND external_request_key IS NOT NULL)
    );

-- Backfill only receipts with an immutable, actor-bound admission audit record.
-- Legacy event-only digests are not promoted into trusted receipts.
WITH receipts AS (
    SELECT DISTINCT ON (a.tenant_id, a.workspace_id, a.resource_id)
        a.tenant_id, a.workspace_id, a.resource_id, a.actor_subject, a.details_redacted AS d
    FROM governance.audit_events AS a
    WHERE a.resource_type = 'run' AND a.action = 'run.admitted'
      AND a.details_redacted->>'request_sha256' ~ '^[a-f0-9]{64}$'
      AND a.details_redacted->>'input_sha256' ~ '^[a-f0-9]{64}$'
      AND a.details_redacted->>'input_ref' LIKE 's3://%'
      AND a.details_redacted->>'input_size_bytes' ~ '^[1-9][0-9]{0,7}$'
    ORDER BY a.tenant_id, a.workspace_id, a.resource_id, a.occurred_at, a.id
)
UPDATE runtime.runs AS r
SET request_sha256 = receipts.d->>'request_sha256',
    input_ref = receipts.d->>'input_ref',
    input_sha256 = receipts.d->>'input_sha256',
    input_size_bytes = (receipts.d->>'input_size_bytes')::bigint,
    receipt_expires_at = CASE
        WHEN r.status IN ('succeeded', 'failed', 'cancelled', 'timed_out')
            AND r.finished_at IS NOT NULL THEN r.finished_at + interval '30 days'
        ELSE NULL END
FROM receipts
WHERE (r.tenant_id, r.workspace_id, r.id::text) =
      (receipts.tenant_id, receipts.workspace_id, receipts.resource_id)
  AND r.initiated_by::text = receipts.actor_subject
  AND r.initiated_by::text = receipts.d->>'actor_user_id'
  AND r.external_request_key = receipts.d->>'operation_key'
  AND (r.status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out') OR r.finished_at IS NOT NULL);

CREATE FUNCTION runtime.guard_run_admission_receipt()
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
               NEW.request_sha256, NEW.input_ref, NEW.input_sha256, NEW.input_size_bytes)
           IS DISTINCT FROM
           ROW(OLD.tenant_id, OLD.workspace_id, OLD.id, OLD.agent_version_id,
               OLD.conversation_id, OLD.initiated_by, OLD.external_request_key,
               OLD.request_sha256, OLD.input_ref, OLD.input_sha256, OLD.input_size_bytes) THEN
            RAISE EXCEPTION 'Admission receipt identity is immutable' USING ERRCODE = '23514';
        END IF;
        IF OLD.status IN ('succeeded', 'failed', 'cancelled', 'timed_out') AND
           (NEW.status IS DISTINCT FROM OLD.status OR NEW.finished_at IS DISTINCT FROM OLD.finished_at) THEN
            RAISE EXCEPTION 'Terminal admission receipt anchor is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.request_sha256 IS NOT NULL THEN
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
CREATE TRIGGER guard_run_admission_receipt
BEFORE INSERT OR UPDATE OR DELETE ON runtime.runs
FOR EACH ROW EXECUTE FUNCTION runtime.guard_run_admission_receipt();

COMMIT;
