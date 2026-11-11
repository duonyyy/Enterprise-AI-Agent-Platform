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
class SessionData:
    session_id: str
    user_id: str
    issuer: str
    subject: str
    created_at: int
    idle_expires_at: int
    absolute_expires_at: int
    rotation_version: int = 1

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


@dataclass(frozen=True, slots=True)
class AuthTransaction:
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    return_to: str
    browser_correlation: str
    created_at: int
