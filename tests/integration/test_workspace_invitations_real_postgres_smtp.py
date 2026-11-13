"""Real PostgreSQL 16 + Real Loopback TCP SMTP Sink integration tests for Step 03-B."""

from __future__ import annotations

import asyncio
import email as email_stdlib
import hashlib
import importlib
import re
import socket
import threading
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from control_api.core import database as control_db
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.repositories.memory_session_store import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from control_api.modules.workspaces.repositories.invitation_repository import InvitationRepository
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from jobs_worker.invitation_consumer import InvitationOutboxConsumer
from jobs_worker.main import main as worker_main
from jobs_worker.smtp_adapter import SmtpConfig, StdlibSmtpTransport
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import InvitationProofProtector
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

workspaces_router = importlib.import_module("control_api.modules.workspaces.api.router")


class _RealSmtpSinkServer:
    """Ephemeral 127.0.0.1 TCP SMTP sink capturing transmitted MIME messages in memory."""

    def __init__(self, mode: str = "accept") -> None:
        self.mode = mode
        self.messages: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            self._sock.settimeout(0.2)
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()

    def _handle_conn(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5.0)
            conn.sendall(b"220 127.0.0.1 ESMTP sink\r\n")
            buf = b""
            reading_data = False
            mail_from = ""
            rcpt_to = ""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if reading_data:
                    if b"\r\n.\r\n" in buf:
                        body_bytes, _rest = buf.split(b"\r\n.\r\n", 1)
                        raw_msg = body_bytes.decode("utf-8", errors="replace")
                        with self._lock:
                            self.messages.append(
                                {"mail_from": mail_from, "rcpt_to": rcpt_to, "raw": raw_msg}
                            )
                        if self.mode == "drop_after_data":
                            conn.close()
                            return
                        if self.mode == "reject_data_550":
                            conn.sendall(b"550 5.7.1 Rejected\r\n")
                        else:
                            conn.sendall(b"250 2.0.0 Queued\r\n")
                        reading_data = False
                        buf = b""
                    continue

                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    cmd = line.decode("ascii", errors="replace")
                    upper = cmd.upper()
                    if upper.startswith(("EHLO", "HELO")):
                        conn.sendall(b"250-127.0.0.1\r\n250 PIPELINING\r\n")
                    elif upper.startswith("MAIL FROM:"):
                        mail_from = cmd
                        conn.sendall(b"250 2.1.0 OK\r\n")
                    elif upper.startswith("RCPT TO:"):
                        rcpt_to = cmd
                        if self.mode == "transient_421":
                            conn.sendall(b"421 4.3.2 Temporary rate limit\r\n")
                        elif self.mode == "reject_rcpt_550":
                            conn.sendall(b"550 5.1.1 Mailbox does not exist\r\n")
                        else:
                            conn.sendall(b"250 2.1.5 OK\r\n")
                    elif upper == "DATA":
                        conn.sendall(b"354 Start mail input; end with <CRLF>.<CRLF>\r\n")
                        reading_data = True
                        break
                    elif upper == "QUIT":
                        conn.sendall(b"221 Bye\r\n")
                        return
                    else:
                        conn.sendall(b"502 Unrecognized command\r\n")

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=1.5)


def _to_async_pg_url(url: str) -> str:
    info = psycopg.conninfo.conninfo_to_dict(url)
    return (
        f"postgresql+asyncpg://{info['user']}:{info.get('password', '')}"
        f"@{info['host']}:{info['port']}/{info['dbname']}"
    )


def _to_sync_pg_url(url: str) -> str:
    info = psycopg.conninfo.conninfo_to_dict(url)
    return (
        f"postgresql+psycopg://{info['user']}:{info.get('password', '')}"
        f"@{info['host']}:{info['port']}/{info['dbname']}"
    )


