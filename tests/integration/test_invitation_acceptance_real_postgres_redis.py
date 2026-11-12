"""Real PostgreSQL 16 (platform_app + FORCE RLS) + Real Redis + Live HTTP OIDC integration & concurrency tests (Step 03-C)."""

from __future__ import annotations

import asyncio
import email as email_stdlib
import importlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import psycopg
import pytest
import redis as redis_lib
from control_api.core import database as control_db
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.identity.providers.oidc_client import oidc_client
from control_api.modules.identity.repositories.auth_transaction_repository import (
    RedisAuthTransactionStore,
)
from control_api.modules.identity.repositories.session_repository import (
    RedisRevocationStore,
)
from control_api.modules.identity.services.session_service import session_manager
from control_api.modules.workspaces.models.errors import (
    InvitationError,
    InvitationForbiddenError,
    InvitationGoneError,
)
from control_api.modules.workspaces.repositories.invitation_repository import (
    InvitationPolicyConfig,
    InvitationRepository,
)
from control_api.modules.workspaces.services import handoff_store as handoff_store_module
from control_api.modules.workspaces.services.handoff_store import RedisInvitationHandoffStore
from control_api.modules.workspaces.services.invitation_acceptance_service import (
    InvitationAcceptanceService,
)
from control_api.modules.workspaces.services.invitation_service import InvitationService
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from jobs_worker.invitation_consumer import InvitationOutboxConsumer
from jobs_worker.smtp_adapter import SmtpConfig, StdlibSmtpTransport
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import InvitationProofProtector
from run_migrations import run_migrations
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.fixtures.oidc_http_server import run_oidc_http_server
from tests.integration.test_workspace_invitations_real_postgres_smtp import (
    _issue_session_cookie_header,
    _RealSmtpSinkServer,
    _seed_workspace_and_users,
    _to_async_pg_url,
    _to_sync_pg_url,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "database/migrations"
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


def _check_redis_available() -> None:
    client = redis_lib.Redis.from_url(REDIS_TEST_URL, socket_timeout=2.0)
    try:
        if not client.ping():
            pytest.fail("Redis ping failed")
        client.flushdb()
    finally:
        client.close()


def _configure_real_stack(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_url: str,
    proof_key: str,
    handoff_key: str,
    issuer_url: str,
    redis_url: str = REDIS_TEST_URL,
):
    rev_store = RedisRevocationStore(redis_url=redis_url)
    tx_store = RedisAuthTransactionStore(redis_url=redis_url)
    hd_store = RedisInvitationHandoffStore(redis_url=redis_url)

    monkeypatch.setattr(session_manager, "denylist", rev_store)
    monkeypatch.setattr(oidc_client, "store", tx_store)
    monkeypatch.setattr(handoff_store_module, "invitation_handoff_store", hd_store)

    async_engine = create_async_engine(_to_async_pg_url(app_url), pool_size=8, max_overflow=8)
    async_session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    workspaces_router = importlib.import_module("control_api.modules.workspaces.api.router")
    accept_router = importlib.import_module(
        "control_api.modules.workspaces.api.invitation_accept_router"
    )
    monkeypatch.setattr(control_db, "engine", async_engine)
    monkeypatch.setattr(control_db, "AsyncSessionLocal", async_session_factory)
    monkeypatch.setattr(
        workspaces_router,
        "invitation_service",
        InvitationService(InvitationRepository(PostgresOutboxEventBus(async_session_factory))),
    )
    monkeypatch.setattr(
        accept_router,
        "acceptance_service",
        InvitationAcceptanceService(InvitationRepository()),
    )

    monkeypatch.setattr(settings, "oidc_issuer", issuer_url)
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://localhost:8000/v1/auth/callback")
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
    monkeypatch.setattr(settings, "invitation_ttl_hours", 72)
    monkeypatch.setattr(settings, "invitation_resend_cooldown_seconds", 1)
    monkeypatch.setattr(settings, "invitation_recipient_hourly_limit", 20)
    monkeypatch.setattr(settings, "invitation_workspace_hourly_limit", 100)
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", [])
    oidc_client._jwks_cache = None

    return async_engine, async_session_factory, rev_store, tx_store, hd_store


def _extract_invite_link_parts(raw_mime: str) -> tuple[str, str]:
    msg = email_stdlib.message_from_string(raw_mime)
    body = msg.get_payload(decode=True).decode("utf-8")
    m = re.search(
        r"http://localhost:3000/invitations/([0-9a-f-]{36})/accept#token=([A-Za-z0-9_-]+)",
        body,
    )
    assert m is not None, f"Invitation fragment URL not found in MIME body:\n{body}"
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# 1. Migration 0006 & PostgreSQL RLS / Least-Privilege Enforcement
# ---------------------------------------------------------------------------


def test_migration_0006_upgrade_and_rls_privilege_isolation(
    isolated_database: dict[str, Any],
) -> None:
    """Clean upgrade 0001..0005 -> 0006; platform_app cannot read invitations without RLS or lookup function."""
    owner_url = isolated_database["owner_url"]
    app_url = isolated_database["app_url"]

    sql_files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    pre_0006 = [p for p in sql_files if not p.name.startswith("0006_")]
    assert len(pre_0006) == 5

    # Upgrade incrementally 0001..0005 then apply 0006
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        for p in pre_0006:
            shutil.copy2(p, Path(tmpdir) / p.name)
        run_migrations(owner_url, Path(tmpdir))

    # Also test upgrading from a DB that had the legacy 1-arg signature before running 0006:
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "CREATE OR REPLACE FUNCTION iam.resolve_invitation_accept_scope(p_invitation_id uuid) "
            "RETURNS TABLE (invitation_id uuid, tenant_id uuid, workspace_id uuid) "
            "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, iam, pg_temp "
            "AS $$ SELECT id, tenant_id, workspace_id FROM iam.workspace_invitations WHERE id = p_invitation_id; $$;"
        )
        conn.execute(
            "GRANT EXECUTE ON FUNCTION iam.resolve_invitation_accept_scope(uuid) TO platform_app;"
        )

    run_migrations(owner_url, MIGRATIONS_DIR)

    # Seed tenant/workspace/invitation/recipient user as owner
    tid, wid, creator_id, recipient_id, inv_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    valid_hash = "a" * 64
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
            "VALUES (%s, 'https://idp.test', 'creator', 'Creator', 'creator@example.com'), "
            "       (%s, 'https://idp.test', 'sub-target', 'Target', 'target@example.com')",
            (creator_id, recipient_id),
        )
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, 't-rls', 'T RLS', 'active')",
            (tid,),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, 'w-rls', 'W RLS', 'active')",
            (tid, wid),
        )
        conn.execute(
            "INSERT INTO iam.workspace_invitations "
            "(id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, role, "
            " token_hash, proof_ciphertext, proof_key_id, status, generation, delivery_state, created_by_user_id, "
            " delivery_requested_by_user_id, expires_at) "
            "VALUES (%s, %s, %s, 'target@example.com', 'https://idp.test', 'developer', "
            " %s, 'dummy-cipher', 'v1', 'pending', 1, 'queued', %s, %s, now() + interval '1 day')",
            (inv_id, tid, wid, valid_hash, creator_id, creator_id),
        )

    # Connect as unprivileged runtime role platform_app
    with psycopg.connect(app_url) as conn:
        role_info = conn.execute(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert role_info == ("platform_app", False, False)

        # 0. Old 1-arg unsafe overload is GONE after fresh bootstrap / upgrade
        with pytest.raises(psycopg.errors.UndefinedFunction):
            conn.execute("SELECT * FROM iam.resolve_invitation_accept_scope(%s)", (inv_id,))
        conn.rollback()

        # 0b. PUBLIC has zero execute privilege; platform_app has execute on 6-arg signature
        pub_priv = conn.execute(
            "SELECT has_function_privilege('public', "
            "'iam.resolve_invitation_accept_scope(uuid, text, uuid, text, text, text)', 'EXECUTE')"
        ).fetchone()[0]
        app_priv = conn.execute(
            "SELECT has_function_privilege('platform_app', "
            "'iam.resolve_invitation_accept_scope(uuid, text, uuid, text, text, text)', 'EXECUTE')"
        ).fetchone()[0]
        assert pub_priv is False
        assert app_priv is True

        # 1. Bound 6-arg call succeeds for legitimate proof holder (even with 0 memberships)
        #    and returns ONLY 3 columns (invitation_id, tenant_id, workspace_id) - zero tenant/workspace metadata!
        scope_row = conn.execute(
            "SELECT * FROM iam.resolve_invitation_accept_scope(%s, %s, %s, %s, %s, %s)",
            (inv_id, valid_hash, recipient_id, "https://idp.test", "sub-target", "target@example.com"),
        ).fetchone()
        assert scope_row is not None
        assert len(scope_row) == 3
        assert scope_row == (inv_id, tid, wid)

        # 1b. Every mismatched/missing/malformed proof, user_id, issuer, subject, email, or unknown ID returns 0 rows
        bad_cases = [
            (inv_id, "b" * 64, recipient_id, "https://idp.test", "sub-target", "target@example.com"),
            (inv_id, "malformed", recipient_id, "https://idp.test", "sub-target", "target@example.com"),
            (inv_id, None, recipient_id, "https://idp.test", "sub-target", "target@example.com"),
            (inv_id, valid_hash, uuid4(), "https://idp.test", "sub-target", "target@example.com"),
            (inv_id, valid_hash, recipient_id, "https://evil.test", "sub-target", "target@example.com"),
            (inv_id, valid_hash, recipient_id, "https://idp.test", "wrong-sub", "target@example.com"),
            (inv_id, valid_hash, recipient_id, "https://idp.test", "sub-target", "other@example.com"),
            (uuid4(), valid_hash, recipient_id, "https://idp.test", "sub-target", "target@example.com"),
        ]
        for params in bad_cases:
            assert (
                conn.execute(
                    "SELECT * FROM iam.resolve_invitation_accept_scope(%s, %s, %s, %s, %s, %s)",
                    params,
                ).fetchall()
                == []
            )

        # 2. Direct query WITHOUT app.tenant_id / app.workspace_id yields 0 rows (FORCE RLS)
        direct_rows = conn.execute("SELECT id FROM iam.workspace_invitations").fetchall()
        assert direct_rows == []

        # 3. Attempting to set a DIFFERENT tenant/workspace returns 0 rows
        other_tid, other_wid = uuid4(), uuid4()
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(other_tid),))
        conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(other_wid),))
        cross_tenant_rows = conn.execute(
            "SELECT id FROM iam.workspace_invitations WHERE id = %s", (inv_id,)
        ).fetchall()
        assert cross_tenant_rows == []


