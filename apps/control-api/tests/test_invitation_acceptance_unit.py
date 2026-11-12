"""Comprehensive Unit & Security Tests for Invitation Acceptance (Step 03-C / ADR-020)."""

from __future__ import annotations

import importlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.identity.models.session import (
    VerifiedIdentityEvidence,
)
from control_api.modules.identity.providers.oidc_client import oidc_client
from control_api.modules.identity.repositories.auth_transaction_repository import (
    InMemoryAuthTransactionStore,
)
from control_api.modules.identity.repositories.session_repository import (
    InMemoryRevocationStore,
)
from control_api.modules.identity.services.session_service import session_manager
from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationGoneError,
    InvitationNotFoundError,
)
from control_api.modules.workspaces.repositories.invitation_repository import (
    InvitationRepository,
)
from control_api.modules.workspaces.services import handoff_store as handoff_store_module
from control_api.modules.workspaces.services.handoff_store import (
    InMemoryInvitationHandoffStore,
    InvitationHandoffProtector,
    generate_browser_binding_token,
    hash_browser_binding_token,
)
from cryptography.fernet import Fernet
from fastapi import status
from fastapi.testclient import TestClient
from platform_common.invitations import hash_invitation_proof

from tests.fixtures.oidc_http_server import run_oidc_http_server

accept_router_module = importlib.import_module(
    "control_api.modules.workspaces.api.invitation_accept_router"
)


class _StubResult:
    def __init__(
        self, item: Any = None, items: list[Any] | None = None, scalar: Any = None
    ) -> None:
        self._item = item
        self._items = items or []
        self._scalar = scalar

    def mappings(self) -> _StubResult:
        return self

    def one_or_none(self) -> Any:
        return self._item

    def one(self) -> Any:
        return self._item

    def all(self) -> list[Any]:
        return self._items

    def scalar_one_or_none(self) -> Any:
        return self._scalar


@pytest.fixture()
def unit_env(monkeypatch: pytest.MonkeyPatch):
    proof_key = Fernet.generate_key().decode("ascii")
    handoff_key = Fernet.generate_key().decode("ascii")

    monkeypatch.setattr(settings, "invitations_enabled", True)
    monkeypatch.setattr(settings, "invitation_public_app_origin", "http://localhost:3000")
    monkeypatch.setattr(settings, "invitation_proof_key_id", "proof-v1")
    monkeypatch.setattr(
        settings.invitation_proof_encryption_key, "get_secret_value", lambda: proof_key
    )
    monkeypatch.setattr(settings, "invitation_handoff_key_id", "handoff-v1")
    monkeypatch.setattr(
        settings.invitation_handoff_encryption_key, "get_secret_value", lambda: handoff_key
    )
    monkeypatch.setattr(settings, "invitation_handoff_ttl_seconds", 600)
    monkeypatch.setattr(settings, "invitation_identity_freshness_seconds", 900)
    monkeypatch.setattr(settings, "invitation_identity_max_future_skew_seconds", 60)
    monkeypatch.setattr(settings, "invitation_handoff_browser_rate_limit", 5)
    monkeypatch.setattr(settings, "invitation_handoff_ip_rate_limit", 30)
    monkeypatch.setattr(settings, "invitation_handoff_rate_window_seconds", 600)
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", [])

    orig_tx_store = oidc_client.store
    orig_denylist = session_manager.denylist
    orig_handoff_store = handoff_store_module.invitation_handoff_store

    oidc_client.store = InMemoryAuthTransactionStore()
    session_manager.denylist = InMemoryRevocationStore()
    handoff_store_module.invitation_handoff_store = InMemoryInvitationHandoffStore()

    users_by_issuer_sub: dict[tuple[str, str], dict[str, Any]] = {}
    users_by_id: dict[str, dict[str, Any]] = {}
    mock_session = AsyncMock()

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> _StubResult:
        sql = str(stmt)
        params = params or {}
        if (
            "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE issuer = :issuer AND subject = :subject"
            in sql
        ):
            return _StubResult(
                item=users_by_issuer_sub.get((str(params["issuer"]), str(params["subject"])))
            )
        if "INSERT INTO iam.users" in sql:
            uid = str(uuid4())
            rec = {
                "id": uid,
                "issuer": params["issuer"],
                "subject": params["subject"],
                "display_name": params.get("display_name"),
                "email_normalized": params.get("email_normalized"),
            }
            users_by_issuer_sub[(str(params["issuer"]), str(params["subject"]))] = rec
            users_by_id[uid] = rec
            return _StubResult(item=rec)
        if (
            "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE id = :id"
            in sql
        ):
            return _StubResult(item=users_by_id.get(str(params["id"])))
        if "iam.get_user_workspaces" in sql:
            return _StubResult(items=[])
        return _StubResult()

    mock_session.execute.side_effect = mock_execute
    app.dependency_overrides[get_db_session] = lambda: mock_session

    client = TestClient(app, base_url="http://localhost:8000")
    try:
        yield {
            "client": client,
            "session": mock_session,
            "users_by_id": users_by_id,
            "users_by_issuer_sub": users_by_issuer_sub,
            "proof_key": proof_key,
            "handoff_key": handoff_key,
        }
    finally:
        app.dependency_overrides.clear()
        oidc_client.store = orig_tx_store
        session_manager.denylist = orig_denylist
        handoff_store_module.invitation_handoff_store = orig_handoff_store
        oidc_client._jwks_cache = None


def _origin_headers(origin: str = "http://localhost:3000") -> dict[str, str]:
    return {"Origin": origin}


