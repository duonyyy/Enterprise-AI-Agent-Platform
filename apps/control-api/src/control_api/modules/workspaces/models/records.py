"""Immutable domain records and typed storage envelopes for the Workspaces module (CT-024, PY-004)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    """Authoritative domain representation of a workspace within a tenant."""

    workspace_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_slug: str
    workspace_name: str
    status: str
    role: str
    created_at: str
    initial_owner_user_id: str | None = None
    audit_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceSummaryRecord:
    """Narrow membership projection returned for workspace discovery and authorization checks."""

    workspace_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_slug: str
    workspace_name: str
    role: str
    membership_status: str = "active"


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    """Scoped workspace membership record without cross-tenant or secret fields."""

    membership_id: str
    user_id: str
    display_name: str | None
    role: str
    status: str
    joined_at: str | None


@dataclass(frozen=True, slots=True)
class MembershipMutationRecord:
    """Committed outcome of a workspace membership role update or revocation."""

    membership_id: str
    user_id: str
    display_name: str | None
    role: str
    status: str
    joined_at: str | None
    outcome: str
    audit_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class InvitationPolicyConfig:
    """Invitation lifecycle TTL and rate-limit settings passed into repository mutations."""

    ttl_hours: int
    resend_cooldown_seconds: int
    recipient_hourly_limit: int
    workspace_hourly_limit: int


@dataclass(frozen=True, slots=True)
class InvitationRecord:
    """Safe projection of an invitation record (never exposes proof, link, or token hashes)."""

    invitation_id: str
    tenant_id: str
    workspace_id: str
    recipient_email_normalized: str
    recipient_issuer: str
    role: str
    status: str
    generation: int
    delivery_state: str
    delivery_error_code: str | None
    created_by_user_id: str
    delivery_requested_by_user_id: str
    revoked_by_user_id: str | None
    accepted_by_user_id: str | None
    expires_at: str
    created_at: str
    updated_at: str
    provider_accepted_at: str | None
    revoked_at: str | None
    accepted_at: str | None
    audit_event_id: str | None = None
    outbox_event_id: str | None = None


@dataclass(frozen=True, slots=True)
class InvitationPreviewRecord:
    """Verified non-consuming preview projection returned for invitation acceptance."""

    invitation_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    role: str
    status: str
    expires_at: str
    can_accept: bool
    acceptance_state: str


@dataclass(frozen=True, slots=True)
class InvitationAcceptRecord:
    """Authoritative commit or same-user replay projection from invitation acceptance."""

    invitation_id: str
    tenant_id: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    role: str
    acceptance_state: str


@dataclass(frozen=True, slots=True)
class DecryptedHandoffPayload:
    """Verified handoff payload recovered from an active encrypted Redis entry."""

    invitation_id: str
    token_hash: str
    browser_binding_digest: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class HandoffPersistResult:
    """Atomic persistence outcome for browser-bound invitation handoffs."""

    outcome: str
    expires_at: int
    remaining_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class ConsumedHandoffReceipt:
    """Non-secret post-commit receipt allowing safe same-user idempotent reload/retry."""

    invitation_id: str
    user_id: str
    token_hash: str
    browser_binding_digest: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class StoredHandoffLookup:
    """Result returned when looking up a browser-bound handoff key."""

    active: DecryptedHandoffPayload | None = None
    consumed: ConsumedHandoffReceipt | None = None


class HandoffEntryDict(TypedDict):
    """Typed envelope stored in Redis/in-memory handoff cache (supports both active & receipt states)."""

    kind: Literal["handoff", "consumed_receipt"]
    expires_at: int
    token_hash: str
    invitation_id: NotRequired[str]
    key_id: NotRequired[str]
    ciphertext: NotRequired[str]
    user_id: NotRequired[str]
    browser_binding_digest: NotRequired[str]


__all__ = [
    "ConsumedHandoffReceipt",
    "DecryptedHandoffPayload",
    "HandoffEntryDict",
    "HandoffPersistResult",
    "InvitationAcceptRecord",
    "InvitationPolicyConfig",
    "InvitationPreviewRecord",
    "InvitationRecord",
    "MembershipMutationRecord",
    "MembershipRecord",
    "StoredHandoffLookup",
    "WorkspaceRecord",
    "WorkspaceSummaryRecord",
]
