"""Security and cryptography package."""

from control_api.modules.identity.security.crypto import (
    SigningKeyError,
    b64url_decode,
    b64url_encode,
    decode_jwt_unverified,
    generate_pkce_pair,
    generate_secure_token,
    sign_jwt_hs256,
    verify_jwt_hs256,
    verify_jwt_rs256_jwks,
    verify_oidc_id_token_rs256,
    verify_pkce,
    verify_rsa_pkcs1v15_sha256,
)

__all__ = [
    "SigningKeyError",
    "b64url_decode",
    "b64url_encode",
    "decode_jwt_unverified",
    "generate_pkce_pair",
    "generate_secure_token",
    "sign_jwt_hs256",
    "verify_jwt_hs256",
    "verify_jwt_rs256_jwks",
    "verify_oidc_id_token_rs256",
    "verify_pkce",
    "verify_rsa_pkcs1v15_sha256",
]
