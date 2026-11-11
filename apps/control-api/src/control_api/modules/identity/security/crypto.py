"""Cryptographic and token utilities for Enterprise Agent Platform Authentication (SPEC-AUTH-001).

Authentication primitives supporting:
- PKCE S256 verification and generation (RFC 7636)
- HMAC-SHA256 (HS256) JWT signing and verification
- PyJWT/cryptography RS256 verification against trusted OIDC JWKS
- Cryptographically secure token and state generation
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Collection, Mapping
from typing import Any, cast

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from jwt.algorithms import RSAAlgorithm
from jwt.types import Options

_MAX_JWT_LENGTH = 65_536
_MAX_JWKS_KEYS = 64
_MAX_OIDC_LEEWAY_SECONDS = 60
_SIGNATURE_ONLY_OPTIONS: Options = {
    "verify_signature": True,
    "verify_exp": False,
    "verify_iat": False,
    "verify_nbf": False,
    "verify_aud": False,
    "verify_iss": False,
    "verify_sub": False,
    "verify_jti": False,
}


class SigningKeyError(ValueError):
    """The trusted JWKS may need refreshing after key rotation."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JWT JSON member")
        result[name] = value
    return result


def b64url_encode(data: bytes) -> str:
    """Encode bytes to URL-safe base64 string without trailing padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(data: str) -> bytes:
    """Decode URL-safe base64 string with automatic padding restoration."""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Invalid base64url encoding") from exc


def generate_secure_token(nbytes: int = 32) -> str:
    """Generate a cryptographically strong random token."""
    return secrets.token_urlsafe(nbytes)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE S256 code verifier and code challenge (RFC 7636).

    Returns:
        tuple[code_verifier, code_challenge]
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = b64url_encode(digest)
    return code_verifier, code_challenge


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Verify that SHA-256(code_verifier) matches code_challenge (S256 method)."""
    if not code_verifier or not code_challenge:
        return False
    calculated_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected_challenge = b64url_encode(calculated_digest)
    return hmac.compare_digest(expected_challenge, code_challenge)


