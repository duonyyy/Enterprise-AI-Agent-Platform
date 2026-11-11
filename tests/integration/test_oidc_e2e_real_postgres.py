"""Real End-to-End Authentication, Session Lifecycle & PostgreSQL Integration (TASK 04 / Gate G2 / D4-E).

Executes the complete real authentication lifecycle against real PostgreSQL 18.6
(under restricted application role platform_app, migrations 0001->0004 applied)
and a real HTTP OIDC IdP Server (bound to an ephemeral TCP loopback socket, serving RS256 JWKS & token endpoint).

No mocks for Database or OIDC communication.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from agent_runtime.auth import (
    get_runtime_principal,
    set_parent_session_verifier,
    verify_runtime_token,
)
from agent_runtime.settings import RuntimeSettings, get_settings
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.providers.oidc_client import oidc_client
from control_api.modules.identity.repositories.auth_transaction_repository import (
    InMemoryAuthTransactionStore,
)
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.fixtures.oidc_http_server import run_oidc_http_server


@pytest.mark.integration
def test_oidc_e2e_full_lifecycle_real_postgres_and_http_idp(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full user journey:
    1. Login initiation -> gets correlation cookie and authorization url with PKCE challenge
    2. Callback -> verifies PKCE S256 + RS256 ID Token over HTTP socket -> provisions iam.users in real PostgreSQL -> receives session cookie
    3. Initial /me -> user has no memberships -> empty workspaces: []; calling workspace API returns 403
    4. Grant active membership in real PostgreSQL -> /me returns active workspace
    5. Session refresh -> rotates ver 1 -> 2; replay of stale cookie ver=1 is rejected 401
    6. Token exchange -> issues Scoped Workspace Token; Agent Runtime verifies token and RLS context
    7. Workspace API -> accessible with session cookie or scoped token; cross-workspace access denied 403
    8. Logout -> revokes session; subsequent requests with session cookie or scoped token denied 401
    """
    psycopg = pytest.importorskip("psycopg")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    conn_dict = psycopg.conninfo.conninfo_to_dict(app_url)
    sa_url = (
        f"postgresql+asyncpg://{conn_dict['user']}:{conn_dict['password']}"
        f"@{conn_dict['host']}:{conn_dict['port']}/{conn_dict['dbname']}"
    )

    db_engine = create_async_engine(sa_url, poolclass=NullPool)
    db_session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db_session():
        async with db_session_factory() as session, session.begin():
            yield session

    app.dependency_overrides[get_db_session] = override_db_session

    tenant_id = uuid4()
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    created_user_id: str | None = None

    # Start live HTTP OIDC IdP server on loopback port
    with run_oidc_http_server() as (oidc_server, issuer_url):
        # Configure Control API to talk to this live HTTP IdP server
        orig_issuer = settings.oidc_issuer
        orig_db_url = settings.database_url
        orig_tx_store = oidc_client.store
        orig_denylist = session_manager.denylist

        # Explicit store injection for standalone integration environment
        redis_test_url = os.environ.get("TEST_REDIS_URL")
        if not redis_test_url:
            oidc_client.store = InMemoryAuthTransactionStore()
            session_manager.denylist = InMemoryRevocationStore()

        settings.oidc_issuer = issuer_url
        settings.database_url = sa_url
        # Force fresh JWKS fetch over HTTP network
        oidc_client._jwks_cache = None
        oidc_client._jwks_fetched_at = 0.0

        # Wire Agent Runtime parent session verifier to use authoritative session manager denylist
        set_parent_session_verifier(session_manager.denylist)

        client = TestClient(app, base_url="http://localhost:8000")

        try:
            # -------------------------------------------------------------
            # STEP 1: OIDC Login Initiation
            # -------------------------------------------------------------
            target_return_to = f"/w/{workspace_id}/agents"
            login_res = client.get(
                f"/v1/auth/login?return_to={target_return_to}",
                headers={"Accept": "application/json"},
            )
            assert login_res.status_code == 200, f"Login initiation failed: {login_res.text}"
            login_data = login_res.json()
            assert "authorization_url" in login_data
            assert "state" in login_data
            auth_url = login_data["authorization_url"]
            state = login_data["state"]

            # Verify browser correlation cookie was set
            corr_cookie = client.cookies.get(settings.oidc_correlation_cookie_name)
            assert corr_cookie, "agent_oidc_corr cookie must be set on login initiation"

            # Parse query params from authorization URL
            parsed_auth_url = urlparse(auth_url)
            query_params = parse_qs(parsed_auth_url.query)
            assert query_params.get("client_id") == [settings.oidc_client_id]
            assert query_params.get("code_challenge_method") == ["S256"]
            code_challenge = query_params.get("code_challenge", [""])[0]
            nonce = query_params.get("nonce", [""])[0]
            assert code_challenge, "code_challenge must be present in authorization_url"
            assert nonce, "nonce must be present in authorization_url"

            # -------------------------------------------------------------
            # STEP 2: IdP Authorization Code Issuance & Callback over Real HTTP
            # -------------------------------------------------------------
            unique_sub = f"sub-e2e-{uuid4().hex[:8]}"
            user_email = f"user.{uuid4().hex[:6]}@enterprise.vn"
            user_display_name = "Real OIDC Enterprise User"

            auth_code = oidc_server.create_authorization_code(
                client_id=settings.oidc_client_id,
                redirect_uri=settings.oidc_redirect_uri,
                state=state,
                nonce=nonce,
                code_challenge=code_challenge,
                subject=unique_sub,
                name=user_display_name,
                email=user_email,
            )

            # Client performs callback (transmitting correlation cookie)
            callback_res = client.get(
                f"/v1/auth/callback?code={auth_code}&state={state}",
                headers={"Accept": "application/json"},
            )
            assert callback_res.status_code == 200, f"Callback failed: {callback_res.text}"
            cb_data = callback_res.json()
            created_user_id = cb_data["user_id"]
            assert cb_data["issuer"] == issuer_url
            assert cb_data["subject"] == unique_sub
            assert cb_data["display_name"] == user_display_name

            session_cookie_1 = client.cookies.get(settings.session_cookie_name)
            assert session_cookie_1, "agent_session cookie must be set upon successful callback"

            # -------------------------------------------------------------
            # STEP 3: Verify iam.users record in Real PostgreSQL (No Mocks)
            # -------------------------------------------------------------
            with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, issuer, subject, email_normalized, display_name FROM iam.users WHERE id = %s",
                    (created_user_id,),
                )
                db_user = cur.fetchone()
                assert db_user is not None, "User must be provisioned in PostgreSQL iam.users table"
                assert db_user[1] == issuer_url
                assert db_user[2] == unique_sub
                assert db_user[3] == user_email.lower()
                assert db_user[4] == user_display_name

            # -------------------------------------------------------------
            # STEP 4: Initial /me with Empty Workspaces State (Finding R08 / D4-A)
            # -------------------------------------------------------------
            me_res_1 = client.get("/v1/auth/me")
            assert me_res_1.status_code == 200
            me_data_1 = me_res_1.json()
            assert me_data_1["user_id"] == created_user_id
            assert me_data_1["workspaces"] == [], "Newly provisioned user must have empty workspaces list"

            # Attempting to access workspace resources without membership is denied 403
            ws_denied = client.get(f"/v1/workspaces/{workspace_id}/agents")
            assert ws_denied.status_code == 403
            assert ws_denied.json()["detail"] == "Active workspace membership required"

            # -------------------------------------------------------------
            # STEP 5: Provision Active Membership in Real PostgreSQL
            # -------------------------------------------------------------
            with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, 'E2E Tenant', 'active')",
                    (tenant_id, f"tenant-{tenant_id.hex[:8]}"),
                )
                cur.execute(
                    "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, 'E2E Workspace', 'active')",
                    (tenant_id, workspace_id, f"ws-{workspace_id.hex[:8]}"),
                )
                cur.execute(
                    "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, 'Other Workspace', 'active')",
                    (tenant_id, other_workspace_id, f"ws-{other_workspace_id.hex[:8]}"),
                )
                cur.execute(
                    "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) VALUES (%s, %s, %s, 'developer', 'active')",
                    (tenant_id, workspace_id, created_user_id),
                )

            # -------------------------------------------------------------
            # STEP 6: /me Discovery Reflects Active Membership
            # -------------------------------------------------------------
            me_res_2 = client.get("/v1/auth/me")
            assert me_res_2.status_code == 200
            me_data_2 = me_res_2.json()
            assert len(me_data_2["workspaces"]) == 1
            ws_info = me_data_2["workspaces"][0]
            assert ws_info["workspace_id"] == str(workspace_id)
            assert ws_info["tenant_id"] == str(tenant_id)
            assert ws_info["role"] == "developer"

            # -------------------------------------------------------------
            # STEP 7: Atomic Session Rotation (POST /v1/auth/refresh)
            # -------------------------------------------------------------
            refresh_res = client.post(
                "/v1/auth/refresh",
                headers={"Origin": "http://localhost:8000"},
            )
            assert refresh_res.status_code == 200
            assert refresh_res.json() == {"status": "refreshed"}

            session_cookie_2 = client.cookies.get(settings.session_cookie_name)
            assert session_cookie_2
            assert session_cookie_2 != session_cookie_1, "Refresh must issue rotated session token"

            # Replay of stale cookie_1 (ver=1) is strictly rejected with 401
            client.cookies.clear()
            stale_me = client.get(
                "/v1/auth/me",
                headers={"Cookie": f"{settings.session_cookie_name}={session_cookie_1}"},
            )
            assert stale_me.status_code == 401, "Stale rotated session token must be denied 401"
            assert "superseded by rotation" in stale_me.json()["detail"]

            # Active cookie_2 (ver=2) is accepted
            valid_me = client.get(
                "/v1/auth/me",
                headers={"Cookie": f"{settings.session_cookie_name}={session_cookie_2}"},
            )
            assert valid_me.status_code == 200

            # -------------------------------------------------------------
            # STEP 8: Token Exchange (POST /v1/auth/token) & Runtime Verification
            # -------------------------------------------------------------
            token_res = client.post(
                "/v1/auth/token",
                json={"workspace_id": str(workspace_id)},
                headers={
                    "Origin": "http://localhost:8000",
                    "Cookie": f"{settings.session_cookie_name}={session_cookie_2}",
                },
            )
            assert token_res.status_code == 200
            token_data = token_res.json()
            scoped_token = token_data["access_token"]
            assert token_data["token_type"] == "Bearer"
            assert token_data["expires_in"] > 0

            # Verify scoped token in Agent Runtime
            expected_secret = (
                settings.runtime_jwt_secret.get_secret_value()
                if len(settings.runtime_jwt_secret.get_secret_value()) >= 32
                else settings.jwt_secret_key.get_secret_value()
            )
            rt_settings = RuntimeSettings(
                runtime_jwt_secret=expected_secret,
                runtime_jwt_issuer=settings.runtime_jwt_issuer or settings.jwt_issuer,
                runtime_jwt_audience=settings.runtime_jwt_audience,
                database_url=sa_url,
            )
            rt_principal = verify_runtime_token(
                scoped_token,
                settings=rt_settings,
                require_session_id=True,
            )
            assert str(rt_principal.workspace_id) == str(workspace_id)
            assert str(rt_principal.tenant_id) == str(tenant_id)
            assert str(rt_principal.user_id) == str(created_user_id)

            # Test DB resolution in Agent Runtime
            monkeypatch.setenv("RUNTIME_JWT_SECRET", expected_secret)
            monkeypatch.setenv(
                "RUNTIME_JWT_ISSUER",
                settings.runtime_jwt_issuer or settings.jwt_issuer,
            )
            monkeypatch.setenv("RUNTIME_JWT_AUDIENCE", settings.runtime_jwt_audience)
            get_settings.cache_clear()

            async def _check_runtime_db():
                async with db_session_factory() as sess, sess.begin():
                    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=scoped_token)
                    active_p = await get_runtime_principal(
                        credentials=creds,
                        session=sess,
                    )
                    assert active_p.roles == ("developer",)
                    assert "run:read" in active_p.scopes

            asyncio.run(_check_runtime_db())

            # -------------------------------------------------------------
            # STEP 9: Access Workspace API on Control API
            # -------------------------------------------------------------
            # 9a. Using Session Cookie
            ws_api_res = client.get(
                f"/v1/workspaces/{workspace_id}/agents",
                headers={"Cookie": f"{settings.session_cookie_name}={session_cookie_2}"},
            )
            assert ws_api_res.status_code == 200, f"Workspace agents list failed: {ws_api_res.text}"

            # 9b. Using Scoped Workspace Token (Bearer)
            client.cookies.clear()
            auth_bearer_headers = {"Authorization": f"Bearer {scoped_token}"}
            ws_api_bearer = client.get(f"/v1/workspaces/{workspace_id}/agents", headers=auth_bearer_headers)
            assert ws_api_bearer.status_code == 200

            # 9c. Cross-workspace confinement denial
            cross_ws_res = client.get(f"/v1/workspaces/{other_workspace_id}/agents", headers=auth_bearer_headers)
            assert cross_ws_res.status_code == 403
            assert "Workspace scope denied" in cross_ws_res.json()["detail"]

            # -------------------------------------------------------------
            # STEP 10: Logout & Revocation Replay Resistance
            # -------------------------------------------------------------
            logout_res = client.post(
                "/v1/auth/logout",
                headers={
                    "Origin": "http://localhost:8000",
                    "Cookie": f"{settings.session_cookie_name}={session_cookie_2}",
                },
            )
            assert logout_res.status_code == 200
            assert logout_res.json() == {"status": "logged_out"}

            # Replay Denied for Session Cookie
            revoked_headers = {"Cookie": f"{settings.session_cookie_name}={session_cookie_2}"}
            assert client.get("/v1/auth/me", headers=revoked_headers).status_code == 401
            assert client.post(
                "/v1/auth/token",
                json={"workspace_id": str(workspace_id)},
                headers={"Origin": "http://localhost:8000", **revoked_headers},
            ).status_code == 401
            assert client.get(f"/v1/workspaces/{workspace_id}/agents", headers=revoked_headers).status_code == 401

            # Replay Denied for Scoped Workspace Token (Parent session revoked on Control API & Agent Runtime)
            client.cookies.clear()
            assert client.get(f"/v1/workspaces/{workspace_id}/agents", headers=auth_bearer_headers).status_code == 401
            with pytest.raises(Exception) as rt_exc:
                asyncio.run(_check_runtime_db())
            assert "401" in str(rt_exc.value)
            assert "parent_session_revoked_or_expired" in str(rt_exc.value)

        finally:
            monkeypatch.undo()
            get_settings.cache_clear()
            app.dependency_overrides.clear()
            set_parent_session_verifier(None)
            oidc_client.store = orig_tx_store
            session_manager.denylist = orig_denylist
            settings.oidc_issuer = orig_issuer
            settings.database_url = orig_db_url
            # Clean up PostgreSQL test records
            with psycopg.connect(owner_url) as conn, conn.cursor() as cur:
                if created_user_id:
                    cur.execute("DELETE FROM iam.memberships WHERE user_id = %s", (created_user_id,))
                cur.execute(
                    "DELETE FROM iam.workspaces WHERE id IN (%s, %s)",
                    (workspace_id, other_workspace_id),
                )
                cur.execute("DELETE FROM iam.tenants WHERE id = %s", (tenant_id,))
                if created_user_id:
                    cur.execute("DELETE FROM iam.users WHERE id = %s", (created_user_id,))
            asyncio.run(db_engine.dispose())