def _fresh_session_for_user(
    unit_env: dict[str, Any],
    *,
    issuer: str = "http://localhost:8080/realms/enterprise",
    email: str = "invitee@example.com",
    email_verified: Any = True,
    iat: int | None = None,
    observed_at: int | None = None,
    with_evidence: bool = True,
) -> tuple[str, str, str]:
    now = int(time.time())
    user_id = str(uuid4())
    subject = f"sub-{uuid4().hex[:8]}"
    rec = {
        "id": user_id,
        "issuer": issuer,
        "subject": subject,
        "display_name": "Invitee User",
        "email_normalized": email,
    }
    unit_env["users_by_issuer_sub"][(issuer, subject)] = rec
    unit_env["users_by_id"][user_id] = rec

    evidence = (
        VerifiedIdentityEvidence(
            issuer=issuer,
            subject=subject,
            user_id=user_id,
            email_normalized=email,
            email_verified=email_verified,
            id_token_issued_at=now if iat is None else iat,
            observed_at=now if observed_at is None else observed_at,
        )
        if with_evidence
        else None
    )
    _sess, session_jwt = session_manager.create_session(
        user_id,
        issuer,
        subject,
        identity_evidence=evidence,
    )
    return user_id, subject, session_jwt


# ---------------------------------------------------------------------------
# 1. VerifiedIdentityEvidence & Session Freshness / Credential Boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_verified", ["true", "1", 1, False, None, "yes"])
def test_identity_evidence_requires_strict_boolean_email_verified(
    unit_env: dict[str, Any], bad_verified: Any
) -> None:
    inv_id = uuid4()
    _uid, _sub, session_jwt = _fresh_session_for_user(unit_env, email_verified=bad_verified)
    client: TestClient = unit_env["client"]
    client.cookies.set(settings.session_cookie_name, session_jwt)

    resp = client.post(f"/v1/invitations/{inv_id}/preview", headers=_origin_headers())
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"]["code"] == "recipient_email_unverified"


def test_identity_evidence_freshness_boundary_899_pass_900_fail() -> None:
    now = 1_700_000_000
    ev = VerifiedIdentityEvidence(
        issuer="https://idp",
        subject="sub-1",
        user_id=str(uuid4()),
        email_normalized="u@example.com",
        email_verified=True,
        id_token_issued_at=now - 899,
        observed_at=now - 899,
    )
    assert ev.is_fresh(now, max_age_seconds=900, max_future_skew_seconds=60) is True
    assert ev.is_fresh(now + 1, max_age_seconds=900, max_future_skew_seconds=60) is False

    # Future skew boundary: +60 OK, +61 rejected
    future_ok = VerifiedIdentityEvidence(
        issuer="https://idp",
        subject="sub-1",
        user_id=str(uuid4()),
        email_normalized="u@example.com",
        email_verified=True,
        id_token_issued_at=now + 60,
        observed_at=now,
    )
    assert future_ok.is_fresh(now, max_age_seconds=900, max_future_skew_seconds=60) is True

    future_bad = VerifiedIdentityEvidence(
        issuer="https://idp",
        subject="sub-1",
        user_id=str(uuid4()),
        email_normalized="u@example.com",
        email_verified=True,
        id_token_issued_at=now + 61,
        observed_at=now,
    )
    assert future_bad.is_fresh(now, max_age_seconds=900, max_future_skew_seconds=60) is False

    # If observed_at is fresh but id_token_issued_at is old (>=900s), must fail
    old_iat = VerifiedIdentityEvidence(
        issuer="https://idp",
        subject="sub-1",
        user_id=str(uuid4()),
        email_normalized="u@example.com",
        email_verified=True,
        id_token_issued_at=now - 900,
        observed_at=now - 10,
    )
    assert old_iat.is_fresh(now, max_age_seconds=900, max_future_skew_seconds=60) is False


def test_session_refresh_preserves_evidence_timestamps_without_extending(
    unit_env: dict[str, Any],
) -> None:
    inv_id = uuid4()
    now = int(time.time())
    stale_iat = now - 895
    stale_obs = now - 895
    _uid, _sub, session_jwt = _fresh_session_for_user(
        unit_env, iat=stale_iat, observed_at=stale_obs
    )
    client: TestClient = unit_env["client"]
    client.cookies.set(settings.session_cookie_name, session_jwt)

    refresh_resp = client.post("/v1/auth/refresh", headers=_origin_headers())
    assert refresh_resp.status_code == status.HTTP_200_OK
    rotated_jwt = refresh_resp.cookies[settings.session_cookie_name]
    verified = session_manager.verify_session_token(rotated_jwt)
    assert verified.identity_evidence is not None
    assert verified.identity_evidence.id_token_issued_at == stale_iat
    assert verified.identity_evidence.observed_at == stale_obs

    # Even if evidence becomes stale (>= 900s), refresh never extends it -> 403 reauthentication_required
    session_manager.denylist.revoke_session(verified.session_id, now + 600)
    _uid2, _sub2, expired_ev_jwt = _fresh_session_for_user(
        unit_env, iat=now - 900, observed_at=now - 900
    )
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, expired_ev_jwt)
    resp = client.post(f"/v1/invitations/{inv_id}/preview", headers=_origin_headers())
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"]["code"] == "reauthentication_required"


