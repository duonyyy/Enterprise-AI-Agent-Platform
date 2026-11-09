"""Cryptographic and token utilities for Enterprise Agent Platform Authentication (SPEC-AUTH-001).

Zero-dependency, pure Python 3.12 implementation supporting:
- PKCE S256 verification and generation (RFC 7636)
- HMAC-SHA256 (HS256) JWT signing and verification
- RSA PKCS#1 v1.5 SHA-256 (RS256) signature verification against OIDC JWKS (RFC 7517/7518)
- Cryptographically secure token and state generation
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


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
    """Verify an RSA-PKCS1v15 signature with SHA-256 using pure-python modular arithmetic.

    Args:
        message: The raw bytes that were signed (e.g. ASCII "header.payload").
        signature: The raw signature bytes.
        n_b64: The RSA modulus in base64url.
        e_b64: The RSA public exponent in base64url.

    Returns:
        bool: True if signature is cryptographically valid, False otherwise.
    """
    try:
        n = int.from_bytes(b64url_decode(n_b64), "big")
        e = int.from_bytes(b64url_decode(e_b64), "big")
        sig_int = int.from_bytes(signature, "big")
    except (ValueError, TypeError, OverflowError):
        return False

    if sig_int >= n or sig_int <= 0 or n <= 0 or e <= 0:
        return False

    # m = s^e mod n
    decrypted_int = pow(sig_int, e, n)
    k = (n.bit_length() + 7) // 8
    em = decrypted_int.to_bytes(k, "big")

    expected_hash = hashlib.sha256(message).digest()
    expected_t = SHA256_DIGEST_INFO + expected_hash
    # Minimum EM length = 11 + len(T)
    if len(em) < len(expected_t) + 11:
        return False
    if em[0] != 0 or em[1] != 1:
        return False

    ps_len = len(em) - len(expected_t) - 3
    if ps_len < 8:
        return False
    if any(b != 0xFF for b in em[2 : 2 + ps_len]):
        return False
    if em[2 + ps_len] != 0:
        return False

    actual_t = em[3 + ps_len :]
    return hmac.compare_digest(actual_t, expected_t)


def verify_jwt_rs256_jwks(token: str, jwks: dict[str, Any]) -> dict[str, Any]:
    """Verify an RS256 token against a JWKS key set."""
    header, payload, signing_input, signature = decode_jwt_unverified(token)
    alg = header.get("alg")
    if alg != "RS256":
        raise ValueError(f"Unsupported algorithm for RS256 verification: {alg}")

    kid = header.get("kid")
    keys = jwks.get("keys", [])
    matching_key = None
    if kid:
        for k in keys:
            if k.get("kid") == kid and k.get("kty") == "RSA":
                matching_key = k
                break
    elif len(keys) == 1 and keys[0].get("kty") == "RSA":
        matching_key = keys[0]

    if not matching_key:
        raise ValueError(f"No matching RSA key found in JWKS for kid: {kid}")

    n_b64 = matching_key.get("n")
    e_b64 = matching_key.get("e")
    if not isinstance(n_b64, str) or not isinstance(e_b64, str):
        raise TypeError("Invalid RSA key parameters in JWKS")

    if not verify_rsa_pkcs1v15_sha256(signing_input, signature, n_b64, e_b64):
        raise ValueError("Invalid RS256 signature against JWKS")

    return payload
