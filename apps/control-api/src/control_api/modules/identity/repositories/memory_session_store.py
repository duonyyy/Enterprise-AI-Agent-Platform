"""Thread-safe in-memory session and revocation store for unit testing (SPEC-AUTH-001).

Implements BaseRevocationStore for isolated, deterministic in-memory execution
without requiring an external Redis infrastructure.
"""

from __future__ import annotations

import threading
import time

from control_api.core.settings import settings
from control_api.modules.identity.models.session import (
    SessionVerificationStatus,
    StoredSessionState,
)
from control_api.modules.identity.repositories.session_base import BaseRevocationStore


class InMemoryRevocationStore(BaseRevocationStore):
    """Thread-safe in-memory authoritative session and revocation store for unit tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[StoredSessionState, int]] = {}
        self._revoked_sessions: dict[str, int] = {}
        # Stores user_id -> (cutoff, retention_deadline)
        self._revoked_users: dict[str, tuple[int, int]] = {}

    def create_session_state(self, state: StoredSessionState, now: int | None = None) -> None:
        current = now if now is not None else int(time.time())
        if state.absolute_expires_at <= current:
            return
        with self._lock:
            self._purge_expired_locked(current)
            self._sessions[state.session_id] = (state, state.absolute_expires_at)

    def get_session_state(
        self, session_id: str, now: int | None = None
    ) -> StoredSessionState | None:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_expired_locked(current)
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            state, exp = entry
            if current >= exp:
                del self._sessions[session_id]
                return None
            return state

    def _is_revoked_locked(
        self,
        session_id: str,
        user_id: str,
        session_created_at: int,
        current: int,
    ) -> bool:
        exp = self._revoked_sessions.get(session_id)
        if exp is not None:
            if exp >= current:
                return True
            del self._revoked_sessions[session_id]

        user_entry = self._revoked_users.get(user_id)
        if user_entry is not None:
            cutoff, deadline = user_entry
            if deadline >= current:
                if session_created_at <= cutoff:
                    return True
            else:
                del self._revoked_users[user_id]

        return False

    def verify_and_get_session(
        self,
        session_id: str,
        user_id: str,
        expected_version: int,
        session_created_at: int,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_expired_locked(current)
            if self._is_revoked_locked(session_id, user_id, session_created_at, current):
                return SessionVerificationStatus.REVOKED, None

            entry = self._sessions.get(session_id)
            if entry is None:
                return SessionVerificationStatus.MISSING, None
            state, _ = entry

            if (
                state.session_id != session_id
                or state.user_id != user_id
                or state.created_at != session_created_at
                or state.rotation_version < 1
            ):
                return SessionVerificationStatus.CORRUPT, None

            if self._is_revoked_locked(session_id, state.user_id, state.created_at, current):
                return SessionVerificationStatus.REVOKED, None

            if current >= state.absolute_expires_at:
                del self._sessions[session_id]
                return SessionVerificationStatus.ABSOLUTE_EXPIRED, None

            if current >= state.idle_expires_at:
                return SessionVerificationStatus.IDLE_EXPIRED, None

            if state.rotation_version != expected_version:
                return SessionVerificationStatus.VERSION_MISMATCH, None

            return SessionVerificationStatus.OK, state

    def verify_parent_session(
        self,
        session_id: str,
        user_id: str,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_expired_locked(current)
            exp = self._revoked_sessions.get(session_id)
            if exp is not None:
                if exp >= current:
                    return SessionVerificationStatus.REVOKED, None
                del self._revoked_sessions[session_id]

            entry = self._sessions.get(session_id)
            if entry is None:
                return SessionVerificationStatus.MISSING, None
            state, _ = entry

            if (
                state.session_id != session_id
                or state.user_id != user_id
                or state.rotation_version < 1
            ):
                return SessionVerificationStatus.CORRUPT, None

            if self._is_revoked_locked(session_id, state.user_id, state.created_at, current):
                return SessionVerificationStatus.REVOKED, None

            if current >= state.absolute_expires_at:
                del self._sessions[session_id]
                return SessionVerificationStatus.ABSOLUTE_EXPIRED, None

            if current >= state.idle_expires_at:
                return SessionVerificationStatus.IDLE_EXPIRED, None

            return SessionVerificationStatus.OK, state

    def rotate_session_state(
        self,
        session_id: str,
        user_id: str,
        expected_version: int,
        session_created_at: int,
        idle_timeout_seconds: int,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_expired_locked(current)
            if self._is_revoked_locked(session_id, user_id, session_created_at, current):
                return SessionVerificationStatus.REVOKED, None

            entry = self._sessions.get(session_id)
            if entry is None:
                return SessionVerificationStatus.MISSING, None
            state, _ = entry

            if (
                state.session_id != session_id
                or state.user_id != user_id
                or state.created_at != session_created_at
                or state.rotation_version < 1
            ):
                return SessionVerificationStatus.CORRUPT, None

            if self._is_revoked_locked(session_id, state.user_id, state.created_at, current):
                return SessionVerificationStatus.REVOKED, None

            if current >= state.absolute_expires_at:
                del self._sessions[session_id]
                return SessionVerificationStatus.ABSOLUTE_EXPIRED, None

            if current >= state.idle_expires_at:
                return SessionVerificationStatus.IDLE_EXPIRED, None

            if state.rotation_version != expected_version:
                return SessionVerificationStatus.VERSION_MISMATCH, None

            new_idle_exp = min(current + idle_timeout_seconds, state.absolute_expires_at)
            new_state = StoredSessionState(
                session_id=state.session_id,
                user_id=state.user_id,
                issuer=state.issuer,
                subject=state.subject,
                rotation_version=state.rotation_version + 1,
                created_at=state.created_at,
                idle_expires_at=new_idle_exp,
                absolute_expires_at=state.absolute_expires_at,
                identity_evidence=state.identity_evidence,
            )
            self._sessions[session_id] = (new_state, new_state.absolute_expires_at)
            return SessionVerificationStatus.OK, new_state

    def revoke_session(self, session_id: str, expires_at: int, now: int | None = None) -> None:
        current = now if now is not None else int(time.time())
        clock_allowance = settings.session_clock_skew_seconds
        deadline = expires_at + clock_allowance
        with self._lock:
            self._revoked_sessions[session_id] = deadline
            self._sessions.pop(session_id, None)
            self._purge_expired_locked(current)

    def revoke_user(
        self,
        user_id: str,
        until_timestamp: int,
        max_session_lifetime: int | None = None,
        now: int | None = None,
    ) -> None:
        current = now if now is not None else int(time.time())
        clock_allowance = settings.session_clock_skew_seconds
        if until_timestamp <= 0:
            raise ValueError("Revocation cutoff must be a positive unix timestamp")
        if until_timestamp > current + clock_allowance:
            raise ValueError("Revocation cutoff cannot be in the future beyond clock allowance")

        effective_lifetime = max_session_lifetime or max(
            settings.session_absolute_timeout_seconds,
            settings.session_max_accepted_absolute_lifetime_seconds,
        )
        retention_deadline = until_timestamp + effective_lifetime + clock_allowance

        if retention_deadline <= current:
            return

        with self._lock:
            if user_id in self._revoked_users:
                curr_cutoff, curr_deadline = self._revoked_users[user_id]
                new_cutoff = max(curr_cutoff, until_timestamp)
                new_deadline = max(curr_deadline, retention_deadline)
                self._revoked_users[user_id] = (new_cutoff, new_deadline)
            else:
                self._revoked_users[user_id] = (until_timestamp, retention_deadline)
            self._purge_expired_locked(current)

    def is_session_revoked(
        self,
        session_id: str,
        user_id: str,
        session_created_at: int,
        now: int | None = None,
    ) -> bool:
        current = now if now is not None else int(time.time())
        with self._lock:
            return self._is_revoked_locked(session_id, user_id, session_created_at, current)

    def _purge_expired_locked(self, now: int | None = None) -> None:
        current = now if now is not None else int(time.time())
        expired_active = [s_id for s_id, (_, exp) in self._sessions.items() if current >= exp]
        for s_id in expired_active:
            del self._sessions[s_id]

        expired_sessions = [s_id for s_id, exp in self._revoked_sessions.items() if exp < current]
        for s_id in expired_sessions:
            del self._revoked_sessions[s_id]

        expired_users = [
            u_id for u_id, (_, deadline) in self._revoked_users.items() if deadline < current
        ]
        for u_id in expired_users:
            del self._revoked_users[u_id]

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._revoked_sessions.clear()
            self._revoked_users.clear()


# Alias for backward compatibility in existing tests
RevocationDenylist = InMemoryRevocationStore

__all__ = ["InMemoryRevocationStore", "RevocationDenylist"]