def _seed_workspace_and_users(owner_url: str) -> dict[str, Any]:
    issuer = settings.jwt_issuer
    tenant_a, ws_a = uuid4(), uuid4()
    tenant_b, ws_b = uuid4(), uuid4()
    owner_a, admin_a, dev_a, owner_b, existing_revoked, bootstrap_only = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    suffix = str(tenant_a)[:8]
    with psycopg.connect(owner_url) as conn:
        for uid, sub, email in (
            (owner_a, f"owner-a-{suffix}", f"owner-a-{suffix}@example.com"),
            (admin_a, f"admin-a-{suffix}", f"admin-a-{suffix}@example.com"),
            (dev_a, f"dev-a-{suffix}", f"dev-a-{suffix}@example.com"),
            (owner_b, f"owner-b-{suffix}", f"owner-b-{suffix}@example.com"),
            (existing_revoked, f"revoked-{suffix}", f"revoked-{suffix}@example.com"),
            (bootstrap_only, f"boot-{suffix}", f"boot-{suffix}@example.com"),
        ):
            conn.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
                "VALUES (%s, %s, %s, %s, %s)",
                (uid, issuer, sub, sub, email),
            )
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, %s, 'active')",
            (tenant_a, f"tenant-a-{suffix}", "Tenant A"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_a, ws_a, f"ws-a-{suffix}", "Workspace A"),
        )
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name, status) VALUES (%s, %s, %s, 'active')",
            (tenant_b, f"tenant-b-{suffix}", "Tenant B"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name, status) VALUES (%s, %s, %s, %s, 'active')",
            (tenant_b, ws_b, f"ws-b-{suffix}", "Workspace B"),
        )
        for tid, wid, uid, role, m_status in (
            (tenant_a, ws_a, owner_a, "owner", "active"),
            (tenant_a, ws_a, admin_a, "admin", "active"),
            (tenant_a, ws_a, dev_a, "developer", "active"),
            (tenant_a, ws_a, existing_revoked, "viewer", "revoked"),
            (tenant_b, ws_b, owner_b, "owner", "active"),
        ):
            conn.execute(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tid, wid, uid, role, m_status),
            )
    return {
        "issuer": issuer,
        "tenant_a": tenant_a,
        "ws_a": ws_a,
        "tenant_b": tenant_b,
        "ws_b": ws_b,
        "owner_a": owner_a,
        "admin_a": admin_a,
        "dev_a": dev_a,
        "owner_b": owner_b,
        "existing_revoked": existing_revoked,
        "revoked_email": f"revoked-{suffix}@example.com",
        "owner_a_email": f"owner-a-{suffix}@example.com",
        "bootstrap_only": bootstrap_only,
        "bootstrap_subject": f"boot-{suffix}",
    }


def _configure_control_api_for_db(
    monkeypatch: pytest.MonkeyPatch,
    app_url: str,
    fernet_key: str,
    bootstrap_subjects: list[str],
):
    store = InMemoryRevocationStore()
    monkeypatch.setattr(session_manager, "denylist", store)
    async_engine = create_async_engine(_to_async_pg_url(app_url), pool_size=5, max_overflow=5)
    async_session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    monkeypatch.setattr(control_db, "engine", async_engine)
    monkeypatch.setattr(control_db, "AsyncSessionLocal", async_session_factory)
    monkeypatch.setattr(
        workspaces_router,
        "invitation_repository",
        InvitationRepository(PostgresOutboxEventBus(async_session_factory)),
    )
    monkeypatch.setattr(settings, "invitations_enabled", True)
    monkeypatch.setattr(settings, "invitation_public_app_origin", "http://localhost:3000")
    monkeypatch.setattr(settings, "invitation_proof_key_id", "v1")
    monkeypatch.setattr(
        settings.invitation_proof_encryption_key, "get_secret_value", lambda: fernet_key
    )
    monkeypatch.setattr(settings, "invitation_ttl_hours", 72)
    monkeypatch.setattr(settings, "invitation_resend_cooldown_seconds", 60)
    monkeypatch.setattr(settings, "invitation_recipient_hourly_limit", 5)
    monkeypatch.setattr(settings, "invitation_workspace_hourly_limit", 100)
    monkeypatch.setattr(settings, "bootstrap_operator_subjects", bootstrap_subjects)

    return async_engine, async_session_factory


def _issue_session_cookie_header(user_id: UUID, issuer: str, subject: str) -> dict[str, str]:
    _session, token = session_manager.create_session(
        user_id=str(user_id), issuer=issuer, subject=subject
    )
    return {"Cookie": f"{settings.session_cookie_name}={token}", "Origin": "http://localhost:3000"}


