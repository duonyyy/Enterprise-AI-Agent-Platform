"""Session management, validation, and revocation for Enterprise Agent Platform (SPEC-AUTH-001).

Zero domain-table expansion: Encrypted/Signed stateless session token with server-side revocation denylist.
Supports idle timeout (30m) and absolute timeout (8h).
Backed by distributed Redis store (AU-02 / Finding R01) with thread-safe in-memory adapter for unit testing.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID, uuid4

from control_api.core.settings import settings
from control_api.modules.identity.crypto import sign_jwt_hs256, verify_jwt_hs256
from fastapi import HTTPException, Response, status


@dataclass(frozen=True, slots=True)
class SessionData:
    session_id: str
    user_id: str
    issuer: str
    subject: str
    created_at: int
    idle_expires_at: int
    absolute_expires_at: int

    def is_idle_expired(self, now: int | None = None) -> bool:
        current = now if now is not None else int(time.time())
        return current > self.idle_expires_at

    def is_absolute_expired(self, now: int | None = None) -> bool:
        current = now if now is not None else int(time.time())
        return current > self.absolute_expires_at


class BaseRevocationStore(ABC):
    """Abstract base class for session revocation storage."""

    @abstractmethod
    def revoke_session(self, session_id: str, expires_at: int) -> None:
        """Revoke a specific session until its absolute expiry plus clock allowance."""

    @abstractmethod
    def revoke_user(self, user_id: str, until_timestamp: int, max_session_lifetime: int | None = None) -> None:
        """Revoke all sessions for a user created at or before until_timestamp."""

    @abstractmethod
    def is_session_revoked(self, session_id: str, user_id: str, session_created_at: int) -> bool:
        """Check if a session or its user has been revoked. Fail-closed on error."""

    @abstractmethod
    def clear_all(self) -> None:
        """Clear entries (for tests)."""


class InMemoryRevocationStore(BaseRevocationStore):
    """Thread-safe in-memory revocation store for unit tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revoked_sessions: dict[str, int] = {}
        # Stores user_id -> (cutoff, retention_deadline)
        self._revoked_users: dict[str, tuple[int, int]] = {}

    def revoke_session(self, session_id: str, expires_at: int) -> None:
        clock_allowance = settings.session_clock_skew_seconds
        deadline = expires_at + clock_allowance
        with self._lock:
            self._revoked_sessions[session_id] = deadline
            self._purge_expired_locked()

    def revoke_user(self, user_id: str, until_timestamp: int, max_session_lifetime: int | None = None) -> None:
        now = int(time.time())
        clock_allowance = settings.session_clock_skew_seconds
        if until_timestamp <= 0:
            raise ValueError("Revocation cutoff must be a positive unix timestamp")
        if until_timestamp > now + clock_allowance:
            raise ValueError("Revocation cutoff cannot be in the future beyond clock allowance")

        effective_lifetime = max_session_lifetime or max(
            settings.session_absolute_timeout_seconds,
            settings.session_max_accepted_absolute_lifetime_seconds,
        )
        retention_deadline = until_timestamp + effective_lifetime + clock_allowance

        if retention_deadline <= now:
            return

        with self._lock:
            if user_id in self._revoked_users:
                curr_cutoff, curr_deadline = self._revoked_users[user_id]
                new_cutoff = max(curr_cutoff, until_timestamp)
                new_deadline = max(curr_deadline, retention_deadline)
                self._revoked_users[user_id] = (new_cutoff, new_deadline)
            else:
                self._revoked_users[user_id] = (until_timestamp, retention_deadline)
            self._purge_expired_locked()

    def is_session_revoked(self, session_id: str, user_id: str, session_created_at: int) -> bool:
        now = int(time.time())
        with self._lock:
            exp = self._revoked_sessions.get(session_id)
            if exp is not None:
                if exp >= now:
                    return True
                else:
                    del self._revoked_sessions[session_id]

            user_entry = self._revoked_users.get(user_id)
            if user_entry is not None:
                cutoff, deadline = user_entry
                if deadline >= now:
                    if session_created_at <= cutoff:
                        return True
                else:
                    del self._revoked_users[user_id]

        return False

    def _purge_expired_locked(self) -> None:
        now = int(time.time())
        expired_sessions = [s_id for s_id, exp in self._revoked_sessions.items() if exp < now]
        for s_id in expired_sessions:
            del self._revoked_sessions[s_id]

        expired_users = [u_id for u_id, (_, deadline) in self._revoked_users.items() if deadline < now]
        for u_id in expired_users:
            del self._revoked_users[u_id]

    def clear_all(self) -> None:
        with self._lock:
            self._revoked_sessions.clear()
            self._revoked_users.clear()


# Alias for backward compatibility in existing tests
RevocationDenylist = InMemoryRevocationStore


