"""Deterministic ID Token tests with real RSA signatures, without a live IdP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
from control_api.modules.identity.crypto import (
    b64url_encode,
    sign_jwt_hs256,
    verify_jwt_hs256,
    verify_jwt_rs256_jwks,
    verify_oidc_id_token_rs256,
    verify_rsa_pkcs1v15_sha256,
)
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.fixtures.oidc_test_server import RsaKeyPair

NOW = 1_800_000_000
ISSUER = "https://identity.example.test/realms/platform"
CLIENT = "enterprise-studio"
NONCE = "browser-bound-nonce"


@pytest.fixture(scope="module")
def signing_key() -> RsaKeyPair:
    return RsaKeyPair("trusted-key")


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime.fromtimestamp(NOW, tz=UTC)

    monkeypatch.setattr(jwt.api_jwt, "datetime", FrozenDatetime)


def claims() -> dict[str, Any]:
    return {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "user-1",
        "iat": NOW - 10,
        "exp": NOW + 300,
        "nonce": NONCE,
    }


def token_for(key: RsaKeyPair, payload: Any, header: dict[str, Any] | None = None) -> str:
    hdr = {"alg": "RS256", "kid": key.kid, **(header or {})}
    return raw_token(key, json.dumps(hdr), json.dumps(payload))


def raw_token(key: RsaKeyPair, header: str, payload: str) -> str:
    message = f"{b64url_encode(header.encode())}.{b64url_encode(payload.encode())}".encode()
    return f"{message.decode()}.{b64url_encode(key.sign_rs256(message))}"


def verify(token: str, key: RsaKeyPair, **policy: Any) -> dict[str, object]:
    return verify_oidc_id_token_rs256(
        token,
        {"keys": [key.to_jwk()]},
        issuer=ISSUER,
        client_id=CLIENT,
        nonce=NONCE,
        **policy,
    )


def test_valid_token_and_compatibility_helpers(signing_key: RsaKeyPair) -> None:
    payload = claims()
    token = token_for(signing_key, payload)
    assert verify(token, signing_key) == payload
    expired = {**payload, "exp": NOW - 1}
    assert (
        verify_jwt_rs256_jwks(token_for(signing_key, expired), {"keys": [signing_key.to_jwk()]})
        == expired
    )  # Legacy helper intentionally checks only the signature.
    signed_input, signature = token.rsplit(".", 1)
    from control_api.modules.identity.crypto import b64url_decode

    jwk = signing_key.to_jwk()
    assert verify_rsa_pkcs1v15_sha256(
        signed_input.encode(), b64url_decode(signature), jwk["n"], jwk["e"]
    )
    assert not verify_rsa_pkcs1v15_sha256(b"tampered", b"bad", jwk["n"], jwk["e"])
    hs_token = sign_jwt_hs256({"sub": "session-user"}, "separate-session-secret")
    assert verify_jwt_hs256(hs_token, "separate-session-secret")["sub"] == "session-user"
    with pytest.raises(ValueError):
        verify(hs_token, signing_key)


@pytest.mark.parametrize("missing", ["iss", "aud", "exp", "iat", "sub", "nonce"])
def test_required_claims(signing_key: RsaKeyPair, missing: str) -> None:
    payload = claims()
    del payload[missing]
    with pytest.raises(ValueError):
        verify(token_for(signing_key, payload), signing_key)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "https://attacker.example.test"),
        ("aud", "another-client"),
        ("aud", []),
        ("aud", [CLIENT, {}]),
        ("aud", [CLIENT, "untrusted-audience"]),
        ("sub", ""),
        ("sub", "  "),
        ("sub", 42),
        ("nonce", "wrong"),
        ("nonce", None),
        ("nonce", "\ud800"),
        ("azp", "wrong-client"),
        ("azp", 42),
        ("azp", None),
        ("exp", NOW),
        ("exp", NOW - 1),
        ("iat", NOW + 1),
        ("nbf", NOW + 1),
    ],
)
def test_invalid_identity_and_time_claims(signing_key: RsaKeyPair, claim: str, value: Any) -> None:
    with pytest.raises(ValueError):
        verify(token_for(signing_key, {**claims(), claim: value}), signing_key)


@pytest.mark.parametrize("claim", ["iat", "exp", "nbf"])
@pytest.mark.parametrize("value", [True, False, "1800000000", None, float("nan"), float("inf")])
def test_numeric_dates_are_finite_numbers(signing_key: RsaKeyPair, claim: str, value: Any) -> None:
    with pytest.raises(ValueError):
        verify(token_for(signing_key, {**claims(), claim: value}), signing_key)


def test_expiry_precision_leeway_and_ordering(signing_key: RsaKeyPair) -> None:
    assert verify(token_for(signing_key, {**claims(), "exp": NOW + 1}), signing_key)
    expired = token_for(signing_key, {**claims(), "exp": NOW})
    assert verify(expired, signing_key, leeway_seconds=1)
    for leeway in [-1, 61, True, 0.5]:
        with pytest.raises(ValueError):
            verify(expired, signing_key, leeway_seconds=leeway)
    for change in ({"iat": NOW, "exp": NOW}, {"nbf": NOW, "exp": NOW}):
        with pytest.raises(ValueError):
            verify(token_for(signing_key, {**claims(), **change}), signing_key, leeway_seconds=1)


def test_additional_audiences_need_trust_and_authorized_party(signing_key: RsaKeyPair) -> None:
    payload = {**claims(), "aud": [CLIENT, "trusted-api"], "azp": CLIENT}
    token = token_for(signing_key, payload)
    assert verify(token, signing_key, additional_trusted_audiences=["trusted-api"]) == payload
    for azp in [None, "wrong-client", 123]:
        with pytest.raises(ValueError):
            verify(
                token_for(signing_key, {**payload, "azp": azp}),
                signing_key,
                additional_trusted_audiences=["trusted-api"],
            )
    with pytest.raises(ValueError):
        verify(token, signing_key)


@pytest.mark.parametrize("name", ["crit", "jku", "jwk", "x5u", "b64"])
def test_untrusted_jose_extensions_rejected(signing_key: RsaKeyPair, name: str) -> None:
    with pytest.raises(ValueError):
        verify(token_for(signing_key, claims(), {name: None}), signing_key)


@pytest.mark.parametrize(
    "header", [{"alg": "none"}, {"alg": "HS256"}, {"kid": ""}, {"kid": 42}, {"kid": "a" * 257}]
)
def test_invalid_header(signing_key: RsaKeyPair, header: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        verify(token_for(signing_key, claims(), header), signing_key)


@pytest.mark.parametrize(
    "change",
    [
        {"alg": "HS256"},
        {"use": "enc"},
        {"key_ops": ["sign"]},
        {"key_ops": "verify"},
        {"key_ops": ["verify", 1]},
        {"n": "!"},
        {"n": "a" * 1501},
        {"e": "a" * 17},
        {"n": None},
        {"e": "AA"},
    ],
)
def test_invalid_jwk(signing_key: RsaKeyPair, change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        verify_oidc_id_token_rs256(
            token_for(signing_key, claims()),
            {"keys": [{**signing_key.to_jwk(), **change}]},
            issuer=ISSUER,
            client_id=CLIENT,
            nonce=NONCE,
        )


@pytest.mark.parametrize("keys", [None, {}, [], [None], [{}] * 65])
def test_malformed_key_sets(signing_key: RsaKeyPair, keys: Any) -> None:
    with pytest.raises(ValueError):
        verify_oidc_id_token_rs256(
            token_for(signing_key, claims()),
            {"keys": keys},
            issuer=ISSUER,
            client_id=CLIENT,
            nonce=NONCE,
        )


def test_ambiguous_keys_and_weak_rsa(signing_key: RsaKeyPair) -> None:
    token = token_for(signing_key, claims())
    jwk = signing_key.to_jwk()
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key()
    weak_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(weak))
    weak_jwk["kid"] = signing_key.kid
    for keys in [[jwk, jwk], [weak_jwk], [{**jwk, "kid": "unknown"}]]:
        with pytest.raises(ValueError):
            verify_oidc_id_token_rs256(
                token, {"keys": keys}, issuer=ISSUER, client_id=CLIENT, nonce=NONCE
            )


def test_signature_tampering_rejected(signing_key: RsaKeyPair) -> None:
    other = RsaKeyPair(signing_key.kid)
    with pytest.raises(ValueError):
        verify(token_for(other, claims()), signing_key)


def test_single_key_without_kid_and_verify_key_operations(signing_key: RsaKeyPair) -> None:
    header = json.dumps({"alg": "RS256"})
    token = raw_token(signing_key, header, json.dumps(claims()))
    jwk = {**signing_key.to_jwk(), "key_ops": ["verify"]}
    assert (
        verify_oidc_id_token_rs256(
            token, {"keys": [jwk]}, issuer=ISSUER, client_id=CLIENT, nonce=NONCE
        )["sub"]
        == "user-1"
    )
    with pytest.raises(ValueError):
        verify_oidc_id_token_rs256(
            token,
            {"keys": [jwk, {**jwk, "kid": "another-key"}]},
            issuer=ISSUER,
            client_id=CLIENT,
            nonce=NONCE,
        )


def test_multiple_audiences_without_azp_rejected(signing_key: RsaKeyPair) -> None:
    payload = {**claims(), "aud": [CLIENT, "trusted-api"]}
    with pytest.raises(ValueError):
        verify(
            token_for(signing_key, payload),
            signing_key,
            additional_trusted_audiences=["trusted-api"],
        )


def test_ambiguous_and_malformed_json_rejected(signing_key: RsaKeyPair) -> None:
    header = json.dumps({"alg": "RS256", "kid": signing_key.kid})
    payload = json.dumps(claims())
    tokens = [
        raw_token(signing_key, header[:-1] + ',"alg":"RS256"}', payload),
        raw_token(signing_key, header, payload[:-1] + ',"sub":"another-user"}'),
        raw_token(signing_key, header, "[]"),
        raw_token(signing_key, header, "[" * 1200 + "0" + "]" * 1200),
        "broken",
        "a.b.c",
        "x" * 65537,
    ]
    for token in tokens:
        with pytest.raises(ValueError):
            verify(token, signing_key)
