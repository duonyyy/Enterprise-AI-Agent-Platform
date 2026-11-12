"""Encrypted browser-bound handoff storage and atomic rate limiting (ADR-020 / Step 03-C)."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from control_api.core.settings import settings
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.workspaces.models.errors import InvitationConfigurationError
from cryptography.fernet import Fernet, InvalidToken
from platform_common.invitations import hash_invitation_proof

_BROWSER_COOKIE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def hash_browser_binding_token(browser_token: str) -> str:
    """Compute canonical SHA-256 hex digest of an opaque browser handoff cookie."""
    return hashlib.sha256(browser_token.encode("utf-8")).hexdigest()


def is_valid_browser_binding_token(value: str | None) -> bool:
    """Return True if cookie value matches minimum 256-bit urlsafe entropy shape."""
    return isinstance(value, str) and bool(_BROWSER_COOKIE_RE.match(value))


def generate_browser_binding_token() -> str:
    """Generate a high-entropy 256-bit urlsafe browser binding secret."""
    return secrets.token_urlsafe(32)


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


class InvitationHandoffProtector:
    """Symmetric authenticated Fernet protector dedicated solely to pre-auth invitation handoffs."""

    def __init__(self, *, fernet_key: str, key_id: str = "v1") -> None:
        if not key_id or not _KEY_ID_RE.match(key_id):
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff key_id is invalid",
            )
        cleaned = fernet_key.strip()
        if not cleaned or "replace-with" in cleaned.lower():
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff encryption key is missing or placeholder",
            )
        try:
            self._fernet = Fernet(cleaned.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff encryption key must be a valid Fernet key",
            ) from exc
        self.key_id = key_id
        self._raw_key = cleaned

    def encrypt_handoff(
        self,
        *,
        invitation_id: str,
        raw_proof: str,
        browser_binding_digest: str,
        issued_at: int,
        expires_at: int,
    ) -> str:
        """Encrypt bound invitation handoff payload; raw proof is hashed immediately."""
        token_hash = hash_invitation_proof(raw_proof)
        payload = {
            "v": 1,
            "invitation_id": invitation_id,
            "token_hash": token_hash,
            "browser_binding_digest": browser_binding_digest,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self._fernet.encrypt(serialized).decode("ascii")

    def decrypt_handoff(
        self,
        *,
        ciphertext: str,
        expected_invitation_id: str,
        expected_browser_digest: str,
        now: int,
    ) -> DecryptedHandoffPayload | None:
        """Verify ciphertext integrity, expiry, and browser/invitation binding."""
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("ascii"))
            data = json.loads(plaintext.decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            return None

        if not isinstance(data, dict) or data.get("v") != 1:
            return None

        inv_id = data.get("invitation_id")
        token_hash = data.get("token_hash")
        digest = data.get("browser_binding_digest")
        issued_at = data.get("issued_at")
        expires_at = data.get("expires_at")
        if (
            not isinstance(inv_id, str)
            or not isinstance(token_hash, str)
            or len(token_hash) != 64
            or not isinstance(digest, str)
            or type(issued_at) is not int
            or type(expires_at) is not int
        ):
            return None

        if not hmac.compare_digest(inv_id, expected_invitation_id):
            return None
        if not hmac.compare_digest(digest, expected_browser_digest):
            return None
        if now >= expires_at:
            return None

        return DecryptedHandoffPayload(
            invitation_id=inv_id,
            token_hash=token_hash,
            browser_binding_digest=digest,
            issued_at=issued_at,
            expires_at=expires_at,
        )


class BaseInvitationHandoffStore(ABC):
    """Abstract store for encrypted invitation handoffs and rate limits."""

    @abstractmethod
    def enforce_handoff_rate_limit(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None:
        """Increment browser & IP counters atomically; return Retry-After seconds if exceeded."""

    @abstractmethod
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
    ) -> HandoffPersistResult:
        """Atomically persist handoff or preserve existing active deadline / consumed receipt."""

    def save_encrypted_handoff(
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
    ) -> HandoffPersistResult:
        """Compatibility alias for save_or_get_encrypted_handoff."""
        return self.save_or_get_encrypted_handoff(
            invitation_id=invitation_id,
            browser_binding_digest=browser_binding_digest,
            token_hash=token_hash,
            key_id=key_id,
            ciphertext=ciphertext,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    @abstractmethod
    def get_handoff_entry(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        """Fetch raw envelope (encrypted handoff OR consumed receipt) without consuming it."""

    @abstractmethod
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
    ) -> None:
        """Consume only the exact verified handoff after commit, without extending its TTL."""

    @abstractmethod
    def clear_all(self) -> None:
        """Clear all stored handoff entries and rate counters (testing only)."""


def _handoff_key(invitation_id: str, browser_binding_digest: str) -> str:
    return f"invite:handoff:{invitation_id}:{browser_binding_digest}"


class InMemoryInvitationHandoffStore(BaseInvitationHandoffStore):
    """Thread-safe in-memory handoff store for isolated unit tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[dict[str, Any], int]] = {}
        self._rates: dict[str, tuple[int, int]] = {}

    def _purge_locked(self, current: int) -> None:
        expired_entries = [k for k, (_, exp) in self._entries.items() if current >= exp]
        for k in expired_entries:
            del self._entries[k]
        expired_rates = [k for k, (_, exp) in self._rates.items() if current >= exp]
        for k in expired_rates:
            del self._rates[k]

    def _increment_counter_locked(
        self, key: str, *, limit: int, window_seconds: int, current: int
    ) -> int | None:
        entry = self._rates.get(key)
        if entry is None or current >= entry[1]:
            count = 1
            reset_at = current + window_seconds
        else:
            count = entry[0] + 1
            reset_at = entry[1]
        self._rates[key] = (count, reset_at)
        if count > limit:
            return max(1, reset_at - current)
        return None

    def enforce_handoff_rate_limit(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_locked(current)
            b_retry = self._increment_counter_locked(
                f"invite:handoff:rl:browser:{browser_binding_digest}",
                limit=browser_limit,
                window_seconds=window_seconds,
                current=current,
            )
            ip_retry = self._increment_counter_locked(
                f"invite:handoff:rl:ip:{client_ip}",
                limit=ip_limit,
                window_seconds=window_seconds,
                current=current,
            )
            retries = [r for r in (b_retry, ip_retry) if r is not None]
            return max(retries) if retries else None

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
    ) -> HandoffPersistResult:
        current = now if now is not None else int(time.time())
        key = _handoff_key(invitation_id, browser_binding_digest)
        with self._lock:
            self._purge_locked(current)
            existing = self._entries.get(key)
            if existing is not None:
                entry_dict, entry_exp = existing
                kind = entry_dict.get("kind")
                existing_hash = str(entry_dict.get("token_hash") or "")
                remaining = max(0, entry_exp - current)
                if kind == "consumed_receipt" and remaining > 0:
                    if hmac.compare_digest(existing_hash, token_hash):
                        return HandoffPersistResult(
                            outcome="preserved_consumed",
                            expires_at=entry_exp,
                            remaining_ttl_seconds=max(1, remaining),
                        )
                    return HandoffPersistResult(
                        outcome="conflict_consumed",
                        expires_at=entry_exp,
                        remaining_ttl_seconds=0,
                    )
                if (
                    kind == "handoff"
                    and remaining > 0
                    and hmac.compare_digest(existing_hash, token_hash)
                ):
                    return HandoffPersistResult(
                        outcome="preserved_handoff",
                        expires_at=entry_exp,
                        remaining_ttl_seconds=max(1, remaining),
                    )

            bounded_exp = min(expires_at, current + max(1, ttl_seconds))
            self._entries[key] = (
                {
                    "kind": "handoff",
                    "key_id": key_id,
                    "token_hash": token_hash,
                    "ciphertext": ciphertext,
                    "expires_at": bounded_exp,
                },
                bounded_exp,
            )
            return HandoffPersistResult(
                outcome="created",
                expires_at=bounded_exp,
                remaining_ttl_seconds=max(1, bounded_exp - current),
            )

    def get_handoff_entry(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_locked(current)
            entry = self._entries.get(_handoff_key(invitation_id, browser_binding_digest))
            if entry is None:
                return None
            return dict(entry[0])

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
    ) -> None:
        current = now if now is not None else int(time.time())
        key = _handoff_key(invitation_id, browser_binding_digest)
        with self._lock:
            self._purge_locked(current)
            existing = self._entries.get(key)
            if existing is None:
                return
            entry_dict, entry_exp = existing
            if entry_dict.get("kind") != "handoff":
                return
            existing_hash = str(entry_dict.get("token_hash") or "")
            if not hmac.compare_digest(existing_hash, token_hash):
                return
            if (
                not expected_handoff_ciphertext
                or entry_dict.get("ciphertext") != expected_handoff_ciphertext
            ):
                return
            effective_exp = min(int(entry_exp), expires_at)
            remaining = effective_exp - current
            if remaining <= 0:
                self._entries.pop(key, None)
                return
            self._entries[key] = (
                {
                    "kind": "consumed_receipt",
                    "invitation_id": invitation_id,
                    "user_id": user_id,
                    "token_hash": token_hash,
                    "browser_binding_digest": browser_binding_digest,
                    "expires_at": effective_exp,
                },
                effective_exp,
            )

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._rates.clear()


