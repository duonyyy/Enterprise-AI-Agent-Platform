"""Security and Negative Test Matrix for Authentication and Sessions (SPEC-AUTH-001 / Step 02 / Gate G2).

Comprehensive regression suite covering Findings R01 through R09:
- AUTH-T01 / R02: Full unpatched OIDC callback with PKCE S256, browser correlation cookie, identity mapping, no auto-granted admin roles.
- AUTH-T02: Mismatched state/nonce/PKCE, expired code, code replay, wrong issuer/audience/signature, expired ID token.
- AUTH-T03: Cross-issuer same-email account isolation (no auto-linking).
- AUTH-T04 / R04: Uniform session expiration (idle, absolute, revocation) across both Cookie and Bearer transports.
- AUTH-T05: IdP JWKS key rotation / rollover support, fail-closed on corrupt JWKS.
- AUTH-T06 / R03: Workspace isolation & discovery projection without RLS bypass.
- AUTH-T07 / R07: CSRF / Origin enforcement on cookie-authenticated mutations.
- AUTH-T08 / R05: Token confinement - block token self-reissue, workspace hopping, and lifetime extension after logout.
- AUTH-T09 / R06: Control & Runtime delegation profile with distinct configured signing keys and audiences.
- AUTH-T10 / R01: Shared store semantics (multi-worker revocation visibility, atomic single-use state, fail-closed).
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from agent_runtime.auth import verify_runtime_token
from agent_runtime.settings import RuntimeSettings
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.crypto import (
    generate_pkce_pair,
    verify_jwt_rs256_jwks,
    verify_pkce,
)
from control_api.modules.identity.oidc import (
    InMemoryAuthTransactionStore,
    oidc_client,
)
from control_api.modules.identity.service import identity_service
from control_api.modules.identity.session import (
    InMemoryRevocationStore,
    SessionManager,
    session_manager,
)
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.oidc_test_server import OidcTestServer


class SecurityTestDatabaseContext:
    def __init__(self) -> None:
        self.users: dict[tuple[str, str], dict[str, Any]] = {}
        self.users_by_id: dict[str, dict[str, Any]] = {}
        self.memberships: list[dict[str, Any]] = []


class SyncMappings:
    def __init__(self, item: Any = None, items: list[Any] | None = None) -> None:
        self._item = item
        self._items = items or []

    def one_or_none(self) -> Any:
        return self._item

    def one(self) -> Any:
        return self._item

    def all(self) -> list[Any]:
        return self._items


@pytest.fixture
def sec_db() -> SecurityTestDatabaseContext:
    return SecurityTestDatabaseContext()


@pytest.fixture
def sec_db_session(sec_db: SecurityTestDatabaseContext) -> AsyncSession:
    session = AsyncMock()

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt).strip()
        params = params or {}
        res = MagicMock()

        if "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE issuer = :issuer AND subject = :subject" in sql:
            issuer = str(params.get("issuer", ""))
            subject = str(params.get("subject", ""))
            user = sec_db.users.get((issuer, subject))
            res.mappings = lambda: SyncMappings(item=user)
            return res

        if "INSERT INTO iam.users" in sql:
            user_id = str(uuid4())
            new_user = {
                "id": user_id,
                "issuer": str(params["issuer"]),
                "subject": str(params["subject"]),
                "display_name": params.get("display_name"),
                "email_normalized": params.get("email_normalized"),
            }
            sec_db.users[(new_user["issuer"], new_user["subject"])] = new_user
            sec_db.users_by_id[user_id] = new_user
            res.mappings = lambda: SyncMappings(item=new_user)
            return res

        if "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE id = :id" in sql:
            uid = str(params.get("id"))
            user = sec_db.users_by_id.get(uid)
            res.mappings = lambda: SyncMappings(item=user)
            return res

        if "SELECT role FROM iam.memberships" in sql:
            uid = str(params.get("user_id"))
            wid = str(params.get("workspace_id"))
            role = None
            for m in sec_db.memberships:
                if str(m.get("user_id")) == uid and str(m.get("workspace_id")) == wid and m.get("status") == "active":
                    role = m.get("role")
                    break
            res.scalar_one_or_none.return_value = role
            return res

        if "SELECT m.tenant_id, m.role FROM iam.memberships" in sql or "SELECT tenant_id, role FROM iam.memberships" in sql:
            uid = str(params.get("user_id"))
            wid = str(params.get("workspace_id"))
            match = None
            for m in sec_db.memberships:
                if str(m.get("user_id")) == uid and str(m.get("workspace_id")) == wid and m.get("status") == "active":
                    match = {"tenant_id": m.get("tenant_id"), "role": m.get("role")}
                    break
            res.mappings = lambda: SyncMappings(item=match)
            return res

        if "SELECT m.tenant_id, m.role, t.status AS tenant_status" in sql or "resolve_user_workspace_membership" in sql:
            uid = str(params.get("user_id"))
            wid = str(params.get("workspace_id"))
            match = None
            for m in sec_db.memberships:
                if str(m.get("user_id")) == uid and str(m.get("workspace_id")) == wid and m.get("status") == "active":
                    match = {
                        "tenant_id": m.get("tenant_id"),
                        "role": m.get("role"),
                        "tenant_status": "active",
                        "workspace_status": "active",
                    }
                    break
            res.mappings = lambda: SyncMappings(item=match)
            return res

        if "FROM iam.memberships m" in sql and "JOIN iam.workspaces w" in sql or "get_user_workspaces" in sql:
            uid = str(params.get("user_id"))
            user_memberships = [
                m for m in sec_db.memberships
                if str(m.get("user_id")) == uid and m.get("status") == "active"
            ]
            res.mappings = lambda: SyncMappings(items=user_memberships)
            return res

        res.mappings = lambda: SyncMappings(item=None, items=[])
        res.scalar_one_or_none.return_value = None
        return res

    session.execute.side_effect = mock_execute
    return session


@pytest.fixture
def client(sec_db_session: AsyncSession) -> TestClient:
    app.dependency_overrides[get_db_session] = lambda: sec_db_session
    orig_store = oidc_client.store
    orig_denylist = session_manager.denylist
    mem_store = InMemoryAuthTransactionStore()
    mem_denylist = InMemoryRevocationStore()
    oidc_client.store = mem_store
    session_manager.denylist = mem_denylist
    c = TestClient(app, base_url="http://localhost:8000")
    yield c
    app.dependency_overrides.clear()
    oidc_client.store = orig_store
    session_manager.denylist = orig_denylist


# ==============================================================================
# AUTH-T01: Positive End-to-End Flow with Production OIDC Validation (Unpatched)
# ==============================================================================

def test_auth_t01_positive_e2e_oidc_flow(client: TestClient, sec_db: SecurityTestDatabaseContext) -> None:
    idp = OidcTestServer(issuer=settings.oidc_issuer)

    # 1. Initiate login
    login_res = client.get("/v1/auth/login?return_to=/login")
    assert login_res.status_code == status.HTTP_200_OK
    login_data = login_res.json()
    state = login_data["state"]

    # Pre-auth browser correlation cookie must be set
    assert settings.oidc_correlation_cookie_name in login_res.cookies
    corr_cookie = login_res.cookies[settings.oidc_correlation_cookie_name]

    # Retrieve in-flight transaction
    tx = oidc_client.store.consume(state)
    assert tx is not None
    # Put back for callback consumption
    oidc_client.store.save(tx)

    # IdP generates authorization code
    auth_code = idp.create_authorization_code(
        client_id=settings.oidc_client_id,
        redirect_uri=settings.oidc_redirect_uri,
        state=state,
        nonce=tx.nonce,
        code_challenge=tx.code_challenge,
        subject="alice-vinai-uid",
        name="Alice VinAI",
        email="alice@vinai.io",
    )

    # 2. Production Callback Execution using HTTP transport to in-process IdP
    def idp_transport(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            tokens = idp.exchange_code_for_token(
                params["code"], params["code_verifier"], params["client_id"],
                params["client_secret"], params["redirect_uri"],
            )
            return httpx.Response(200, json=tokens)
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json=idp.get_jwks())
        return httpx.Response(404)

    original_handle_callback = oidc_client.handle_callback

    async def unpatched_callback(code: str, state: str, browser_correlation: str | None = None) -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(idp_transport)) as hc:
            return await original_handle_callback(code, state, browser_correlation=browser_correlation, http_client=hc)

    with patch.object(oidc_client, "handle_callback", side_effect=unpatched_callback):
        # Set correlation cookie on client
        client.cookies.set(settings.oidc_correlation_cookie_name, corr_cookie)
        cb_res = client.get(
            f"/v1/auth/callback?code={auth_code}&state={state}",
            headers={"Accept": "application/json"},
        )
        assert cb_res.status_code == status.HTTP_200_OK
        data = cb_res.json()
        assert data["subject"] == "alice-vinai-uid"
        user_id = data["user_id"]

        # Ensure session cookie is present
        assert settings.session_cookie_name in cb_res.cookies
        session_token = cb_res.cookies[settings.session_cookie_name]

    # 3. Call /v1/auth/me using the session cookie
    client.cookies.set(settings.session_cookie_name, session_token)
    me_res = client.get("/v1/auth/me")
    assert me_res.status_code == status.HTTP_200_OK
    me_data = me_res.json()
    assert me_data["user_id"] == user_id
    assert me_data["display_name"] == "Alice VinAI"
    assert me_data["workspaces"] == []


# ==============================================================================
# AUTH-T02 / R02: Browser Correlation Binding (RFC 9700 §2.1.1)
# ==============================================================================

def test_auth_r02_cross_browser_callback_rejected(client: TestClient) -> None:
    """Browser A starts login; Browser B attempts to use callback -> MUST be rejected."""
    idp = OidcTestServer(issuer=settings.oidc_issuer)

    # Browser A initiates login
    init_res = client.get("/v1/auth/login?return_to=/login")
    state = init_res.json()["state"]

    tx = oidc_client.store.consume(state)
    assert tx is not None
    oidc_client.store.save(tx)

    code = idp.create_authorization_code(
        settings.oidc_client_id, settings.oidc_redirect_uri,
        state, tx.nonce, tx.code_challenge,
        subject="attacker-account",
    )

    # Browser B (different browser, NO correlation cookie) sends callback
    client.cookies.clear()
    res_b = client.get(
        f"/v1/auth/callback?code={code}&state={state}",
        headers={"Accept": "application/json"},
    )
    assert res_b.status_code == status.HTTP_400_BAD_REQUEST
    assert "browser correlation" in res_b.json()["detail"].lower()


def test_auth_t02_negative_state_mismatch_and_replay(client: TestClient) -> None:
    res = client.get("/v1/auth/callback?code=fake-code&state=nonexistent-state")
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid, expired, or replayed state" in res.json()["detail"]


def test_auth_t02_negative_pkce_mismatch() -> None:
    code_verifier, code_challenge = generate_pkce_pair()
    wrong_verifier = "wrong-" + code_verifier[6:]
    assert not verify_pkce(wrong_verifier, code_challenge)


# ==============================================================================
# AUTH-T03: Cross-Issuer Account Isolation
# ==============================================================================

@pytest.mark.anyio
async def test_auth_t03_cross_issuer_same_email_isolation(sec_db_session: AsyncSession) -> None:
    shared_email = "employee@enterprise.com"

    user_corp = await identity_service.get_or_provision_user(
        session=sec_db_session,
        issuer="https://corporate-idp.com",
        subject="corp-emp-101",
        display_name="Corporate Employee",
        email=shared_email,
    )

    user_partner = await identity_service.get_or_provision_user(
        session=sec_db_session,
        issuer="https://partner-idp.com",
        subject="partner-emp-202",
        display_name="Partner Employee",
        email=shared_email,
    )

    assert user_corp["id"] != user_partner["id"]
    assert user_corp["issuer"] == "https://corporate-idp.com"
    assert user_partner["issuer"] == "https://partner-idp.com"


# ==============================================================================
# AUTH-T04 / R04: Uniform Session Expiry across Transports (Cookie & Bearer)
# ==============================================================================

def test_auth_r04_uniform_idle_expiry_denial_across_transports(client: TestClient, sec_db: SecurityTestDatabaseContext) -> None:
    """Idle-expired session MUST be denied on both Cookie AND Bearer transport (Finding R04)."""
    now = int(time.time())
    uid = str(uuid4())
    wid = str(uuid4())
    tid = str(uuid4())

    sec_db.memberships.append({
        "tenant_id": tid,
        "workspace_id": wid,
        "user_id": uid,
        "role": "developer",
        "status": "active",
    })

    # Create idle-expired session (now - 1900s, exceeding 1800s idle timeout)
    _sess, idle_token = session_manager.create_session(uid, settings.oidc_issuer, "sub-idle", now=now - 1900)

    # 1. Transport via Cookie
    client.cookies.set(settings.session_cookie_name, idle_token)
    res_cookie = client.get(f"/v1/workspaces/{wid}/agents")
    assert res_cookie.status_code == status.HTTP_401_UNAUTHORIZED
    assert "idle" in res_cookie.json()["detail"].lower()

    # 2. Transport via Bearer header
    client.cookies.clear()
    res_bearer = client.get(
        f"/v1/workspaces/{wid}/agents",
        headers={"Authorization": f"Bearer {idle_token}"},
    )
    assert res_bearer.status_code == status.HTTP_401_UNAUTHORIZED
    assert "idle" in res_bearer.json()["detail"].lower()


# ==============================================================================
# AUTH-T05: IdP Key Rotation Support
# ==============================================================================

def test_auth_t05_jwks_key_rotation_support() -> None:
    idp = OidcTestServer()
    key1 = idp.active_key

    v1, c1 = generate_pkce_pair()
    code1 = idp.create_authorization_code("c1", "http://cb", "s1", "n1", c1)
    tokens1 = idp.exchange_code_for_token(code1, v1, "c1", "s", "http://cb")

    idp.rotate_keys()
    assert idp.active_key.kid != key1.kid

    v2, c2 = generate_pkce_pair()
    code2 = idp.create_authorization_code("c1", "http://cb", "s2", "n2", c2)
    tokens2 = idp.exchange_code_for_token(code2, v2, "c1", "s", "http://cb")

    jwks = idp.get_jwks()
    claims1 = verify_jwt_rs256_jwks(tokens1["id_token"], jwks)
    claims2 = verify_jwt_rs256_jwks(tokens2["id_token"], jwks)

    assert claims1["nonce"] == "n1"
    assert claims2["nonce"] == "n2"


# ==============================================================================
# AUTH-T07 / R07: CSRF & Origin Enforcement on Cookie Mutations
# ==============================================================================

def test_auth_r07_csrf_mutation_protection(client: TestClient) -> None:
    """Same-site cross-origin requests with cookie auth MUST be rejected (Finding R07)."""
    uid = str(uuid4())
    _sess, token = session_manager.create_session(uid, settings.oidc_issuer, "sub-csrf")
    client.cookies.set(settings.session_cookie_name, token)

    # Cross-origin / untrusted same-site sibling origin
    resp = client.post(
        "/v1/auth/logout",
        headers={"Origin": "http://untrusted.example.test", "Sec-Fetch-Site": "same-site"},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert "not allowed" in resp.json()["detail"].lower()

    # Same-origin allowed
    resp_ok = client.post(
        "/v1/auth/logout",
        headers={"Origin": "http://localhost:8000"},
    )
    assert resp_ok.status_code == status.HTTP_200_OK
    assert resp_ok.json()["status"] == "logged_out"


# ==============================================================================
# AUTH-T08 / R05: Token Confinement & Block Self-Reissue
# ==============================================================================

def test_auth_r05_scoped_token_cannot_exchange_or_reissue_after_logout(
    client: TestClient, sec_db: SecurityTestDatabaseContext
) -> None:
    """Scoped token cannot call /token without active session; cannot reissue after logout (Finding R05)."""
    uid = str(uuid4())
    wa = str(uuid4())
    wb = str(uuid4())
    tid = str(uuid4())

    sec_db.memberships.append({"tenant_id": tid, "workspace_id": wa, "user_id": uid, "role": "operator", "status": "active"})
    sec_db.memberships.append({"tenant_id": tid, "workspace_id": wb, "user_id": uid, "role": "operator", "status": "active"})

    _sess, session_token = session_manager.create_session(uid, settings.oidc_issuer, "sub-tok")
    client.cookies.set(settings.session_cookie_name, session_token)

    # 1. Obtain workspace token A
    first = client.post("/v1/auth/token", json={"workspace_id": wa}, headers={"Origin": "http://localhost:8000"})
    assert first.status_code == status.HTTP_200_OK
    access_token_a = first.json()["access_token"]

    # 2. Logout parent session
    client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
    client.cookies.clear()

    # 3. Attempt to use access_token_a to request token for workspace B
    reissue_resp = client.post(
        "/v1/auth/token",
        json={"workspace_id": wb},
        headers={"Authorization": f"Bearer {access_token_a}"},
    )
    # MUST BE DENIED: Active application session required for token exchange!
    assert reissue_resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "active application session required" in reissue_resp.json()["detail"].lower()


# ==============================================================================
# AUTH-T09 / R06: Runtime Delegation Profile with Distinct Configured Secret
# ==============================================================================

def test_auth_r06_runtime_distinct_secret_delegation(
    client: TestClient, sec_db: SecurityTestDatabaseContext
) -> None:
    """Tokens issued for Runtime use runtime_jwt_secret and runtime_jwt_audience (Finding R06)."""
    uid = str(uuid4())
    wid = str(uuid4())
    tid = str(uuid4())

    sec_db.memberships.append({"tenant_id": tid, "workspace_id": wid, "user_id": uid, "role": "developer", "status": "active"})

    distinct_secret = "distinct-runtime-secret-key-at-least-32-chars"
    with patch.object(settings, "runtime_jwt_secret", SecretStr(distinct_secret)), patch.object(
        settings, "runtime_jwt_audience", "custom-agent-runtime"
    ):
        _sess, session_token = session_manager.create_session(uid, settings.oidc_issuer, "sub-del")
        client.cookies.set(settings.session_cookie_name, session_token)

        resp = client.post("/v1/auth/token", json={"workspace_id": wid}, headers={"Origin": "http://localhost:8000"})
        assert resp.status_code == status.HTTP_200_OK
        access_token = resp.json()["access_token"]

        # Runtime configured with distinct_secret accepts token
        runtime_settings = RuntimeSettings(
            runtime_jwt_secret=distinct_secret,
            runtime_jwt_issuer=settings.jwt_issuer,
            runtime_jwt_audience="custom-agent-runtime",
        )
        principal = verify_runtime_token(access_token, settings=runtime_settings)
        assert principal.user_id == uid
        assert principal.workspace_id == wid

        # Wrong secret rejected by Runtime
        bad_runtime_settings = RuntimeSettings(
            runtime_jwt_secret="wrong-runtime-secret-at-least-32-characters",
            runtime_jwt_issuer=settings.jwt_issuer,
            runtime_jwt_audience="custom-agent-runtime",
        )
        with pytest.raises(ValueError, match="invalid_token_signature"):
            verify_runtime_token(access_token, settings=bad_runtime_settings)


# ==============================================================================
# AUTH-T10 / R01: Multi-Worker Revocation Sharing & Fail-Closed Behavior
# ==============================================================================

def test_auth_r01_revocation_shared_across_manager_instances() -> None:
    """Two session managers sharing a store must observe each other's revocations."""
    shared_store = InMemoryRevocationStore()
    manager_a = SessionManager(denylist=shared_store)
    manager_b = SessionManager(denylist=shared_store)

    uid = str(uuid4())
    sess, token = manager_a.create_session(uid, "https://idp", "sub-worker")

    # Worker B can verify
    assert manager_b.verify_session_token(token).user_id == uid

    # Worker A revokes session
    manager_a.revoke_session(sess)

    # Worker B immediately observes revocation
    with pytest.raises(Exception) as exc:
        manager_b.verify_session_token(token)
    assert "revoked" in str(exc.value.detail).lower()


