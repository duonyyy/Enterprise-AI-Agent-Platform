"""API and Unit tests for Control Plane API Authentication (SPEC-AUTH-001 / Step 02)."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.crypto import generate_pkce_pair
from control_api.modules.identity.oidc import (
    AuthTransaction,
    InMemoryAuthTransactionStore,
    oidc_client,
)
from control_api.modules.identity.session import (
    InMemoryRevocationStore,
    session_manager,
)
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "oidc_test_server.py"
_spec = importlib.util.spec_from_file_location("oidc_test_server_fixture", str(FIXTURE_PATH))
assert _spec and _spec.loader
_oidc_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_oidc_module)
OidcTestServer = _oidc_module.OidcTestServer


class MockDatabaseContext:
    def __init__(self) -> None:
        self.users_db: dict[tuple[str, str], dict[str, Any]] = {}
        self.users_by_id: dict[str, dict[str, Any]] = {}
        self.memberships_db: list[dict[str, Any]] = []


@pytest.fixture
def mock_db() -> MockDatabaseContext:
    return MockDatabaseContext()


class MockMappings:
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
def mock_db_session(mock_db: MockDatabaseContext) -> AsyncSession:
    """Mock database session that handles user lookup, provisioning, and memberships."""
    session = AsyncMock()

    async def mock_execute(stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt).strip()
        params = params or {}
        res = AsyncMock()

        if "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE issuer = :issuer AND subject = :subject" in sql:
            issuer = str(params.get("issuer", ""))
            subject = str(params.get("subject", ""))
            user = mock_db.users_db.get((issuer, subject))
            res.mappings = lambda: MockMappings(item=user)
            return res

        if "INSERT INTO iam.users" in sql:
            user_id = str(uuid4())
            new_user = {
                "id": user_id,
                "issuer": params.get("issuer"),
                "subject": params.get("subject"),
                "display_name": params.get("display_name"),
                "email_normalized": params.get("email_normalized"),
            }
            mock_db.users_db[(str(params["issuer"]), str(params["subject"]))] = new_user
            mock_db.users_by_id[user_id] = new_user
            res.mappings = lambda: MockMappings(item=new_user)
            return res

        if "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE id = :id" in sql:
            uid = str(params.get("id"))
            user = mock_db.users_by_id.get(uid)
            res.mappings = lambda: MockMappings(item=user)
            return res

        if "SELECT role FROM iam.memberships" in sql:
            uid = str(params.get("user_id"))
            wid = str(params.get("workspace_id"))
            role = None
            for m in mock_db.memberships_db:
                if str(m.get("user_id")) == uid and str(m.get("workspace_id")) == wid and m.get("status") == "active":
                    role = m.get("role")
                    break
            res.scalar_one_or_none.return_value = role
            return res

        if "SELECT tenant_id, role FROM iam.memberships" in sql or "iam.resolve_user_workspace_membership" in sql:
            uid = str(params.get("user_id"))
            wid = str(params.get("workspace_id"))
            match = None
            for m in mock_db.memberships_db:
                if str(m.get("user_id")) == uid and str(m.get("workspace_id")) == wid and m.get("status") == "active":
                    match = {
                        "tenant_id": m.get("tenant_id"),
                        "role": m.get("role"),
                        "tenant_status": m.get("tenant_status", "active"),
                        "workspace_status": m.get("workspace_status", "active"),
                    }
                    break
            res.mappings = lambda: MockMappings(item=match)
            return res

        if ("FROM iam.memberships m" in sql and "JOIN iam.workspaces w" in sql) or "iam.get_user_workspaces" in sql:
            uid = str(params.get("user_id"))
            user_memberships = [
                m
                for m in mock_db.memberships_db
                if str(m.get("user_id")) == uid
                and m.get("status") == "active"
                and m.get("tenant_status", "active") == "active"
                and m.get("workspace_status", "active") == "active"
            ]
            res.mappings = lambda: MockMappings(items=user_memberships)
            return res

        res.mappings = lambda: MockMappings(item=None, items=[])
        res.scalar_one_or_none.return_value = None
        return res

    session.execute.side_effect = mock_execute
    return session


@pytest.fixture
def test_client(mock_db_session: AsyncSession) -> TestClient:
    app.dependency_overrides[get_db_session] = lambda: mock_db_session
    orig_store = oidc_client.store
    orig_denylist = session_manager.denylist
    mem_store = InMemoryAuthTransactionStore()
    mem_denylist = InMemoryRevocationStore()
    oidc_client.store = mem_store
    session_manager.denylist = mem_denylist
    client = TestClient(app, base_url="http://localhost:8000")
    yield client
    app.dependency_overrides.clear()
    oidc_client.store = orig_store
    session_manager.denylist = orig_denylist


def test_login_initiate_generates_valid_url_and_state(test_client: TestClient) -> None:
    resp = test_client.get("/v1/auth/login?return_to=/w/default/agents")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "authorization_url" in data
    assert "state" in data
    assert "code_challenge=" in data["authorization_url"]
    assert "code_challenge_method=S256" in data["authorization_url"]
    assert data["state"] in data["authorization_url"]
    assert settings.oidc_correlation_cookie_name in resp.cookies


def test_login_open_redirect_fallback(test_client: TestClient) -> None:
    # Evil return_to must be rejected and sanitized to safe fallback
    resp = test_client.get("/v1/auth/login?return_to=https://evil-phishing.com/steal-token")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    state = data["state"]
    tx = oidc_client.store.consume(state)
    assert tx is not None
    assert tx.return_to == "/login"


def test_callback_exchange_success_and_cookie_set(
    test_client: TestClient,
    mock_db: MockDatabaseContext,
) -> None:
    idp_server = OidcTestServer(issuer=settings.oidc_issuer)
    code_verifier, code_challenge = generate_pkce_pair()
    state = "state-test-123"
    nonce = "nonce-test-123"
    corr = "corr-test-browser-123"

    tx = AuthTransaction(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        code_challenge=code_challenge,
        return_to="/w/ws-alpha/agents",
        browser_correlation=corr,
        created_at=int(time.time()),
    )
    oidc_client.store.save(tx)
    test_client.cookies.set(settings.oidc_correlation_cookie_name, corr)

    auth_code = idp_server.create_authorization_code(
        client_id=settings.oidc_client_id,
        redirect_uri=settings.oidc_redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
        subject="sub-vinai-001",
        name="Engineer VinAI",
        email="engineer@vinai.io",
    )

    async def mock_handle_callback(
        code: str,
        state: str,
        http_client: Any = None,
        browser_correlation: str | None = None,
    ) -> tuple[dict[str, Any], AuthTransaction]:
        consumed_tx = oidc_client.store.consume(state)
        assert consumed_tx is not None
        token_res = idp_server.exchange_code_for_token(
            code=code,
            code_verifier=consumed_tx.code_verifier,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret.get_secret_value(),
            redirect_uri=settings.oidc_redirect_uri,
        )
        from control_api.modules.identity.crypto import verify_jwt_rs256_jwks
        claims = verify_jwt_rs256_jwks(token_res["id_token"], idp_server.get_jwks())
        return claims, consumed_tx

    with patch.object(oidc_client, "handle_callback", side_effect=mock_handle_callback):
        resp = test_client.get(
            f"/v1/auth/callback?code={auth_code}&state={state}",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["subject"] == "sub-vinai-001"
        assert data["issuer"] == settings.oidc_issuer
        assert data["display_name"] == "Engineer VinAI"
        assert data["email_normalized"] == "engineer@vinai.io"
        assert data["redirect_url"] == "/w/ws-alpha/agents"

        # Check session cookie is set
        assert settings.session_cookie_name in resp.cookies
        cookie_val = resp.cookies[settings.session_cookie_name]

        verified_session = session_manager.verify_session_token(cookie_val)
        assert verified_session.subject == "sub-vinai-001"


def test_callback_fails_on_state_replay(test_client: TestClient) -> None:
    resp = test_client.get("/v1/auth/callback?code=some-code&state=nonexistent-or-consumed-state")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid, expired, or replayed state" in resp.json()["detail"]


def test_get_current_user_me_flow(test_client: TestClient, mock_db: MockDatabaseContext) -> None:
    user_id = str(uuid4())
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())

    mock_db.users_db[(settings.oidc_issuer, "sub-user-99")] = {
        "id": user_id,
        "issuer": settings.oidc_issuer,
        "subject": "sub-user-99",
        "display_name": "Senior Researcher",
        "email_normalized": "researcher@vinai.io",
    }
    mock_db.users_by_id[user_id] = mock_db.users_db[(settings.oidc_issuer, "sub-user-99")]

    mock_db.memberships_db.append({
        "tenant_id": tenant_id,
        "tenant_slug": "vinai-corp",
        "tenant_name": "VinAI Corporation",
        "workspace_id": workspace_id,
        "workspace_slug": "ws-ai-research",
        "workspace_name": "AI Research Lab",
        "user_id": user_id,
        "role": "developer",
        "status": "active",
    })

    _session, session_token = session_manager.create_session(user_id, settings.oidc_issuer, "sub-user-99")

    test_client.cookies.set(settings.session_cookie_name, session_token)
    resp = test_client.get("/v1/auth/me")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["user_id"] == user_id
    assert data["subject"] == "sub-user-99"
    assert data["display_name"] == "Senior Researcher"
    assert len(data["workspaces"]) == 1
    assert data["workspaces"][0]["workspace_slug"] == "ws-ai-research"
    assert data["workspaces"][0]["role"] == "developer"


def test_session_refresh_and_logout(test_client: TestClient) -> None:
    user_id = str(uuid4())
    _session, session_token = session_manager.create_session(user_id, "https://idp.local", "sub-test")
    test_client.cookies.set(settings.session_cookie_name, session_token)
    origin_header = {"Origin": "http://localhost:8000"}

    # 1. Refresh session (atomically rotates credential version)
    resp_refresh = test_client.post("/v1/auth/refresh", headers=origin_header)
    assert resp_refresh.status_code == status.HTTP_200_OK
    assert resp_refresh.json()["status"] == "refreshed"
    assert settings.session_cookie_name in resp_refresh.cookies
    new_token = resp_refresh.cookies[settings.session_cookie_name]
    assert new_token != session_token

    # 1b. Old credential before rotation must now be rejected
    test_client.cookies.clear()
    test_client.cookies.set(settings.session_cookie_name, session_token)
    resp_old = test_client.get("/v1/auth/me")
    assert resp_old.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Logout
    test_client.cookies.clear()
    test_client.cookies.set(settings.session_cookie_name, new_token)
    resp_logout = test_client.post("/v1/auth/logout", headers=origin_header)
    assert resp_logout.status_code == status.HTTP_200_OK
    assert resp_logout.json()["status"] == "logged_out"

    # 3. Access /v1/auth/me after logout must be rejected (revoked session)
    test_client.cookies.clear()
    test_client.cookies.set(settings.session_cookie_name, new_token)
    resp_after = test_client.get("/v1/auth/me")
    assert resp_after.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in resp_after.json()["detail"].lower()


def test_callback_idp_error_handling(test_client: TestClient) -> None:
    # 1. JSON mode IdP error
    res_json = test_client.get(
        "/v1/auth/callback?error=access_denied&error_description=User+cancelled&state=st-err",
        headers={"Accept": "application/json"},
    )
    assert res_json.status_code == status.HTTP_400_BAD_REQUEST
    assert "access_denied" in res_json.json()["detail"]

    # 2. HTML navigation IdP error -> 302 redirect
    res_html = test_client.get(
        "/v1/auth/callback?error=access_denied&error_description=User+cancelled&state=st-err-html",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert res_html.status_code == status.HTTP_302_FOUND
    assert "/login?error=access_denied" in res_html.headers["location"]

    # 3. Missing code
    res_no_code = test_client.get(
        "/v1/auth/callback?state=st-no-code",
        headers={"Accept": "application/json"},
    )
    assert res_no_code.status_code == status.HTTP_400_BAD_REQUEST
    assert "Missing authorization code" in res_no_code.json()["detail"]


def test_token_exchange_denied_when_not_member(test_client: TestClient) -> None:
    user_id = str(uuid4())
    _, session_token = session_manager.create_session(user_id, "https://idp.local", "sub-stranger")
    test_client.cookies.set(settings.session_cookie_name, session_token)

    forbidden_workspace = str(uuid4())
    res = test_client.post(
        "/v1/auth/token",
        json={"workspace_id": forbidden_workspace},
        headers={"Origin": "http://localhost:8000"},
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert "Active workspace membership required" in res.json()["detail"]
