"""OIDC Test Server Fixture for Enterprise Agent Platform (Step 02 / PR-02 / G2).

Provides a synthetic in-process OpenID Connect fixture for component tests:
- OpenID Connect Discovery (/.well-known/openid-configuration)
- JWKS Key Discovery and Key Rollover (/protocol/openid-connect/certs)
- Authorization endpoint with PKCE challenge tracking
- Token exchange with PKCE S256 validation and RS256 ID token issuance.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from control_api.modules.identity.security.crypto import b64url_encode, verify_pkce
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class RsaKeyPair:
    """RSA 2048-bit test key generated and used by cryptography."""

    def __init__(self, kid: str = "test-key-1") -> None:
        self.kid = kid
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self._private_key.public_key().public_numbers()
        self.n = numbers.n
        self.e = numbers.e

    def to_jwk(self) -> dict[str, str]:
        n_bytes = self.n.to_bytes((self.n.bit_length() + 7) // 8, "big")
        e_bytes = self.e.to_bytes((self.e.bit_length() + 7) // 8, "big")
        return {
            "kty": "RSA",
            "kid": self.kid,
            "use": "sig",
            "alg": "RS256",
            "n": b64url_encode(n_bytes),
            "e": b64url_encode(e_bytes),
        }

    def sign_rs256(self, message: bytes) -> bytes:
        """Sign message using RSA PKCS#1 v1.5 with SHA-256."""
        return self._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())


class OidcTestServer:
    """Mock OIDC server supporting PKCE S256, code exchange, and JWKS key rotation."""

    def __init__(self, issuer: str = "http://localhost:8080/realms/enterprise") -> None:
        self.issuer = issuer
        self.active_key = RsaKeyPair(kid="key-v1")
        self.previous_keys: list[RsaKeyPair] = []
        # code -> auth_data
        self.issued_codes: dict[str, dict[str, Any]] = {}

    def rotate_keys(self) -> None:
        """Simulate IdP key rollover / rotation."""
        self.previous_keys.append(self.active_key)
        new_kid = f"key-v{len(self.previous_keys) + 1}"
        self.active_key = RsaKeyPair(kid=new_kid)

    def get_jwks(self) -> dict[str, Any]:
        """Return JWKS key set containing active and previous keys."""
        keys = [self.active_key.to_jwk()]
        for pk in reversed(self.previous_keys):
            keys.append(pk.to_jwk())
        return {"keys": keys}

    def get_openid_configuration(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{self.issuer}/protocol/openid-connect/token",
            "jwks_uri": f"{self.issuer}/protocol/openid-connect/certs",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256", "HS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    def create_authorization_code(
        self,
        client_id: str,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        subject: str = "user-12345",
        name: str = "Test User",
        email: str = "test.user@enterprise.vn",
    ) -> str:
        """Generate authorization code bound to PKCE challenge and user identity."""
        code = f"auth-code-{secrets.token_urlsafe(24)}"
        self.issued_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "subject": subject,
            "name": name,
            "email": email,
            "created_at": time.time(),
        }
        return code

    def exchange_code_for_token(
        self,
        code: str,
        code_verifier: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        custom_now: int | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code with PKCE verification and issue signed ID token."""
        auth_data = self.issued_codes.pop(code, None)
        if not auth_data:
            raise ValueError("invalid_grant: code not found or already consumed")

        # Verify PKCE S256
        if not verify_pkce(code_verifier, auth_data["code_challenge"]):
            raise ValueError("invalid_grant: PKCE code_verifier does not match code_challenge")

        if auth_data["redirect_uri"] != redirect_uri:
            raise ValueError("invalid_grant: redirect_uri mismatch")

        now = custom_now if custom_now is not None else int(time.time())

        # Construct ID Token Claims
        payload = {
            "iss": self.issuer,
            "sub": auth_data["subject"],
            "aud": client_id,
            "azp": client_id,
            "nonce": auth_data["nonce"],
            "name": auth_data["name"],
            "preferred_username": auth_data["email"],
            "email": auth_data["email"],
            "iat": now,
            "exp": now + 3600,
            "auth_time": now,
        }

        # Sign ID Token with active RSA key
        header = {"alg": "RS256", "typ": "JWT", "kid": self.active_key.kid}
        raw_header = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        raw_payload = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{raw_header}.{raw_payload}".encode("ascii")
        sig = self.active_key.sign_rs256(signing_input)
        id_token = f"{raw_header}.{raw_payload}.{b64url_encode(sig)}"

        return {
            "access_token": f"at-{secrets.token_urlsafe(32)}",
            "token_type": "Bearer",
            "id_token": id_token,
            "expires_in": 3600,
            "scope": "openid profile email",
        }
