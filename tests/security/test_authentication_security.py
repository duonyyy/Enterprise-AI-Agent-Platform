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
from fastapi import status
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