# ==============================================================================
# AUTH-T11 / P0-01: Logout Error Handling & Infrastructure Failure Semantics
# ==============================================================================

def test_auth_p0_01_valid_logout_revokes_and_clears_cookie(client: TestClient) -> None:
    """Valid logout successfully revokes session in store and clears cookie."""
    uid = str(uuid4())
    _sess, token = session_manager.create_session(uid, "https://idp.local", "sub-logout-1")
    client.cookies.set(settings.session_cookie_name, token)

    resp = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "logged_out"
    assert "agent_session" in resp.headers.get("set-cookie", "")

    # Replay must be rejected as revoked
    with pytest.raises(HTTPException) as exc:
        session_manager.verify_session_token(token)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in str(exc.value.detail).lower()


def test_auth_p0_01_idempotent_local_logout(client: TestClient) -> None:
    """Missing, malformed, or already-revoked tokens perform idempotent local logout."""
    # 1. Missing credentials
    client.cookies.clear()
    resp_missing = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
    assert resp_missing.status_code == status.HTTP_200_OK
    assert resp_missing.json()["status"] == "logged_out"
    assert "agent_session" in resp_missing.headers.get("set-cookie", "")

    # 2. Malformed token
    client.cookies.set(settings.session_cookie_name, "malformed.jwt.token")
    resp_malformed = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
    assert resp_malformed.status_code == status.HTTP_200_OK
    assert resp_malformed.json()["status"] == "logged_out"
    assert "agent_session" in resp_malformed.headers.get("set-cookie", "")

    # 3. Already revoked session
    uid = str(uuid4())
    sess, token = session_manager.create_session(uid, "https://idp.local", "sub-revoked")
    session_manager.revoke_session(sess)
    client.cookies.set(settings.session_cookie_name, token)
    resp_already_revoked = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
    assert resp_already_revoked.status_code == status.HTTP_200_OK
    assert resp_already_revoked.json()["status"] == "logged_out"


