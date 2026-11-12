BEGIN;

CREATE TABLE iam.workspace_invitations (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_email_normalized text NOT NULL CHECK (
        char_length(recipient_email_normalized) >= 3
        AND char_length(recipient_email_normalized) <= 254
        AND recipient_email_normalized = lower(recipient_email_normalized)
        AND recipient_email_normalized = btrim(recipient_email_normalized)
        AND position('@' IN recipient_email_normalized) > 1
        AND recipient_email_normalized !~ '[\r\n]'
    ),
    recipient_issuer text NOT NULL CHECK (char_length(btrim(recipient_issuer)) > 0),
    role text NOT NULL CHECK (role IN ('admin', 'developer', 'operator', 'viewer')),
    created_by_user_id uuid NOT NULL REFERENCES iam.users(id),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    generation integer NOT NULL DEFAULT 1 CHECK (generation > 0),
    token_hash char(64) NOT NULL CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    proof_ciphertext text,
    proof_key_id text,
    delivery_state text NOT NULL DEFAULT 'queued' CHECK (
        delivery_state IN ('queued', 'sending', 'provider_accepted', 'failed', 'unknown', 'cancelled')
    ),
    delivery_requested_by_user_id uuid NOT NULL REFERENCES iam.users(id),
    delivery_error_code text,
    provider_accepted_at timestamptz,
    lease_token uuid,
    leased_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    revoked_at timestamptz,
    revoked_by_user_id uuid REFERENCES iam.users(id),
    accepted_at timestamptz,
    accepted_by_user_id uuid REFERENCES iam.users(id),
    UNIQUE (tenant_id, workspace_id, id),
    FOREIGN KEY (tenant_id, workspace_id) REFERENCES iam.workspaces(tenant_id, id),
    CONSTRAINT chk_workspace_invitations_proof_pair CHECK (
        (proof_ciphertext IS NULL) = (proof_key_id IS NULL)
    ),
    CONSTRAINT chk_workspace_invitations_lease_pair CHECK (
        (lease_token IS NULL) = (leased_until IS NULL)
    ),
    CONSTRAINT chk_workspace_invitations_sending_lease CHECK (
        (delivery_state = 'sending') = (lease_token IS NOT NULL AND leased_until IS NOT NULL)
    ),
    CONSTRAINT chk_workspace_invitations_provider_accepted_ts CHECK (
        delivery_state <> 'provider_accepted' OR provider_accepted_at IS NOT NULL
    ),
    CONSTRAINT chk_workspace_invitations_revoked_fields CHECK (
        (status = 'revoked') = (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)
    ),
    CONSTRAINT chk_workspace_invitations_accepted_fields CHECK (
        (status = 'accepted') = (accepted_at IS NOT NULL AND accepted_by_user_id IS NOT NULL)
    ),
    CONSTRAINT chk_workspace_invitations_no_proof_retention CHECK (
        (delivery_state NOT IN ('provider_accepted', 'failed', 'unknown', 'cancelled') OR proof_ciphertext IS NULL)
        AND (status = 'pending' OR (proof_ciphertext IS NULL AND lease_token IS NULL))
    )
);

CREATE UNIQUE INDEX uq_workspace_invitations_pending_recipient
    ON iam.workspace_invitations (tenant_id, workspace_id, recipient_issuer, recipient_email_normalized)
    WHERE status = 'pending';

CREATE INDEX ix_workspace_invitations_list
    ON iam.workspace_invitations (tenant_id, workspace_id, created_at DESC, id DESC);

CREATE INDEX ix_workspace_invitations_pending_expiry
    ON iam.workspace_invitations (tenant_id, workspace_id, expires_at)
    WHERE status = 'pending' OR proof_ciphertext IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_users_issuer_email_normalized
    ON iam.users (issuer, email_normalized)
    WHERE email_normalized IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_outbox_invitation_rate_limit
    ON integration.outbox_events (tenant_id, workspace_id, created_at DESC)
    WHERE event_type = 'workspace.invitation.delivery_requested.v1';

ALTER TABLE iam.workspace_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE iam.workspace_invitations FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON iam.workspace_invitations
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        AND workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid
    );

REVOKE ALL ON TABLE iam.workspace_invitations FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE iam.workspace_invitations TO platform_app;

COMMIT;