def test_legacy_session_without_evidence_and_workspace_scoped_token_rejected(
    unit_env: dict[str, Any],
) -> None:
    inv_id = uuid4()
    client: TestClient = unit_env["client"]

    # 1. Legacy session without identity_evidence -> 403 reauthentication_required
    _uid, _sub, legacy_jwt = _fresh_session_for_user(unit_env, with_evidence=False)
    client.cookies.set(settings.session_cookie_name, legacy_jwt)
    resp_legacy = client.post(f"/v1/invitations/{inv_id}/preview", headers=_origin_headers())
    assert resp_legacy.status_code == status.HTTP_403_FORBIDDEN
    assert resp_legacy.json()["detail"]["code"] == "reauthentication_required"

    # 2. Bearer session JWT preserves Redis identity_evidence & validates against handoff
    uid, _sub2, fresh_jwt = _fresh_session_for_user(unit_env)
    client.cookies.clear()
    handoff_resp = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": "opaque-proof-123"},
        headers=_origin_headers(),
    )
    assert handoff_resp.status_code == status.HTTP_201_CREATED

    async def fake_preview(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "invitation_id": str(inv_id),
            "tenant_id": str(uuid4()),
            "tenant_slug": "vinai",
            "tenant_name": "VinAI",
            "workspace_id": str(uuid4()),
            "workspace_slug": "alpha",
            "workspace_name": "Alpha",
            "role": "developer",
            "status": "pending",
            "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
            "can_accept": True,
            "acceptance_state": "ready",
        }

    orig_preview = accept_router_module.acceptance_service._repository.preview_invitation
    accept_router_module.acceptance_service._repository.preview_invitation = fake_preview
    try:
        resp_bearer = client.post(
            f"/v1/invitations/{inv_id}/preview",
            headers={**_origin_headers(), "Authorization": f"Bearer {fresh_jwt}"},
        )
        assert resp_bearer.status_code == status.HTTP_200_OK
    finally:
        accept_router_module.acceptance_service._repository.preview_invitation = orig_preview

    # 3. Scoped workspace token (session_data is None) -> 403 credential_type_not_allowed
    from control_api.modules.identity.security.crypto import sign_jwt_hs256

    fresh_sess = session_manager.verify_session_token(fresh_jwt)
    now_ts = int(time.time())
    scoped_secret = settings.runtime_jwt_secret.get_secret_value()
    if len(scoped_secret) < 32:
        scoped_secret = settings.jwt_secret_key.get_secret_value()
    scoped_jwt = sign_jwt_hs256(
        {
            "sub": uid,
            "user_id": uid,
            "email": "invitee@example.com",
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "session_id": fresh_sess.session_id,
            "iss": settings.runtime_jwt_issuer or settings.jwt_issuer,
            "aud": [settings.jwt_audience, settings.runtime_jwt_audience],
            "roles": ["developer"],
            "scopes": ["agents:read"],
            "iat": now_ts,
            "nbf": now_ts,
            "exp": now_ts + 300,
        },
        scoped_secret,
    )
    resp_scoped = client.post(
        f"/v1/invitations/{inv_id}/accept",
        headers={**_origin_headers(), "Authorization": f"Bearer {scoped_jwt}"},
    )
    assert resp_scoped.status_code == status.HTTP_403_FORBIDDEN
    assert resp_scoped.json()["detail"]["code"] == "credential_type_not_allowed"


# ---------------------------------------------------------------------------
# 2. OIDC /login & /callback Evidence + Browser Handoff Cookie Binding
# ---------------------------------------------------------------------------


