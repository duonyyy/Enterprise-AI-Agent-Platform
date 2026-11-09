"""OIDC Authorization Code Flow with PKCE S256 (RFC 7636 / OIDC Core 1.0).

Implements:
- Open redirect prevention via redirect URI allowlist
- PKCE S256 code challenge and verifier exchange
- Browser correlation check (RFC 9700 §2.1.1 / Finding R02)
- Single-use state and nonce bound to auth transaction in shared Redis store (Finding R01)
- Key discovery and JWKS caching with rollover support
- ID Token verification (issuer, audience, expiry, nonce, RS256/HS256 signature)
- Sanitized upstream errors preventing sensitive information disclosure
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from control_api.core.settings import settings
from control_api.modules.identity.crypto import (
    decode_jwt_unverified,
    generate_pkce_pair,
    generate_secure_token,
    verify_jwt_hs256,
    verify_jwt_rs256_jwks,
)
from fastapi import HTTPException, status


@dataclass(frozen=True, slots=True)
class AuthTransaction:
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    return_to: str
    browser_correlation: str
    created_at: int


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
        expired = [s for s, tx in self._transactions.items() if now - tx.created_at > self._ttl_seconds]
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
    Fail-closed: raises HTTPException(503) if Redis is unavailable.
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
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth transaction store unavailable",
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
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Auth transaction store unavailable",
                    ) from sub_exc
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Auth transaction store unavailable",
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
    if settings.auth_store_type == "redis":
        return RedisAuthTransactionStore(settings.redis_url)
    return InMemoryAuthTransactionStore()


auth_transaction_store: BaseAuthTransactionStore = create_auth_transaction_store()


class OidcClient:
    """OIDC Client handling Authorization Code Flow + PKCE S256."""

    def __init__(
        self,
        transaction_store: BaseAuthTransactionStore | None = None,
    ) -> None:
        self.store = transaction_store or auth_transaction_store
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0.0
        self._jwks_lock = threading.Lock()

    def validate_return_to(self, return_to: str | None) -> str:
        """Validate return_to URL to prevent open redirects (AU-01 / Finding R08)."""
        if not return_to:
            return "/login"
        # Relative paths on same host are allowed
        if return_to.startswith("/") and not return_to.startswith("//") and "\\" not in return_to:
            # Prevent defaulting to fake workspace
            if return_to == "/w/default/agents":
                return "/login"
            return return_to

        parsed = urlparse(return_to)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in settings.oidc_redirect_allowlist:
            return return_to

        # Fallback safe path
        return "/login"

    def initiate_login(
        self,
        return_to: str | None = None,
        browser_correlation: str = "",
    ) -> tuple[str, str]:
        """Initiate OIDC Authorization Code Flow with PKCE S256 and browser-bound correlation."""
        safe_return_to = self.validate_return_to(return_to)
        state = generate_secure_token(32)
        nonce = generate_secure_token(32)
        code_verifier, code_challenge = generate_pkce_pair()

        tx = AuthTransaction(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            return_to=safe_return_to,
            browser_correlation=browser_correlation,
            created_at=int(time.time()),
        )
        self.store.save(tx)

        issuer = settings.oidc_issuer.rstrip("/")
        auth_endpoint = f"{issuer}/protocol/openid-connect/auth"

        params = {
            "client_id": settings.oidc_client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": settings.oidc_redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        authorization_url = f"{auth_endpoint}?{urlencode(params)}"
        return authorization_url, state

    async def fetch_jwks(self, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        """Fetch and cache JWKS keys with rollover support."""
        now = time.time()
        with self._jwks_lock:
            if self._jwks_cache and (now - self._jwks_fetched_at) < 300:
                return self._jwks_cache

        issuer = settings.oidc_issuer.rstrip("/")
        jwks_url = f"{issuer}/protocol/openid-connect/certs"

        async def _do_fetch(c: httpx.AsyncClient) -> dict[str, Any]:
            resp = await c.get(jwks_url, timeout=5.0)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Failed to fetch JWKS from IdP ({resp.status_code})",
                )
            return resp.json()  # type: ignore[no-any-return]

        try:
            if client is not None:
                jwks = await _do_fetch(client)
            else:
                async with httpx.AsyncClient() as new_client:
                    jwks = await _do_fetch(new_client)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not connect to IdP JWKS endpoint: {type(exc).__name__}",
            ) from exc

        with self._jwks_lock:
            self._jwks_cache = jwks
            self._jwks_fetched_at = now
            return jwks

    async def handle_callback(
        self,
        code: str,
        state: str,
        browser_correlation: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> tuple[dict[str, Any], AuthTransaction]:
        """Handle OIDC Callback, verify state/nonce/PKCE, browser binding, and ID token."""
        # 1. Single-use state verification
        tx = self.store.consume(state)
        if not tx:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or replayed state parameter",
            )

        now = int(time.time())
        if now - tx.created_at > 600:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authentication transaction timed out",
            )

        # 2. Browser correlation check (RFC 9700 §2.1.1 / Finding R02)
        if tx.browser_correlation and (
            not browser_correlation or not hmac.compare_digest(browser_correlation, tx.browser_correlation)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing or invalid browser correlation; login transaction must be completed by the initiating browser",
            )

        # 3. Exchange authorization code at Token Endpoint
        issuer = settings.oidc_issuer.rstrip("/")
        token_endpoint = f"{issuer}/protocol/openid-connect/token"

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.oidc_redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret.get_secret_value(),
            "code_verifier": tx.code_verifier,
        }

        async def _do_exchange(c: httpx.AsyncClient) -> dict[str, Any]:
            resp = await c.post(token_endpoint, data=data, timeout=10.0)
            if resp.status_code != 200:
                # Sanitize error to avoid leaking credentials/secrets/URLs (Finding R02)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Token exchange failed at IdP: invalid authorization code or grant",
                )
            return resp.json()  # type: ignore[no-any-return]

        try:
            if http_client is not None:
                token_response = await _do_exchange(http_client)
            else:
                async with httpx.AsyncClient() as new_client:
                    token_response = await _do_exchange(new_client)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to communicate with IdP Token Endpoint: {type(exc).__name__}",
            ) from exc

        id_token_raw = token_response.get("id_token")
        if not isinstance(id_token_raw, str):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="IdP token response missing valid id_token",
            )

        # 4. Decode & Verify ID Token
        header, _, _, _ = decode_jwt_unverified(id_token_raw)
        alg = header.get("alg")

        if alg == "RS256":
            jwks = await self.fetch_jwks(http_client)
            try:
                payload = verify_jwt_rs256_jwks(id_token_raw, jwks)
            except ValueError:
                # Key rollover: re-fetch JWKS once if validation failed
                with self._jwks_lock:
                    self._jwks_cache = None
                jwks = await self.fetch_jwks(http_client)
                try:
                    payload = verify_jwt_rs256_jwks(id_token_raw, jwks)
                except ValueError as err:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Invalid ID token signature: {err}",
                    ) from err
        elif alg == "HS256":
            try:
                payload = verify_jwt_hs256(
                    id_token_raw, settings.oidc_client_secret.get_secret_value()
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid HS256 ID token signature: {exc}",
                ) from exc
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Unsupported ID token algorithm: {alg}",
            )

        # 5. Validate Claims (OIDC Core §3.1.3.7)
        iss = payload.get("iss")
        if iss != settings.oidc_issuer:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"ID token issuer mismatch (expected {settings.oidc_issuer}, got {iss})",
            )

        aud = payload.get("aud")
        audiences = [aud] if isinstance(aud, str) else (aud or [])
        if settings.oidc_client_id not in audiences:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"ID token audience mismatch (expected {settings.oidc_client_id})",
            )

        exp = payload.get("exp")
        if not isinstance(exp, (int, float)) or int(exp) <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="ID token has expired",
            )

        token_nonce = payload.get("nonce")
        if token_nonce != tx.nonce:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="ID token nonce mismatch",
            )

        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="ID token missing valid sub (subject) claim",
            )

        return payload, tx


oidc_client = OidcClient()
