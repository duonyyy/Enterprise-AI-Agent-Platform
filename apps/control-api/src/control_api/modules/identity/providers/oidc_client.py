"""OIDC Provider client handling Authorization Code Flow + PKCE S256 (SPEC-AUTH-001).

Implements:
- Open redirect prevention via redirect URI allowlist
- PKCE S256 code challenge and verifier exchange
- Browser correlation check (RFC 9700 §2.1.1 / Finding R02)
- Single-use state and nonce bound to auth transaction in shared Redis store (Finding R01)
- Key discovery and JWKS caching with rollover support
- Fixed RS256 ID Token profile with issuer, audience/azp, timestamps and nonce checks
- Sanitized upstream errors preventing sensitive information disclosure
"""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlencode, urlparse

import httpx
from control_api.core.settings import settings
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.identity.models.session import AuthTransaction
from control_api.modules.identity.repositories.auth_transaction_repository import (
    BaseAuthTransactionStore,
    auth_transaction_store,
)
from control_api.modules.identity.security.crypto import (
    SigningKeyError,
    generate_pkce_pair,
    generate_secure_token,
    verify_oidc_id_token_rs256,
)
from fastapi import HTTPException, status


def _response_object(response: httpx.Response) -> dict[str, Any]:
    """Reject malformed upstream JSON without leaking provider response content."""
    try:
        payload = response.json()
    except (ValueError, RecursionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid IdP response"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid IdP response")
    return cast(dict[str, Any], payload)


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
        self._run_store_op(lambda: self.store.save(tx))

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

    def _run_store_op[T](self, op: Callable[[], T]) -> T:
        """Execute a transaction store operation and map store outages to HTTP 503."""
        try:
            return op()
        except RepositoryUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    def consume_transaction(self, state: str) -> AuthTransaction | None:
        """Consume a single-use OIDC transaction while translating store outages to HTTP 503."""
        return self._run_store_op(lambda: self.store.consume(state))

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
            return _response_object(resp)

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
        tx = self.consume_transaction(state)
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
            not browser_correlation
            or not hmac.compare_digest(browser_correlation, tx.browser_correlation)
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
            return _response_object(resp)

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

        # The IdP profile is fixed; a token header cannot enable an HS256 fallback.
        def verify_id_token(keys: dict[str, Any]) -> dict[str, object]:
            return verify_oidc_id_token_rs256(
                id_token_raw,
                keys,
                issuer=settings.oidc_issuer,
                client_id=settings.oidc_client_id,
                nonce=tx.nonce,
                additional_trusted_audiences=settings.oidc_additional_trusted_audiences,
            )

        jwks = await self.fetch_jwks(http_client)
        try:
            payload = verify_id_token(jwks)
        except SigningKeyError:
            # Refresh only for key/signature rotation, never for invalid identity claims.
            with self._jwks_lock:
                self._jwks_cache = None
            jwks = await self.fetch_jwks(http_client)
            try:
                payload = verify_id_token(jwks)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid OIDC ID token",
                ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid OIDC ID token",
            ) from exc
        return payload, tx


oidc_client = OidcClient()
