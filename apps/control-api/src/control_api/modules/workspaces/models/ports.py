"""Explicit pure-Python Protocol ports for the Workspaces application services (AR-002, AR-008, CT-023)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from control_api.modules.workspaces.models.enums import (
    ExpectedActiveStatus,
    InvitableRole,
    MembershipStatus,
    WorkspaceRole,
)
from control_api.modules.workspaces.models.records import (
    HandoffEntryDict,
    HandoffPersistResult,
    InvitationAcceptRecord,
    InvitationPolicyConfig,
    InvitationPreviewRecord,
    InvitationRecord,
    MembershipMutationRecord,
    MembershipRecord,
    WorkspaceRecord,
    WorkspaceSummaryRecord,
)
from platform_common.invitations import InvitationProofProtector


class IdentityEvidencePort(Protocol):
    """Contract for verified OIDC identity evidence attached to an authenticated session."""

    @property
    def email_normalized(self) -> str: ...

    @property
    def email_verified(self) -> bool: ...

    @property
    def id_token_issued_at(self) -> int: ...

    @property
    def observed_at(self) -> int: ...

    def is_fresh(
        self,
        now: int | None = None,
        *,
        max_age_seconds: int = 900,
        max_future_skew_seconds: int = 60,
    ) -> bool: ...

    def matches_session(self, *, issuer: str, subject: str, user_id: str) -> bool: ...


class AuthenticatedSessionPort(Protocol):
    """Contract for an authenticated server-side session carrying OIDC identity evidence."""

    @property
    def identity_evidence(self) -> IdentityEvidencePort | None: ...


class AuthenticatedUserPort(Protocol):
    """Contract for authenticated session-backed caller context (workspace-independent)."""

    @property
    def user_id(self) -> str: ...

    @property
    def session_data(self) -> AuthenticatedSessionPort | None: ...

    @property
    def issuer(self) -> str: ...

    @property
    def subject(self) -> str: ...


class WorkspacePrincipalPort(Protocol):
    """Contract for workspace-scoped authenticated principal context."""

    @property
    def tenant_id(self) -> str: ...

    @property
    def workspace_id(self) -> str: ...

    @property
    def user_id(self) -> str: ...


class WorkspaceRepositoryPort(Protocol):
    """Persistence port for tenant/workspace bootstrap and scoped workspace discovery."""

    async def user_exists(self, user_id: UUID) -> bool: ...

    async def get_user_workspaces(self, user_id: UUID) -> list[WorkspaceSummaryRecord]: ...

    async def lock_authorizing_owner_membership(
        self,
        *,
        tenant_id: UUID,
        authorizing_workspace_id: UUID,
        user_id: UUID,
    ) -> bool: ...

    async def create_workspace_atomic(
        self,
        *,
        actor_user_id: UUID,
        owner_user_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        workspace_slug: str,
        workspace_name: str,
        reason_code: str,
        tenant_slug: str | None = None,
        tenant_name: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> WorkspaceRecord: ...

    async def get_workspace_in_scope(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceRecord | None: ...


class MembershipRepositoryPort(Protocol):
    """Persistence port for scoped workspace membership queries and mutations."""

    async def list_memberships(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
        status_filter: MembershipStatus | None,
    ) -> tuple[list[MembershipRecord], str | None]: ...

    async def update_member_role(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        membership_id: UUID,
        new_role: WorkspaceRole,
        expected_role: WorkspaceRole,
        expected_status: ExpectedActiveStatus,
        request_id: str,
        trace_id: str,
    ) -> MembershipMutationRecord: ...

    async def revoke_member(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        membership_id: UUID,
        expected_role: WorkspaceRole,
        expected_status: ExpectedActiveStatus,
        request_id: str,
        trace_id: str,
    ) -> MembershipMutationRecord: ...


class InvitationRepositoryPort(Protocol):
    """Persistence port for workspace invitation administration and atomic acceptance."""

    async def create_invitation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        recipient_email_normalized: str,
        recipient_issuer: str,
        role: InvitableRole | str,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord: ...

    async def list_invitations(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[InvitationRecord], str | None]: ...

    async def resend_invitation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord: ...

    async def revoke_invitation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord: ...

    async def preview_invitation(
        self,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_email_normalized: str,
        recipient_subject: str | None = None,
    ) -> InvitationPreviewRecord: ...

    async def accept_invitation(
        self,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
        request_id: str,
        trace_id: str,
        identity_freshness_deadline: int | None = None,
        handoff_expires_at: int | None = None,
        enforce_wall_clock_deadlines: bool = True,
    ) -> InvitationAcceptRecord: ...

    async def commit(self) -> None: ...


class HandoffStorePort(Protocol):
    """Port for encrypted browser-bound handoff cache and atomic rate limiting."""

    def enforce_handoff_rate_limit(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None: ...

    def save_or_get_encrypted_handoff(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        token_hash: str,
        key_id: str,
        ciphertext: str,
        expires_at: int,
        ttl_seconds: int,
        now: int | None = None,
    ) -> HandoffPersistResult: ...

    def get_handoff_entry(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> HandoffEntryDict | None: ...

    def convert_to_consumed_receipt(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        user_id: str,
        token_hash: str,
        expected_handoff_ciphertext: str,
        expires_at: int,
        now: int | None = None,
    ) -> None: ...

    def clear_all(self) -> None: ...

    async def enforce_handoff_rate_limit_async(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None: ...

    async def save_or_get_encrypted_handoff_async(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        token_hash: str,
        key_id: str,
        ciphertext: str,
        expires_at: int,
        ttl_seconds: int,
        now: int | None = None,
    ) -> HandoffPersistResult: ...

    async def get_handoff_entry_async(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> HandoffEntryDict | None: ...

    async def convert_to_consumed_receipt_async(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        user_id: str,
        token_hash: str,
        expected_handoff_ciphertext: str,
        expires_at: int,
        now: int | None = None,
    ) -> None: ...


__all__ = [
    "AuthenticatedSessionPort",
    "AuthenticatedUserPort",
    "HandoffStorePort",
    "IdentityEvidencePort",
    "InvitationRepositoryPort",
    "MembershipRepositoryPort",
    "WorkspacePrincipalPort",
    "WorkspaceRepositoryPort",
]