def test_oidc_callback_extracts_strict_evidence_and_enforces_handoff_binding(
    unit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id = uuid4()
    client: TestClient = unit_env["client"]

    with run_oidc_http_server() as (idp_server, issuer_url):
        monkeypatch.setattr(settings, "oidc_issuer", issuer_url)
        monkeypatch.setattr(settings, "oidc_redirect_uri", "http://localhost:8000/v1/auth/callback")

        # 2a. Browser creates handoff -> initiates /login with invitation_id & reauth=true
        handoff_resp = client.post(
            f"/v1/invitations/{inv_id}/handoff",
            json={"token": "proof-token-secret"},
            headers=_origin_headers(),
        )
        assert handoff_resp.status_code == status.HTTP_201_CREATED
        login_resp = client.get(
            f"/v1/auth/login?return_to=/invitations/{inv_id}/accept&invitation_id={inv_id}&reauth=true"
        )
        assert login_resp.status_code == status.HTTP_200_OK
        auth_url = login_resp.json()["authorization_url"]
        state = login_resp.json()["state"]
        assert "prompt=login" in auth_url

        tx = oidc_client.store._transactions.get(state)
        assert tx is not None
        assert tx.pending_invitation_id == str(inv_id)
        assert tx.pending_handoff_binding_digest is not None

        # Extract query params from authorization_url via urllib
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(auth_url).query)
        code = idp_server.create_authorization_code(
            client_id=settings.oidc_client_id,
            redirect_uri=settings.oidc_redirect_uri,
            state=state,
            nonce=q["nonce"][0],
            code_challenge=q["code_challenge"][0],
            subject="sub-invitee-oidc",
            name="Invitee",
            email="invitee@example.com",
            email_verified="true",  # string "true" must fail strict boolean check!
        )
        cb_unverified = client.get(
            f"/v1/auth/callback?code={code}&state={state}",
            headers={"Accept": "application/json"},
        )
        assert cb_unverified.status_code == status.HTTP_200_OK
        sess_unv = session_manager.verify_session_token(
            cb_unverified.cookies[settings.session_cookie_name]
        )
        assert sess_unv.identity_evidence is not None
        assert sess_unv.identity_evidence.email_verified is False

        # Mismatched handoff cookie at /callback must be rejected (400, no session cookie)
        login_resp2 = client.get(
            f"/v1/auth/login?return_to=/invitations/{inv_id}/accept&invitation_id={inv_id}"
        )
        state2 = login_resp2.json()["state"]
        q2 = parse_qs(urlparse(login_resp2.json()["authorization_url"]).query)
        code2 = idp_server.create_authorization_code(
            client_id=settings.oidc_client_id,
            redirect_uri=settings.oidc_redirect_uri,
            state=state2,
            nonce=q2["nonce"][0],
            code_challenge=q2["code_challenge"][0],
            subject="sub-invitee-oidc",
            name="Invitee",
            email="invitee@example.com",
            email_verified=True,
        )
        orig_handoff_cookie = client.cookies.get(settings.invitation_handoff_cookie_name)
        assert orig_handoff_cookie
        corr_cookie = cb_unverified.cookies.get(settings.oidc_correlation_cookie_name) or ""
        client.cookies.clear()
        for cookie_obj in login_resp2.cookies.jar:
            client.cookies.set(cookie_obj.name, cookie_obj.value)
        client.cookies.set(
            settings.invitation_handoff_cookie_name, generate_browser_binding_token()
        )
        cb_mismatch = client.get(
            f"/v1/auth/callback?code={code2}&state={state2}",
            headers={"Accept": "application/json"},
        )
        assert cb_mismatch.status_code == status.HTTP_400_BAD_REQUEST
        assert "handoff browser binding mismatch" in cb_mismatch.json()["detail"].lower()

        # Restoring exact handoff cookie succeeds and populates strict email_verified=True evidence
        client.cookies.clear()
        client.cookies.set(settings.invitation_handoff_cookie_name, orig_handoff_cookie)
        _ = corr_cookie
        login_resp3 = client.get(
            f"/v1/auth/login?return_to=/invitations/{inv_id}/accept&invitation_id={inv_id}"
        )
        state3 = login_resp3.json()["state"]
        q3 = parse_qs(urlparse(login_resp3.json()["authorization_url"]).query)
        code3 = idp_server.create_authorization_code(
            client_id=settings.oidc_client_id,
            redirect_uri=settings.oidc_redirect_uri,
            state=state3,
            nonce=q3["nonce"][0],
            code_challenge=q3["code_challenge"][0],
            subject="sub-invitee-oidc",
            name="Invitee",
            email="invitee@example.com",
            email_verified=True,
        )
        cb_ok = client.get(
            f"/v1/auth/callback?code={code3}&state={state3}",
            headers={"Accept": "application/json"},
        )
        assert cb_ok.status_code == status.HTTP_200_OK
        sess_ok = session_manager.verify_session_token(cb_ok.cookies[settings.session_cookie_name])
        assert sess_ok.identity_evidence is not None
        assert sess_ok.identity_evidence.email_verified is True
        assert sess_ok.identity_evidence.issuer == issuer_url
        assert sess_ok.identity_evidence.email_normalized == "invitee@example.com"


# ---------------------------------------------------------------------------
# 3. Handoff Protector, Key Separation, TTL, Rate Limit, Proxy IP, 422 Redaction, CSRF
# ---------------------------------------------------------------------------


