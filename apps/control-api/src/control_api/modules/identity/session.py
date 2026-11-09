"""Session management, validation, rotation, and revocation for Enterprise Agent Platform (SPEC-AUTH-001).

Zero domain-table expansion: Signed application-session JWT with server-side authoritative
session state and revocation denylist in Redis (AU-02 / Finding R01 / TASK 02).
Supports idle timeout (30m), absolute timeout (8h), and atomic single-winner rotation.
Backed by distributed Redis store with thread-safe in-memory adapter for unit testing.
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
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


class BaseRevocationStore(ABC):
    """Abstract base class for authoritative session state and revocation storage."""

    @abstractmethod
    def create_session_state(self, state: StoredSessionState, now: int | None = None) -> None:
        """Persist initial authoritative session state in store with bounded TTL."""

    @abstractmethod
    def get_session_state(self, session_id: str, now: int | None = None) -> StoredSessionState | None:
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

    def get_session_state(self, session_id: str, now: int | None = None) -> StoredSessionState | None:
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
                return "REVOKED", None

            entry = self._sessions.get(session_id)
            if entry is None:
                return "MISSING", None
            state, _ = entry

            if (
                state.session_id != session_id
                or state.user_id != user_id
                or state.created_at != session_created_at
                or state.rotation_version < 1
            ):
                return "CORRUPT", None

            if self._is_revoked_locked(session_id, state.user_id, state.created_at, current):
                return "REVOKED", None

            if current >= state.absolute_expires_at:
                del self._sessions[session_id]
                return "ABSOLUTE_EXPIRED", None

            if current >= state.idle_expires_at:
                return "IDLE_EXPIRED", None

            if state.rotation_version != expected_version:
                return "VERSION_MISMATCH", None

            return "OK", state

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
                return "REVOKED", None

            entry = self._sessions.get(session_id)
            if entry is None:
                return "MISSING", None
            state, _ = entry

            if (
                state.session_id != session_id
                or state.user_id != user_id
                or state.created_at != session_created_at
                or state.rotation_version < 1
            ):
                return "CORRUPT", None

            if self._is_revoked_locked(session_id, state.user_id, state.created_at, current):
                return "REVOKED", None

            if current >= state.absolute_expires_at:
                del self._sessions[session_id]
                return "ABSOLUTE_EXPIRED", None

            if current >= state.idle_expires_at:
                return "IDLE_EXPIRED", None

            if state.rotation_version != expected_version:
                return "VERSION_MISMATCH", None

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
            )
            self._sessions[session_id] = (new_state, new_state.absolute_expires_at)
            return "OK", new_state

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

        expired_users = [u_id for u_id, (_, deadline) in self._revoked_users.items() if deadline < current]
        for u_id in expired_users:
            del self._revoked_users[u_id]

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._revoked_sessions.clear()
            self._revoked_users.clear()


# Alias for backward compatibility in existing tests
RevocationDenylist = InMemoryRevocationStore


def _parse_stored_session_state(raw: str | None) -> StoredSessionState | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        session_id = data.get("session_id")
        user_id = data.get("user_id")
        issuer = data.get("issuer")
        subject = data.get("subject")
        rotation_version = data.get("rotation_version")
        created_at = data.get("created_at")
        idle_expires_at = data.get("idle_expires_at")
        absolute_expires_at = data.get("absolute_expires_at")
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(user_id, str)
            or not user_id
            or not isinstance(issuer, str)
            or not isinstance(subject, str)
            or type(rotation_version) is not int
            or rotation_version < 1
            or type(created_at) is not int
            or created_at <= 0
            or type(idle_expires_at) is not int
            or idle_expires_at <= 0
            or type(absolute_expires_at) is not int
            or absolute_expires_at <= 0
        ):
            return None
        return StoredSessionState(
            session_id=session_id,
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            rotation_version=rotation_version,
            created_at=created_at,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


class RedisRevocationStore(BaseRevocationStore):
    """Distributed Redis-backed authoritative session and revocation store (AU-02 / Finding R01 / P0-02 / TASK 02).

    Keys:
      auth:session:{session_id} -> JSON(StoredSessionState) (TTL = max(1, absolute_expires_at - now))
      revocation:session:{session_id} -> expires_at (TTL = max(1, expires_at - now + clock_allowance))
      revocation:user:{user_id} -> cutoff (TTL = retention_deadline - now)
        where retention_deadline = cutoff + max_session_lifetime + clock_allowance

    Supported Redis topology:
      Single primary/sentinel or non-sharded instance where multi-key Lua scripts
      can access auth:session:{session_id}, revocation:session:{session_id}, and
      revocation:user:{user_id}. Redis Cluster is NOT claimed because session_id
      and user_id do not share a hash tag and would trigger CROSSSLOT on multi-key scripts.

    Fail-closed policy:
      If Redis is unreachable or times out, raises HTTPException(503) so that
      unverified credentials are NEVER mistakenly treated as valid or rotated.
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

    _LUA_REVOKE_SESSION = """
    local rev_key = KEYS[1]
    local sess_key = KEYS[2]
    local exp_str = ARGV[1]
    local ttl = tonumber(ARGV[2])
    redis.call('SET', rev_key, exp_str, 'EX', ttl)
    redis.call('DEL', sess_key)
    return 1
    """

    _LUA_ROTATE_SESSION = """
    local sess_key = KEYS[1]
    local rev_sess_key = KEYS[2]
    local rev_user_key = KEYS[3]

    local expected_user_id = ARGV[1]
    local expected_ver = tonumber(ARGV[2])
    local expected_created_at = tonumber(ARGV[3])
    local override_now = tonumber(ARGV[4])
    local idle_timeout = tonumber(ARGV[5])

    local now = override_now
    if not now or now <= 0 then
        local t = redis.call('TIME')
        now = tonumber(t[1])
    end

    local rev_sess_raw = redis.call('GET', rev_sess_key)
    if rev_sess_raw then
        local rev_sess_exp = tonumber(rev_sess_raw)
        if not rev_sess_exp or rev_sess_exp >= now then
            return {'REVOKED'}
        end
    end

    local rev_user_raw = redis.call('GET', rev_user_key)
    if rev_user_raw then
        local user_cutoff = tonumber(rev_user_raw)
        if not user_cutoff or expected_created_at <= user_cutoff then
            return {'REVOKED'}
        end
    end

    local raw = redis.call('GET', sess_key)
    if not raw then
        return {'MISSING'}
    end

    local ok, state = pcall(cjson.decode, raw)
    if not ok or type(state) ~= 'table' then
        return {'CORRUPT'}
    end

    local s_id = state['session_id']
    local u_id = state['user_id']
    local iss = state['issuer']
    local sub = state['subject']
    local ver = state['rotation_version']
    local c_at = state['created_at']
    local i_exp = state['idle_expires_at']
    local a_exp = state['absolute_expires_at']

    if type(s_id) ~= 'string' or type(u_id) ~= 'string' or type(iss) ~= 'string' or type(sub) ~= 'string' then
        return {'CORRUPT'}
    end
    if type(ver) ~= 'number' or ver < 1 or type(c_at) ~= 'number' or type(i_exp) ~= 'number' or type(a_exp) ~= 'number' then
        return {'CORRUPT'}
    end
    if u_id ~= expected_user_id or c_at ~= expected_created_at then
        return {'CORRUPT'}
    end

    if rev_user_raw then
        local user_cutoff = tonumber(rev_user_raw)
        if not user_cutoff or c_at <= user_cutoff then
            return {'REVOKED'}
        end
    end

    if now >= a_exp then
        redis.call('DEL', sess_key)
        return {'ABSOLUTE_EXPIRED'}
    end

    if now >= i_exp then
        return {'IDLE_EXPIRED'}
    end

    if ver ~= expected_ver then
        return {'VERSION_MISMATCH'}
    end

    local new_ver = ver + 1
    local new_idle = now + idle_timeout
    if new_idle > a_exp then
        new_idle = a_exp
    end
    local remaining_ttl = a_exp - now
    if remaining_ttl <= 0 then
        redis.call('DEL', sess_key)
        return {'ABSOLUTE_EXPIRED'}
    end

    state['rotation_version'] = new_ver
    state['idle_expires_at'] = new_idle
    local updated_raw = cjson.encode(state)
    redis.call('SET', sess_key, updated_raw, 'EX', remaining_ttl)
    return {'OK', updated_raw}
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

    def _unavailable(self, exc: Exception) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Revocation store unavailable",
        )

    def create_session_state(self, state: StoredSessionState, now: int | None = None) -> None:
        import redis

        current = now if now is not None else int(time.time())
        ttl = state.absolute_expires_at - current
        if ttl <= 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session absolute lifetime expired",
            )
        payload = json.dumps(
            {
                "session_id": state.session_id,
                "user_id": state.user_id,
                "issuer": state.issuer,
                "subject": state.subject,
                "rotation_version": state.rotation_version,
                "created_at": state.created_at,
                "idle_expires_at": state.idle_expires_at,
                "absolute_expires_at": state.absolute_expires_at,
            },
            separators=(",", ":"),
        )
        try:
            self._client.set(f"auth:session:{state.session_id}", payload, ex=ttl)
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

    def get_session_state(self, session_id: str, now: int | None = None) -> StoredSessionState | None:
        import redis

        current = now if now is not None else int(time.time())
        try:
            raw = self._client.get(f"auth:session:{session_id}")
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        state = _parse_stored_session_state(raw if isinstance(raw, str) else None)
        if state is None:
            return None
        if current >= state.absolute_expires_at:
            return None
        return state

    def verify_and_get_session(
        self,
        session_id: str,
        user_id: str,
        expected_version: int,
        session_created_at: int,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        import redis

        try:
            pipe = self._client.pipeline()
            pipe.get(f"revocation:session:{session_id}")
            pipe.get(f"revocation:user:{user_id}")
            pipe.get(f"auth:session:{session_id}")
            if now is None:
                pipe.time()
            results = pipe.execute()
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

        sess_exp_raw = results[0]
        user_until_raw = results[1]
        raw_state = results[2]
        if now is not None:
            current = now
        else:
            redis_time = results[3]
            current = int(redis_time[0]) if isinstance(redis_time, (tuple, list)) and redis_time else int(time.time())

        if sess_exp_raw is not None:
            try:
                if int(sess_exp_raw) >= current:
                    return "REVOKED", None
            except ValueError:
                return "REVOKED", None

        if user_until_raw is not None:
            try:
                if session_created_at <= int(user_until_raw):
                    return "REVOKED", None
            except ValueError:
                return "REVOKED", None

        if raw_state is None:
            return "MISSING", None

        state = _parse_stored_session_state(raw_state if isinstance(raw_state, str) else None)
        if state is None:
            return "CORRUPT", None

        if (
            state.session_id != session_id
            or state.user_id != user_id
            or state.created_at != session_created_at
        ):
            return "CORRUPT", None

        if current >= state.absolute_expires_at:
            return "ABSOLUTE_EXPIRED", None

        if current >= state.idle_expires_at:
            return "IDLE_EXPIRED", None

        if state.rotation_version != expected_version:
            return "VERSION_MISMATCH", None

        return "OK", state

    def rotate_session_state(
        self,
        session_id: str,
        user_id: str,
        expected_version: int,
        session_created_at: int,
        idle_timeout_seconds: int,
        now: int | None = None,
    ) -> tuple[str, StoredSessionState | None]:
        import redis

        override_now = now if now is not None else 0
        try:
            res: Any = self._client.eval(
                self._LUA_ROTATE_SESSION,
                3,
                f"auth:session:{session_id}",
                f"revocation:session:{session_id}",
                f"revocation:user:{user_id}",
                user_id,
                str(expected_version),
                str(session_created_at),
                str(override_now),
                str(idle_timeout_seconds),
            )
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

        if not isinstance(res, list) or not res:
            return "CORRUPT", None

        status_code = str(res[0])
        if status_code != "OK":
            return status_code, None

        raw_updated = res[1] if len(res) > 1 else None
        state = _parse_stored_session_state(raw_updated if isinstance(raw_updated, str) else None)
        if state is None:
            return "CORRUPT", None
        return "OK", state

    def revoke_session(self, session_id: str, expires_at: int, now: int | None = None) -> None:
        import redis

        current = now if now is not None else int(time.time())
        clock_allowance = settings.session_clock_skew_seconds
        ttl = max(1, (expires_at - current) + clock_allowance)
        try:
            self._client.eval(
                self._LUA_REVOKE_SESSION,
                2,
                f"revocation:session:{session_id}",
                f"auth:session:{session_id}",
                str(expires_at),
                str(ttl),
            )
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

    def revoke_user(
        self,
        user_id: str,
        until_timestamp: int,
        max_session_lifetime: int | None = None,
        now: int | None = None,
    ) -> None:
        import redis

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
        ttl = retention_deadline - current
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
            raise self._unavailable(exc) from exc

    def is_session_revoked(
        self,
        session_id: str,
        user_id: str,
        session_created_at: int,
        now: int | None = None,
    ) -> bool:
        import redis

        current = now if now is not None else int(time.time())
        try:
            pipe = self._client.pipeline()
            pipe.get(f"revocation:session:{session_id}")
            pipe.get(f"revocation:user:{user_id}")
            results = pipe.execute()
            sess_exp_raw, user_until_raw = results[0], results[1]
        except (redis.RedisError, OSError) as exc:
            # FAIL CLOSED: Do not allow unverified sessions through if Redis is down
            raise self._unavailable(exc) from exc

        if sess_exp_raw is not None:
            try:
                if int(sess_exp_raw) >= current:
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
            for pattern in ("revocation:*", "auth:session:*"):
                keys = self._client.keys(pattern)
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


def _strict_positive_int(value: Any) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


class SessionManager:
    """Manages creation, signing, verification, atomic rotation, and revocation of sessions."""

    def __init__(self, denylist: BaseRevocationStore | None = None) -> None:
        self.denylist = denylist or revocation_denylist

    def _sign_session(self, session: SessionData) -> str:
        payload = {
            "type": "session",
            "session_id": session.session_id,
            "ver": session.rotation_version,
            "sub": session.user_id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "original_iss": session.issuer,
            "original_sub": session.subject,
            "iat": session.created_at,
            "idle_exp": session.idle_expires_at,
            "exp": session.absolute_expires_at,
        }
        return sign_jwt_hs256(payload, settings.jwt_secret_key.get_secret_value())

    def _decode_and_validate_session_claims(self, token: str) -> SessionData:
        try:
            payload = verify_jwt_hs256(token, settings.jwt_secret_key.get_secret_value())
        except (ValueError, TypeError) as exc:
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

        if payload.get("iss") != settings.jwt_issuer:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token issuer",
                headers={"WWW-Authenticate": "Bearer"},
            )

        aud = payload.get("aud")
        audiences = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        if settings.jwt_audience not in audiences:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token audience",
                headers={"WWW-Authenticate": "Bearer"},
            )

        session_id = payload.get("session_id")
        user_id = payload.get("sub")
        original_iss = payload.get("original_iss")
        original_sub = payload.get("original_sub")
        ver = _strict_positive_int(payload.get("ver"))
        created_at = _strict_positive_int(payload.get("iat"))
        idle_exp = _strict_positive_int(payload.get("idle_exp"))
        abs_exp = _strict_positive_int(payload.get("exp"))

        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(user_id, str)
            or not user_id
            or not isinstance(original_iss, str)
            or not original_iss
            or not isinstance(original_sub, str)
            or not original_sub
            or ver is None
            or created_at is None
            or idle_exp is None
            or abs_exp is None
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token missing essential claims",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            UUID(user_id)
            UUID(session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session identifier claims",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        return SessionData(
            session_id=session_id,
            user_id=user_id,
            issuer=original_iss,
            subject=original_sub,
            created_at=created_at,
            idle_expires_at=idle_exp,
            absolute_expires_at=abs_exp,
            rotation_version=ver,
        )

    def _raise_for_store_status(self, status_code: str) -> None:
        if status_code == "REVOKED":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if status_code == "ABSOLUTE_EXPIRED":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session absolute lifetime expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if status_code == "IDLE_EXPIRED":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session idle timeout expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if status_code == "VERSION_MISMATCH":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session credential has been superseded by rotation",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if status_code == "CORRUPT":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session state is invalid or corrupted",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session state not found or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def create_session(
        self,
        user_id: str,
        issuer: str,
        subject: str,
        now: int | None = None,
    ) -> tuple[SessionData, str]:
        current = now if now is not None else int(time.time())
        session_id = str(uuid4())
        UUID(user_id)

        idle_timeout = settings.session_idle_timeout_seconds
        absolute_timeout = settings.session_absolute_timeout_seconds

        idle_expires_at = min(current + idle_timeout, current + absolute_timeout)
        absolute_expires_at = current + absolute_timeout
        rotation_version = 1

        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            created_at=current,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            rotation_version=rotation_version,
        )

        # Sign credential first, then persist authoritative state in store before returning.
        # If Redis/store creation fails, it raises 503 and no session or cookie is issued.
        token = self._sign_session(session)
        state = StoredSessionState(
            session_id=session.session_id,
            user_id=session.user_id,
            issuer=session.issuer,
            subject=session.subject,
            rotation_version=session.rotation_version,
            created_at=session.created_at,
            idle_expires_at=session.idle_expires_at,
            absolute_expires_at=session.absolute_expires_at,
        )
        self.denylist.create_session_state(state, now=current)
        return session, token

    def verify_session_token(self, token: str, now: int | None = None) -> SessionData:
        current = now if now is not None else int(time.time())
        token_session = self._decode_and_validate_session_claims(token)

        # Verify against authoritative store (revocation + existence + version + lifecycle)
        status_code, state = self.denylist.verify_and_get_session(
            session_id=token_session.session_id,
            user_id=token_session.user_id,
            expected_version=token_session.rotation_version,
            session_created_at=token_session.created_at,
            now=now,
        )
        if status_code != "OK" or state is None:
            self._raise_for_store_status(status_code)
            raise AssertionError("unreachable")

        if (
            state.issuer != token_session.issuer
            or state.subject != token_session.subject
            or state.absolute_expires_at != token_session.absolute_expires_at
            or state.idle_expires_at != token_session.idle_expires_at
        ):
            self._raise_for_store_status("CORRUPT")

        # Defense-in-depth local clock check when explicit clock is passed
        if token_session.is_absolute_expired(current):
            self._raise_for_store_status("ABSOLUTE_EXPIRED")
        if token_session.is_idle_expired(current):
            self._raise_for_store_status("IDLE_EXPIRED")

        return SessionData(
            session_id=state.session_id,
            user_id=state.user_id,
            issuer=state.issuer,
            subject=state.subject,
            created_at=state.created_at,
            idle_expires_at=state.idle_expires_at,
            absolute_expires_at=state.absolute_expires_at,
            rotation_version=state.rotation_version,
        )

    def rotate_session_by_token(self, token: str, now: int | None = None) -> tuple[SessionData, str]:
        """Verify presented session token and atomically rotate it to the next credential version."""
        token_session = self._decode_and_validate_session_claims(token)
        return self.refresh_session(token_session, now=now)

    def refresh_session(self, current_session: SessionData, now: int | None = None) -> tuple[SessionData, str]:
        """Atomically rotate session state in store and issue a new credential with incremented version.

        Caller-supplied SessionData never bypasses the authoritative store check at rotation time.
        """
        idle_timeout = settings.session_idle_timeout_seconds
        status_code, updated_state = self.denylist.rotate_session_state(
            session_id=current_session.session_id,
            user_id=current_session.user_id,
            expected_version=current_session.rotation_version,
            session_created_at=current_session.created_at,
            idle_timeout_seconds=idle_timeout,
            now=now,
        )
        if status_code != "OK" or updated_state is None:
            self._raise_for_store_status(status_code)
            raise AssertionError("unreachable")

        new_session = SessionData(
            session_id=updated_state.session_id,
            user_id=updated_state.user_id,
            issuer=updated_state.issuer,
            subject=updated_state.subject,
            created_at=updated_state.created_at,
            idle_expires_at=updated_state.idle_expires_at,
            absolute_expires_at=updated_state.absolute_expires_at,
            rotation_version=updated_state.rotation_version,
        )
        new_token = self._sign_session(new_session)
        return new_session, new_token

    def revoke_session(self, session: SessionData, now: int | None = None) -> None:
        """Revoke a specific session until its absolute expiry and delete active state."""
        self.denylist.revoke_session(session.session_id, session.absolute_expires_at, now=now)

    def revoke_user(
        self,
        user_id: str,
        cutoff: int | None = None,
        max_session_lifetime: int | None = None,
        now: int | None = None,
    ) -> None:
        """Revoke all active sessions for a user issued at or before cutoff timestamp."""
        current = now if now is not None else int(time.time())
        actual_cutoff = cutoff if cutoff is not None else current
        self.denylist.revoke_user(
            user_id,
            actual_cutoff,
            max_session_lifetime=max_session_lifetime,
            now=current,
        )

    def revoke_session_by_token(self, token: str, now: int | None = None) -> None:
        """Revoke a session identified by a signed JWT session token (including superseded versions).

        - If token signature/claims are invalid or malformed: no-op (idempotent local cleanup).
        - If token is already absolute-expired (now >= exp): no-op (already unusable everywhere).
        - If token is already marked revoked: ensures active state is removed (idempotent).
        - If token is active, idle-expired, or an older rotated version of the session:
          writes revocation to denylist until absolute expiry and removes active session state.
        - If Redis fails: raises HTTPException(503).
        """
        current = now if now is not None else int(time.time())
        try:
            payload = verify_jwt_hs256(token, settings.jwt_secret_key.get_secret_value())
        except (ValueError, TypeError):
            return

        if payload.get("type") != "session":
            return
        if payload.get("iss") != settings.jwt_issuer:
            return
        aud = payload.get("aud")
        audiences = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        if settings.jwt_audience not in audiences:
            return

        session_id = payload.get("session_id")
        user_id = payload.get("sub")
        abs_exp = _strict_positive_int(payload.get("exp"))
        created_at = _strict_positive_int(payload.get("iat"))

        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(user_id, str)
            or not user_id
            or abs_exp is None
            or created_at is None
        ):
            return

        try:
            UUID(session_id)
            UUID(user_id)
        except ValueError:
            return

        if current >= abs_exp:
            return

        # Check existing revocation status (raises 503 if Redis is unavailable)
        if self.denylist.is_session_revoked(session_id, user_id, created_at, now=current):
            return

        # Write revocation marker and delete active session state (raises 503 if Redis is unavailable)
        self.denylist.revoke_session(session_id, abs_exp, now=current)


session_manager = SessionManager()


def set_session_cookie(
    response: Response,
    session_token: str,
    *,
    max_age: int | None = None,
    absolute_expires_at: int | None = None,
    now: int | None = None,
) -> None:
    """Set hardened HTTP-Only session cookie on the response.

    Max-Age is bounded by the remaining absolute lifetime of the session so that
    refresh never extends the browser cookie by another full absolute lifetime.
    """
    if max_age is not None:
        effective_max_age = max(0, min(max_age, settings.session_absolute_timeout_seconds))
    elif absolute_expires_at is not None:
        current = now if now is not None else int(time.time())
        remaining = max(0, absolute_expires_at - current)
        effective_max_age = min(remaining, settings.session_absolute_timeout_seconds)
    else:
        effective_max_age = settings.session_absolute_timeout_seconds

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=effective_max_age,
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