def decode_jwt_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    """Split and decode JWT into (header, payload, signing_input, signature_bytes)."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT: must contain exactly 3 segments separated by dots")
    raw_header, raw_payload, raw_sig = parts
    try:
        header = json.loads(b64url_decode(raw_header))
        payload = json.loads(b64url_decode(raw_payload))
        signature = b64url_decode(raw_sig)
    except Exception as exc:
        raise ValueError("Malformed JWT segments") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise TypeError("JWT header and payload must be JSON objects")
    signing_input = f"{raw_header}.{raw_payload}".encode("ascii")
    return header, payload, signing_input, signature


def sign_jwt_hs256(
    payload: dict[str, Any],
    secret: str,
    headers: dict[str, Any] | None = None,
) -> str:
    """Sign payload using HMAC-SHA256 (HS256)."""
    hdr = {"alg": "HS256", "typ": "JWT"}
    if headers:
        hdr.update(headers)
    encoded_header = b64url_encode(json.dumps(hdr, separators=(",", ":")).encode("utf-8"))
    encoded_payload = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    encoded_sig = b64url_encode(sig)
    return f"{encoded_header}.{encoded_payload}.{encoded_sig}"


def verify_jwt_hs256(token: str, secret: str) -> dict[str, Any]:
    """Verify and decode HS256 signed JWT."""
    header, payload, signing_input, signature = decode_jwt_unverified(token)
    if header.get("alg") != "HS256":
        raise ValueError(f"Unsupported algorithm: {header.get('alg')}")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, signature):
        raise ValueError("Invalid JWT signature")
    return payload


def verify_rsa_pkcs1v15_sha256(
    message: bytes,
    signature: bytes,
    n_b64: str,
    e_b64: str,
) -> bool:
    """Compatibility wrapper; RSA verification is delegated to cryptography."""
    try:
        key = _rsa_public_key(n_b64, e_b64)
        key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError, OverflowError):
        return False
    return True


def _rsa_public_key(n_b64: object, e_b64: object) -> rsa.RSAPublicKey:
    if not isinstance(n_b64, str) or not isinstance(e_b64, str):
        raise ValueError("Invalid RSA signing key")  # noqa: TRY004 -- Invalid external key data.
    if len(n_b64) > 1500 or len(e_b64) > 16:
        raise ValueError("Oversized RSA key material")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", n_b64) or not re.fullmatch(r"[A-Za-z0-9_-]+", e_b64):
        raise ValueError("Invalid RSA key encoding")
    try:
        modulus = int.from_bytes(b64url_decode(n_b64), "big")
        if not 2048 <= modulus.bit_length() <= 8192:
            raise ValueError("Unsupported RSA key length")
        # Only public parameters reach the library; ignore any private JWK material.
        key = RSAAlgorithm.from_jwk(json.dumps({"kty": "RSA", "n": n_b64, "e": e_b64}))
    except (jwt.PyJWTError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid RSA signing key") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("Invalid RSA public key")  # noqa: TRY004 -- Preserve verifier error contract.
    return key


def _rs256_header(token: str) -> dict[str, object]:
    if not isinstance(token, str) or not token or len(token) > _MAX_JWT_LENGTH:
        raise ValueError("Invalid JWT size or type")
    try:
        segments = token.split(".")
        if len(segments) != 3:
            raise ValueError("Invalid JWT segments")
        # Reject ambiguous JSON before passing the bounded token to the library.
        for segment in segments[:2]:
            obj = json.loads(b64url_decode(segment), object_pairs_hook=_unique_json_object)
            if not isinstance(obj, dict):
                raise TypeError("JWT header and payload must be objects")
        header = jwt.get_unverified_header(token)
    except (jwt.PyJWTError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("Invalid JWT header") from exc
    if header.get("alg") != "RS256":
        raise ValueError("Unexpected JWT signing algorithm")
    if any(name in header for name in ("crit", "jku", "jwk", "x5u", "b64")):
        raise ValueError("Unsupported JOSE header")
    kid = header.get("kid")
    if kid is not None and (not isinstance(kid, str) or not kid or len(kid) > 256):
        raise ValueError("Invalid JWT key ID")
    return cast(dict[str, object], header)


def _rsa_verification_key(token: str, jwks: Mapping[str, object]) -> rsa.RSAPublicKey:
    header = _rs256_header(token)
    kid = header.get("kid")
    keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
    if not isinstance(keys, list) or len(keys) > _MAX_JWKS_KEYS:
        raise ValueError("Invalid JWKS key set")
    candidates: list[dict[str, object]] = []
    for key in keys:
        if not isinstance(key, dict) or key.get("kty") != "RSA":
            continue
        if kid is not None and key.get("kid") != kid:
            continue
        if key.get("alg", "RS256") != "RS256" or key.get("use", "sig") != "sig":
            continue
        ops = key.get("key_ops")
        if ops is not None and (
            not isinstance(ops, list)
            or any(not isinstance(op, str) for op in ops)
            or "verify" not in ops
        ):
            continue
        candidates.append(key)
    if not candidates:
        raise SigningKeyError("No matching JWKS signing key")
    if len(candidates) != 1:
        raise ValueError("Ambiguous JWKS signing key")
    return _rsa_public_key(candidates[0].get("n"), candidates[0].get("e"))


def verify_jwt_rs256_jwks(token: str, jwks: Mapping[str, object]) -> dict[str, object]:
    """Legacy signature-only verifier; OIDC callers must use the full ID Token verifier."""
    key = _rsa_verification_key(token, jwks)
    try:
        return cast(
            dict[str, object],
            jwt.decode(token, key, algorithms=["RS256"], options=_SIGNATURE_ONLY_OPTIONS),
        )
    except (jwt.PyJWTError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid RS256 JWT") from exc


def _numeric_date(payload: Mapping[str, object], claim: str) -> int | float:
    value = payload[claim]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid OIDC timestamp")  # noqa: TRY004 -- Invalid external claim data.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Invalid OIDC timestamp")
    return value


def _validate_oidc_claims(
    payload: Mapping[str, object], client_id: str, nonce: str, trusted_audiences: set[str]
) -> None:
    aud = payload["aud"]
    audiences = [aud] if isinstance(aud, str) else aud
    if (
        not isinstance(audiences, list)
        or not audiences
        or any(not isinstance(a, str) or not a for a in audiences)
        or any(a not in trusted_audiences for a in audiences)
    ):
        raise ValueError("Invalid OIDC audience")
    azp = payload.get("azp")
    if (len(audiences) > 1 and azp != client_id) or ("azp" in payload and azp != client_id):
        raise ValueError("Invalid OIDC authorized party")
    subject = payload["sub"]
    token_nonce = payload["nonce"]
    if not isinstance(subject, str) or not subject.strip() or not isinstance(token_nonce, str):
        raise ValueError("Invalid OIDC subject or nonce")
    try:
        nonce_matches = hmac.compare_digest(token_nonce.encode("utf-8"), nonce.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid OIDC nonce encoding") from exc
    if not nonce_matches:
        raise ValueError("Invalid OIDC nonce")
    expires_at = _numeric_date(payload, "exp")
    if _numeric_date(payload, "iat") >= expires_at:
        raise ValueError("Invalid OIDC timestamp ordering")
    if "nbf" in payload and _numeric_date(payload, "nbf") >= expires_at:
        raise ValueError("Invalid OIDC timestamp ordering")


def verify_oidc_id_token_rs256(
    token: str,
    jwks: Mapping[str, object],
    *,
    issuer: str,
    client_id: str,
    nonce: str,
    leeway_seconds: int = 0,
    additional_trusted_audiences: Collection[str] = (),
) -> dict[str, object]:
    """Verify RS256 OIDC identity, time, audience/azp and nonce using trusted issuer keys.

    The default accepts only this client's audience. Other audiences need an explicit
    allowlist and matching azp. This profile never falls back to an OIDC HS256 verifier;
    application-session and Runtime HS256 tokens are separate, unchanged profiles.
    """
    if not all(isinstance(v, str) and v for v in (issuer, client_id, nonce)):
        raise ValueError("Invalid OIDC verifier configuration")
    if (
        type(leeway_seconds) is not int
        or not 0 <= leeway_seconds <= _MAX_OIDC_LEEWAY_SECONDS
        or isinstance(additional_trusted_audiences, str)
        or len(additional_trusted_audiences) > _MAX_JWKS_KEYS
        or any(not isinstance(a, str) or not a for a in additional_trusted_audiences)
    ):
        raise ValueError("Invalid OIDC verifier policy")
    key = _rsa_verification_key(token, jwks)
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=client_id,
            leeway=leeway_seconds,
            options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]},
        )
    except jwt.InvalidSignatureError as exc:
        raise SigningKeyError("Invalid OIDC token signature") from exc
    except (jwt.PyJWTError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid OIDC ID Token") from exc
    _validate_oidc_claims(payload, client_id, nonce, {client_id, *additional_trusted_audiences})
    return payload