class RedisRevocationStore(BaseRevocationStore):
    """Distributed Redis-backed revocation store (AU-02 / Finding R01 / P0-02).

    Keys:
      revocation:session:{session_id} -> expires_at (TTL = max(1, expires_at - now + clock_allowance))
      revocation:user:{user_id} -> cutoff (TTL = retention_deadline - now)
        where retention_deadline = cutoff + max_session_lifetime + clock_allowance

    Fail-closed policy:
      If Redis is unreachable or times out, raises HTTPException(503) so that
      unverified credentials are NEVER mistakenly treated as valid.
    """

    _LUA_REVOKE_USER = """
    local key = KEYS[1]
    local new_cutoff = tonumber(ARGV[1])
    local new_ttl = tonumber(ARGV[2])

    local current_cutoff_str = redis.call('GET', key)
    if current_cutoff_str then
        local current_cutoff = tonumber(current_cutoff_str)
        if new_cutoff > current_cutoff then
            redis.call('SET', key, tostring(new_cutoff), 'EX', new_ttl)
            return 1
        else
            local current_ttl = redis.call('TTL', key)
            if new_ttl > current_ttl then
                redis.call('EXPIRE', key, new_ttl)
            end
            return 0
        end
    else
        redis.call('SET', key, tostring(new_cutoff), 'EX', new_ttl)
        return 1
    end
    """

    def __init__(self, redis_url: str | None = None) -> None:
        import redis

        self.redis_url = redis_url or settings.redis_url
        self._client = redis.Redis.from_url(
            self.redis_url,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            decode_responses=True,
        )

    def revoke_session(self, session_id: str, expires_at: int) -> None:
        import redis

        now = int(time.time())
        clock_allowance = settings.session_clock_skew_seconds
        ttl = max(1, (expires_at - now) + clock_allowance)
        try:
            self._client.set(f"revocation:session:{session_id}", str(expires_at), ex=ttl)
        except (redis.RedisError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Revocation store unavailable",
            ) from exc

    def revoke_user(self, user_id: str, until_timestamp: int, max_session_lifetime: int | None = None) -> None:
        import redis

        now = int(time.time())
        clock_allowance = settings.session_clock_skew_seconds
        if until_timestamp <= 0:
            raise ValueError("Revocation cutoff must be a positive unix timestamp")
        if until_timestamp > now + clock_allowance:
            raise ValueError("Revocation cutoff cannot be in the future beyond clock allowance")

        effective_lifetime = max_session_lifetime or max(
            settings.session_absolute_timeout_seconds,
            settings.session_max_accepted_absolute_lifetime_seconds,
        )
        retention_deadline = until_timestamp + effective_lifetime + clock_allowance
        ttl = retention_deadline - now
        if ttl <= 0:
            return

        try:
            self._client.eval(
                self._LUA_REVOKE_USER,
                1,
                f"revocation:user:{user_id}",
                str(until_timestamp),
                str(ttl),
            )
        except (redis.RedisError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Revocation store unavailable",
            ) from exc

    def is_session_revoked(self, session_id: str, user_id: str, session_created_at: int) -> bool:
        import redis

        now = int(time.time())
        try:
            pipe = self._client.pipeline()
            pipe.get(f"revocation:session:{session_id}")
            pipe.get(f"revocation:user:{user_id}")
            results = pipe.execute()
            sess_exp_raw, user_until_raw = results[0], results[1]
        except (redis.RedisError, OSError) as exc:
            # FAIL CLOSED: Do not allow unverified sessions through if Redis is down
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Revocation store unavailable",
            ) from exc

        if sess_exp_raw is not None:
            try:
                if int(sess_exp_raw) >= now:
                    return True
            except ValueError:
                return True

        if user_until_raw is not None:
            try:
                if session_created_at <= int(user_until_raw):
                    return True
            except ValueError:
                return True

        return False

    def clear_all(self) -> None:
        import redis

        try:
            keys = self._client.keys("revocation:*")
            if keys:
                self._client.delete(*keys)
        except (redis.RedisError, OSError):
            pass


def create_revocation_store() -> BaseRevocationStore:
    if settings.auth_store_type == "redis":
        return RedisRevocationStore(settings.redis_url)
    return InMemoryRevocationStore()


# Global revocation store singleton
revocation_denylist: BaseRevocationStore = create_revocation_store()