@pytest.mark.integration
def test_rls_and_db_constraints_fail_closed_under_platform_app(migrated_database: dict[str, Any]):
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seeded = _seed_workspace_and_users(owner_url)

    # 1. Missing RLS context returns 0 rows on SELECT and raises InsufficientPrivilege on INSERT
    with psycopg.connect(app_url) as conn:
        assert conn.execute("SELECT * FROM iam.workspace_invitations").fetchall() == []
        conn.rollback()

    with psycopg.connect(app_url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO iam.workspace_invitations ("
                "tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, role, "
                "created_by_user_id, token_hash, proof_ciphertext, proof_key_id, "
                "delivery_requested_by_user_id, expires_at"
                ") VALUES (%s, %s, 'x@example.com', %s, 'developer', %s, %s, 'ct', 'v1', %s, now() + interval '1 hour')",
                (
                    seeded["tenant_a"],
                    seeded["ws_a"],
                    seeded["issuer"],
                    seeded["owner_a"],
                    "a" * 64,
                    seeded["owner_a"],
                ),
            )
        conn.rollback()

    # 2. Pooled connection reuse isolation: SET LOCAL scope clears after transaction commit
    with psycopg.connect(app_url) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                (str(seeded["tenant_a"]), str(seeded["ws_a"])),
            )
            conn.execute(
                "INSERT INTO iam.workspace_invitations ("
                "tenant_id, workspace_id, recipient_email_normalized, recipient_issuer, role, "
                "created_by_user_id, token_hash, proof_ciphertext, proof_key_id, "
                "delivery_requested_by_user_id, expires_at"
                ") VALUES (%s, %s, 'pooled@example.com', %s, 'viewer', %s, %s, 'ct', 'v1', %s, now() + interval '1 hour')",
                (
                    seeded["tenant_a"],
                    seeded["ws_a"],
                    seeded["issuer"],
                    seeded["owner_a"],
                    "b" * 64,
                    seeded["owner_a"],
                ),
            )

        with conn.transaction():
            conn.execute(
                "SELECT set_config('app.tenant_id', %s, true), set_config('app.workspace_id', %s, true)",
                (str(seeded["tenant_b"]), str(seeded["ws_b"])),
            )
            rows = conn.execute("SELECT id FROM iam.workspace_invitations").fetchall()
            assert rows == []