def test_handoff_protector_encryption_distinct_key_ttl_and_binding(
    unit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_700_000_000
    inv_id = str(uuid4())
    cookie_token = generate_browser_binding_token()
    digest = hash_browser_binding_token(cookie_token)

    protector = InvitationHandoffProtector(
        fernet_key=unit_env["handoff_key"],
        key_id="handoff-v1",
    )
    ciphertext = protector.encrypt_handoff(
        invitation_id=inv_id,
        raw_proof="secret-invite-proof",
        browser_binding_digest=digest,
        issued_at=now,
        expires_at=now + 600,
    )
    assert "secret-invite-proof" not in ciphertext

    # 599s passes
    dec = protector.decrypt_handoff(
        ciphertext=ciphertext,
        expected_invitation_id=inv_id,
        expected_browser_digest=digest,
        now=now + 599,
    )
    assert dec is not None
    assert dec.token_hash == hash_invitation_proof("secret-invite-proof")

    # 600s expired -> None -> 404
    assert (
        protector.decrypt_handoff(
            ciphertext=ciphertext,
            expected_invitation_id=inv_id,
            expected_browser_digest=digest,
            now=now + 600,
        )
        is None
    )

    # Binding mismatch (wrong invitation or wrong browser digest) -> None
    assert (
        protector.decrypt_handoff(
            ciphertext=ciphertext,
            expected_invitation_id=str(uuid4()),
            expected_browser_digest=digest,
            now=now + 10,
        )
        is None
    )
    assert (
        protector.decrypt_handoff(
            ciphertext=ciphertext,
            expected_invitation_id=inv_id,
            expected_browser_digest="0" * 64,
            now=now + 10,
        )
        is None
    )

    # Identical proof & handoff keys must fail closed with 503
    monkeypatch.setattr(
        settings.invitation_handoff_encryption_key,
        "get_secret_value",
        lambda: unit_env["proof_key"],
    )
    client: TestClient = unit_env["client"]
    resp_same_key = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": "raw-proof"},
        headers=_origin_headers(),
    )
    assert resp_same_key.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert resp_same_key.json()["detail"]["code"] == "invitation_handoff_configuration_invalid"

    # Missing/placeholder handoff key must fail closed with 503
    monkeypatch.setattr(
        settings.invitation_handoff_encryption_key,
        "get_secret_value",
        lambda: "dev-change-me-handoff",
    )
    resp_missing = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": "raw-proof"},
        headers=_origin_headers(),
    )
    assert resp_missing.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_handoff_rate_limits_trusted_proxy_csrf_and_422_redaction(
    unit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id = uuid4()
    client: TestClient = unit_env["client"]
    secret_proof = "ultra-secret-proof-do-not-echo"

    # 1. CSRF checks even when unauthenticated
    for bad_headers in [
        {},
        {"Origin": "null"},
        {"Origin": "https://evil.example"},
        {"Origin": "http://localhost:3000", "Sec-Fetch-Site": "cross-site"},
    ]:
        r = client.post(
            f"/v1/invitations/{inv_id}/handoff",
            json={"token": secret_proof},
            headers=bad_headers,
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN

    # 2. 422 redaction on /handoff never echoes raw token or malformed input
    r_bad_handoff = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": secret_proof, "unexpected": "extra-field"},
        headers=_origin_headers(),
    )
    assert r_bad_handoff.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert secret_proof not in r_bad_handoff.text
    for err in r_bad_handoff.json()["detail"]:
        assert "input" not in err

    # 3. /preview and /accept reject non-empty or malformed JSON bodies with 422 (authenticated)
    _uid, _sub, session_jwt = _fresh_session_for_user(unit_env)
    client.cookies.set(settings.session_cookie_name, session_jwt)
    for ep in ("preview", "accept"):
        r_body = client.post(
            f"/v1/invitations/{inv_id}/{ep}",
            json={"token": secret_proof},
            headers=_origin_headers(),
        )
        assert r_body.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert secret_proof not in r_body.text
        r_malformed = client.post(
            f"/v1/invitations/{inv_id}/{ep}",
            content=b"{not-json",
            headers={**_origin_headers(), "Content-Type": "application/json"},
        )
        assert r_malformed.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 4. Rate limit: 5/600s browser limit -> 6th returns 429 + Retry-After
    client.cookies.clear()
    for idx in range(5):
        ok_r = client.post(
            f"/v1/invitations/{inv_id}/handoff",
            json={"token": f"proof-token-00000-{idx}"},
            headers={**_origin_headers(), "X-Forwarded-For": "203.0.113.10"},
        )
        assert ok_r.status_code == status.HTTP_201_CREATED
        assert "no-store" in ok_r.headers.get("cache-control", "").lower()

    limited_r = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": "proof-token-00000-limit"},
        headers={**_origin_headers(), "X-Forwarded-For": "198.51.100.77"},
    )
    assert limited_r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert int(limited_r.headers["retry-after"]) >= 1

    # 5. IP extraction trusts X-Forwarded-For ONLY from trusted_proxy_cidrs
    assert (
        accept_router_module._extract_trusted_client_ip(
            type(
                "Req",
                (),
                {
                    "client": type("C", (), {"host": "198.51.100.5"})(),
                    "headers": {"x-forwarded-for": "1.2.3.4"},
                },
            )()
        )
        == "198.51.100.5"
    )
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", ["10.0.0.0/8"])
    assert (
        accept_router_module._extract_trusted_client_ip(
            type(
                "Req",
                (),
                {
                    "client": type("C", (), {"host": "10.1.2.3"})(),
                    "headers": {"x-forwarded-for": "203.0.113.99, 10.1.2.4"},
                },
            )()
        )
        == "203.0.113.99"
    )


# ---------------------------------------------------------------------------
# 4. Privacy-Preserving Error Precedence, Repository Logic, & Logout Cookie Clearing
# ---------------------------------------------------------------------------


def _base_inv_row(
    *,
    token_hash: str,
    inviter_id: UUID,
    issuer: str = "https://idp.local",
    email: str = "invitee@example.com",
    inv_status: str = "pending",
    is_expired: bool = False,
    accepted_by: UUID | None = None,
    role: str = "developer",
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "token_hash": token_hash,
        "recipient_issuer": issuer,
        "recipient_email_normalized": email,
        "role": role,
        "status": inv_status,
        "is_expired": is_expired,
        "accepted_by_user_id": accepted_by,
        "delivery_requested_by_user_id": inviter_id,
    }