class RedisInvitationHandoffStore(BaseInvitationHandoffStore):
    """Fail-closed Redis store for encrypted invitation handoffs & atomic rate limits."""

    _LUA_RATE_LIMIT = """
    local key1 = KEYS[1]
    local key2 = KEYS[2]
    local limit1 = tonumber(ARGV[1])
    local limit2 = tonumber(ARGV[2])
    local window = tonumber(ARGV[3])

    local c1 = redis.call('INCR', key1)
    if c1 == 1 then
        redis.call('EXPIRE', key1, window)
    end
    local t1 = redis.call('TTL', key1)
    if t1 < 1 then
        redis.call('EXPIRE', key1, window)
        t1 = window
    end

    local c2 = redis.call('INCR', key2)
    if c2 == 1 then
        redis.call('EXPIRE', key2, window)
    end
    local t2 = redis.call('TTL', key2)
    if t2 < 1 then
        redis.call('EXPIRE', key2, window)
        t2 = window
    end

    local retry = 0
    if c1 > limit1 and t1 > retry then
        retry = t1
    end
    if c2 > limit2 and t2 > retry then
        retry = t2
    end
    return retry
    """

    _LUA_SAVE_OR_PRESERVE_HANDOFF = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local new_hash = ARGV[2]
    local new_exp = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])
    local new_envelope = ARGV[5]

    local raw = redis.call('GET', key)
    local pttl_ms = redis.call('PTTL', key)
    if raw and pttl_ms > 0 then
        local ok, existing = pcall(cjson.decode, raw)
        if ok and type(existing) == 'table' then
            local exp = tonumber(existing.expires_at)
            local kind = existing.kind
            local existing_hash = existing.token_hash
            if exp ~= nil and exp > now then
                local rem = math.min(exp - now, math.ceil(pttl_ms / 1000))
                if rem < 1 then
                    rem = 1
                end
                if kind == 'consumed_receipt' then
                    if existing_hash == new_hash then
                        return {'preserved_consumed', tostring(exp), tostring(rem)}
                    end
                    return {'conflict_consumed', tostring(exp), '0'}
                end
                if kind == 'handoff' and existing_hash == new_hash then
                    return {'preserved_handoff', tostring(exp), tostring(rem)}
                end
            end
        end
    end

    redis.call('SET', key, new_envelope, 'EX', ttl)
    return {'created', tostring(new_exp), tostring(ttl)}
    """

    _LUA_CONVERT_TO_CONSUMED_RECEIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local expected_hash = ARGV[2]
    local max_exp = tonumber(ARGV[3])
    local receipt_json = ARGV[4]
    local expected_ciphertext = ARGV[5]

    local raw = redis.call('GET', key)
    local pttl_ms = redis.call('PTTL', key)
    if not raw or pttl_ms <= 0 then
        return 'noop_missing'
    end

    local ok, existing = pcall(cjson.decode, raw)
    if not ok or type(existing) ~= 'table' then
        return 'noop_invalid'
    end
    if existing.kind ~= 'handoff' then
        return 'noop_not_handoff'
    end
    if existing.token_hash ~= expected_hash then
        return 'noop_mismatch'
    end
    -- Proof and expiry can be reused by a later handoff. Bind both conversion
    -- and deletion to the exact encrypted instance read before PostgreSQL commit.
    if expected_ciphertext == '' or existing.ciphertext ~= expected_ciphertext then
        return 'noop_context_mismatch'
    end

    local stored_exp = tonumber(existing.expires_at) or max_exp
    local effective_exp = math.min(stored_exp, max_exp)
    local remaining = effective_exp - now
    local redis_rem = math.ceil(pttl_ms / 1000)
    if redis_rem < remaining then
        remaining = redis_rem
    end
    if remaining <= 0 then
        redis.call('DEL', key)
        return 'deleted_expired'
    end

    redis.call('SET', key, receipt_json, 'PX', math.min(pttl_ms, remaining * 1000))
    return 'converted'
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

    @staticmethod
    def _unavailable(exc: Exception) -> RepositoryUnavailableError:
        return RepositoryUnavailableError("Invitation handoff store temporarily unavailable")

    def enforce_handoff_rate_limit(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None:
        import redis

        try:
            retry_after: Any = self._client.eval(
                self._LUA_RATE_LIMIT,
                2,
                f"invite:handoff:rl:browser:{browser_binding_digest}",
                f"invite:handoff:rl:ip:{client_ip}",
                str(browser_limit),
                str(ip_limit),
                str(window_seconds),
            )
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

        retry_int = int(retry_after or 0)
        return retry_int if retry_int > 0 else None

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
    ) -> HandoffPersistResult:
        import redis

        current = now if now is not None else int(time.time())
        effective_ttl = max(1, ttl_seconds)
        bounded_exp = min(expires_at, current + effective_ttl)
        envelope = json.dumps(
            {
                "kind": "handoff",
                "key_id": key_id,
                "token_hash": token_hash,
                "ciphertext": ciphertext,
                "expires_at": bounded_exp,
            },
            separators=(",", ":"),
        )
        key = _handoff_key(invitation_id, browser_binding_digest)
        try:
            raw_res: Any = self._client.eval(
                self._LUA_SAVE_OR_PRESERVE_HANDOFF,
                1,
                key,
                str(current),
                token_hash,
                str(bounded_exp),
                str(effective_ttl),
                envelope,
            )
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

        if not isinstance(raw_res, (list, tuple)) or len(raw_res) < 3:
            raise RepositoryUnavailableError("Unexpected response from invitation handoff store")

        return HandoffPersistResult(
            outcome=str(raw_res[0]),
            expires_at=int(raw_res[1]),
            remaining_ttl_seconds=int(raw_res[2]),
        )

    def get_handoff_entry(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        import redis

        try:
            raw = self._client.get(_handoff_key(invitation_id, browser_binding_digest))
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        if not raw or not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        if now is not None:
            exp = data.get("expires_at")
            if type(exp) is int and now >= exp:
                return None
        return data

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
    ) -> None:
        import redis

        current = now if now is not None else int(time.time())
        key = _handoff_key(invitation_id, browser_binding_digest)
        receipt = json.dumps(
            {
                "kind": "consumed_receipt",
                "invitation_id": invitation_id,
                "user_id": user_id,
                "token_hash": token_hash,
                "browser_binding_digest": browser_binding_digest,
                "expires_at": expires_at,
            },
            separators=(",", ":"),
        )
        try:
            self._client.eval(
                self._LUA_CONVERT_TO_CONSUMED_RECEIPT,
                1,
                key,
                str(current),
                token_hash,
                str(expires_at),
                receipt,
                expected_handoff_ciphertext,
            )
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

    def clear_all(self) -> None:
        import redis

        try:
            for pattern in ("invite:handoff:*",):
                keys = self._client.keys(pattern)
                if keys:
                    self._client.delete(*keys)
        except (redis.RedisError, OSError):
            pass


def create_invitation_handoff_store() -> BaseInvitationHandoffStore:
    store_type = settings.auth_store_type.lower()
    if store_type == "redis":
        return RedisInvitationHandoffStore(settings.redis_url)
    if store_type == "memory":
        return InMemoryInvitationHandoffStore()
    raise ValueError(f"Unsupported auth_store_type: {settings.auth_store_type}")


invitation_handoff_store: BaseInvitationHandoffStore = create_invitation_handoff_store()