@pytest.mark.integration
def test_end_to_end_http_outbox_worker_cli_and_real_tcp_smtp_sink(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seeded = _seed_workspace_and_users(owner_url)
    fernet_key = Fernet.generate_key().decode("ascii")
    sink = _RealSmtpSinkServer("accept")

    async def _scenario() -> None:
        async_engine, _ = _configure_control_api_for_db(
            monkeypatch,
            app_url,
            fernet_key,
            [f"{seeded['issuer']}|{seeded['bootstrap_subject']}"],
        )
        try:
            owner_headers = _issue_session_cookie_header(
                seeded["owner_a"], seeded["issuer"], f"owner-{seeded['owner_a']}"
            )
            admin_headers = _issue_session_cookie_header(
                seeded["admin_a"], seeded["issuer"], f"admin-{seeded['admin_a']}"
            )
            dev_headers = _issue_session_cookie_header(
                seeded["dev_a"], seeded["issuer"], f"dev-{seeded['dev_a']}"
            )
            boot_headers = _issue_session_cookie_header(
                seeded["bootstrap_only"], seeded["issuer"], seeded["bootstrap_subject"]
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost:8000"
            ) as client:
                # Scoped workspace API token denied on invitations
                scoped_resp = await client.post(
                    "/v1/auth/token",
                    headers=owner_headers,
                    json={"workspace_id": str(seeded["ws_a"])},
                )
                assert scoped_resp.status_code == 200
                scoped_token = scoped_resp.json()["access_token"]
                r_scoped = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers={"Authorization": f"Bearer {scoped_token}"},
                    json={"email": "blocked-scoped@example.com", "role": "viewer"},
                )
                assert r_scoped.status_code == 403
                assert r_scoped.json()["detail"]["code"] == "active_session_required"

                # Bootstrap operator without workspace membership denied
                r_boot = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=boot_headers,
                    json={"email": "blocked-boot@example.com", "role": "viewer"},
                )
                assert r_boot.status_code == 403

                # Developer denied
                r_dev = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=dev_headers,
                    json={"email": "blocked-dev@example.com", "role": "viewer"},
                )
                assert r_dev.status_code == 403

                # Revoked or active membership conflict -> 409 (no revival)
                r_revoked_member = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": seeded["revoked_email"], "role": "viewer"},
                )
                assert r_revoked_member.status_code == 409
                assert r_revoked_member.json()["detail"]["code"] == "membership_conflict"

                r_active_member = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": seeded["owner_a_email"], "role": "viewer"},
                )
                assert r_active_member.status_code == 409
                assert r_active_member.json()["detail"]["code"] == "membership_conflict"

                # Admin cannot invite admin (403), owner cannot invite owner (422)
                assert (
                    await client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations",
                        headers=admin_headers,
                        json={"email": "other-admin@example.com", "role": "admin"},
                    )
                ).status_code == 403
                assert (
                    await client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations",
                        headers=owner_headers,
                        json={"email": "other-owner@example.com", "role": "owner"},
                    )
                ).status_code == 422

                # Owner creates admin invitation (201) -> Admin cannot revoke admin invitation (403)
                r_admin_inv = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": "New.Admin+01@Example.COM", "role": "admin"},
                )
                assert r_admin_inv.status_code == 201
                admin_inv_id = r_admin_inv.json()["invitation_id"]
                assert (
                    await client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations/{admin_inv_id}/revoke",
                        headers=admin_headers,
                    )
                ).status_code == 403

                # Owner creates developer invitation (201), resend blocked by 60s cooldown (429 + Retry-After)
                r_dev_inv = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": "Dev.Invitee+Tag@Example.COM", "role": "developer"},
                )
                assert r_dev_inv.status_code == 201
                dev_inv = r_dev_inv.json()
                assert dev_inv["recipient_email_normalized"] == "dev.invitee+tag@example.com"
                assert dev_inv["generation"] == 1
                assert dev_inv["delivery_state"] == "queued"

                r_cooldown = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations/{dev_inv['invitation_id']}/resend",
                    headers=owner_headers,
                )
                assert r_cooldown.status_code == 429
                assert int(r_cooldown.headers["retry-after"]) >= 1
                assert r_cooldown.json()["detail"]["code"] == "resend_cooldown_active"

                # Keyset pagination check (limit=1 -> cursor -> second page)
                page1 = await client.get(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations?limit=1",
                    headers=owner_headers,
                )
                assert page1.status_code == 200
                p1_json = page1.json()
                assert len(p1_json["items"]) == 1
                assert p1_json["next_cursor"] is not None
                page2 = await client.get(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations?limit=1&cursor={p1_json['next_cursor']}",
                    headers=owner_headers,
                )
                assert page2.status_code == 200
                p2_json = page2.json()
                assert len(p2_json["items"]) == 1
                assert p1_json["items"][0]["invitation_id"] != p2_json["items"][0]["invitation_id"]
        finally:
            await async_engine.dispose()

    try:
        asyncio.run(_scenario())

        # Invoke real worker CLI entrypoint against real PostgreSQL + real TCP SMTP sink
        monkeypatch.setenv("DATABASE_URL", _to_sync_pg_url(app_url))
        monkeypatch.setenv("WORKER_TENANT_ID", str(seeded["tenant_a"]))
        monkeypatch.setenv("WORKER_WORKSPACE_ID", str(seeded["ws_a"]))
        monkeypatch.setenv("INVITATIONS_ENABLED", "true")
        monkeypatch.setenv("INVITATION_PUBLIC_APP_ORIGIN", "http://localhost:3000")
        monkeypatch.setenv("INVITATION_PROOF_ENCRYPTION_KEY", fernet_key)
        monkeypatch.setenv("INVITATION_PROOF_KEY_ID", "v1")
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(sink.port))
        monkeypatch.setenv("SMTP_FROM", "no-reply@example.com")
        monkeypatch.setenv("SMTP_TLS_MODE", "none")
        monkeypatch.setenv("ALLOW_PLAINTEXT_LOCAL_SMTP", "true")

        worker_main(["--mode", "invitations", "--once"])

        assert len(sink.messages) == 2
        with psycopg.connect(owner_url) as conn:
            invs = conn.execute(
                "SELECT id, recipient_email_normalized, role, status, delivery_state, "
                "       proof_ciphertext, proof_key_id, lease_token, leased_until, "
                "       token_hash, provider_accepted_at "
                "FROM iam.workspace_invitations WHERE workspace_id = %s",
                (seeded["ws_a"],),
            ).fetchall()
            assert len(invs) == 2
            for row in invs:
                (
                    inv_id,
                    email_norm,
                    _role,
                    status_val,
                    deliv_state,
                    ct,
                    kid,
                    lt,
                    lu,
                    tok_hash,
                    accepted_at,
                ) = row
                assert status_val == "pending"
                assert deliv_state == "provider_accepted"
                assert ct is None and kid is None and lt is None and lu is None
                assert accepted_at is not None

                matched_msg = next(m["raw"] for m in sink.messages if email_norm in m["rcpt_to"])
                parsed_mime = email_stdlib.message_from_string(matched_msg)
                decoded_body = parsed_mime.get_payload(decode=True)
                assert decoded_body is not None
                body_text = decoded_body.decode("utf-8")
                m_tok = re.search(
                    rf"http://localhost:3000/invitations/{inv_id}/accept#token=([^\s\r\n]+)",
                    body_text,
                )
                assert m_tok is not None
                raw_proof = m_tok.group(1)
                assert hashlib.sha256(raw_proof.encode("utf-8")).hexdigest() == tok_hash

                # Zero leak in outbox & audit tables
                audit_rows = conn.execute(
                    "SELECT details_redacted::text FROM governance.audit_events WHERE workspace_id = %s",
                    (seeded["ws_a"],),
                ).fetchall()
                outbox_rows = conn.execute(
                    "SELECT state, payload::text FROM integration.outbox_events WHERE workspace_id = %s",
                    (seeded["ws_a"],),
                ).fetchall()
                receipts = conn.execute(
                    "SELECT consumer_key, event_id FROM integration.inbox_receipts WHERE workspace_id = %s",
                    (seeded["ws_a"],),
                ).fetchall()
                assert len(outbox_rows) == 2 and all(r[0] == "delivered" for r in outbox_rows)
                assert len(receipts) == 2 and all(
                    r[0] == "workspace_invitation_email_v1" for r in receipts
                )
                for (details_txt,) in audit_rows:
                    assert raw_proof not in details_txt
                    assert tok_hash not in details_txt
                for _state, payload_txt in outbox_rows:
                    assert raw_proof not in payload_txt
                    assert tok_hash not in payload_txt
                    assert email_norm not in payload_txt
    finally:
        sink.close()