def test_repository_evaluation_privacy_precedence_and_lifecycle_cases() -> None:
    inviter_id = uuid4()
    recipient_id = uuid4()
    other_user_id = uuid4()
    good_hash = hash_invitation_proof("good-proof")
    wrong_hash = hash_invitation_proof("wrong-proof")
    active_inviter = {
        str(inviter_id): {"id": uuid4(), "user_id": inviter_id, "role": "admin", "status": "active"}
    }

    # Nonexistent or wrong proof -> identical 404 (even if invite is revoked/expired or wrong recipient!)
    for row in [
        None,
        _base_inv_row(
            token_hash=good_hash,
            inviter_id=inviter_id,
            email="someone@example.com",
            inv_status="revoked",
        ),
    ]:
        with pytest.raises(InvitationNotFoundError) as exc_nf:
            InvitationRepository._evaluate_invitation_acceptance(
                inv_row=row,
                expected_token_hash=wrong_hash if row else good_hash,
                recipient_user_id=recipient_id,
                recipient_issuer="https://idp.local",
                recipient_email_normalized="attacker@example.com",
                tenant_status="active",
                workspace_status="active",
                memberships_by_user_id=active_inviter,
            )
        assert exc_nf.value.code == "invitation_not_found"

    # Valid proof + wrong issuer OR wrong email (even when revoked/expired) -> 403 recipient_mismatch
    for bad_issuer, bad_email in [
        ("https://other-idp.local", "invitee@example.com"),
        ("https://idp.local", "wrong@example.com"),
    ]:
        with pytest.raises(InvitationForbiddenError) as exc_mm:
            InvitationRepository._evaluate_invitation_acceptance(
                inv_row=_base_inv_row(
                    token_hash=good_hash, inviter_id=inviter_id, inv_status="revoked"
                ),
                expected_token_hash=good_hash,
                recipient_user_id=recipient_id,
                recipient_issuer=bad_issuer,
                recipient_email_normalized=bad_email,
                tenant_status="active",
                workspace_status="active",
                memberships_by_user_id=active_inviter,
            )
        assert exc_mm.value.code == "recipient_mismatch"
        assert "invitee@example.com" not in exc_mm.value.message

    # Valid proof + matching recipient + revoked/expired -> 410 Gone
    for st, exp, expected_code in [
        ("revoked", False, "invitation_revoked"),
        ("pending", True, "invitation_expired"),
        ("expired", False, "invitation_expired"),
    ]:
        with pytest.raises(InvitationGoneError) as exc_gone:
            InvitationRepository._evaluate_invitation_acceptance(
                inv_row=_base_inv_row(
                    token_hash=good_hash, inviter_id=inviter_id, inv_status=st, is_expired=exp
                ),
                expected_token_hash=good_hash,
                recipient_user_id=recipient_id,
                recipient_issuer="https://idp.local",
                recipient_email_normalized="invitee@example.com",
                tenant_status="active",
                workspace_status="active",
                memberships_by_user_id=active_inviter,
            )
        assert exc_gone.value.code == expected_code

    # Accepted by different user -> 409; accepted by same user with active membership -> already_accepted
    with pytest.raises(InvitationConflictError):
        InvitationRepository._evaluate_invitation_acceptance(
            inv_row=_base_inv_row(
                token_hash=good_hash,
                inviter_id=inviter_id,
                inv_status="accepted",
                accepted_by=other_user_id,
            ),
            expected_token_hash=good_hash,
            recipient_user_id=recipient_id,
            recipient_issuer="https://idp.local",
            recipient_email_normalized="invitee@example.com",
            tenant_status="active",
            workspace_status="active",
            memberships_by_user_id=active_inviter,
        )

    state_replay, role_replay = InvitationRepository._evaluate_invitation_acceptance(
        inv_row=_base_inv_row(
            token_hash=good_hash,
            inviter_id=inviter_id,
            inv_status="accepted",
            accepted_by=recipient_id,
        ),
        expected_token_hash=good_hash,
        recipient_user_id=recipient_id,
        recipient_issuer="https://idp.local",
        recipient_email_normalized="invitee@example.com",
        tenant_status="active",
        workspace_status="active",
        memberships_by_user_id={
            **active_inviter,
            str(recipient_id): {
                "id": uuid4(),
                "user_id": recipient_id,
                "role": "developer",
                "status": "active",
            },
        },
    )
    assert (state_replay, role_replay) == ("already_accepted", "developer")

    # Revoked membership -> 409 (even if accepted earlier!)
    with pytest.raises(InvitationConflictError) as exc_rev_mem:
        InvitationRepository._evaluate_invitation_acceptance(
            inv_row=_base_inv_row(
                token_hash=good_hash,
                inviter_id=inviter_id,
                inv_status="accepted",
                accepted_by=recipient_id,
            ),
            expected_token_hash=good_hash,
            recipient_user_id=recipient_id,
            recipient_issuer="https://idp.local",
            recipient_email_normalized="invitee@example.com",
            tenant_status="active",
            workspace_status="active",
            memberships_by_user_id={
                **active_inviter,
                str(recipient_id): {
                    "id": uuid4(),
                    "user_id": recipient_id,
                    "role": "developer",
                    "status": "revoked",
                },
            },
        )
    assert exc_rev_mem.value.code == "membership_revoked"

    # Revoked inviter or inviter demoted to viewer cannot grant -> 409 inviter_authority_invalid
    for bad_inviter in [
        {},
        {
            str(inviter_id): {
                "id": uuid4(),
                "user_id": inviter_id,
                "role": "admin",
                "status": "revoked",
            }
        },
        {
            str(inviter_id): {
                "id": uuid4(),
                "user_id": inviter_id,
                "role": "viewer",
                "status": "active",
            }
        },
    ]:
        with pytest.raises(InvitationConflictError) as exc_inv:
            InvitationRepository._evaluate_invitation_acceptance(
                inv_row=_base_inv_row(
                    token_hash=good_hash, inviter_id=inviter_id, inv_status="pending"
                ),
                expected_token_hash=good_hash,
                recipient_user_id=recipient_id,
                recipient_issuer="https://idp.local",
                recipient_email_normalized="invitee@example.com",
                tenant_status="active",
                workspace_status="active",
                memberships_by_user_id=bad_inviter,
            )
        assert exc_inv.value.code == "inviter_authority_invalid"


