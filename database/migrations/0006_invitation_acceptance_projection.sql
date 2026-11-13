-- Forward Migration 0006: Proof- & Identity-Bound SECURITY DEFINER Scope Projection for Invitation Acceptance
-- Approved by ADR-020 / Step 03-C (Invitation Acceptance & Membership Activation, R01 remediation).
-- Zero new tables or status changes: preserves DB-56 + ADR-019 = 57 canonical tables across 7 schemas.
-- Only exposes minimal (invitation_id, tenant_id, workspace_id) scope when BOTH current proof digest AND
-- trusted authenticated user identity (user_id, issuer, subject, verified normalized email) match.

BEGIN;

DROP FUNCTION IF EXISTS iam.resolve_invitation_accept_scope(uuid);

CREATE OR REPLACE FUNCTION iam.resolve_invitation_accept_scope(
    p_invitation_id uuid,
    p_token_hash text,
    p_user_id uuid,
    p_issuer text,
    p_subject text,
    p_email_normalized text
)
RETURNS TABLE (
    invitation_id uuid,
    tenant_id uuid,
    workspace_id uuid
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, iam, pg_temp
AS $$
    SELECT
        inv.id AS invitation_id,
        inv.tenant_id AS tenant_id,
        inv.workspace_id AS workspace_id
    FROM iam.workspace_invitations AS inv
    JOIN iam.users AS u
      ON u.id = p_user_id
     AND u.issuer = p_issuer
     AND u.subject = p_subject
    WHERE p_invitation_id IS NOT NULL
      AND p_token_hash IS NOT NULL
      AND p_user_id IS NOT NULL
      AND p_issuer IS NOT NULL
      AND p_subject IS NOT NULL
      AND p_email_normalized IS NOT NULL
      AND inv.id = p_invitation_id
      AND inv.token_hash = p_token_hash
      AND inv.recipient_issuer = p_issuer
      AND inv.recipient_email_normalized = p_email_normalized
    LIMIT 1;
$$;

REVOKE ALL ON FUNCTION iam.resolve_invitation_accept_scope(uuid, text, uuid, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION iam.resolve_invitation_accept_scope(uuid, text, uuid, text, text, text) TO platform_app;

COMMIT;
