"""Encrypted browser-bound handoff cache adapters and atomic rate limiting (ADR-020 / Step 03-C)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

from control_api.core.errors import RepositoryUnavailableError
from control_api.core.settings import settings
from control_api.modules.workspaces.models.handoff import (
    InvitationHandoffProtector,
    generate_browser_binding_token,
    hash_browser_binding_token,
    is_valid_browser_binding_token,
)
from control_api.modules.workspaces.models.records import (
    ConsumedHandoffReceipt,
    DecryptedHandoffPayload,
    HandoffEntryDict,
    HandoffPersistResult,
    StoredHandoffLookup,
)

logger = logging.getLogger(__name__)


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
    ) -> HandoffEntryDict | None:
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

    async def enforce_handoff_rate_limit_async(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None:
        """Async variant of enforce_handoff_rate_limit (non-blocking for Redis stores)."""
        return self.enforce_handoff_rate_limit(
            browser_binding_digest=browser_binding_digest,
            client_ip=client_ip,
            browser_limit=browser_limit,
            ip_limit=ip_limit,
            window_seconds=window_seconds,
            now=now,
        )

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
    ) -> HandoffPersistResult:
        """Async variant of save_or_get_encrypted_handoff (non-blocking for Redis stores)."""
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

    async def get_handoff_entry_async(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> HandoffEntryDict | None:
        """Async variant of get_handoff_entry (non-blocking for Redis stores)."""
        return self.get_handoff_entry(
            invitation_id=invitation_id,
            browser_binding_digest=browser_binding_digest,
            now=now,
        )

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
    ) -> None:
        """Async variant of convert_to_consumed_receipt (non-blocking for Redis stores)."""
        self.convert_to_consumed_receipt(
            invitation_id=invitation_id,
            browser_binding_digest=browser_binding_digest,
            user_id=user_id,
            token_hash=token_hash,
            expected_handoff_ciphertext=expected_handoff_ciphertext,
            expires_at=expires_at,
            now=now,
        )

    async def clear_all_async(self) -> None:
        """Async variant of clear_all (non-blocking for Redis stores)."""
        self.clear_all()


def _copy_handoff_entry(entry: HandoffEntryDict) -> HandoffEntryDict:
    copied: HandoffEntryDict = {
        "kind": entry["kind"],
        "expires_at": entry["expires_at"],
        "token_hash": entry["token_hash"],
    }
    if "invitation_id" in entry:
        copied["invitation_id"] = entry["invitation_id"]
    if "key_id" in entry:
        copied["key_id"] = entry["key_id"]
    if "ciphertext" in entry:
        copied["ciphertext"] = entry["ciphertext"]
    if "user_id" in entry:
        copied["user_id"] = entry["user_id"]
    if "browser_binding_digest" in entry:
        copied["browser_binding_digest"] = entry["browser_binding_digest"]
    return copied


def _handoff_key(invitation_id: str, browser_binding_digest: str) -> str:
    return f"invite:handoff:{invitation_id}:{browser_binding_digest}"


def _coerce_handoff_entry(raw_obj: dict[str, Any], now: int | None) -> HandoffEntryDict | None:
    kind = raw_obj.get("kind")
    exp = raw_obj.get("expires_at")
    token_hash = raw_obj.get("token_hash")
    if kind not in {"handoff", "consumed_receipt"}:
        return None
    if type(exp) is not int or not isinstance(token_hash, str):
        return None
    if now is not None and now >= exp:
        return None

    if kind == "handoff":
        entry: HandoffEntryDict = {
            "kind": "handoff",
            "expires_at": exp,
            "token_hash": token_hash,
        }
        key_id = raw_obj.get("key_id")
        if isinstance(key_id, str):
            entry["key_id"] = key_id
        ciphertext = raw_obj.get("ciphertext")
        if isinstance(ciphertext, str):
            entry["ciphertext"] = ciphertext
        return entry

    receipt: HandoffEntryDict = {
        "kind": "consumed_receipt",
        "expires_at": exp,
        "token_hash": token_hash,
    }
    inv_id = raw_obj.get("invitation_id")
    if isinstance(inv_id, str):
        receipt["invitation_id"] = inv_id
    user_id = raw_obj.get("user_id")
    if isinstance(user_id, str):
        receipt["user_id"] = user_id
    digest = raw_obj.get("browser_binding_digest")
    if isinstance(digest, str):
        receipt["browser_binding_digest"] = digest
    return receipt


class InMemoryInvitationHandoffStore(BaseInvitationHandoffStore):
    """Thread-safe in-memory handoff store for isolated unit tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[HandoffEntryDict, int]] = {}
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
            new_entry: HandoffEntryDict = {
                "kind": "handoff",
                "key_id": key_id,
                "token_hash": token_hash,
                "ciphertext": ciphertext,
                "expires_at": bounded_exp,
            }
            self._entries[key] = (new_entry, bounded_exp)
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
    ) -> HandoffEntryDict | None:
        current = now if now is not None else int(time.time())
        with self._lock:
            self._purge_locked(current)
            entry = self._entries.get(_handoff_key(invitation_id, browser_binding_digest))
            if entry is None:
                return None
            return _copy_handoff_entry(entry[0])

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
            receipt: HandoffEntryDict = {
                "kind": "consumed_receipt",
                "invitation_id": invitation_id,
                "user_id": user_id,
                "token_hash": token_hash,
                "browser_binding_digest": browser_binding_digest,
                "expires_at": effective_exp,
            }
            self._entries[key] = (receipt, effective_exp)

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
        self.redis_url = redis_url or settings.redis_url
        self._sync_client: Any = None
        self._async_client: Any = None
        self._async_loop: asyncio.AbstractEventLoop | None = None

    @property
    def _client(self) -> Any:
        if self._sync_client is None:
            import redis

            self._sync_client = redis.Redis.from_url(
                self.redis_url,
                socket_timeout=settings.redis_socket_timeout_seconds,
                socket_connect_timeout=settings.redis_connect_timeout_seconds,
                decode_responses=True,
            )
        return self._sync_client

    def _get_async_client(self) -> Any:
        import redis.asyncio as aioredis

        loop = asyncio.get_running_loop()
        if (
            self._async_client is None
            or self._async_loop is not loop
            or self._async_loop.is_closed()
        ):
            self._async_client = aioredis.Redis.from_url(
                self.redis_url,
                socket_timeout=settings.redis_socket_timeout_seconds,
                socket_connect_timeout=settings.redis_connect_timeout_seconds,
                decode_responses=True,
            )
            self._async_loop = loop
        return self._async_client

    async def aclose(self) -> None:
        """Close underlying async and sync Redis clients if initialized."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
            self._async_loop = None
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    @staticmethod
    def _unavailable(exc: Exception) -> RepositoryUnavailableError:
        logger.warning(
            "Invitation handoff Redis operation failed (%s)",
            type(exc).__name__,
            extra={"error_type": type(exc).__name__},
        )
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

    async def enforce_handoff_rate_limit_async(
        self,
        *,
        browser_binding_digest: str,
        client_ip: str,
        browser_limit: int,
        ip_limit: int,
        window_seconds: int,
        now: int | None = None,
    ) -> int | None:
        import redis.exceptions

        try:
            client = self._get_async_client()
            retry_after: Any = await client.eval(
                self._LUA_RATE_LIMIT,
                2,
                f"invite:handoff:rl:browser:{browser_binding_digest}",
                f"invite:handoff:rl:ip:{client_ip}",
                str(browser_limit),
                str(ip_limit),
                str(window_seconds),
            )
        except (redis.exceptions.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

        retry_int = int(retry_after or 0)
        return retry_int if retry_int > 0 else None

    @staticmethod
    def _build_handoff_envelope(
        *,
        token_hash: str,
        key_id: str,
        ciphertext: str,
        expires_at: int,
        ttl_seconds: int,
        now: int | None,
    ) -> tuple[int, int, int, str]:
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
        return current, effective_ttl, bounded_exp, envelope

    @staticmethod
    def _parse_persist_result(raw_res: Any) -> HandoffPersistResult:
        if not isinstance(raw_res, (list, tuple)) or len(raw_res) < 3:
            raise RepositoryUnavailableError("Unexpected response from invitation handoff store")
        return HandoffPersistResult(
            outcome=str(raw_res[0]),
            expires_at=int(raw_res[1]),
            remaining_ttl_seconds=int(raw_res[2]),
        )

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

        current, effective_ttl, bounded_exp, envelope = self._build_handoff_envelope(
            token_hash=token_hash,
            key_id=key_id,
            ciphertext=ciphertext,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            now=now,
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
        return self._parse_persist_result(raw_res)

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
    ) -> HandoffPersistResult:
        import redis.exceptions

        current, effective_ttl, bounded_exp, envelope = self._build_handoff_envelope(
            token_hash=token_hash,
            key_id=key_id,
            ciphertext=ciphertext,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        key = _handoff_key(invitation_id, browser_binding_digest)
        try:
            client = self._get_async_client()
            raw_res: Any = await client.eval(
                self._LUA_SAVE_OR_PRESERVE_HANDOFF,
                1,
                key,
                str(current),
                token_hash,
                str(bounded_exp),
                str(effective_ttl),
                envelope,
            )
        except (redis.exceptions.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        return self._parse_persist_result(raw_res)

    @staticmethod
    def _decode_handoff_entry(raw: Any, now: int | None) -> HandoffEntryDict | None:
        if not raw or not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return _coerce_handoff_entry(data, now)

    def get_handoff_entry(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> HandoffEntryDict | None:
        import redis

        try:
            raw = self._client.get(_handoff_key(invitation_id, browser_binding_digest))
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        return self._decode_handoff_entry(raw, now)

    async def get_handoff_entry_async(
        self,
        *,
        invitation_id: str,
        browser_binding_digest: str,
        now: int | None = None,
    ) -> HandoffEntryDict | None:
        import redis.exceptions

        try:
            client = self._get_async_client()
            raw = await client.get(_handoff_key(invitation_id, browser_binding_digest))
        except (redis.exceptions.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc
        return self._decode_handoff_entry(raw, now)

    @staticmethod
    def _build_receipt_payload(
        *,
        invitation_id: str,
        browser_binding_digest: str,
        user_id: str,
        token_hash: str,
        expires_at: int,
    ) -> str:
        return json.dumps(
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
        receipt = self._build_receipt_payload(
            invitation_id=invitation_id,
            browser_binding_digest=browser_binding_digest,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
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
    ) -> None:
        import redis.exceptions

        current = now if now is not None else int(time.time())
        key = _handoff_key(invitation_id, browser_binding_digest)
        receipt = self._build_receipt_payload(
            invitation_id=invitation_id,
            browser_binding_digest=browser_binding_digest,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        try:
            client = self._get_async_client()
            await client.eval(
                self._LUA_CONVERT_TO_CONSUMED_RECEIPT,
                1,
                key,
                str(current),
                token_hash,
                str(expires_at),
                receipt,
                expected_handoff_ciphertext,
            )
        except (redis.exceptions.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

    def clear_all(self) -> None:
        import redis

        batch: list[str] = []
        try:
            for key in self._client.scan_iter(match="invite:handoff:*", count=200):
                batch.append(key)
                if len(batch) >= 500:
                    self._client.delete(*batch)
                    batch.clear()
            if batch:
                self._client.delete(*batch)
        except (redis.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc

    async def clear_all_async(self) -> None:
        import redis.exceptions

        batch: list[str] = []
        try:
            client = self._get_async_client()
            async for key in client.scan_iter(match="invite:handoff:*", count=200):
                batch.append(key)
                if len(batch) >= 500:
                    await client.delete(*batch)
                    batch.clear()
            if batch:
                await client.delete(*batch)
        except (redis.exceptions.RedisError, OSError) as exc:
            raise self._unavailable(exc) from exc


def create_invitation_handoff_store() -> BaseInvitationHandoffStore:
    store_type = settings.auth_store_type.lower()
    if store_type == "redis":
        return RedisInvitationHandoffStore(settings.redis_url)
    if store_type == "memory":
        return InMemoryInvitationHandoffStore()
    raise ValueError(f"Unsupported auth_store_type: {settings.auth_store_type}")


invitation_handoff_store: BaseInvitationHandoffStore | None = None


def get_invitation_handoff_store() -> BaseInvitationHandoffStore:
    """Return the active handoff store, lazily initializing it on first access."""
    global invitation_handoff_store
    if invitation_handoff_store is None:
        invitation_handoff_store = create_invitation_handoff_store()
    return invitation_handoff_store


async def close_invitation_handoff_store() -> None:
    """Close cached handoff store clients during application shutdown."""
    global invitation_handoff_store
    store = invitation_handoff_store
    if isinstance(store, RedisInvitationHandoffStore):
        await store.aclose()
    invitation_handoff_store = None


__all__ = [
    "BaseInvitationHandoffStore",
    "ConsumedHandoffReceipt",
    "DecryptedHandoffPayload",
    "HandoffPersistResult",
    "InMemoryInvitationHandoffStore",
    "InvitationHandoffProtector",
    "RedisInvitationHandoffStore",
    "StoredHandoffLookup",
    "close_invitation_handoff_store",
    "create_invitation_handoff_store",
    "generate_browser_binding_token",
    "get_invitation_handoff_store",
    "hash_browser_binding_token",
    "invitation_handoff_store",
    "is_valid_browser_binding_token",
]