def test_logout_clears_both_session_and_handoff_cookies_even_on_503(
    unit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = unit_env["client"]
    _uid, _sub, session_jwt = _fresh_session_for_user(unit_env)
    handoff_cookie = generate_browser_binding_token()

    client.cookies.set(settings.session_cookie_name, session_jwt)
    client.cookies.set(settings.invitation_handoff_cookie_name, handoff_cookie)

    # Normal logout -> 200 and TWO Set-Cookie headers clearing both cookies
    resp_ok = client.post("/v1/auth/logout", headers=_origin_headers())
    assert resp_ok.status_code == status.HTTP_200_OK
    set_cookies = resp_ok.headers.get_list("set-cookie")
    joined = "\n".join(set_cookies)
    assert settings.session_cookie_name in joined
    assert settings.invitation_handoff_cookie_name in joined

    # Degraded Redis (RepositoryUnavailableError) -> 503 AND still emits both Set-Cookie deletions!
    client.cookies.set(settings.session_cookie_name, session_jwt)
    client.cookies.set(settings.invitation_handoff_cookie_name, handoff_cookie)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RepositoryUnavailableError("redis down")

    monkeypatch.setattr(session_manager, "revoke_session_by_token", _boom)
    resp_503 = client.post("/v1/auth/logout", headers=_origin_headers())
    assert resp_503.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    set_cookies_503 = "\n".join(resp_503.headers.get_list("set-cookie"))
    assert settings.session_cookie_name in set_cookies_503
    assert settings.invitation_handoff_cookie_name in set_cookies_503


def test_r04_handoff_idempotency_original_deadline_cookie_max_age_and_cas_cleanup(
    unit_env: dict[str, Any],
) -> None:
    """R04 unit regression: duplicate replay preserves original deadline, shrinks cookie Max-Age, and protects receipts/newer proofs."""
    from control_api.modules.workspaces.services.invitation_acceptance_service import (
        InvitationAcceptanceService,
    )

    store = handoff_store_module.invitation_handoff_store
    assert isinstance(store, InMemoryInvitationHandoffStore)
    service = InvitationAcceptanceService(store=store)
    inv_id = uuid4()
    t0 = 1_762_950_000
    ttl = settings.invitation_handoff_ttl_seconds  # 600

    # 1. Initial creation at t0 -> expires_at = t0 + 600
    dto1, cookie = service.create_handoff(
        invitation_id=inv_id,
        raw_token="proof-gen-1",
        existing_browser_cookie=None,
        client_ip="127.0.0.10",
        now=t0,
    )
    assert int(datetime.fromisoformat(dto1.expires_at).timestamp()) == t0 + ttl
    original_entry = store.get_handoff_entry(
        invitation_id=str(inv_id),
        browser_binding_digest=hash_browser_binding_token(cookie),
        now=t0,
    )
    assert original_entry is not None

    # 2. Replay 120s later with same invitation + proof + browser cookie -> preserves exact original deadline
    dto2, cookie2 = service.create_handoff(
        invitation_id=inv_id,
        raw_token="proof-gen-1",
        existing_browser_cookie=cookie,
        client_ip="127.0.0.10",
        now=t0 + 120,
    )
    assert cookie2 == cookie
    assert dto2.expires_at == dto1.expires_at

    # 3. Exact boundary now == t0 + 600 -> previous entry is treated as expired, allowing fresh creation
    entry_at_boundary = store.get_handoff_entry(
        invitation_id=str(inv_id),
        browser_binding_digest=hash_browser_binding_token(cookie),
        now=t0 + ttl,
    )
    assert entry_at_boundary is None

    # 4. Create new handoff at t0 + ttl, convert to consumed_receipt -> replay never reverts it to handoff;
    #    conflicting new token against active consumed_receipt -> 409 conflict without clobbering receipt
    dto3, cookie3 = service.create_handoff(
        invitation_id=inv_id,
        raw_token="proof-gen-2",
        existing_browser_cookie=cookie,
        client_ip="127.0.0.10",
        now=t0 + ttl,
    )
    exp3 = int(datetime.fromisoformat(dto3.expires_at).timestamp())
    assert exp3 == t0 + (2 * ttl)

    digest3 = hash_browser_binding_token(cookie3)
    hash_gen2 = hash_invitation_proof("proof-gen-2")
    hash_gen1 = hash_invitation_proof("proof-gen-1")

    # Delayed post-commit cleanup with OLD token_hash (gen 1) MUST NOT delete/overwrite newer active handoff (gen 2)
    store.convert_to_consumed_receipt(
        invitation_id=str(inv_id),
        browser_binding_digest=digest3,
        user_id=str(uuid4()),
        token_hash=hash_gen1,
        expected_handoff_ciphertext=original_entry["ciphertext"],
        expires_at=t0 + ttl,
        now=t0 + ttl + 10,
    )
    still_active = store.get_handoff_entry(
        invitation_id=str(inv_id),
        browser_binding_digest=digest3,
        now=t0 + ttl + 10,
    )
    assert still_active is not None and still_active["kind"] == "handoff"

    # Correct token_hash (gen 2) converts cleanly without extending TTL
    consumer_uid = str(uuid4())
    store.convert_to_consumed_receipt(
        invitation_id=str(inv_id),
        browser_binding_digest=digest3,
        user_id=consumer_uid,
        token_hash=hash_gen2,
        expected_handoff_ciphertext=still_active["ciphertext"],
        expires_at=exp3,
        now=t0 + ttl + 20,
    )
    receipt = store.get_handoff_entry(
        invitation_id=str(inv_id),
        browser_binding_digest=digest3,
        now=t0 + ttl + 20,
    )
    assert receipt is not None and receipt["kind"] == "consumed_receipt"

    # Replaying SAME proof after consumption preserves consumed_receipt cleanly
    dto_after_consume, _ = service.create_handoff(
        invitation_id=inv_id,
        raw_token="proof-gen-2",
        existing_browser_cookie=cookie3,
        client_ip="127.0.0.10",
        now=t0 + ttl + 30,
    )
    assert dto_after_consume.expires_at == dto3.expires_at
    still_receipt = store.get_handoff_entry(
        invitation_id=str(inv_id),
        browser_binding_digest=digest3,
        now=t0 + ttl + 30,
    )
    assert still_receipt is not None and still_receipt["kind"] == "consumed_receipt"

    # Replaying DIFFERENT token against active consumed_receipt -> 409 and never clobbers receipt
    with pytest.raises(InvitationConflictError) as exc_conf:
        service.create_handoff(
            invitation_id=inv_id,
            raw_token="spoofed-or-other-proof",
            existing_browser_cookie=cookie3,
            client_ip="127.0.0.10",
            now=t0 + ttl + 35,
        )
    assert exc_conf.value.code == "invitation_already_accepted"
    assert store.get_handoff_entry(
        invitation_id=str(inv_id),
        browser_binding_digest=digest3,
        now=t0 + ttl + 35,
    )["kind"] == "consumed_receipt"


def test_r04_http_handoff_replay_shrinks_cookie_max_age_to_remaining_ttl(
    unit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Late HTTP replay sets Set-Cookie Max-Age <= remaining server TTL, never extending browser cookie lifetime."""
    client: TestClient = unit_env["client"]
    inv_id = uuid4()
    base_now = int(time.time())

    # First POST creates handoff
    resp1 = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": "http-replay-proof"},
        headers=_origin_headers(),
    )
    assert resp1.status_code == status.HTTP_201_CREATED
    exp1 = resp1.json()["expires_at"]
    cookie_val = resp1.cookies[settings.invitation_handoff_cookie_name]

    # Simulate 200s later on router time.time check while Redis store preserves original expires_at
    monkeypatch.setattr(accept_router_module.time, "time", lambda: base_now + 200)
    client.cookies.set(settings.invitation_handoff_cookie_name, cookie_val)
    resp2 = client.post(
        f"/v1/invitations/{inv_id}/handoff",
        json={"token": "http-replay-proof"},
        headers=_origin_headers(),
    )
    assert resp2.status_code == status.HTTP_201_CREATED
    assert resp2.json()["expires_at"] == exp1
    set_cookie_hdr = "\n".join(resp2.headers.get_list("set-cookie"))
    assert "Max-Age=" in set_cookie_hdr
    import re

    m = re.search(r"Max-Age=(\d+)", set_cookie_hdr)
    assert m is not None
    assert int(m.group(1)) <= settings.invitation_handoff_ttl_seconds - 195


def test_r01_service_propagates_subject_to_repository_and_r02_enforces_post_lock_deadlines(
    unit_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service propagates verified subject to repository preview/accept and checks deadlines."""
    import asyncio

    from control_api.modules.workspaces.services.invitation_acceptance_service import (
        InvitationAcceptanceService,
    )

    store = handoff_store_module.invitation_handoff_store
    assert isinstance(store, InMemoryInvitationHandoffStore)
    captured: list[dict[str, Any]] = []

    class _FakeRepo:
        async def preview_invitation(self, session: Any, **kwargs: Any) -> dict[str, Any]:
            captured.append({"op": "preview", **kwargs})
            return {
                "invitation_id": str(kwargs["invitation_id"]),
                "tenant_id": str(uuid4()),
                "tenant_slug": "t",
                "tenant_name": "T",
                "workspace_id": str(uuid4()),
                "workspace_slug": "w",
                "workspace_name": "W",
                "role": "developer",
                "status": "pending",
                "expires_at": datetime.now(UTC).isoformat(),
                "can_accept": True,
                "acceptance_state": "ready",
            }

        async def accept_invitation(self, session: Any, **kwargs: Any) -> dict[str, Any]:
            captured.append({"op": "accept", **kwargs})
            return {
                "invitation_id": str(kwargs["invitation_id"]),
                "tenant_id": str(uuid4()),
                "workspace_id": str(uuid4()),
                "workspace_slug": "w",
                "workspace_name": "W",
                "role": "developer",
                "acceptance_state": "accepted",
            }

    uid = str(uuid4())
    now_i = int(time.time())
    session_data = type(
        "Sess",
        (),
        {
            "identity_evidence": VerifiedIdentityEvidence(
                issuer=settings.oidc_issuer,
                subject="bound-subject-xyz",
                user_id=uid,
                email_normalized="bound@example.com",
                email_verified=True,
                id_token_issued_at=now_i - 5,
                observed_at=now_i - 5,
            )
        },
    )()

    service = InvitationAcceptanceService(repository=_FakeRepo(), store=store)  # type: ignore[arg-type]
    inv_id = uuid4()
    _, browser_cookie = service.create_handoff(
        invitation_id=inv_id,
        raw_token="bound-token",
        existing_browser_cookie=None,
        client_ip="127.0.0.1",
        now=now_i,
    )

    asyncio.run(
        service.preview_invitation(
            AsyncMock(),
            invitation_id=inv_id,
            session_data=session_data,
            issuer=settings.oidc_issuer,
            subject="bound-subject-xyz",
            user_id=uid,
            browser_cookie=browser_cookie,
            now=now_i,
        )
    )
    asyncio.run(
        service.accept_invitation(
            AsyncMock(),
            invitation_id=inv_id,
            session_data=session_data,
            issuer=settings.oidc_issuer,
            subject="bound-subject-xyz",
            user_id=uid,
            browser_cookie=browser_cookie,
            request_id="req-u",
            trace_id="tr-u",
        )
    )
    assert len(captured) == 2
    assert captured[0]["recipient_subject"] == "bound-subject-xyz"
    assert captured[1]["recipient_subject"] == "bound-subject-xyz"
    assert captured[1]["identity_freshness_deadline"] == (
        now_i - 5 + settings.invitation_identity_freshness_seconds
    )
    assert captured[1]["handoff_expires_at"] is not None
    assert captured[1]["enforce_wall_clock_deadlines"] is True