@pytest.mark.integration
def test_concurrency_atomic_rollback_and_resend_revocation_races(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seeded = _seed_workspace_and_users(owner_url)
    fernet_key = Fernet.generate_key().decode("ascii")
    sink = _RealSmtpSinkServer("accept")

    async def _run() -> None:
        async_engine, async_session_factory = _configure_control_api_for_db(
            monkeypatch, app_url, fernet_key, []
        )
        # Disable cooldown in this test to exercise resend generation rotation directly
        monkeypatch.setattr(settings, "invitation_resend_cooldown_seconds", 0)
        try:
            owner_headers = _issue_session_cookie_header(
                seeded["owner_a"], seeded["issuer"], f"owner-{seeded['owner_a']}"
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost:8000"
            ) as client:
                # 1. Parallel duplicate create -> exactly one 201 and one 409
                r1, r2 = await asyncio.gather(
                    client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations",
                        headers=owner_headers,
                        json={"email": "race@example.com", "role": "developer"},
                    ),
                    client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations",
                        headers=owner_headers,
                        json={"email": "race@example.com", "role": "developer"},
                    ),
                )
                assert sorted([r1.status_code, r2.status_code]) == [201, 409]
                race_inv = r1.json() if r1.status_code == 201 else r2.json()
                race_id = race_inv["invitation_id"]

                # 2. Resend before worker runs -> generation=2, old gen=1 outbox dead-lettered
                r_resend = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations/{race_id}/resend",
                    headers=owner_headers,
                )
                assert r_resend.status_code == 202
                assert r_resend.json()["generation"] == 2

                # 3. Revoke pending invitation -> idempotent second revoke -> fresh invite allowed
                r_rev = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations/{race_id}/revoke",
                    headers=owner_headers,
                )
                assert r_rev.status_code == 200
                assert r_rev.json()["status"] == "revoked"
                assert r_rev.json()["delivery_state"] == "cancelled"

                r_rev_idem = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations/{race_id}/revoke",
                    headers=owner_headers,
                )
                assert r_rev_idem.status_code == 200
                assert r_rev_idem.json()["status"] == "revoked"

                # Fresh invite for same email allowed now that prior was revoked
                r_recreate = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": "race@example.com", "role": "operator"},
                )
                assert r_recreate.status_code == 201

                # 4. Atomic rollback if outbox insert raises
                broken_bus = PostgresOutboxEventBus(async_session_factory)

                async def _boom(*_args: Any, **_kwargs: Any) -> str:
                    raise RuntimeError("Injected outbox failure")

                monkeypatch.setattr(broken_bus, "publish_in_transaction", _boom)
                monkeypatch.setattr(
                    workspaces_router,
                    "invitation_repository",
                    InvitationRepository(broken_bus),
                )
                # Exercise the HTTP 500 boundary for this deliberately unhandled
                # failure; other requests retain ASGITransport's exception checks.
                async with AsyncClient(
                    transport=ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://localhost:8000",
                ) as failed_client:
                    r_boom = await failed_client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations",
                        headers=owner_headers,
                        json={"email": "rollback@example.com", "role": "viewer"},
                    )
                assert r_boom.status_code == 500
        finally:
            await async_engine.dispose()

    try:
        asyncio.run(_run())
        with psycopg.connect(owner_url) as conn:
            rb_rows = conn.execute(
                "SELECT id FROM iam.workspace_invitations WHERE recipient_email_normalized = 'rollback@example.com'"
            ).fetchall()
            assert rb_rows == []

        # Run worker: revoked gen 1/2 are dead, only recreated fresh invite sends SMTP once!
        consumer = InvitationOutboxConsumer(
            database_url=_to_sync_pg_url(app_url),
            tenant_id=str(seeded["tenant_a"]),
            workspace_id=str(seeded["ws_a"]),
            protector=InvitationProofProtector(fernet_key, "v1"),
            public_app_origin="http://localhost:3000",
            smtp_transport=StdlibSmtpTransport(
                SmtpConfig(
                    host="127.0.0.1",
                    port=sink.port,
                    sender="no-reply@example.com",
                    tls_mode="none",
                    allow_plaintext_local_smtp=True,
                )
            ),
        )
        consumer.run_once()
        assert len(sink.messages) == 1
        assert "race@example.com" in sink.messages[0]["rcpt_to"]
    finally:
        sink.close()


