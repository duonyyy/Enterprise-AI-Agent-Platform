"""Authoritative session state and revocation storage in Redis / Memory (SPEC-AUTH-001).

Guarantees atomic session rotation, session revocation, and user-level cutoff revocation.
Backed by distributed Redis store with Lua scripts, or thread-safe in-memory adapter for testing.
"""

from __future__ import annotations

import json
import time
from typing import Any

from control_api.core.settings import settings
from control_api.modules.identity.models.errors import (
    RevocationStoreUnavailableError,
    SessionExpiredError,
)
from control_api.modules.identity.models.session import (
    SessionVerificationStatus,
    StoredSessionState,
)
from control_api.modules.identity.repositories.memory_session_store import (
    InMemoryRevocationStore,
    RevocationDenylist,
)
from control_api.modules.identity.repositories.session_base import BaseRevocationStore


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

    Fail-closed policy:
      If Redis is unreachable or times out, raises RevocationStoreUnavailableError so that
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

    def _unavailable(self, exc: Exception) -> RevocationStoreUnavailableError:
        return RevocationStoreUnavailableError("Revocation store unavailable")

    def create_session_state(self, state: StoredSessionState, now: int | None = None) -> None:
        import redis

        current = now if now is not None else int(time.time())
        ttl = state.absolute_expires_at - current
        if ttl <= 0:
            raise SessionExpiredError("Session absolute lifetime expired")
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

    def get_session_state(
        self, session_id: str, now: int | None = None
    ) -> StoredSessionState | None:
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
            current = (
                int(redis_time[0])
                if isinstance(redis_time, (tuple, list)) and redis_time
                else int(time.time())
            )

        if sess_exp_raw is not None:
            try:
                if int(sess_exp_raw) >= current:
                    return SessionVerificationStatus.REVOKED, None
            except ValueError:
                return SessionVerificationStatus.REVOKED, None

        if user_until_raw is not None:
            try:
                if session_created_at <= int(user_until_raw):
                    return SessionVerificationStatus.REVOKED, None
            except ValueError:
                return SessionVerificationStatus.REVOKED, None

        if raw_state is None:
            return SessionVerificationStatus.MISSING, None

        state = _parse_stored_session_state(raw_state if isinstance(raw_state, str) else None)
        if state is None:
            return SessionVerificationStatus.CORRUPT, None

        if (
            state.session_id != session_id
            or state.user_id != user_id
            or state.created_at != session_created_at
        ):
            return SessionVerificationStatus.CORRUPT, None

        if current >= state.absolute_expires_at:
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
            current = (
                int(redis_time[0])
                if isinstance(redis_time, (tuple, list)) and redis_time
                else int(time.time())
            )

        if sess_exp_raw is not None:
            try:
                if int(sess_exp_raw) >= current:
                    return SessionVerificationStatus.REVOKED, None
            except ValueError:
                return SessionVerificationStatus.REVOKED, None

        if raw_state is None:
            return SessionVerificationStatus.MISSING, None

        state = _parse_stored_session_state(raw_state if isinstance(raw_state, str) else None)
        if state is None:
            return SessionVerificationStatus.CORRUPT, None

        if state.session_id != session_id or state.user_id != user_id:
            return SessionVerificationStatus.CORRUPT, None

        if user_until_raw is not None:
            try:
                if state.created_at <= int(user_until_raw):
                    return SessionVerificationStatus.REVOKED, None
            except ValueError:
                return SessionVerificationStatus.REVOKED, None

        if current >= state.absolute_expires_at:
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
            return SessionVerificationStatus.CORRUPT, None

        status_code = str(res[0])
        if status_code != SessionVerificationStatus.OK:
            return status_code, None

        raw_updated = res[1] if len(res) > 1 else None
        state = _parse_stored_session_state(raw_updated if isinstance(raw_updated, str) else None)
        if state is None:
            return SessionVerificationStatus.CORRUPT, None
        return SessionVerificationStatus.OK, state

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
    store_type = settings.auth_store_type.lower()
    if store_type == "redis":
        return RedisRevocationStore(settings.redis_url)
    elif store_type == "memory":
        return InMemoryRevocationStore()
    else:
        raise ValueError(f"Unsupported auth_store_type: {settings.auth_store_type}")


revocation_denylist: BaseRevocationStore = create_revocation_store()

__all__ = [
    "BaseRevocationStore",
    "InMemoryRevocationStore",
    "RedisRevocationStore",
    "RevocationDenylist",
    "create_revocation_store",
    "revocation_denylist",
]
