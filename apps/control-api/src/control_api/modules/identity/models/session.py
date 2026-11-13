"""Internal domain models for identity and authentication sessions (SPEC-AUTH-001)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class SessionVerificationStatus(StrEnum):
    """Finite outcome states for authoritative session store verification (CT-025)."""

    OK = "OK"
    REVOKED = "REVOKED"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    ABSOLUTE_EXPIRED = "ABSOLUTE_EXPIRED"
    IDLE_EXPIRED = "IDLE_EXPIRED"


@dataclass(frozen=True, slots=True)
class VerifiedIdentityEvidence:
    """Authoritative OIDC identity verification evidence bound to a session (ADR-020)."""

    issuer: str
    subject: str
    user_id: str
    email_normalized: str
    email_verified: bool
    id_token_issued_at: int
    observed_at: int

    def is_fresh(
        self,
        now: int | None = None,
        *,
        max_age_seconds: int = 900,
        max_future_skew_seconds: int = 60,
    ) -> bool:
        """Return True iff both observation and ID Token issuance are strictly within window."""
        if (
            type(self.id_token_issued_at) is not int
            or type(self.observed_at) is not int
            or self.id_token_issued_at <= 0
            or self.observed_at <= 0
        ):
            return False
        current = now if now is not None else int(time.time())
        if self.id_token_issued_at > current + max_future_skew_seconds:
            return False
        if self.observed_at > current + max_future_skew_seconds:
            return False
        return (
            current - self.observed_at < max_age_seconds
            and current - self.id_token_issued_at < max_age_seconds
        )

    def matches_session(self, *, issuer: str, subject: str, user_id: str) -> bool:
        """Ensure evidence belongs cleanly to the session's authenticated principal."""
        return (
            self.issuer == issuer
            and self.subject == subject
            and self.user_id == user_id
        )


@dataclass(frozen=True, slots=True)
class SessionData:
    session_id: str
    user_id: str
    issuer: str
    subject: str
    created_at: int
    idle_expires_at: int
    absolute_expires_at: int
    rotation_version: int = 1
    identity_evidence: VerifiedIdentityEvidence | None = None

    def is_idle_expired(self, now: int | None = None) -> bool:
        current = now if now is not None else int(time.time())
        return current >= self.idle_expires_at

    def is_absolute_expired(self, now: int | None = None) -> bool:
        current = now if now is not None else int(time.time())
        return current >= self.absolute_expires_at


@dataclass(frozen=True, slots=True)
class StoredSessionState:
    session_id: str
    user_id: str
    issuer: str
    subject: str
    rotation_version: int
    created_at: int
    idle_expires_at: int
    absolute_expires_at: int
    identity_evidence: VerifiedIdentityEvidence | None = None


@dataclass(frozen=True, slots=True)
class AuthTransaction:
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    return_to: str
    browser_correlation: str
    created_at: int
    pending_invitation_id: str | None = None
    pending_handoff_binding_digest: str | None = None