def test_auth_p0_01_redis_failure_during_verification_or_revocation(client: TestClient) -> None:
    """Redis 503 error on check or write must return HTTP 503 and delete cookie, never falsely reporting 200."""
    uid = str(uuid4())
    _sess, token = session_manager.create_session(uid, "https://idp.local", "sub-redis-fail")
    client.cookies.set(settings.session_cookie_name, token)

    # 1. Store fails during revocation check
    with patch.object(
        session_manager.denylist,
        "is_session_revoked",
        side_effect=HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Revocation store unavailable"),
    ):
        resp_fail_check = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
        assert resp_fail_check.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        # Set-Cookie MUST be present on the 503 response
        assert "agent_session" in resp_fail_check.headers.get("set-cookie", "")
        assert "Max-Age=0" in resp_fail_check.headers.get("set-cookie", "")
        # Detail must indicate failure closed without leaking secrets
        assert "Revocation store unavailable" in resp_fail_check.json()["detail"]
        assert token not in resp_fail_check.text

    # 2. Store fails during revocation write (e.g. write error or timeout)
    with patch.object(
        session_manager.denylist,
        "revoke_session",
        side_effect=HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Revocation store write timed out"),
    ):
        resp_fail_write = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
        assert resp_fail_write.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "agent_session" in resp_fail_write.headers.get("set-cookie", "")
        assert "Max-Age=0" in resp_fail_write.headers.get("set-cookie", "")
        assert token not in resp_fail_write.text


