"""Exercise the actual callback with synthetic HTTP responses and real RSA signatures."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from control_api.core.settings import settings
from control_api.modules.identity.providers.oidc_client import OidcClient
from control_api.modules.identity.repositories.auth_transaction_repository import (
    InMemoryAuthTransactionStore,
)
from control_api.modules.identity.security.crypto import b64url_encode, sign_jwt_hs256
from fastapi import HTTPException

from tests.fixtures.oidc_test_server import OidcTestServer, RsaKeyPair


@pytest.fixture(scope="module")
def idp() -> OidcTestServer:
    return OidcTestServer("https://identity.example.test/realms/platform")


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, idp: OidcTestServer) -> None:
    monkeypatch.setattr(settings, "oidc_issuer", idp.issuer)
    monkeypatch.setattr(settings, "oidc_client_id", "enterprise-studio")
    monkeypatch.setattr(settings, "oidc_additional_trusted_audiences", [])


def signed_token(payload: dict[str, Any], key: RsaKeyPair) -> str:
    header = {"alg": "RS256", "kid": key.kid}
    message = (
        f"{b64url_encode(json.dumps(header).encode())}."
        f"{b64url_encode(json.dumps(payload).encode())}"
    ).encode()
    return f"{message.decode()}.{b64url_encode(key.sign_rs256(message))}"


def prepare_callback(
    idp: OidcTestServer,
    transform: Callable[[str], str] | None = None,
    upstream_error: str = "",
) -> tuple[OidcClient, str, str, httpx.MockTransport, list[str]]:
    client = OidcClient(InMemoryAuthTransactionStore())
    url, state = client.initiate_login("/login", browser_correlation="bound-browser")
    params = {name: values[0] for name, values in parse_qs(urlparse(url).query).items()}
    code = idp.create_authorization_code(
        client_id=params["client_id"],
        redirect_uri=params["redirect_uri"],
        state=state,
        nonce=params["nonce"],
        code_challenge=params["code_challenge"],
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path.endswith("/token"):
            if upstream_error == "token-timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if upstream_error == "token-json":
                return httpx.Response(200, content=b"invalid-json")
            if upstream_error == "token-array":
                return httpx.Response(200, json=[])
            data = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
            assert data["code"] == code
            assert data["client_id"] == params["client_id"]
            tokens = idp.exchange_code_for_token(
                code=data["code"],
                code_verifier=data["code_verifier"],
                client_id=data["client_id"],
                client_secret=data["client_secret"],
                redirect_uri=data["redirect_uri"],
            )
            if transform is not None:
                tokens["id_token"] = transform(tokens["id_token"])
            return httpx.Response(200, json=tokens)
        assert path.endswith("/certs")
        if upstream_error == "jwks-timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if upstream_error == "jwks-http":
            return httpx.Response(503)
        if upstream_error == "jwks-json":
            return httpx.Response(200, content=b"invalid-json")
        if upstream_error == "jwks-array":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=idp.get_jwks())

    return client, state, code, httpx.MockTransport(handler), requests


def callback(client: OidcClient, state: str, code: str, transport: httpx.MockTransport) -> Any:
    async def invoke() -> Any:
        async with httpx.AsyncClient(transport=transport) as http:
            return await client.handle_callback(
                code, state, browser_correlation="bound-browser", http_client=http
            )

    return asyncio.run(invoke())


def test_callback_validates_real_signature_and_one_use_state(idp: OidcTestServer) -> None:
    client, state, code, transport, requests = prepare_callback(idp)
    payload, tx = callback(client, state, code, transport)
    assert payload["sub"] == "user-12345"
    assert payload["nonce"] == tx.nonce
    with pytest.raises(HTTPException) as error:
        callback(client, state, code, transport)
    assert error.value.status_code == 400
    assert len(requests) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"nonce": "wrong"},
        {"iss": "https://evil.example.test"},
        {"aud": "another-client"},
        {"iat": int(time.time()) + 3600},
        {"exp": 1},
        {"azp": "another-client"},
        {"sub": ""},
    ],
)
def test_callback_rejects_claims_without_refreshing_keys(
    idp: OidcTestServer, change: dict[str, Any]
) -> None:
    import jwt

    def tamper(token: str) -> str:
        # Only the fixture decodes unverified data to create a malicious signed token.
        payload = jwt.decode(token, options={"verify_signature": False})
        return signed_token({**payload, **change}, idp.active_key)

    client, state, code, transport, requests = prepare_callback(idp, tamper)
    with pytest.raises(HTTPException) as error:
        callback(client, state, code, transport)
    assert error.value.status_code == 401
    assert error.value.detail == "Invalid OIDC ID token"
    assert sum(path.endswith("/certs") for path in requests) == 1


def test_key_rotation_refreshes_stale_cache_once(idp: OidcTestServer) -> None:
    client, state, code, transport, requests = prepare_callback(idp)
    client._jwks_cache = idp.get_jwks()
    client._jwks_fetched_at = time.time()
    idp.rotate_keys()
    payload, _ = callback(client, state, code, transport)
    assert payload["sub"] == "user-12345"
    assert sum(path.endswith("/certs") for path in requests) == 1


def test_bad_signature_refreshes_once_then_rejects(idp: OidcTestServer) -> None:
    wrong_key = RsaKeyPair(idp.active_key.kid)
    import jwt

    def tamper(token: str) -> str:
        payload = jwt.decode(token, options={"verify_signature": False})
        return signed_token(payload, wrong_key)

    client, state, code, transport, requests = prepare_callback(idp, tamper)
    with pytest.raises(HTTPException) as error:
        callback(client, state, code, transport)
    assert error.value.status_code == 401
    assert sum(path.endswith("/certs") for path in requests) == 2


def test_callback_has_no_hs256_fallback(idp: OidcTestServer) -> None:
    import jwt

    def tamper(token: str) -> str:
        payload = jwt.decode(token, options={"verify_signature": False})
        return sign_jwt_hs256(payload, settings.oidc_client_secret.get_secret_value())

    client, state, code, transport, _ = prepare_callback(idp, tamper)
    with pytest.raises(HTTPException) as error:
        callback(client, state, code, transport)
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("token-timeout", 503),
        ("jwks-timeout", 503),
        ("jwks-http", 503),
        ("token-json", 502),
        ("token-array", 502),
        ("jwks-json", 502),
        ("jwks-array", 502),
    ],
)
def test_upstream_failures_are_sanitized(
    idp: OidcTestServer, failure: str, expected_status: int
) -> None:
    client, state, code, transport, _ = prepare_callback(idp, upstream_error=failure)
    with pytest.raises(HTTPException) as error:
        callback(client, state, code, transport)
    assert error.value.status_code == expected_status
    assert code not in error.value.detail


def test_callback_respects_configured_additional_audiences(
    idp: OidcTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jwt

    def multiple_audiences(token: str) -> str:
        payload = jwt.decode(token, options={"verify_signature": False})
        return signed_token(
            {**payload, "aud": [settings.oidc_client_id, "trusted-api"]}, idp.active_key
        )

    monkeypatch.setattr(settings, "oidc_additional_trusted_audiences", ["trusted-api"])
    client, state, code, transport, _ = prepare_callback(idp, multiple_audiences)
    payload, _ = callback(client, state, code, transport)
    assert payload["aud"] == [settings.oidc_client_id, "trusted-api"]
