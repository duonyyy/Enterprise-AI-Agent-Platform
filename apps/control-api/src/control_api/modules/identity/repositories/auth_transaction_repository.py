"""Authoritative store for in-flight OIDC authentication transactions (SPEC-AUTH-001).

Guarantees atomic single-use state consumption via Redis GETDEL or Lua fallback.
Backed by distributed Redis store with thread-safe in-memory adapter for unit testing.
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod

from control_api.core.settings import settings
from control_api.modules.identity.models.errors import AuthTransactionStoreUnavailableError
from control_api.modules.identity.models.session import AuthTransaction


class BaseAuthTransactionStore(ABC):
    """Abstract base class for in-flight authentication transaction stores."""

    @abstractmethod
    def save(self, tx: AuthTransaction) -> None:
        """Store in-flight transaction with TTL."""

    @abstractmethod
    def consume(self, state: str) -> AuthTransaction | None:
        """Atomically retrieve and delete transaction (single-use semantics)."""

    @abstractmethod
    def clear_all(self) -> None:
        """Clear all transactions (for testing)."""


class InMemoryAuthTransactionStore(BaseAuthTransactionStore):
    """Thread-safe store for in-flight authentication transactions (unit testing only)."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._lock = threading.Lock()
        self._transactions: dict[str, AuthTransaction] = {}
        self._ttl_seconds = ttl_seconds

    def save(self, tx: AuthTransaction) -> None:
        with self._lock:
            self._purge_expired_locked()
            self._transactions[tx.state] = tx

    def consume(self, state: str) -> AuthTransaction | None:
        """Retrieve and immediately remove transaction (single-use semantics)."""
        with self._lock:
            self._purge_expired_locked()
            return self._transactions.pop(state, None)

    def _purge_expired_locked(self) -> None:
        now = int(time.time())
        expired = [
            s for s, tx in self._transactions.items() if now - tx.created_at > self._ttl_seconds
        ]
        for s in expired:
            del self._transactions[s]

    def clear_all(self) -> None:
        with self._lock:
            self._transactions.clear()


# Alias for backward compatibility
AuthTransactionStore = InMemoryAuthTransactionStore


class RedisAuthTransactionStore(BaseAuthTransactionStore):
    """Distributed Redis-backed store for in-flight auth transactions (AU-01 / Finding R01).

    Guarantees atomic single-use consume via GETDEL (Redis >= 6.2) or Lua script.
    Fail-closed: raises AuthTransactionStoreUnavailableError if Redis is unavailable.
    """

    def __init__(self, redis_url: str | None = None, ttl_seconds: int = 600) -> None:
        import redis

        self.redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds
        self._client = redis.Redis.from_url(
            self.redis_url,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            decode_responses=True,
        )

    def save(self, tx: AuthTransaction) -> None:
        import redis

        data = {
            "state": tx.state,
            "nonce": tx.nonce,
            "code_verifier": tx.code_verifier,
            "code_challenge": tx.code_challenge,
            "return_to": tx.return_to,
            "browser_correlation": tx.browser_correlation,
            "created_at": tx.created_at,
        }
        try:
            self._client.set(f"auth_tx:{tx.state}", json.dumps(data), ex=self._ttl_seconds)
        except (redis.RedisError, OSError) as exc:
            raise AuthTransactionStoreUnavailableError(
                "Auth transaction store unavailable"
            ) from exc

    def consume(self, state: str) -> AuthTransaction | None:
        import redis

        try:
            raw = self._client.getdel(f"auth_tx:{state}")
        except (redis.RedisError, OSError) as exc:
            # Fallback to lua script if GETDEL is not supported on older Redis or connection error
            if isinstance(exc, redis.ResponseError) and "unknown command" in str(exc).lower():
                lua = """
                local val = redis.call('GET', KEYS[1])
                if val then redis.call('DEL', KEYS[1]) end
                return val
                """
                try:
                    raw = self._client.eval(lua, 1, f"auth_tx:{state}")
                except (redis.RedisError, OSError) as sub_exc:
                    raise AuthTransactionStoreUnavailableError(
                        "Auth transaction store unavailable"
                    ) from sub_exc
            else:
                raise AuthTransactionStoreUnavailableError(
                    "Auth transaction store unavailable"
                ) from exc

        if not raw:
            return None

        try:
            data = json.loads(raw)
            return AuthTransaction(
                state=data["state"],
                nonce=data["nonce"],
                code_verifier=data["code_verifier"],
                code_challenge=data["code_challenge"],
                return_to=data["return_to"],
                browser_correlation=data.get("browser_correlation", ""),
                created_at=int(data["created_at"]),
            )
        except (KeyError, ValueError, json.JSONDecodeError):
            return None

    def clear_all(self) -> None:
        import redis

        try:
            keys = self._client.keys("auth_tx:*")
            if keys:
                self._client.delete(*keys)
        except (redis.RedisError, OSError):
            pass


def create_auth_transaction_store() -> BaseAuthTransactionStore:
    store_type = settings.auth_store_type.lower()
    if store_type == "redis":
        return RedisAuthTransactionStore(settings.redis_url)
    elif store_type == "memory":
        return InMemoryAuthTransactionStore()
    else:
        raise ValueError(f"Unsupported auth_store_type: {settings.auth_store_type}")


auth_transaction_store: BaseAuthTransactionStore = create_auth_transaction_store()