class SessionManager:
    """Manages creation, signing, verification, rotation, and revocation of sessions."""

    def __init__(self, denylist: BaseRevocationStore | None = None) -> None:
        self.denylist = denylist or revocation_denylist

    def create_session(
        self,
        user_id: str,
        issuer: str,
        subject: str,
        now: int | None = None,
    ) -> tuple[SessionData, str]:
        current = now if now is not None else int(time.time())
        session_id = str(uuid4())
        # Validate UUID format
        UUID(user_id)

        idle_timeout = settings.session_idle_timeout_seconds
        absolute_timeout = settings.session_absolute_timeout_seconds

        idle_expires_at = current + idle_timeout
        absolute_expires_at = current + absolute_timeout

        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            created_at=current,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
        )

        payload = {
            "type": "session",
            "session_id": session.session_id,
            "sub": session.user_id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "original_iss": session.issuer,
            "original_sub": session.subject,
            "iat": session.created_at,
            "idle_exp": session.idle_expires_at,
            "exp": session.absolute_expires_at,
        }

        token = sign_jwt_hs256(payload, settings.jwt_secret_key.get_secret_value())
        return session, token

    def verify_session_token(self, token: str, now: int | None = None) -> SessionData:
        current = now if now is not None else int(time.time())
        try:
            payload = verify_jwt_hs256(token, settings.jwt_secret_key.get_secret_value())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token signature or format",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        if payload.get("type") != "session":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is not an application session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        session_id = str(payload.get("session_id", ""))
        user_id = str(payload.get("sub", ""))
        original_iss = str(payload.get("original_iss", ""))
        original_sub = str(payload.get("original_sub", ""))
        created_at = int(payload.get("iat", 0))
        idle_exp = int(payload.get("idle_exp", 0))
        abs_exp = int(payload.get("exp", 0))

        if not session_id or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token missing essential claims",
            )

        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            issuer=original_iss,
            subject=original_sub,
            created_at=created_at,
            idle_expires_at=idle_exp,
            absolute_expires_at=abs_exp,
        )

        # 1. Check revocation store (fail-closed if Redis raises 503)
        if self.denylist.is_session_revoked(session.session_id, session.user_id, session.created_at):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Check absolute expiration
        if session.is_absolute_expired(current):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session absolute lifetime expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 3. Check idle expiration (Uniform verification for Finding R04)
        if session.is_idle_expired(current):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session idle timeout expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return session

    def refresh_session(self, current_session: SessionData, now: int | None = None) -> tuple[SessionData, str]:
        current = now if now is not None else int(time.time())

        # Cannot refresh an already absolute-expired session
        if current >= current_session.absolute_expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session absolute lifetime expired, re-authentication required",
            )

        # Cannot refresh a revoked session
        if self.denylist.is_session_revoked(
            current_session.session_id, current_session.user_id, current_session.created_at
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cannot refresh a revoked session",
            )

        idle_timeout = settings.session_idle_timeout_seconds
        new_idle_exp = min(current + idle_timeout, current_session.absolute_expires_at)

        new_session = SessionData(
            session_id=current_session.session_id,
            user_id=current_session.user_id,
            issuer=current_session.issuer,
            subject=current_session.subject,
            created_at=current_session.created_at,
            idle_expires_at=new_idle_exp,
            absolute_expires_at=current_session.absolute_expires_at,
        )

        payload = {
            "type": "session",
            "session_id": new_session.session_id,
            "sub": new_session.user_id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "original_iss": new_session.issuer,
            "original_sub": new_session.subject,
            "iat": new_session.created_at,
            "idle_exp": new_session.idle_expires_at,
            "exp": new_session.absolute_expires_at,
        }

        token = sign_jwt_hs256(payload, settings.jwt_secret_key.get_secret_value())
        return new_session, token

    def revoke_session(self, session: SessionData) -> None:
        """Revoke a specific session until its absolute expiry."""
        self.denylist.revoke_session(session.session_id, session.absolute_expires_at)

    def revoke_user(
        self,
        user_id: str,
        cutoff: int | None = None,
        max_session_lifetime: int | None = None,
    ) -> None:
        """Revoke all active sessions for a user issued at or before cutoff timestamp."""
        now = int(time.time())
        actual_cutoff = cutoff if cutoff is not None else now
        self.denylist.revoke_user(user_id, actual_cutoff, max_session_lifetime=max_session_lifetime)

    def revoke_session_by_token(self, token: str) -> None:
        """Revoke a session identified by a signed JWT session token.

        - If token signature/claims are invalid or malformed: no-op (idempotent local cleanup).
        - If token is already absolute-expired: no-op (already unusable everywhere).
        - If token is already marked revoked: no-op (idempotent).
        - If token is active or idle-expired: writes revocation to denylist until absolute expiry.
        - If Redis fails: raises HTTPException(503).
        """
        try:
            payload = verify_jwt_hs256(token, settings.jwt_secret_key.get_secret_value())
        except ValueError:
            return

        if payload.get("type") != "session":
            return

        session_id = str(payload.get("session_id", ""))
        user_id = str(payload.get("sub", ""))
        abs_exp = int(payload.get("exp", 0))
        created_at = int(payload.get("iat", 0))

        if not session_id or not user_id or not abs_exp:
            return

        now = int(time.time())
        if now > abs_exp:
            return

        # Check existing revocation status (raises 503 if Redis is unavailable)
        if self.denylist.is_session_revoked(session_id, user_id, created_at):
            return

        # Write revocation marker (raises 503 if Redis is unavailable)
        self.denylist.revoke_session(session_id, abs_exp)


session_manager = SessionManager()


def set_session_cookie(response: Response, session_token: str) -> None:
    """Set hardened HTTP-Only session cookie on the response."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=settings.session_absolute_timeout_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,  # type: ignore[arg-type]
        path=settings.session_cookie_path,
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie from the client."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=settings.session_cookie_path,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,  # type: ignore[arg-type]
    )


def get_clear_session_cookie_headers() -> dict[str, str]:
    """Generate Set-Cookie deletion headers for clearing the session cookie on exception responses."""
    temp_resp = Response()
    clear_session_cookie(temp_resp)
    cookie_val = temp_resp.headers.get("set-cookie")
    return {"Set-Cookie": cookie_val} if cookie_val else {}