# ---------------------------------------------------------------------------
# 2. End-to-End Real PostgreSQL + Redis + Live HTTP OIDC + Post-Commit Recovery
# ---------------------------------------------------------------------------


def test_e2e_invitation_acceptance_real_postgres_redis_and_oidc(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _check_redis_available()
    proof_key = Fernet.generate_key().decode("ascii")
    handoff_key = Fernet.generate_key().decode("ascii")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]

    with run_oidc_http_server() as (idp_server, issuer_url):
        seed = _seed_workspace_and_users(owner_url)
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE iam.users SET issuer = %s WHERE id = %s",
                (issuer_url, seed["admin_a"]),
            )
        async_engine, _async_factory, _rev_store, _tx_store, _hd_store = _configure_real_stack(
            monkeypatch,
            app_url=app_url,
            proof_key=proof_key,
            handoff_key=handoff_key,
            issuer_url=issuer_url,
        )

        invitee_email = f"newdev-{seed['tenant_a'].hex[:6]}@example.com"
        invitee_sub = f"sub-newdev-{seed['tenant_a'].hex[:6]}"
        redis_raw = redis_lib.Redis.from_url(REDIS_TEST_URL, decode_responses=True)

        async def _scenario() -> None:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                # 1. Admin creates invitation & worker delivers email with #token=proof
                admin_headers = {
                    **_issue_session_cookie_header(
                        seed["admin_a"], issuer_url, f"admin-a-{seed['tenant_a'].hex[:8]}"
                    ),
                    "Origin": "http://localhost:3000",
                }
                create_resp = await client.post(
                    f"/v1/workspaces/{seed['ws_a']}/invitations",
                    json={"email": invitee_email, "role": "developer"},
                    headers=admin_headers,
                )
                assert create_resp.status_code == 201, create_resp.text

            smtp = _RealSmtpSinkServer(mode="accept")
            sync_engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
            try:
                consumer = InvitationOutboxConsumer(
                    database_url=_to_sync_pg_url(app_url),
                    tenant_id=str(seed["tenant_a"]),
                    workspace_id=str(seed["ws_a"]),
                    protector=InvitationProofProtector(proof_key, key_id="proof-v1"),
                    public_app_origin="http://localhost:3000",
                    smtp_transport=StdlibSmtpTransport(
                        SmtpConfig(
                            host="127.0.0.1",
                            port=smtp.port,
                            sender="no-reply@example.com",
                            tls_mode="none",
                            allow_plaintext_local_smtp=True,
                        )
                    ),
                    engine=sync_engine,
                )
                processed = consumer.run_once()
                assert processed == 1
                assert len(smtp.messages) == 1
                inv_id_str, raw_proof = _extract_invite_link_parts(smtp.messages[0]["raw"])
            finally:
                smtp.close()
                sync_engine.dispose()

            # 2. Invitee opens link in fresh browser: handoff -> cookie -> unauth preview 401
            async with AsyncClient(
                transport=transport, base_url="http://localhost:8000"
            ) as browser:
                h_resp = await browser.post(
                    f"/v1/invitations/{inv_id_str}/handoff",
                    json={"token": raw_proof},
                    headers={"Origin": "http://localhost:3000"},
                )
                assert h_resp.status_code == 201
                assert settings.invitation_handoff_cookie_name in h_resp.cookies

                # Redis must store ciphertext, NEVER raw_proof plaintext
                handoff_keys = redis_raw.keys("invite:handoff:*")
                data_keys = [k for k in handoff_keys if ":rl:" not in k]
                assert len(data_keys) == 1
                stored_payload = json.loads(redis_raw.get(data_keys[0]))
                assert stored_payload["kind"] == "handoff"
                assert raw_proof not in stored_payload["ciphertext"]

                # Unauth preview -> 401
                unauth_prev = await browser.post(
                    f"/v1/invitations/{inv_id_str}/preview",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert unauth_prev.status_code == 401

                # 3. Live HTTP OIDC /login + /callback with email_verified=True
                login_resp = await browser.get(
                    f"/v1/auth/login?return_to=/invitations/{inv_id_str}/accept&invitation_id={inv_id_str}&reauth=true"
                )
                assert login_resp.status_code == 200
                auth_url = login_resp.json()["authorization_url"]
                state = login_resp.json()["state"]
                q = parse_qs(urlparse(auth_url).query)
                code = idp_server.create_authorization_code(
                    client_id=settings.oidc_client_id,
                    redirect_uri=settings.oidc_redirect_uri,
                    state=state,
                    nonce=q["nonce"][0],
                    code_challenge=q["code_challenge"][0],
                    subject=invitee_sub,
                    name="Invitee Dev",
                    email=invitee_email,
                    email_verified=True,
                )
                cb_resp = await browser.get(
                    f"/v1/auth/callback?code={code}&state={state}",
                    headers={"Accept": "application/json"},
                )
                assert cb_resp.status_code == 200, cb_resp.text
                invitee_user_id = UUID(cb_resp.json()["user_id"])

                # 4. /refresh preserves id_token_issued_at/observed_at; simulating server restart retains Redis state
                sess_before = session_manager.verify_session_token(
                    cb_resp.cookies[settings.session_cookie_name]
                )
                ref_resp = await browser.post(
                    "/v1/auth/refresh",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert ref_resp.status_code == 200
                sess_after = session_manager.verify_session_token(
                    ref_resp.cookies[settings.session_cookie_name]
                )
                assert sess_after.identity_evidence is not None
                assert (
                    sess_after.identity_evidence.observed_at
                    == sess_before.identity_evidence.observed_at  # type: ignore[union-attr]
                )

                # Restart simulation: brand-new Redis store instances
                monkeypatch.setattr(
                    session_manager, "denylist", RedisRevocationStore(redis_url=REDIS_TEST_URL)
                )
                monkeypatch.setattr(
                    handoff_store_module,
                    "invitation_handoff_store",
                    RedisInvitationHandoffStore(redis_url=REDIS_TEST_URL),
                )

                # 5. Preview shows workspace metadata, 0 memberships in PG yet!
                prev_resp = await browser.post(
                    f"/v1/invitations/{inv_id_str}/preview",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert prev_resp.status_code == 200, prev_resp.text
                assert prev_resp.json()["acceptance_state"] == "ready"
                assert prev_resp.json()["can_accept"] is True

                with psycopg.connect(owner_url) as conn:
                    mem_count = conn.execute(
                        "SELECT count(*) FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                        (seed["ws_a"], invitee_user_id),
                    ).fetchone()[0]
                    assert mem_count == 0

                # 6. Inject post-commit Redis failure ONCE on convert_to_consumed_receipt -> DB still commits cleanly, retry works!
                real_store = handoff_store_module.invitation_handoff_store
                orig_convert = real_store.convert_to_consumed_receipt

                def _fail_once(**kwargs: Any) -> None:
                    raise RepositoryUnavailableError("simulated redis blip")

                monkeypatch.setattr(real_store, "convert_to_consumed_receipt", _fail_once)
                acc_resp = await browser.post(
                    f"/v1/invitations/{inv_id_str}/accept",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert acc_resp.status_code == 200, acc_resp.text
                assert acc_resp.json()["acceptance_state"] == "accepted"
                assert acc_resp.json()["redirect_url"] == f"/w/{seed['ws_a']}/agents"

                # Restore real convert and retry -> returns already_accepted, converts to consumed_receipt!
                monkeypatch.setattr(real_store, "convert_to_consumed_receipt", orig_convert)
                acc_retry = await browser.post(
                    f"/v1/invitations/{inv_id_str}/accept",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert acc_retry.status_code == 200
                assert acc_retry.json()["acceptance_state"] == "already_accepted"

                # Verify Redis key now holds consumed_receipt WITHOUT ciphertext
                after_payload = json.loads(redis_raw.get(data_keys[0]))
                assert after_payload["kind"] == "consumed_receipt"
                assert "ciphertext" not in after_payload

                # Verify /v1/auth/me and /v1/auth/token reflect active membership immediately!
                me_resp = await browser.get("/v1/auth/me")
                assert me_resp.status_code == 200
                ws_ids = [w["workspace_id"] for w in me_resp.json()["workspaces"]]
                assert str(seed["ws_a"]) in ws_ids

                tok_resp = await browser.post(
                    "/v1/auth/token",
                    json={"workspace_id": str(seed["ws_a"])},
                    headers={"Origin": "http://localhost:3000"},
                )
                assert tok_resp.status_code == 200

                # Verify PG state + audit event has zero raw proof/email leak
                with psycopg.connect(owner_url) as conn:
                    inv_db = conn.execute(
                        "SELECT status, accepted_by_user_id, proof_ciphertext, lease_token "
                        "FROM iam.workspace_invitations WHERE id = %s",
                        (UUID(inv_id_str),),
                    ).fetchone()
                    assert inv_db is not None
                    assert inv_db[0] == "accepted"
                    assert inv_db[1] == invitee_user_id
                    assert inv_db[2] is None
                    assert inv_db[3] is None

                    mems = conn.execute(
                        "SELECT status, role FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                        (seed["ws_a"], invitee_user_id),
                    ).fetchall()
                    assert mems == [("active", "developer")]

                    audits = conn.execute(
                        "SELECT details_redacted FROM governance.audit_events "
                        "WHERE action = 'workspace.invitation.accept' AND resource_id = %s",
                        (inv_id_str,),
                    ).fetchall()
                    assert len(audits) == 1
                    audit_str = json.dumps(audits[0][0])
                    assert raw_proof not in audit_str
                    assert invitee_email not in audit_str

                    # 7. Revoking membership permanently blocks replay via invitation!
                    conn.execute(
                        "UPDATE iam.memberships SET status = 'revoked' WHERE workspace_id = %s AND user_id = %s",
                        (seed["ws_a"], invitee_user_id),
                    )
                rev_prev = await browser.post(
                    f"/v1/invitations/{inv_id_str}/preview",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert rev_prev.status_code == 409
                assert rev_prev.json()["detail"]["code"] == "membership_revoked"
                await async_engine.dispose()

        try:
            asyncio.run(_scenario())
        finally:
            redis_raw.flushdb()
            redis_raw.close()


def test_failure_injection_db_rollback_and_redis_outage_fail_closed(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject DB error during accept -> clean rollback & intact Redis handoff; dead Redis -> 503."""
    _check_redis_available()
    proof_key = Fernet.generate_key().decode("ascii")
    handoff_key = Fernet.generate_key().decode("ascii")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    issuer_url = "http://localhost:8080/realms/enterprise"
    seed = _seed_workspace_and_users(owner_url)
    async_engine, async_factory, _r, _t, _h = _configure_real_stack(
        monkeypatch,
        app_url=app_url,
        proof_key=proof_key,
        handoff_key=handoff_key,
        issuer_url=issuer_url,
    )

    invitee_id = uuid4()
    invitee_sub = f"sub-rb-{invitee_id.hex[:8]}"
    invitee_email = f"rb-{invitee_id.hex[:8]}@example.com"
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
            "VALUES (%s, %s, %s, 'RB User', %s)",
            (invitee_id, issuer_url, invitee_sub, invitee_email),
        )

    async def _run() -> None:
        # Create invite directly via InvitationRepository
        repo = InvitationRepository(PostgresOutboxEventBus(async_factory))
        protector = InvitationProofProtector(proof_key, key_id="proof-v1")
        from control_api.modules.identity.models.session import VerifiedIdentityEvidence
        from platform_common.invitations import InvitationProofBinding

        async with async_factory() as db_sess:
            created = await repo.create_invitation(
                db_sess,
                tenant_id=seed["tenant_a"],
                workspace_id=seed["ws_a"],
                actor_user_id=seed["admin_a"],
                recipient_email_normalized=invitee_email,
                recipient_issuer=issuer_url,
                role="developer",
                protector=protector,
                policy=InvitationPolicyConfig(
                    ttl_hours=72,
                    resend_cooldown_seconds=1,
                    recipient_hourly_limit=20,
                    workspace_hourly_limit=100,
                ),
                request_id="req-rb-1",
                trace_id="tr-rb-1",
            )
            await db_sess.commit()
        inv_id = created["invitation_id"]
        with psycopg.connect(owner_url) as conn:
            cipher = conn.execute(
                "SELECT proof_ciphertext FROM iam.workspace_invitations WHERE id = %s",
                (inv_id,),
            ).fetchone()[0]
        raw_proof = protector.decrypt_and_verify_proof(
            cipher,
            "proof-v1",
            InvitationProofBinding(
                invitation_id=str(inv_id),
                tenant_id=str(seed["tenant_a"]),
                workspace_id=str(seed["ws_a"]),
                generation=1,
                recipient_issuer=issuer_url,
                recipient_email_normalized=invitee_email,
                role="developer",
            ),
        )

        transport = ASGITransport(app=app)
        now = int(time.time())
        ev = VerifiedIdentityEvidence(
            issuer=issuer_url,
            subject=invitee_sub,
            user_id=str(invitee_id),
            email_normalized=invitee_email,
            email_verified=True,
            id_token_issued_at=now,
            observed_at=now,
        )
        _sess, sess_jwt = session_manager.create_session(
            str(invitee_id), issuer_url, invitee_sub, identity_evidence=ev
        )

        async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            client.cookies.set(settings.session_cookie_name, sess_jwt)
            h = await client.post(
                f"/v1/invitations/{inv_id}/handoff",
                json={"token": raw_proof},
                headers={"Origin": "http://localhost:3000"},
            )
            assert h.status_code == 201

            # Inject SQLAlchemy DB error AFTER INSERT iam.memberships (on audit event insert!)
            from sqlalchemy.exc import OperationalError

            accept_router = importlib.import_module(
                "control_api.modules.workspaces.api.invitation_accept_router"
            )
            orig_audit = accept_router.acceptance_service._repository._insert_audit_event

            async def _boom_audit(*args: Any, **kwargs: Any) -> None:
                raise OperationalError(
                    "INSERT INTO governance.audit_events", {}, Exception("Injected mid-tx failure")
                )

            monkeypatch.setattr(
                accept_router.acceptance_service._repository, "_insert_audit_event", _boom_audit
            )
            r_503_db = await client.post(
                f"/v1/invitations/{inv_id}/accept",
                headers={"Origin": "http://localhost:3000"},
            )
            assert r_503_db.status_code == 503

            # Verify rollback: 0 membership rows AND invitation still pending AND retry succeeds!
            with psycopg.connect(owner_url) as conn:
                cnt = conn.execute(
                    "SELECT count(*) FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                    (seed["ws_a"], invitee_id),
                ).fetchone()[0]
                assert cnt == 0

            monkeypatch.setattr(
                accept_router.acceptance_service._repository, "_insert_audit_event", orig_audit
            )
            acc_ok = await client.post(
                f"/v1/invitations/{inv_id}/accept",
                headers={"Origin": "http://localhost:3000"},
            )
            assert acc_ok.status_code == 200
            assert acc_ok.json()["acceptance_state"] == "accepted"

            # Dead Redis port -> 503 fail closed on /handoff and /preview
            dead_store = RedisInvitationHandoffStore(redis_url="redis://127.0.0.1:59999/15")
            monkeypatch.setattr(handoff_store_module, "invitation_handoff_store", dead_store)
            r_503_h = await client.post(
                f"/v1/invitations/{inv_id}/handoff",
                json={"token": raw_proof},
                headers={"Origin": "http://localhost:3000"},
            )
            assert r_503_h.status_code == 503
            r_503_p = await client.post(
                f"/v1/invitations/{inv_id}/preview",
                headers={"Origin": "http://localhost:3000"},
            )
            assert r_503_p.status_code == 503
            await async_engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Real PostgreSQL Concurrency & Worker Race Tests (R01 Lock Order)
# ---------------------------------------------------------------------------


def test_concurrency_accept_vs_accept_revoke_resend_and_worker(
    migrated_database: dict[str, Any],
) -> None:
    """Barrier-synchronized concurrency tests across real PostgreSQL transactions."""
    proof_key = Fernet.generate_key().decode("ascii")
    protector = InvitationProofProtector(proof_key, key_id="proof-v1")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    issuer = "http://localhost:8080/realms/enterprise"
    seed = _seed_workspace_and_users(owner_url)
    policy = InvitationPolicyConfig(
        ttl_hours=72,
        resend_cooldown_seconds=0,
        recipient_hourly_limit=50,
        workspace_hourly_limit=100,
    )

    def _create_user_and_invite(tag: str) -> tuple[UUID, str, UUID, str]:
        uid = uuid4()
        email = f"conc-{tag}-{uid.hex[:6]}@example.com"
        sub = f"sub-{uid.hex[:8]}"
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
                "VALUES (%s, %s, %s, 'Conc User', %s)",
                (uid, issuer, sub, email),
            )

        async def _create() -> UUID:
            eng = create_async_engine(_to_async_pg_url(app_url))
            sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            repo = InvitationRepository(PostgresOutboxEventBus(sf))
            try:
                async with sf() as s:
                    res = await repo.create_invitation(
                        s,
                        tenant_id=seed["tenant_a"],
                        workspace_id=seed["ws_a"],
                        actor_user_id=seed["admin_a"],
                        recipient_email_normalized=email,
                        recipient_issuer=issuer,
                        role="developer",
                        protector=protector,
                        policy=policy,
                        request_id=f"req-{tag}",
                        trace_id=f"tr-{tag}",
                    )
                    await s.commit()
                    return UUID(str(res["invitation_id"]))
            finally:
                await eng.dispose()

        inv_id = asyncio.run(_create())
        with psycopg.connect(owner_url) as conn:
            th = conn.execute(
                "SELECT token_hash FROM iam.workspace_invitations WHERE id = %s",
                (inv_id,),
            ).fetchone()[0]
        return uid, sub, email, inv_id, str(th).strip()

    def _run_accept(inv_id: UUID, uid: UUID, sub: str, email: str, token_hash: str) -> str:
        async def _go() -> str:
            eng = create_async_engine(_to_async_pg_url(app_url))
            sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            repo = InvitationRepository()
            try:
                async with sf() as s:
                    res = await repo.accept_invitation(
                        s,
                        invitation_id=inv_id,
                        expected_token_hash=token_hash,
                        recipient_user_id=uid,
                        recipient_issuer=issuer,
                        recipient_subject=sub,
                        recipient_email_normalized=email,
                        request_id=f"req-acc-{uuid4().hex[:6]}",
                        trace_id=f"tr-acc-{uuid4().hex[:6]}",
                    )
                    return str(res["acceptance_state"])
            finally:
                await eng.dispose()

        return asyncio.run(_go())

    # Case A: accept vs accept -> 1 accepted, 1 already_accepted, 1 membership, 1 audit
    uid_a, sub_a, email_a, inv_a, th_a = _create_user_and_invite("aa")
    b1 = Barrier(2)

    def _w_aa() -> str:
        b1.wait(timeout=10)
        return _run_accept(inv_a, uid_a, sub_a, email_a, th_a)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results_aa = sorted(f.result(timeout=15) for f in [pool.submit(_w_aa), pool.submit(_w_aa)])
    assert results_aa == ["accepted", "already_accepted"]
    with psycopg.connect(owner_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                (seed["ws_a"], uid_a),
            ).fetchone()[0]
            == 1
        )

    # Case B: accept vs revoke -> mutual exclusion, zero deadlocks
    uid_b, sub_b, email_b, inv_b, th_b = _create_user_and_invite("ar")
    b2 = Barrier(2)

    def _w_acc_b() -> str:
        b2.wait(timeout=10)
        try:
            return _run_accept(inv_b, uid_b, sub_b, email_b, th_b)
        except InvitationError as exc:
            return type(exc).__name__

    def _w_rev_b() -> str:
        b2.wait(timeout=10)

        async def _rev() -> str:
            eng = create_async_engine(_to_async_pg_url(app_url))
            sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            repo = InvitationRepository()
            try:
                async with sf() as s:
                    await repo.revoke_invitation(
                        s,
                        tenant_id=seed["tenant_a"],
                        workspace_id=seed["ws_a"],
                        invitation_id=inv_b,
                        actor_user_id=seed["admin_a"],
                        request_id="req-rev-b",
                        trace_id="tr-rev-b",
                    )
                    await s.commit()
                    return "revoked"
            finally:
                await eng.dispose()

        try:
            return asyncio.run(_rev())
        except InvitationError as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        fb1 = pool.submit(_w_acc_b)
        fb2 = pool.submit(_w_rev_b)
        r_acc_b, r_rev_b = fb1.result(timeout=15), fb2.result(timeout=15)
    assert (r_acc_b, r_rev_b) in {
        ("accepted", "InvitationConflictError"),
        ("InvitationGoneError", "revoked"),
    }

    # Case C: accept vs resend -> old proof either wins BEFORE resend or gets 404 InvitationNotFoundError AFTER resend
    uid_c, sub_c, email_c, inv_c, old_th_c = _create_user_and_invite("as")
    b3 = Barrier(2)

    def _w_acc_c() -> str:
        b3.wait(timeout=10)
        try:
            return _run_accept(inv_c, uid_c, sub_c, email_c, old_th_c)
        except InvitationError as exc:
            return type(exc).__name__

    def _w_res_c() -> str:
        b3.wait(timeout=10)

        async def _res() -> str:
            eng = create_async_engine(_to_async_pg_url(app_url))
            sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            repo = InvitationRepository(PostgresOutboxEventBus(sf))
            try:
                async with sf() as s:
                    await repo.resend_invitation(
                        s,
                        tenant_id=seed["tenant_a"],
                        workspace_id=seed["ws_a"],
                        invitation_id=inv_c,
                        actor_user_id=seed["admin_a"],
                        protector=protector,
                        policy=policy,
                        request_id="req-res-c",
                        trace_id="tr-res-c",
                    )
                    await s.commit()
                    return "resent"
            finally:
                await eng.dispose()

        try:
            return asyncio.run(_res())
        except InvitationError as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        fc1 = pool.submit(_w_acc_c)
        fc2 = pool.submit(_w_res_c)
        r_acc_c, r_res_c = fc1.result(timeout=15), fc2.result(timeout=15)
    assert (r_acc_c, r_res_c) in {
        ("accepted", "InvitationConflictError"),
        ("InvitationNotFoundError", "resent"),
    }

    # Case D: accept while outbox event is claimed/in-flight -> worker completion CAS cannot undo accepted state!
    # Use ws_b so no residual outbox events from Cases A-C exist in ws_b.
    uid_d = uuid4()
    sub_d = f"sub-{uid_d.hex[:8]}"
    email_d = f"conc-aw-{uid_d.hex[:6]}@example.com"
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
            "VALUES (%s, %s, %s, 'Conc Worker User', %s)",
            (uid_d, issuer, sub_d, email_d),
        )

    async def _create_in_ws_b() -> UUID:
        eng = create_async_engine(_to_async_pg_url(app_url))
        sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        repo = InvitationRepository(PostgresOutboxEventBus(sf))
        try:
            async with sf() as s:
                res = await repo.create_invitation(
                    s,
                    tenant_id=seed["tenant_b"],
                    workspace_id=seed["ws_b"],
                    actor_user_id=seed["owner_b"],
                    recipient_email_normalized=email_d,
                    recipient_issuer=issuer,
                    role="developer",
                    protector=protector,
                    policy=policy,
                    request_id="req-aw-b",
                    trace_id="tr-aw-b",
                )
                await s.commit()
                return UUID(str(res["invitation_id"]))
        finally:
            await eng.dispose()

    inv_d = asyncio.run(_create_in_ws_b())
    with psycopg.connect(owner_url) as conn:
        th_d = str(
            conn.execute(
                "SELECT token_hash FROM iam.workspace_invitations WHERE id = %s",
                (inv_d,),
            ).fetchone()[0]
        ).strip()
        conn.execute(
            "UPDATE iam.workspace_invitations "
            "SET delivery_state = 'sending', lease_token = %s, leased_until = now() + interval '5 minutes' "
            "WHERE id = %s",
            (uuid4(), inv_d),
        )
    res_d = _run_accept(inv_d, uid_d, sub_d, email_d, th_d)
    assert res_d == "accepted"

    # Now worker CAS completion runs against PG in ws_b: it must NOT process, re-queue, un-accept, or restore proof_ciphertext!
    sync_engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
    smtp = _RealSmtpSinkServer(mode="accept")
    try:
        consumer = InvitationOutboxConsumer(
            database_url=_to_sync_pg_url(app_url),
            tenant_id=str(seed["tenant_b"]),
            workspace_id=str(seed["ws_b"]),
            protector=protector,
            public_app_origin="http://localhost:3000",
            smtp_transport=StdlibSmtpTransport(
                SmtpConfig(
                    host="127.0.0.1",
                    port=smtp.port,
                    sender="no-reply@example.com",
                    tls_mode="none",
                    allow_plaintext_local_smtp=True,
                )
            ),
            engine=sync_engine,
        )
        assert consumer.run_once() == 0
    finally:
        smtp.close()
        sync_engine.dispose()

    with psycopg.connect(owner_url) as conn:
        final_inv = conn.execute(
            "SELECT status, delivery_state, proof_ciphertext, lease_token FROM iam.workspace_invitations WHERE id = %s",
            (inv_d,),
        ).fetchone()
        assert final_inv is not None
        assert final_inv[0] == "accepted"
        assert final_inv[1] == "cancelled"
        assert final_inv[2] is None
        assert final_inv[3] is None


# ---------------------------------------------------------------------------
# 5. R02 Regression: Wall-Clock Expiry & Deadlines After Lock Wait (Real PG)
# ---------------------------------------------------------------------------


def test_r02_expiry_after_lock_wait_boundary_and_rollback(
    migrated_database: dict[str, Any],
) -> None:
    """Block accept_invitation on workspace lock past expires_at -> denied, 0 memberships, clean rollback."""
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seed = _seed_workspace_and_users(owner_url)
    issuer = "https://idp.r02.test"

    def _seed_inv(*, expires_offset_seconds: int) -> tuple[UUID, str, str, UUID, str]:
        uid = uuid4()
        sub = f"sub-r02-{uid.hex[:8]}"
        email = f"r02-{uid.hex[:8]}@example.com"
        inv_id = uuid4()
        th = uuid4().hex + uuid4().hex
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
                "VALUES (%s, %s, %s, 'R02 User', %s)",
                (uid, issuer, sub, email),
            )
            conn.execute(
                "INSERT INTO iam.workspace_invitations "
                "(id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, role, "
                " token_hash, proof_ciphertext, proof_key_id, status, generation, delivery_state, "
                " created_by_user_id, delivery_requested_by_user_id, created_at, updated_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, 'developer', %s, 'cipher', 'v1', 'pending', 1, 'queued', "
                "        %s, %s, clock_timestamp() - interval '10 seconds', clock_timestamp() - interval '10 seconds', "
                "        clock_timestamp() + (%s * interval '1 second'))",
                (inv_id, seed["tenant_a"], seed["ws_a"], email, issuer, th, seed["admin_a"], seed["admin_a"], expires_offset_seconds),
            )
        return uid, sub, email, inv_id, th

    # Case 1: Worker starts BEFORE expiry (expires in 2s), blocks on workspace FOR UPDATE lock,
    # blocker holds lock until AFTER clock_timestamp() >= expires_at -> MUST fail closed with InvitationGoneError!
    uid_1, sub_1, email_1, inv_1, th_1 = _seed_inv(expires_offset_seconds=2)
    blocker = psycopg.connect(owner_url)
    blocker.execute("SELECT id FROM iam.workspaces WHERE id = %s FOR UPDATE", (seed["ws_a"],))

    def _run_accept_app(
        *,
        inv_id: UUID,
        uid: UUID,
        sub: str,
        email: str,
        th: str,
        app_name: str,
        identity_deadline: int | None = None,
    ) -> dict[str, Any]:
        async def _go() -> dict[str, Any]:
            eng = create_async_engine(
                _to_async_pg_url(app_url),
                connect_args={"server_settings": {"application_name": app_name}},
            )
            sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            try:
                async with sf() as s:
                    return await InvitationRepository().accept_invitation(
                        s,
                        invitation_id=inv_id,
                        expected_token_hash=th,
                        recipient_user_id=uid,
                        recipient_issuer=issuer,
                        recipient_subject=sub,
                        recipient_email_normalized=email,
                        request_id="req-r02-lock",
                        trace_id="tr-r02-lock",
                        identity_freshness_deadline=identity_deadline,
                    )
            finally:
                await eng.dispose()

        return asyncio.run(_go())

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            _run_accept_app,
            inv_id=inv_1,
            uid=uid_1,
            sub=sub_1,
            email=email_1,
            th=th_1,
            app_name="r02-lock-wait-expiry",
        )
        try:
            with psycopg.connect(owner_url, autocommit=True) as obs:
                wait_deadline = time.monotonic() + 8.0
                waiting = False
                while time.monotonic() < wait_deadline:
                    waiting = bool(
                        obs.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = 'r02-lock-wait-expiry' AND wait_event_type = 'Lock')"
                        ).fetchone()[0]
                    )
                    if waiting:
                        break
                    time.sleep(0.05)
                assert waiting, "Worker never entered PostgreSQL Lock wait on workspace"
                while not obs.execute(
                    "SELECT expires_at <= clock_timestamp() FROM iam.workspace_invitations WHERE id = %s",
                    (inv_1,),
                ).fetchone()[0]:
                    time.sleep(0.05)
            blocker.commit()
            with pytest.raises(InvitationGoneError) as exc_exp:
                fut.result(timeout=10)
            assert exc_exp.value.code == "invitation_expired"
        finally:
            blocker.close()

    with psycopg.connect(owner_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                (seed["ws_a"], uid_1),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT status FROM iam.workspace_invitations WHERE id = %s", (inv_1,)
            ).fetchone()[0]
            == "pending"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM governance.audit_events "
                "WHERE action = 'workspace.invitation.accept' AND resource_id = %s",
                (str(inv_1),),
            ).fetchone()[0]
            == 0
        )

    # Case 2: Boundary now == expires_at (expires_offset_seconds = 0 -> expires_at <= clock_timestamp()) -> denied!
    uid_2, sub_2, email_2, inv_2, th_2 = _seed_inv(expires_offset_seconds=0)
    with pytest.raises(InvitationGoneError) as exc_b:
        _run_accept_app(
            inv_id=inv_2,
            uid=uid_2,
            sub=sub_2,
            email=email_2,
            th=th_2,
            app_name="r02-boundary",
        )
    assert exc_b.value.code == "invitation_expired"
    with psycopg.connect(owner_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                (seed["ws_a"], uid_2),
            ).fetchone()[0]
            == 0
        )

    # Case 3: Freshness deadline expires while blocked on workspace lock -> rolls back cleanly!
    uid_3, sub_3, email_3, inv_3, th_3 = _seed_inv(expires_offset_seconds=3600)
    blocker3 = psycopg.connect(owner_url)
    blocker3.execute("SELECT id FROM iam.workspaces WHERE id = %s FOR UPDATE", (seed["ws_a"],))
    short_deadline = int(time.time()) + 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut3 = pool.submit(
            _run_accept_app,
            inv_id=inv_3,
            uid=uid_3,
            sub=sub_3,
            email=email_3,
            th=th_3,
            app_name="r02-freshness-wait",
            identity_deadline=short_deadline,
        )
        try:
            time.sleep(1.4)
            blocker3.commit()
            with pytest.raises(InvitationForbiddenError) as exc_fr:
                fut3.result(timeout=10)
            assert exc_fr.value.code == "reauthentication_required"
        finally:
            blocker3.close()
    with psycopg.connect(owner_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM iam.memberships WHERE workspace_id = %s AND user_id = %s",
                (seed["ws_a"], uid_3),
            ).fetchone()[0]
            == 0
        )

    # Case 4: Normal acceptance strictly before expiry succeeds cleanly
    res_ok = _run_accept_app(
        inv_id=inv_3,
        uid=uid_3,
        sub=sub_3,
        email=email_3,
        th=th_3,
        app_name="r02-normal",
        identity_deadline=int(time.time()) + 900,
    )
    assert res_ok["acceptance_state"] == "accepted"


# ---------------------------------------------------------------------------
# 6. R04 Regression: Real Redis Atomic Handoff Idempotency & CAS Cleanup
# ---------------------------------------------------------------------------


def test_r04_real_redis_atomic_handoff_idempotency_and_cas_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify real Redis Lua CAS preserves original deadline across replays & concurrency, and prevents stale cleanup overwrite."""
    _check_redis_available()
    from platform_common.invitations import hash_invitation_proof

    store1 = RedisInvitationHandoffStore(redis_url=REDIS_TEST_URL)
    store2 = RedisInvitationHandoffStore(redis_url=REDIS_TEST_URL)
    store1._client.flushdb()
    monkeypatch.setattr(settings, "invitations_enabled", True)
    handoff_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings.invitation_handoff_encryption_key, "get_secret_value", lambda: handoff_key)
    monkeypatch.setattr(settings.invitation_proof_encryption_key, "get_secret_value", lambda: "distinct-proof-key")

    try:
        svc1 = InvitationAcceptanceService(store=store1)
        svc2 = InvitationAcceptanceService(store=store2)
        iid = uuid4()

        # 1. Sequential duplicate replay preserves original expires_at & remaining TTL monotonically
        dto_a, cookie_a = svc1.create_handoff(
            invitation_id=iid,
            raw_token="real-redis-proof-1",
            existing_browser_cookie=None,
            client_ip="127.0.0.51",
        )
        from control_api.modules.workspaces.services.handoff_store import hash_browser_binding_token

        rkey = f"invite:handoff:{iid}:{hash_browser_binding_token(cookie_a)}"
        original_entry = store1.get_handoff_entry(
            invitation_id=str(iid),
            browser_binding_digest=hash_browser_binding_token(cookie_a),
        )
        assert original_entry is not None
        time.sleep(2.1)
        pttl_before = store1._client.pttl(rkey)
        dto_b, cookie_b = svc2.create_handoff(
            invitation_id=iid,
            raw_token="real-redis-proof-1",
            existing_browser_cookie=cookie_a,
            client_ip="127.0.0.51",
        )
        pttl_after = store1._client.pttl(rkey)
        assert cookie_b == cookie_a
        assert dto_b.expires_at == dto_a.expires_at
        # Server PTTL stays bounded and never resets back upward toward 600s
        assert 0 < pttl_after <= pttl_before + 100

        # 2. Concurrent creation across two independent Redis store instances shares one deadline
        iid_conc = uuid4()
        from control_api.modules.workspaces.services.handoff_store import (
            generate_browser_binding_token,
        )

        fixed_cookie = generate_browser_binding_token()
        bar = Barrier(2)

        def _worker(svc: InvitationAcceptanceService, ip: str) -> str:
            bar.wait(timeout=5)
            d, _ = svc.create_handoff(
                invitation_id=iid_conc,
                raw_token="conc-handoff-proof",
                existing_browser_cookie=fixed_cookie,
                client_ip=ip,
            )
            return d.expires_at

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_worker, svc1, "127.0.0.61")
            f2 = pool.submit(_worker, svc2, "127.0.0.62")
            e1, e2 = f1.result(timeout=10), f2.result(timeout=10)
        assert e1 == e2

        # 3. Delayed convert_to_consumed_receipt with OLD proof hash MUST NOT clobber newer active proof (resend)
        dto_new, _ = svc1.create_handoff(
            invitation_id=iid,
            raw_token="real-redis-proof-gen2",
            existing_browser_cookie=cookie_a,
            client_ip="127.0.0.51",
        )
        old_exp = int(time.time()) + 500
        store1.convert_to_consumed_receipt(
            invitation_id=str(iid),
            browser_binding_digest=hash_browser_binding_token(cookie_a),
            user_id=str(uuid4()),
            token_hash=hash_invitation_proof("real-redis-proof-1"),
            expected_handoff_ciphertext=original_entry["ciphertext"],
            expires_at=old_exp,
        )
        stored_after_stale = store1.get_handoff_entry(
            invitation_id=str(iid),
            browser_binding_digest=hash_browser_binding_token(cookie_a),
        )
        assert stored_after_stale is not None
        assert stored_after_stale["kind"] == "handoff"
        assert stored_after_stale["token_hash"] == hash_invitation_proof("real-redis-proof-gen2")

        # 4. Valid consume converts atomically; replay of same proof keeps consumed_receipt intact without reverting
        from datetime import datetime

        new_exp = int(datetime.fromisoformat(dto_new.expires_at).timestamp())
        store1.convert_to_consumed_receipt(
            invitation_id=str(iid),
            browser_binding_digest=hash_browser_binding_token(cookie_a),
            user_id=str(uuid4()),
            token_hash=hash_invitation_proof("real-redis-proof-gen2"),
            expected_handoff_ciphertext=stored_after_stale["ciphertext"],
            expires_at=new_exp,
        )
        dto_replay_consumed, _ = svc1.create_handoff(
            invitation_id=iid,
            raw_token="real-redis-proof-gen2",
            existing_browser_cookie=cookie_a,
            client_ip="127.0.0.51",
        )
        assert dto_replay_consumed.expires_at == dto_new.expires_at
        stored_consumed = store1.get_handoff_entry(
            invitation_id=str(iid),
            browser_binding_digest=hash_browser_binding_token(cookie_a),
        )
        assert stored_consumed is not None and stored_consumed["kind"] == "consumed_receipt"

        # 5. Redis unavailable fails closed cleanly with RepositoryUnavailableError
        dead_store = RedisInvitationHandoffStore(redis_url="redis://127.0.0.1:19999/0")
        dead_svc = InvitationAcceptanceService(store=dead_store)
        with pytest.raises(RepositoryUnavailableError):
            dead_svc.create_handoff(
                invitation_id=uuid4(),
                raw_token="down-proof",
                existing_browser_cookie=None,
                client_ip="127.0.0.90",
            )
    finally:
        store1._client.flushdb()
        store1._client.close()
        store2._client.close()


# ---------------------------------------------------------------------------
# 7. R01 Privacy-Safe API Check: Wrong Authenticated User Gets Generic 404
# ---------------------------------------------------------------------------


def test_r01_privacy_safe_wrong_user_api_returns_generic_404(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong authenticated user holding a valid proof receives generic 404 without tenant/workspace/email oracle."""
    _check_redis_available()
    proof_key = Fernet.generate_key().decode("ascii")
    handoff_key = Fernet.generate_key().decode("ascii")
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seed = _seed_workspace_and_users(owner_url)

    with run_oidc_http_server() as (idp_server, issuer_url):
        async_engine, _async_factory, _r_store, _t_store, _h_store = _configure_real_stack(
            monkeypatch,
            app_url=app_url,
            proof_key=proof_key,
            handoff_key=handoff_key,
            issuer_url=issuer_url,
        )
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE iam.users SET issuer = %s WHERE id = %s",
                (issuer_url, seed["admin_a"]),
            )
            ws_name = str(
                conn.execute(
                    "SELECT name FROM iam.workspaces WHERE id = %s", (seed["ws_a"],)
                ).fetchone()[0]
            )

        admin_hdrs = {
            **_issue_session_cookie_header(
                seed["admin_a"], issuer_url, f"admin-a-{seed['tenant_a'].hex[:8]}"
            ),
            "Origin": "http://localhost:3000",
        }
        target_email = f"r01-target-{seed['tenant_a'].hex[:6]}@example.com"
        wrong_email = f"r01-wrong-{seed['tenant_a'].hex[:6]}@example.com"

        async def _go() -> None:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as admin_cli, AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as attacker_cli:
                c_resp = await admin_cli.post(
                    f"/v1/workspaces/{seed['ws_a']}/invitations",
                    json={"email": target_email, "role": "developer"},
                    headers=admin_hdrs,
                )
                assert c_resp.status_code == 201
                inv_id = UUID(c_resp.json()["invitation_id"])

                protector = InvitationProofProtector(proof_key, key_id="proof-v1")
                with psycopg.connect(owner_url) as conn:
                    cipher = conn.execute(
                        "SELECT proof_ciphertext FROM iam.workspace_invitations WHERE id = %s",
                        (inv_id,),
                    ).fetchone()[0]
                from platform_common.invitations import InvitationProofBinding

                raw_proof = protector.decrypt_and_verify_proof(
                    cipher,
                    "proof-v1",
                    InvitationProofBinding(
                        invitation_id=str(inv_id),
                        tenant_id=str(seed["tenant_a"]),
                        workspace_id=str(seed["ws_a"]),
                        generation=1,
                        recipient_email_normalized=target_email,
                        recipient_issuer=issuer_url,
                        role="developer",
                    ),
                )

                h_resp = await attacker_cli.post(
                    f"/v1/invitations/{inv_id}/handoff",
                    json={"token": raw_proof},
                    headers={"Origin": "http://localhost:3000"},
                )
                assert h_resp.status_code == 201

                login_r = await attacker_cli.get(
                    f"/v1/auth/login?return_to=/invitations/{inv_id}/accept&invitation_id={inv_id}"
                )
                q = parse_qs(urlparse(login_r.json()["authorization_url"]).query)
                code = idp_server.create_authorization_code(
                    client_id=settings.oidc_client_id,
                    redirect_uri=settings.oidc_redirect_uri,
                    state=login_r.json()["state"],
                    nonce=q["nonce"][0],
                    code_challenge=q["code_challenge"][0],
                    subject=f"sub-wrong-{inv_id.hex[:6]}",
                    name="Wrong Invitee",
                    email=wrong_email,
                    email_verified=True,
                )
                cb = await attacker_cli.get(
                    f"/v1/auth/callback?code={code}&state={login_r.json()['state']}",
                    headers={"Accept": "application/json"},
                )
                assert cb.status_code == 200

                prev_r = await attacker_cli.post(
                    f"/v1/invitations/{inv_id}/preview",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert prev_r.status_code == 404
                assert prev_r.json()["detail"]["code"] == "invitation_not_found"
                assert ws_name not in prev_r.text
                assert target_email not in prev_r.text

                acc_r = await attacker_cli.post(
                    f"/v1/invitations/{inv_id}/accept",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert acc_r.status_code == 404
                assert acc_r.json()["detail"]["code"] == "invitation_not_found"
                await async_engine.dispose()

        try:
            asyncio.run(_go())
        finally:
            redis_raw = redis_lib.Redis.from_url(REDIS_TEST_URL, socket_timeout=2.0)
            redis_raw.flushdb()
            redis_raw.close()
