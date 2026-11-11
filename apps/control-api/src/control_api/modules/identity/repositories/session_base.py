"""Authoritative session state and revocation store abstract base contract (SPEC-AUTH-001)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from control_api.modules.identity.models.session import StoredSessionState


class BaseRevocationStore(ABC):
    """Abstract base class for authoritative session state and revocation storage."""

    @abstractmethod
    def create_session_state(self, state: StoredSessionState, now: int | None = None) -> None:
        """Persist initial authoritative session state in store with bounded TTL."""

    @abstractmethod
    def get_session_state(
        self, session_id: str, now: int | None = None
    ) -> StoredSessionState | None:
        """Fetch authoritative session state, or None if missing/corrupt/expired."""

    @abstractmethod
    def verify_and_get_session(
        self,
        session_id: str,
        user_id: str,
        expected_version: int,
        session_created_at: int,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        """Atomically or consistently verify revocation and active session state.

        Returns:
            tuple[status_code, StoredSessionState | None] where status_code is one of:
            - "OK": valid and current
            - "REVOKED": session or user revoked
            - "MISSING": session state not found
            - "CORRUPT": session state corrupted or mismatched binding
            - "VERSION_MISMATCH": token rotation_version != current stored rotation_version
            - "ABSOLUTE_EXPIRED": now >= absolute_expires_at
            - "IDLE_EXPIRED": now >= idle_expires_at
        """

    @abstractmethod
    def verify_parent_session(
        self,
        session_id: str,
        user_id: str,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        """Verify that a parent browser session is active and unrevoked (D3-B).

        Does not require matching rotation_version (`ver`) so parent session refresh
        does not invalidate already-issued short-lived workspace tokens, while logout,
        user revocation, idle timeout, or absolute timeout immediately rejects them.
        """

    @abstractmethod
    def rotate_session_state(
        self,
        session_id: str,
        user_id: str,
        expected_version: int,
        session_created_at: int,
        idle_timeout_seconds: int,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        """Atomically verify and advance session rotation_version and idle_expires_at.

        Returns:
            tuple[status_code, StoredSessionState | None] with the same status codes as
            verify_and_get_session. Only when status_code == "OK" is the new state returned.
        """

    @abstractmethod
    def revoke_session(self, session_id: str, expires_at: int, now: int | None = None) -> None:
        """Revoke a specific session until its absolute expiry plus clock allowance and delete active state."""

    @abstractmethod
    def revoke_user(
        self,
        user_id: str,
        until_timestamp: int,
        max_session_lifetime: int | None = None,
        now: int | None = None,
    ) -> None:
        """Revoke all sessions for a user created at or before until_timestamp."""

    @abstractmethod
    def is_session_revoked(
        self,
        session_id: str,
        user_id: str,
        session_created_at: int,
        now: int | None = None,
    ) -> bool:
        """Check if a session or its user has been revoked. Fail-closed on error."""

    @abstractmethod
    def clear_all(self) -> None:
        """Clear entries (for unit tests only)."""


__all__ = ["BaseRevocationStore"]