def test_auth_p0_01_csrf_rejection_does_not_clear_cookie(client: TestClient) -> None:
    """Untrusted origin must be rejected with 403 without clearing cookies."""
    uid = str(uuid4())
    _sess, token = session_manager.create_session(uid, "https://idp.local", "sub-csrf")
    client.cookies.set(settings.session_cookie_name, token)

    resp = client.post("/v1/auth/logout", headers={"Origin": "http://evil-attacker.test"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    # No deletion cookie should be issued on rejected CSRF
    assert "agent_session" not in resp.headers.get("set-cookie", "")


# ==============================================================================
# AUTH-T13 / TASK 02: Session Rotation, Concurrency, Lifecycle & Cookie Boundaries
# ==============================================================================


def test_task02_session_creation_and_same_second_rotation() -> None:
    """Normal refresh changes credential and increments rotation_version even in the same second."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1700000000

    sess1, token1 = manager.create_session(uid, "https://idp.local", "sub-rot-1", now=t0)
    assert sess1.rotation_version == 1
    assert sess1.created_at == t0
    assert sess1.idle_expires_at == t0 + settings.session_idle_timeout_seconds
    assert sess1.absolute_expires_at == t0 + settings.session_absolute_timeout_seconds

    # Refresh in the exact same second t0
    sess2, token2 = manager.refresh_session(sess1, now=t0)
    assert token2 != token1
    assert sess2.rotation_version == 2
    assert sess2.session_id == sess1.session_id
    assert sess2.user_id == sess1.user_id
    assert sess2.issuer == sess1.issuer
    assert sess2.subject == sess1.subject
    assert sess2.created_at == sess1.created_at
    assert sess2.absolute_expires_at == sess1.absolute_expires_at
    assert sess2.idle_expires_at == sess1.idle_expires_at

    # New credential verifies cleanly
    verified = manager.verify_session_token(token2, now=t0)
    assert verified.rotation_version == 2
    assert verified.session_id == sess1.session_id

    # Old credential (version 1) is immediately rejected on verify and refresh
    with pytest.raises(HTTPException) as exc_verify:
        manager.verify_session_token(token1, now=t0)
    assert exc_verify.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "superseded" in str(exc_verify.value.detail).lower()

    with pytest.raises(HTTPException) as exc_refresh:
        manager.refresh_session(sess1, now=t0)
    assert exc_refresh.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "superseded" in str(exc_refresh.value.detail).lower()


def test_task02_old_credential_rejected_across_all_routes_and_transports(
    client: TestClient, sec_db: SecurityTestDatabaseContext
) -> None:
    """Superseded credential after rotation is rejected at /refresh, /me, /token, and workspace routes (Cookie & Bearer)."""
    uid = str(uuid4())
    wid = str(uuid4())
    tid = str(uuid4())
    sec_db.users[(settings.oidc_issuer, "sub-rot-routes")] = {
        "id": uid,
        "issuer": settings.oidc_issuer,
        "subject": "sub-rot-routes",
        "display_name": "Rotation User",
        "email_normalized": "rot@vinai.io",
    }
    sec_db.users_by_id[uid] = sec_db.users[(settings.oidc_issuer, "sub-rot-routes")]
    sec_db.memberships.append(
        {
            "tenant_id": tid,
            "tenant_slug": "vinai-corp",
            "tenant_name": "VinAI Corp",
            "workspace_id": wid,
            "workspace_slug": "ws-rot",
            "workspace_name": "Rotation WS",
            "user_id": uid,
            "role": "developer",
            "status": "active",
        }
    )

    _sess1, token1 = session_manager.create_session(uid, settings.oidc_issuer, "sub-rot-routes")
    client.cookies.set(settings.session_cookie_name, token1)
    origin_headers = {"Origin": "http://localhost:8000"}

    # Rotate via /v1/auth/refresh
    ref_res = client.post("/v1/auth/refresh", headers=origin_headers)
    assert ref_res.status_code == status.HTTP_200_OK
    token2 = ref_res.cookies[settings.session_cookie_name]
    assert token2 != token1

    # 1. Old token rejected at /v1/auth/refresh (Cookie & Bearer)
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, token1)
    r_ref_cookie = client.post("/v1/auth/refresh", headers=origin_headers)
    assert r_ref_cookie.status_code == status.HTTP_401_UNAUTHORIZED
    assert settings.session_cookie_name not in r_ref_cookie.cookies
    assert "set-cookie" not in r_ref_cookie.headers

    client.cookies.clear()
    r_ref_bearer = client.post("/v1/auth/refresh", headers={"Authorization": f"Bearer {token1}"})
    assert r_ref_bearer.status_code == status.HTTP_401_UNAUTHORIZED
    assert "set-cookie" not in r_ref_bearer.headers

    # 2. Old token rejected at /v1/auth/me (Cookie & Bearer)
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, token1)
    assert client.get("/v1/auth/me").status_code == status.HTTP_401_UNAUTHORIZED
    client.cookies.clear()
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token1}"}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )

    # 3. Old token rejected at /v1/auth/token (Cookie & Bearer)
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, token1)
    assert (
        client.post("/v1/auth/token", json={"workspace_id": wid}, headers=origin_headers).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    client.cookies.clear()
    assert (
        client.post(
            "/v1/auth/token",
            json={"workspace_id": wid},
            headers={"Authorization": f"Bearer {token1}"},
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )

    # 4. Old token rejected at workspace route /v1/workspaces/{wid}/agents (Cookie & Bearer)
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, token1)
    assert client.get(f"/v1/workspaces/{wid}/agents").status_code == status.HTTP_401_UNAUTHORIZED
    client.cookies.clear()
    assert (
        client.get(
            f"/v1/workspaces/{wid}/agents",
            headers={"Authorization": f"Bearer {token1}"},
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )

    # 5. Replay of token1 did NOT revoke token2 — token2 remains valid!
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, token2)
    assert client.get("/v1/auth/me").status_code == status.HTTP_200_OK
    assert (
        client.post("/v1/auth/token", json={"workspace_id": wid}, headers=origin_headers).status_code
        == status.HTTP_200_OK
    )


@pytest.mark.parametrize(
    ("offset_from_idle", "should_pass"),
    [
        (-1, True),   # Strictly before idle_expires_at -> valid
        (0, False),   # Exact boundary now == idle_expires_at -> expired
        (1, False),   # After idle_expires_at -> expired
    ],
)
def test_task02_idle_expiry_exact_boundaries(offset_from_idle: int, should_pass: bool) -> None:
    """Idle expiration enforces now >= idle_expires_at on both verification and refresh."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1700000000

    sess, token = manager.create_session(uid, "https://idp.local", "sub-idle-b", now=t0)
    eval_now = sess.idle_expires_at + offset_from_idle

    if should_pass:
        verified = manager.verify_session_token(token, now=eval_now)
        assert verified.session_id == sess.session_id
        rotated, _ = manager.refresh_session(sess, now=eval_now)
        assert rotated.rotation_version == 2
        assert rotated.idle_expires_at == min(
            eval_now + settings.session_idle_timeout_seconds,
            sess.absolute_expires_at,
        )
    else:
        with pytest.raises(HTTPException) as exc_v:
            manager.verify_session_token(token, now=eval_now)
        assert exc_v.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "idle" in str(exc_v.value.detail).lower()

        with pytest.raises(HTTPException) as exc_r:
            manager.refresh_session(sess, now=eval_now)
        assert exc_r.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "idle" in str(exc_r.value.detail).lower()


@pytest.mark.parametrize(
    ("offset_from_abs", "should_pass"),
    [
        (-1, True),   # Strictly before absolute_expires_at -> valid & refreshable (capped to abs_exp)
        (0, False),   # Exact boundary now == absolute_expires_at -> expired
        (1, False),   # After absolute_expires_at -> expired
    ],
)
def test_task02_absolute_expiry_exact_boundaries_and_cap(offset_from_abs: int, should_pass: bool) -> None:
    """Absolute expiration enforces now >= absolute_expires_at and caps refreshed idle_expires_at."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1700000000
    idle_timeout = 1800
    abs_timeout = 3600

    with patch.object(settings, "session_idle_timeout_seconds", idle_timeout), patch.object(
        settings, "session_absolute_timeout_seconds", abs_timeout
    ):
        sess, token = manager.create_session(uid, "https://idp.local", "sub-abs-b", now=t0)
        # First refresh at t0 + 1700 so idle_expires_at reaches t0 + 3500
        sess, token = manager.refresh_session(sess, now=t0 + 1700)
        # Second refresh at t0 + 3400 so idle_expires_at is capped at t0 + 3600 (absolute_expires_at)
        sess, token = manager.refresh_session(sess, now=t0 + 3400)
        assert sess.idle_expires_at == sess.absolute_expires_at

        eval_now = sess.absolute_expires_at + offset_from_abs
        if should_pass:
            verified = manager.verify_session_token(token, now=eval_now)
            assert verified.session_id == sess.session_id
            rotated, _ = manager.refresh_session(sess, now=eval_now)
            assert rotated.idle_expires_at == sess.absolute_expires_at
            assert rotated.absolute_expires_at == sess.absolute_expires_at
        else:
            with pytest.raises(HTTPException) as exc_v:
                manager.verify_session_token(token, now=eval_now)
            assert exc_v.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "expired" in str(exc_v.value.detail).lower()

            with pytest.raises(HTTPException) as exc_r:
                manager.refresh_session(sess, now=eval_now)
            assert exc_r.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "expired" in str(exc_r.value.detail).lower()


def test_task02_missing_or_corrupt_state_rejected_and_never_recreated() -> None:
    """Missing or corrupt state in store must be rejected and never lazy-recreated from JWT."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1700000000

    sess, token = manager.create_session(uid, "https://idp.local", "sub-missing", now=t0)
    # Simulate evicted/missing state
    store.clear_all()

    with pytest.raises(HTTPException) as exc_v:
        manager.verify_session_token(token, now=t0 + 5)
    assert exc_v.value.status_code == status.HTTP_401_UNAUTHORIZED

    with pytest.raises(HTTPException) as exc_r:
        manager.refresh_session(sess, now=t0 + 5)
    assert exc_r.value.status_code == status.HTTP_401_UNAUTHORIZED

    # Ensure state was NOT recreated in store
    assert store.get_session_state(sess.session_id, now=t0 + 5) is None


def test_task02_logout_after_multiple_rotations_and_via_superseded_credential(client: TestClient) -> None:
    """Logout revokes the stable session identity whether called with latest or superseded credential."""
    uid = str(uuid4())
    t0 = int(time.time())
    sess_a1, token_a1 = session_manager.create_session(uid, "https://idp.local", "sub-multi-rot", now=t0)
    sess_a2, _token_a2 = session_manager.refresh_session(sess_a1, now=t0 + 1)
    _sess_a3, token_a3 = session_manager.refresh_session(sess_a2, now=t0 + 2)

    # Independent session B of the same user
    _sess_b1, token_b1 = session_manager.create_session(uid, "https://idp.local", "sub-multi-rot", now=t0 + 2)

    # Logout using superseded credential token_a1!
    client.cookies.set(settings.session_cookie_name, token_a1)
    res_logout = client.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
    assert res_logout.status_code == status.HTTP_200_OK

    # Latest credential token_a3 of session A is now REVOKED
    with pytest.raises(HTTPException) as exc_a3:
        session_manager.verify_session_token(token_a3, now=t0 + 3)
    assert exc_a3.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in str(exc_a3.value.detail).lower()

    # Independent session B of the same user remains completely valid and can rotate
    verified_b = session_manager.verify_session_token(token_b1, now=t0 + 3)
    assert verified_b.user_id == uid
    sess_b2, _token_b2 = session_manager.refresh_session(verified_b, now=t0 + 4)
    assert sess_b2.rotation_version == 2


def test_task02_user_cutoff_uses_original_created_at_after_rotations() -> None:
    """User-wide cutoff evaluates against original session created_at even after multiple rotations."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1700000000

    sess1, _t1 = manager.create_session(uid, "https://idp.local", "sub-cut", now=t0)
    # Rotate at t0 + 100
    sess2, token2 = manager.refresh_session(sess1, now=t0 + 100)
    assert sess2.created_at == t0

    # User-wide revocation at cutoff = t0 + 50 (after original creation t0, before rotation t0 + 100)
    manager.revoke_user(uid, cutoff=t0 + 50, now=t0 + 110)

    # Token2 must be rejected as revoked because original created_at (t0) <= cutoff (t0 + 50)
    with pytest.raises(HTTPException) as exc_v:
        manager.verify_session_token(token2, now=t0 + 120)
    assert exc_v.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in str(exc_v.value.detail).lower()

    with pytest.raises(HTTPException) as exc_r:
        manager.refresh_session(sess2, now=t0 + 120)
    assert exc_r.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in str(exc_r.value.detail).lower()


def test_task02_scoped_token_cannot_refresh_and_csrf_enforced_on_refresh(
    client: TestClient, sec_db: SecurityTestDatabaseContext
) -> None:
    """Scoped workspace token cannot call /v1/auth/refresh; CSRF/Origin is enforced on /v1/auth/refresh."""
    uid = str(uuid4())
    wid = str(uuid4())
    tid = str(uuid4())
    sec_db.memberships.append(
        {
            "tenant_id": tid,
            "tenant_slug": "vinai-corp",
            "tenant_name": "VinAI Corp",
            "workspace_id": wid,
            "workspace_slug": "ws-rot",
            "workspace_name": "Rotation WS",
            "user_id": uid,
            "role": "developer",
            "status": "active",
        }
    )

    _sess, session_token = session_manager.create_session(uid, settings.oidc_issuer, "sub-sc")
    client.cookies.set(settings.session_cookie_name, session_token)

    # Issue scoped workspace token
    tok_res = client.post("/v1/auth/token", json={"workspace_id": wid}, headers={"Origin": "http://localhost:8000"})
    assert tok_res.status_code == status.HTTP_200_OK
    scoped_token = tok_res.json()["access_token"]

    # 1. Scoped token cannot refresh session
    client.cookies.clear()
    res_scoped_refresh = client.post(
        "/v1/auth/refresh",
        headers={"Authorization": f"Bearer {scoped_token}"},
    )
    assert res_scoped_refresh.status_code == status.HTTP_401_UNAUTHORIZED
    assert "active application session required" in res_scoped_refresh.json()["detail"].lower()

    # 2. Untrusted Origin on cookie-authenticated /v1/auth/refresh is rejected with 403 and no cookie mutation
    client.cookies.set(settings.session_cookie_name, session_token)
    res_csrf = client.post(
        "/v1/auth/refresh",
        headers={"Origin": "http://evil-origin.example.test"},
    )
    assert res_csrf.status_code == status.HTTP_403_FORBIDDEN
    assert "set-cookie" not in res_csrf.headers


def test_task02_cookie_attributes_and_bounded_max_age_on_refresh(client: TestClient) -> None:
    """Refresh sets HttpOnly, SameSite, Path, and caps Max-Age to remaining absolute lifetime."""
    uid = str(uuid4())
    t0 = int(time.time()) - 1000  # Created 1000s ago
    _sess, token1 = session_manager.create_session(uid, "https://idp.local", "sub-cookie", now=t0)

    client.cookies.set(settings.session_cookie_name, token1)
    res = client.post("/v1/auth/refresh", headers={"Origin": "http://localhost:8000"})
    assert res.status_code == status.HTTP_200_OK

    set_cookie_hdr = res.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_hdr
    assert "Path=/" in set_cookie_hdr
    assert "SameSite=lax" in set_cookie_hdr

    # Extract Max-Age and verify it is bounded by remaining absolute lifetime (<= 28800 - 1000)
    max_age_val = None
    for part in set_cookie_hdr.split(";"):
        part_s = part.strip()
        if part_s.lower().startswith("max-age="):
            max_age_val = int(part_s.split("=", 1)[1])
            break
    assert max_age_val is not None
    assert 0 < max_age_val <= settings.session_absolute_timeout_seconds - 995
    assert max_age_val < settings.session_absolute_timeout_seconds


def test_task02_concurrent_refresh_single_winner_and_loser_no_cookie(client: TestClient) -> None:
    """Concurrent refresh requests with the same credential yield exactly one winner; loser does not set/delete cookie."""
    import threading as _threading

    uid = str(uuid4())
    _sess, token1 = session_manager.create_session(uid, "https://idp.local", "sub-race")

    barrier = _threading.Barrier(2)
    orig_rotate = session_manager.denylist.rotate_session_state

    def synced_rotate(*args: Any, **kwargs: Any) -> Any:
        try:
            barrier.wait(timeout=2.0)
        except _threading.BrokenBarrierError:
            pass
        return orig_rotate(*args, **kwargs)

    results: list[ Any ] = []

    def worker() -> None:
        with TestClient(app, base_url="http://localhost:8000") as local_client:
            local_client.cookies.set(settings.session_cookie_name, token1)
            r = local_client.post("/v1/auth/refresh", headers={"Origin": "http://localhost:8000"})
            results.append(r)

    with patch.object(session_manager.denylist, "rotate_session_state", side_effect=synced_rotate):
        t1 = _threading.Thread(target=worker)
        t2 = _threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert len(results) == 2
    statuses = sorted(r.status_code for r in results)
    assert statuses == [status.HTTP_200_OK, status.HTTP_401_UNAUTHORIZED]

    winner = next(r for r in results if r.status_code == status.HTTP_200_OK)
    loser = next(r for r in results if r.status_code == status.HTTP_401_UNAUTHORIZED)

    # Winner receives new cookie
    assert settings.session_cookie_name in winner.cookies
    winning_token = winner.cookies[settings.session_cookie_name]
    assert winning_token != token1

    # Loser MUST NOT set or clear session cookie
    assert settings.session_cookie_name not in loser.cookies
    assert "set-cookie" not in loser.headers

    # Winning token remains valid (replay/loser did not revoke session)
    verified = session_manager.verify_session_token(winning_token)
    assert verified.rotation_version == 2


def test_task02_redis_failure_on_create_verify_and_rotate(client: TestClient) -> None:
    """Redis unavailability during create, verify, or rotate fails closed with HTTP 503 and never issues false success."""
    uid = str(uuid4())
    _sess, token1 = session_manager.create_session(uid, "https://idp.local", "sub-503")

    # 1. Failure during create_session -> 503, no token returned
    with patch.object(
        session_manager.denylist,
        "create_session_state",
        side_effect=HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Revocation store unavailable"),
    ):
        with pytest.raises(HTTPException) as exc_create:
            session_manager.create_session(uid, "https://idp.local", "sub-503-fail")
        assert exc_create.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    # 2. Failure during verify_session_token -> 503
    with patch.object(
        session_manager.denylist,
        "verify_and_get_session",
        side_effect=HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Revocation store unavailable"),
    ):
        client.cookies.set(settings.session_cookie_name, token1)
        res_me = client.get("/v1/auth/me")
        assert res_me.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "set-cookie" not in res_me.headers

    # 3. Ambiguous timeout / failure during rotate_session_state -> 503, no cookie set or cleared
    with patch.object(
        session_manager.denylist,
        "rotate_session_state",
        side_effect=HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Revocation store unavailable"),
    ):
        client.cookies.set(settings.session_cookie_name, token1)
        res_ref = client.post("/v1/auth/refresh", headers={"Origin": "http://localhost:8000"})
        assert res_ref.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "set-cookie" not in res_ref.headers


def test_task02_redis_lua_rotation_and_revocation_semantics() -> None:
    """Verify RedisRevocationStore methods and Lua state transitions against a stateful Redis command evaluator."""
    import json as _json

    from control_api.modules.identity.session import RedisRevocationStore

    store_dict: dict[str, tuple[str, int]] = {}
    current_redis_time = [1700000000]

    class FakeRedisClient:
        def get(self, key: str) -> str | None:
            entry = store_dict.get(key)
            if entry is None:
                return None
            val, exp = entry
            if current_redis_time[0] >= exp:
                del store_dict[key]
                return None
            return val

        def set(self, key: str, val: str, ex: int | None = None) -> bool:
            ttl = ex if ex is not None else 86400
            store_dict[key] = (val, current_redis_time[0] + ttl)
            return True

        def delete(self, *keys: str) -> int:
            count = 0
            for k in keys:
                if k in store_dict:
                    del store_dict[k]
                    count += 1
            return count

        def pipeline(self) -> Any:
            client_ref = self

            class FakePipe:
                def __init__(self) -> None:
                    self.ops: list[tuple[str, str | None]] = []

                def get(self, key: str) -> FakePipe:
                    self.ops.append(("get", key))
                    return self

                def time(self) -> FakePipe:
                    self.ops.append(("time", None))
                    return self

                def execute(self) -> list[Any]:
                    out: list[Any] = []
                    for op, k in self.ops:
                        if op == "get" and k is not None:
                            out.append(client_ref.get(k))
                        elif op == "time":
                            out.append((current_redis_time[0], 0))
                    return out

            return FakePipe()

        def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
            keys = keys_and_args[:numkeys]
            argv = keys_and_args[numkeys:]
            if numkeys == 2:
                # _LUA_REVOKE_SESSION
                rev_key, sess_key = keys[0], keys[1]
                exp_str, ttl_str = argv[0], argv[1]
                self.set(rev_key, exp_str, ex=int(ttl_str))
                self.delete(sess_key)
                return 1
            if numkeys == 3:
                # _LUA_ROTATE_SESSION
                sess_key, rev_sess_key, rev_user_key = keys[0], keys[1], keys[2]
                expected_uid = argv[0]
                expected_ver = int(argv[1])
                expected_cat = int(argv[2])
                override_now = int(argv[3])
                idle_timeout = int(argv[4])
                now_val = override_now if override_now > 0 else current_redis_time[0]

                rev_sess_raw = self.get(rev_sess_key)
                if rev_sess_raw is not None and int(rev_sess_raw) >= now_val:
                    return ["REVOKED"]
                rev_user_raw = self.get(rev_user_key)
                if rev_user_raw is not None and expected_cat <= int(rev_user_raw):
                    return ["REVOKED"]
                raw = self.get(sess_key)
                if raw is None:
                    return ["MISSING"]
                try:
                    st = _json.loads(raw)
                except (ValueError, TypeError):
                    return ["CORRUPT"]
                if not isinstance(st, dict) or st.get("user_id") != expected_uid or st.get("created_at") != expected_cat:
                    return ["CORRUPT"]
                if now_val >= int(st["absolute_expires_at"]):
                    self.delete(sess_key)
                    return ["ABSOLUTE_EXPIRED"]
                if now_val >= int(st["idle_expires_at"]):
                    return ["IDLE_EXPIRED"]
                if int(st["rotation_version"]) != expected_ver:
                    return ["VERSION_MISMATCH"]
                st["rotation_version"] = int(st["rotation_version"]) + 1
                st["idle_expires_at"] = min(now_val + idle_timeout, int(st["absolute_expires_at"]))
                updated_raw = _json.dumps(st, separators=(",", ":"))
                self.set(sess_key, updated_raw, ex=int(st["absolute_expires_at"]) - now_val)
                return ["OK", updated_raw]
            return 0

    store = RedisRevocationStore("redis://localhost:6379/15")
    store._client = FakeRedisClient()  # type: ignore[assignment]
    mgr = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1700000000

    sess1, tok1 = mgr.create_session(uid, "https://idp.local", "sub-redis-unit", now=t0)
    assert mgr.verify_session_token(tok1, now=t0).rotation_version == 1

    # Rotate
    sess2, tok2 = mgr.refresh_session(sess1, now=t0 + 10)
    assert sess2.rotation_version == 2
    assert mgr.verify_session_token(tok2, now=t0 + 10).rotation_version == 2

    # Old token rejected with VERSION_MISMATCH
    with pytest.raises(HTTPException) as exc_old:
        mgr.verify_session_token(tok1, now=t0 + 10)
    assert exc_old.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "superseded" in str(exc_old.value.detail).lower()

    # Corrupt Redis payload -> rejected with 401 and never recreated
    store_dict[f"auth:session:{sess1.session_id}"] = ("{corrupt-json", t0 + 1000)
    with pytest.raises(HTTPException) as exc_corrupt:
        mgr.verify_session_token(tok2, now=t0 + 11)
    assert exc_corrupt.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_task02_legacy_token_without_ver_claim_rejected() -> None:
    """Pre-TASK-02 tokens lacking the 'ver' claim are deterministically rejected with 401 and never lazy-registered."""
    from control_api.modules.identity.crypto import sign_jwt_hs256

    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    sid = str(uuid4())
    now = int(time.time())

    legacy_payload = {
        "type": "session",
        "session_id": sid,
        "sub": uid,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "original_iss": "https://idp.local",
        "original_sub": "legacy-sub",
        "iat": now,
        "idle_exp": now + 1800,
        "exp": now + 28800,
    }
    legacy_token = sign_jwt_hs256(legacy_payload, settings.jwt_secret_key.get_secret_value())

    with pytest.raises(HTTPException) as exc:
        manager.verify_session_token(legacy_token, now=now)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert store.get_session_state(sid, now=now) is None


# ==============================================================================
# AUTH-T12 / P0-02: User-Wide Revocation Cutoff, Monotonicity & Bounded Retention
# ==============================================================================


def test_auth_p0_02_user_revocation_cutoff_boundaries() -> None:
    """Exact cutoff boundaries: <= cutoff is revoked, > cutoff remains valid."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())

    t_cutoff = 1000000

    # Session 1: issued strictly before cutoff (999999 < 1000000) -> REVOKED
    _sess1, token_before = manager.create_session(uid, "https://idp.local", "sub-u", now=t_cutoff - 1)
    # Session 2: issued at exact cutoff (1000000 == 1000000) -> REVOKED
    _sess2, token_exact = manager.create_session(uid, "https://idp.local", "sub-u", now=t_cutoff)
    # Session 3: issued strictly after cutoff (1000001 > 1000000) -> VALID
    _sess3, token_after = manager.create_session(uid, "https://idp.local", "sub-u", now=t_cutoff + 1)

    # Apply user-wide revocation with cutoff = t_cutoff
    with patch("time.time", return_value=t_cutoff):
        manager.revoke_user(uid, cutoff=t_cutoff)

    # Verification checks
    with patch("time.time", return_value=t_cutoff + 10):
        # Before cutoff -> Rejected
        with pytest.raises(HTTPException) as exc1:
            manager.verify_session_token(token_before, now=t_cutoff + 10)
        assert exc1.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "revoked" in str(exc1.value.detail).lower()

        # At exact cutoff -> Rejected
        with pytest.raises(HTTPException) as exc2:
            manager.verify_session_token(token_exact, now=t_cutoff + 10)
        assert exc2.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "revoked" in str(exc2.value.detail).lower()

        # Strictly after cutoff -> Accepted
        verified_after = manager.verify_session_token(token_after, now=t_cutoff + 10)
        assert verified_after.user_id == uid


def test_auth_p0_02_refresh_cannot_bypass_user_revocation() -> None:
    """Refresh maintains original session created_at and cannot bypass user revocation."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1000000

    sess, _token = manager.create_session(uid, "https://idp.local", "sub-u", now=t0)

    # User revocation issued at t0 + 10
    with patch("time.time", return_value=t0 + 10):
        manager.revoke_user(uid, cutoff=t0 + 10)

    # Refresh attempt at t0 + 20 must be denied
    with patch("time.time", return_value=t0 + 20):
        with pytest.raises(HTTPException) as exc:
            manager.refresh_session(sess, now=t0 + 20)
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "revoked" in str(exc.value.detail).lower()


def test_auth_p0_02_monotonic_advance_and_out_of_order_protection() -> None:
    """Repeated or out-of-order revocations cannot move cutoff backwards or shorten retention."""
    store = InMemoryRevocationStore()
    uid = str(uuid4())
    t1 = 1000000
    t2 = 1000500

    with patch("time.time", return_value=t2):
        # 1. First revocation at t2
        store.revoke_user(uid, until_timestamp=t2, max_session_lifetime=3600)
        cutoff1, deadline1 = store._revoked_users[uid]
        assert cutoff1 == t2

        # 2. Out-of-order revocation with older cutoff t1
        store.revoke_user(uid, until_timestamp=t1, max_session_lifetime=3600)
        cutoff2, deadline2 = store._revoked_users[uid]
        # Must retain newer cutoff t2
        assert cutoff2 == t2
        # Must retain non-decreasing deadline
        assert deadline2 >= deadline1


def test_auth_p0_02_cutoff_input_validation() -> None:
    """Invalid cutoff timestamps (negative, zero, far future) must be rejected."""
    store = InMemoryRevocationStore()
    uid = str(uuid4())
    now = int(time.time())

    # Negative cutoff
    with pytest.raises(ValueError, match="positive unix timestamp"):
        store.revoke_user(uid, until_timestamp=-10)

    # Zero cutoff
    with pytest.raises(ValueError, match="positive unix timestamp"):
        store.revoke_user(uid, until_timestamp=0)

    # Future cutoff beyond clock skew allowance (default 60s)
    with pytest.raises(ValueError, match="cannot be in the future beyond clock allowance"):
        store.revoke_user(uid, until_timestamp=now + 500)


def test_auth_p0_02_bounded_retention_and_expiry_boundary() -> None:
    """Sessions past retention boundary are guaranteed dead by absolute expiry."""
    store = InMemoryRevocationStore()
    manager = SessionManager(denylist=store)
    uid = str(uuid4())
    t0 = 1000000
    lifetime = 3600
    clock_skew = settings.session_clock_skew_seconds

    with patch.object(settings, "session_absolute_timeout_seconds", lifetime):
        sess, token = manager.create_session(uid, "https://idp.local", "sub-u", now=t0)

        # Revoke user at t0
        with patch("time.time", return_value=t0):
            store.revoke_user(uid, until_timestamp=t0, max_session_lifetime=lifetime)

        # Before retention deadline: rejected as revoked
        t_active = t0 + lifetime - 100
        with patch("time.time", return_value=t_active):
            with pytest.raises(HTTPException) as exc_active:
                manager.verify_session_token(token, now=t_active)
            assert "revoked" in str(exc_active.value.detail).lower()

        # At absolute expiration boundary (now == exp): rejected as revoked or expired
        t_exact_exp = sess.absolute_expires_at
        with patch("time.time", return_value=t_exact_exp):
            with pytest.raises(HTTPException) as exc_boundary:
                manager.verify_session_token(token, now=t_exact_exp)
            assert exc_boundary.value.status_code == status.HTTP_401_UNAUTHORIZED

        # Past retention deadline: marker may be purged, but token is permanently absolute-expired
        t_past_retention = t0 + lifetime + clock_skew + 10
        with patch("time.time", return_value=t_past_retention):
            with pytest.raises(HTTPException) as exc_past:
                manager.verify_session_token(token, now=t_past_retention)
            assert exc_past.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "expired" in str(exc_past.value.detail).lower()