@pytest.mark.integration
def test_worker_crash_lease_expiry_fencing_dedupe_and_smtp_failures(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seeded = _seed_workspace_and_users(owner_url)
    fernet_key = Fernet.generate_key().decode("ascii")

    async def _seed_via_api(emails: list[str]) -> list[dict[str, Any]]:
        async_engine, _ = _configure_control_api_for_db(monkeypatch, app_url, fernet_key, [])
        try:
            owner_headers = _issue_session_cookie_header(
                seeded["owner_a"], seeded["issuer"], f"owner-{seeded['owner_a']}"
            )
            created: list[dict[str, Any]] = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost:8000"
            ) as client:
                for email in emails:
                    r = await client.post(
                        f"/v1/workspaces/{seeded['ws_a']}/invitations",
                        headers=owner_headers,
                        json={"email": email, "role": "developer"},
                    )
                    assert r.status_code == 201
                    created.append(r.json())
            return created
        finally:
            await async_engine.dispose()

    invs = asyncio.run(
        _seed_via_api(
            [
                "crash@example.com",
                "dedupe@example.com",
                "transient@example.com",
                "unknown@example.com",
                "perm550@example.com",
            ]
        )
    )
    inv_crash, inv_dedupe, inv_transient, inv_unknown, inv_perm = invs

    protector = InvitationProofProtector(fernet_key, "v1")
    sink_ok = _RealSmtpSinkServer("accept")
    sink_421 = _RealSmtpSinkServer("transient_421")
    sink_drop = _RealSmtpSinkServer("drop_after_data")
    sink_550 = _RealSmtpSinkServer("reject_rcpt_550")

    def _make_consumer(sink: _RealSmtpSinkServer, retry_delays=(1, 1)) -> InvitationOutboxConsumer:
        return InvitationOutboxConsumer(
            database_url=_to_sync_pg_url(app_url),
            tenant_id=str(seeded["tenant_a"]),
            workspace_id=str(seeded["ws_a"]),
            protector=protector,
            public_app_origin="http://localhost:3000",
            smtp_transport=StdlibSmtpTransport(
                SmtpConfig(
                    host="127.0.0.1",
                    port=sink.port,
                    sender="no-reply@example.com",
                    tls_mode="none",
                    allow_plaintext_local_smtp=True,
                )
            ),
            retry_delays_seconds=retry_delays,
        )

    try:
        # Process all non-target events carefully by moving specific events or sequentially driving scenarios:
        # Pause all except crash first by pushing available_at forward for the other 4
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE integration.outbox_events SET available_at = now() + interval '1 hour' "
                "WHERE aggregate_id <> %s",
                (UUID(inv_crash["invitation_id"]),),
            )

        c_ok = _make_consumer(sink_ok)
        # 1. Crash simulation + lease expiry + stale worker fencing
        had, claim = c_ok._claim_next_outbox_delivery()
        assert had and claim is not None
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE iam.workspace_invitations SET leased_until = now() - interval '5 seconds' WHERE id = %s",
                (UUID(inv_crash["invitation_id"]),),
            )
        assert c_ok.reclaim_expired_leases() == 1
        with psycopg.connect(owner_url) as conn:
            row = conn.execute(
                "SELECT delivery_state, proof_ciphertext, lease_token FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_crash["invitation_id"]),),
            ).fetchone()
            assert row == ("unknown", None, None)

        # Stale worker wakes up and attempts CAS completion -> fenced, 0 side effects!
        from jobs_worker.smtp_adapter import SmtpSendResult

        c_ok._complete_delivery_cas(claim, SmtpSendResult(outcome="provider_accepted"))
        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT delivery_state FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_crash["invitation_id"]),),
            ).fetchone() == ("unknown",)
            assert conn.execute(
                "SELECT count(*) FROM integration.inbox_receipts WHERE event_id = %s",
                (UUID(claim.outbox_event_id),),
            ).fetchone() == (0,)

        # 2. Duplicate outbox event already in inbox_receipts -> skipped without SMTP
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "INSERT INTO integration.inbox_receipts (tenant_id, workspace_id, consumer_key, event_id) "
                "VALUES (%s, %s, 'workspace_invitation_email_v1', %s)",
                (seeded["tenant_a"], seeded["ws_a"], UUID(inv_dedupe["outbox_event_id"])),
            )
            conn.execute(
                "UPDATE integration.outbox_events SET available_at = now() - interval '1 second' WHERE id = %s",
                (UUID(inv_dedupe["outbox_event_id"]),),
            )
        assert c_ok.process_next_event() is True
        assert len(sink_ok.messages) == 0  # NO duplicate SMTP sent!
        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT state FROM integration.outbox_events WHERE id = %s",
                (UUID(inv_dedupe["outbox_event_id"]),),
            ).fetchone() == ("delivered",)

        # 3. Transient 421 before DATA -> requeues as queued/pending with proof preserved!
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE integration.outbox_events SET available_at = now() - interval '1 second' WHERE id = %s",
                (UUID(inv_transient["outbox_event_id"]),),
            )
        c_421 = _make_consumer(sink_421, retry_delays=(30, 120))
        assert c_421.process_next_event() is True
        with psycopg.connect(owner_url) as conn:
            inv_row = conn.execute(
                "SELECT delivery_state, proof_ciphertext IS NOT NULL FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_transient["invitation_id"]),),
            ).fetchone()
            ob_row = conn.execute(
                "SELECT state, attempt, available_at > now() + interval '10 seconds' "
                "FROM integration.outbox_events WHERE id = %s",
                (UUID(inv_transient["outbox_event_id"]),),
            ).fetchone()
            assert inv_row == ("queued", True)
            assert ob_row == ("pending", 1, True)
            # Push transient outbox back into the future so next events run isolated
            conn.execute(
                "UPDATE integration.outbox_events SET available_at = now() + interval '1 hour' WHERE id = %s",
                (UUID(inv_transient["outbox_event_id"]),),
            )

        # 4. Drop after DATA dot -> ambiguous_unknown + ciphertext cleared + dead outbox + no inbox_receipts
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE integration.outbox_events SET available_at = now() - interval '1 second' WHERE id = %s",
                (UUID(inv_unknown["outbox_event_id"]),),
            )
        c_drop = _make_consumer(sink_drop)
        assert c_drop.process_next_event() is True
        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT delivery_state, proof_ciphertext FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_unknown["invitation_id"]),),
            ).fetchone() == ("unknown", None)
            assert conn.execute(
                "SELECT state FROM integration.outbox_events WHERE id = %s",
                (UUID(inv_unknown["outbox_event_id"]),),
            ).fetchone() == ("dead",)

        # 5. Permanent 550 refusal -> failed + ciphertext cleared + dead outbox
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE integration.outbox_events SET available_at = now() - interval '1 second' WHERE id = %s",
                (UUID(inv_perm["outbox_event_id"]),),
            )
        c_550 = _make_consumer(sink_550)
        assert c_550.process_next_event() is True
        with psycopg.connect(owner_url) as conn:
            assert conn.execute(
                "SELECT delivery_state, delivery_error_code, proof_ciphertext FROM iam.workspace_invitations WHERE id = %s",
                (UUID(inv_perm["invitation_id"]),),
            ).fetchone() == ("failed", "SMTP_RCPT_REJECTED", None)
            assert conn.execute(
                "SELECT state FROM integration.outbox_events WHERE id = %s",
                (UUID(inv_perm["outbox_event_id"]),),
            ).fetchone() == ("dead",)
    finally:
        sink_ok.close()
        sink_421.close()
        sink_drop.close()
        sink_550.close()


@pytest.mark.integration
def test_rate_limits_and_worker_requester_revocation_recheck(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seeded = _seed_workspace_and_users(owner_url)
    fernet_key = Fernet.generate_key().decode("ascii")
    sink = _RealSmtpSinkServer("accept")

    async def _exercise_limits() -> str:
        async_engine, _ = _configure_control_api_for_db(monkeypatch, app_url, fernet_key, [])
        monkeypatch.setattr(settings, "invitation_resend_cooldown_seconds", 0)
        monkeypatch.setattr(settings, "invitation_recipient_hourly_limit", 2)
        monkeypatch.setattr(settings, "invitation_workspace_hourly_limit", 3)
        try:
            owner_headers = _issue_session_cookie_header(
                seeded["owner_a"], seeded["issuer"], f"owner-{seeded['owner_a']}"
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost:8000"
            ) as client:
                r1 = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": "quota@example.com", "role": "viewer"},
                )
                assert r1.status_code == 201
                inv_id = r1.json()["invitation_id"]

                r2 = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations/{inv_id}/resend",
                    headers=owner_headers,
                )
                assert r2.status_code == 202

                # 3rd event for same recipient within 60m -> 429 recipient_rate_limit_exceeded
                r3 = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations/{inv_id}/resend",
                    headers=owner_headers,
                )
                assert r3.status_code == 429
                assert r3.json()["detail"]["code"] == "recipient_rate_limit_exceeded"
                assert int(r3.headers["retry-after"]) >= 1

                # Another recipient consumes workspace's 3rd slot; 4th hits workspace_rate_limit_exceeded
                r4 = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": "other-user@example.com", "role": "viewer"},
                )
                assert r4.status_code == 201

                r5 = await client.post(
                    f"/v1/workspaces/{seeded['ws_a']}/invitations",
                    headers=owner_headers,
                    json={"email": "third-user@example.com", "role": "viewer"},
                )
                assert r5.status_code == 429
                assert r5.json()["detail"]["code"] == "workspace_rate_limit_exceeded"
                return str(r4.json()["invitation_id"])
        finally:
            await async_engine.dispose()

    try:
        second_inv_id = asyncio.run(_exercise_limits())

        # Revoke requester (owner_a) membership BEFORE worker claims queued delivery
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE iam.memberships SET status = 'revoked' WHERE workspace_id = %s AND user_id = %s",
                (seeded["ws_a"], seeded["owner_a"]),
            )

        consumer = InvitationOutboxConsumer(
            database_url=_to_sync_pg_url(app_url),
            tenant_id=str(seeded["tenant_a"]),
            workspace_id=str(seeded["ws_a"]),
            protector=InvitationProofProtector(fernet_key, "v1"),
            public_app_origin="http://localhost:3000",
            smtp_transport=StdlibSmtpTransport(
                SmtpConfig(
                    host="127.0.0.1",
                    port=sink.port,
                    sender="no-reply@example.com",
                    tls_mode="none",
                    allow_plaintext_local_smtp=True,
                )
            ),
        )
        consumer.run_once()

        # Worker MUST cancel delivery without sending any SMTP email
        assert len(sink.messages) == 0
        with psycopg.connect(owner_url) as conn:
            row = conn.execute(
                "SELECT delivery_state, delivery_error_code, proof_ciphertext "
                "FROM iam.workspace_invitations WHERE id = %s",
                (UUID(second_inv_id),),
            ).fetchone()
            assert row == ("cancelled", "REQUESTER_UNAUTHORIZED", None)
    finally:
        sink.close()
