"""Real Chromium Playwright browser E2E test for Step 03-C Invitation Acceptance."""

from __future__ import annotations

import email as email_stdlib
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
import redis as redis_lib
from control_api.core.settings import settings
from control_api.modules.identity.repositories.session_repository import RedisRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from cryptography.fernet import Fernet
from jobs_worker.invitation_consumer import InvitationOutboxConsumer
from jobs_worker.smtp_adapter import SmtpConfig, StdlibSmtpTransport
from platform_common.invitations import InvitationProofProtector
from sqlalchemy import create_engine

from tests.fixtures.oidc_http_server import run_oidc_http_server
from tests.integration.test_workspace_invitations_real_postgres_smtp import (
    _issue_session_cookie_header,
    _RealSmtpSinkServer,
    _seed_workspace_and_users,
    _to_async_pg_url,
    _to_sync_pg_url,
)

ROOT = Path(__file__).resolve().parents[2]
REDIS_E2E_URL = os.environ.get("TEST_REDIS_E2E_URL", "redis://127.0.0.1:6379/14")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _find_playwright_python() -> str:
    candidates: list[str] = []
    if env_py := os.environ.get("PLAYWRIGHT_PYTHON"):
        candidates.append(env_py)
    candidates.append(sys.executable)
    local_py313 = Path.home() / "AppData/Local/Programs/Python/Python313/python.exe"
    if local_py313.exists():
        candidates.append(str(local_py313))
    for name in ("python3", "python"):
        if found := shutil.which(name):
            candidates.append(found)
    seen: set[str] = set()
    for exe in candidates:
        if not exe or exe in seen:
            continue
        seen.add(exe)
        probe = subprocess.run(
            [exe, "-c", "import playwright.sync_api"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode == 0:
            return exe
    pytest.skip("Playwright with Chromium is not installed in any available Python runtime")


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _wait_http_ready(
    url: str,
    *,
    proc: subprocess.Popen[Any] | None = None,
    log_path: Path | None = None,
    timeout: float = 45.0,
    accepted_statuses: tuple[int, ...] = (200,),
) -> None:
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            log_tail = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path and log_path.exists()
                else ""
            )
            raise RuntimeError(
                f"Process exited early (code={proc.returncode}) for {url}:\n{log_tail[-3000:]}"
            )
        try:
            with _NO_PROXY_OPENER.open(url, timeout=5) as resp:
                if resp.status in accepted_statuses:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code in accepted_statuses:
                return
            last_err = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        time.sleep(0.35)
    log_tail = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path and log_path.exists()
        else ""
    )
    raise TimeoutError(
        f"Service did not become ready at {url} within {timeout}s (last: {last_err})\n{log_tail[-3000:]}"
    )


def _start_control_shim(oidc_server: Any, owner_url: str) -> tuple[str, Any]:
    class _ShimHandler(http.server.BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if urlparse(self.path).path == "/observed":
                self._send_json(200, oidc_server.observed_auth_queries)
                return
            self._send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            path = urlparse(self.path).path
            if path == "/set-profile":
                oidc_server.interactive_login_profile = data
                self._send_json(200, {"ok": True})
                return
            if path == "/membership-count":
                with psycopg.connect(owner_url) as conn:
                    count = conn.execute(
                        "SELECT count(*) "
                        "FROM iam.memberships m "
                        "JOIN iam.users u ON u.id = m.user_id "
                        "WHERE m.workspace_id = %s "
                        "  AND u.email_normalized = lower(%s) "
                        "  AND m.status = 'active'",
                        (data["workspace_id"], data["email"]),
                    ).fetchone()[0]
                self._send_json(200, {"count": int(count)})
                return
            self._send_json(404, {"error": "not_found"})

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ShimHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", server


def _api_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with _NO_PROXY_OPENER.open(req, timeout=10) as resp:
        assert resp.status in (200, 201, 202), f"Unexpected status {resp.status}"
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _extract_invite_link_parts(raw_mime: str, web_base: str) -> tuple[str, str]:
    msg = email_stdlib.message_from_string(raw_mime)
    body = msg.get_payload(decode=True).decode("utf-8")
    pattern = re.escape(web_base) + r"/invitations/([0-9a-f-]{36})/accept#token=([A-Za-z0-9_-]+)"
    m = re.search(pattern, body)
    assert m is not None, f"Invitation link not found in MIME body:\n{body}"
    return m.group(1), m.group(2)


def _terminate_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_real_chromium_invitation_acceptance_journeys(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Full Chromium + Next.js + FastAPI + PG16 + Redis + live OIDC E2E for Step 03-C."""
    playwright_py = _find_playwright_python()
    r = redis_lib.Redis.from_url(REDIS_E2E_URL, socket_timeout=2.0)
    try:
        assert r.ping()
        r.flushdb()
    finally:
        r.close()

    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seed = _seed_workspace_and_users(owner_url)
    proof_key = Fernet.generate_key().decode("ascii")
    handoff_key = Fernet.generate_key().decode("ascii")

    api_port = _find_free_port()
    web_port = _find_free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://localhost:{web_port}"

    with run_oidc_http_server() as (idp_server, issuer_url):
        idp_server.observed_auth_queries = []
        with psycopg.connect(owner_url) as conn:
            conn.execute(
                "UPDATE iam.users SET issuer = %s WHERE id = %s",
                (issuer_url, seed["admin_a"]),
            )
            ws_name = str(
                conn.execute(
                    "SELECT name FROM iam.workspaces WHERE id = %s",
                    (seed["ws_a"],),
                ).fetchone()[0]
            )

        shim_url, shim_server = _start_control_shim(idp_server, owner_url)

        monkeypatch.setattr(settings, "oidc_issuer", issuer_url)
        jwt_secret = settings.jwt_secret_key.get_secret_value()
        runtime_secret = settings.runtime_jwt_secret.get_secret_value()
        monkeypatch.setattr(
            session_manager,
            "denylist",
            RedisRevocationStore(redis_url=REDIS_E2E_URL),
        )

        api_env = os.environ.copy()
        api_env.update(
            {
                "ENVIRONMENT": "development",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "DATABASE_URL": _to_async_pg_url(app_url),
                "REDIS_URL": REDIS_E2E_URL,
                "AUTH_STORE_TYPE": "redis",
                "JWT_SECRET_KEY": jwt_secret,
                "RUNTIME_JWT_SECRET": runtime_secret,
                "OIDC_ISSUER": issuer_url,
                "OIDC_CLIENT_ID": settings.oidc_client_id,
                "OIDC_CLIENT_SECRET": settings.oidc_client_secret.get_secret_value(),
                "OIDC_REDIRECT_URI": f"{web_base}/v1/auth/callback",
                "ALLOWED_ORIGINS": json.dumps([web_base]),
                "OIDC_REDIRECT_ALLOWLIST": json.dumps([web_base]),
                "SESSION_COOKIE_SECURE": "false",
                "INVITATION_HANDOFF_COOKIE_SECURE": "false",
                "OIDC_CORRELATION_COOKIE_SECURE": "false",
                "INVITATIONS_ENABLED": "true",
                "INVITATION_PUBLIC_APP_ORIGIN": web_base,
                "INVITATION_PROOF_ENCRYPTION_KEY": proof_key,
                "INVITATION_PROOF_KEY_ID": "proof-v1",
                "INVITATION_HANDOFF_ENCRYPTION_KEY": handoff_key,
                "INVITATION_HANDOFF_KEY_ID": "handoff-v1",
                "INVITATION_RESEND_COOLDOWN_SECONDS": "0",
                "INVITATION_RECIPIENT_HOURLY_LIMIT": "50",
                "INVITATION_WORKSPACE_HOURLY_LIMIT": "200",
            }
        )

        web_env = os.environ.copy()
        web_env.update(
            {
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "CONTROL_API_INTERNAL_URL": api_base,
                "NEXT_PUBLIC_CONTROL_API_URL": "",
                "NEXT_TELEMETRY_DISABLED": "1",
            }
        )

        api_log_path = tmp_path / "api.log"
        web_log_path = tmp_path / "web.log"
        api_log_f = api_log_path.open("w", encoding="utf-8")
        web_log_f = web_log_path.open("w", encoding="utf-8")

        api_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "control_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=str(ROOT),
            env=api_env,
            stdout=api_log_f,
            stderr=subprocess.STDOUT,
        )
        web_proc = subprocess.Popen(
            [
                "node",
                str(ROOT / "apps/studio-web/node_modules/next/dist/bin/next"),
                "dev",
                "-p",
                str(web_port),
                "-H",
                "localhost",
            ],
            cwd=str(ROOT / "apps/studio-web"),
            env=web_env,
            stdout=web_log_f,
            stderr=subprocess.STDOUT,
        )

        try:
            _wait_http_ready(
                f"{api_base}/v1/auth/me",
                proc=api_proc,
                log_path=api_log_path,
                timeout=45.0,
                accepted_statuses=(200, 401),
            )
            _wait_http_ready(
                f"{web_base}/login", proc=web_proc, log_path=web_log_path, timeout=90.0
            )
            _wait_http_ready(
                f"{web_base}/invitations/00000000-0000-0000-0000-000000000000/accept",
                proc=web_proc,
                log_path=web_log_path,
                timeout=90.0,
                accepted_statuses=(200,),
            )

            admin_headers = {
                **_issue_session_cookie_header(
                    seed["admin_a"], issuer_url, f"admin-a-{seed['tenant_a'].hex[:8]}"
                ),
                "Origin": web_base,
            }
            protector = InvitationProofProtector(proof_key, key_id="proof-v1")

            def _create(email_addr: str) -> str:
                res = _api_post(
                    f"{api_base}/v1/workspaces/{seed['ws_a']}/invitations",
                    {"email": email_addr, "role": "developer"},
                    admin_headers,
                )
                return str(res["invitation_id"])

            # 1..9. Create all invitations and deliver real emails via outbox worker -> SMTP MIME
            happy_email = f"happy-{seed['tenant_a'].hex[:6]}@example.com"
            unverified_email = f"unverified-{seed['tenant_a'].hex[:6]}@example.com"
            mismatch_email = f"mismatch-{seed['tenant_a'].hex[:6]}@example.com"
            stale_email = f"stale-{seed['tenant_a'].hex[:6]}@example.com"
            revoked_email = f"revoked-{seed['tenant_a'].hex[:6]}@example.com"
            expired_email = f"expired-{seed['tenant_a'].hex[:6]}@example.com"
            resent_email = f"resent-{seed['tenant_a'].hex[:6]}@example.com"
            retry_503_email = f"retry503-{seed['tenant_a'].hex[:6]}@example.com"
            lost_resp_email = f"lostresp-{seed['tenant_a'].hex[:6]}@example.com"

            happy_id = _create(happy_email)
            unverified_id = _create(unverified_email)
            mismatch_id = _create(mismatch_email)
            stale_id = _create(stale_email)
            revoked_id = _create(revoked_email)
            expired_id = _create(expired_email)
            resent_id = _create(resent_email)
            retry_503_id = _create(retry_503_email)
            lost_resp_id = _create(lost_resp_email)

            smtp = _RealSmtpSinkServer(mode="accept")
            sync_engine = create_engine(_to_sync_pg_url(app_url), pool_pre_ping=True)
            proofs_by_id: dict[str, str] = {}
            try:
                consumer = InvitationOutboxConsumer(
                    database_url=_to_sync_pg_url(app_url),
                    tenant_id=str(seed["tenant_a"]),
                    workspace_id=str(seed["ws_a"]),
                    protector=protector,
                    public_app_origin=web_base,
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
                assert consumer.run_once() == 9
                assert len(smtp.messages) == 9
                for msg in smtp.messages:
                    parsed_id, parsed_proof = _extract_invite_link_parts(msg["raw"], web_base)
                    proofs_by_id[parsed_id] = parsed_proof
            finally:
                smtp.close()
                sync_engine.dispose()

            happy_proof = proofs_by_id[happy_id]
            unverified_proof = proofs_by_id[unverified_id]
            mismatch_proof = proofs_by_id[mismatch_id]
            stale_proof = proofs_by_id[stale_id]
            revoked_proof = proofs_by_id[revoked_id]
            expired_proof = proofs_by_id[expired_id]
            resent_old_proof = proofs_by_id[resent_id]
            retry_503_proof = proofs_by_id[retry_503_id]
            lost_resp_proof = proofs_by_id[lost_resp_id]

            # Post-delivery lifecycle transitions for error scenarios
            _api_post(
                f"{api_base}/v1/workspaces/{seed['ws_a']}/invitations/{revoked_id}/revoke",
                {},
                admin_headers,
            )
            with psycopg.connect(owner_url) as conn:
                conn.execute(
                    "UPDATE iam.workspace_invitations "
                    "SET status = 'pending', "
                    "    created_at = now() - interval '3 hours', "
                    "    updated_at = now() - interval '3 hours', "
                    "    expires_at = now() - interval '1 hour' "
                    "WHERE id = %s",
                    (expired_id,),
                )
            _api_post(
                f"{api_base}/v1/workspaces/{seed['ws_a']}/invitations/{resent_id}/resend",
                {},
                admin_headers,
            )

            e2e_cfg = {
                "web_base": web_base,
                "oidc_ctrl": shim_url,
                "workspace_id": str(seed["ws_a"]),
                "workspace_name": ws_name,
                "happy": {
                    "invitation_id": happy_id,
                    "proof": happy_proof,
                    "subject": f"sub-happy-{seed['tenant_a'].hex[:6]}",
                    "email": happy_email,
                },
                "unverified": {
                    "invitation_id": unverified_id,
                    "proof": unverified_proof,
                    "subject": f"sub-unverified-{seed['tenant_a'].hex[:6]}",
                    "email": unverified_email,
                },
                "mismatch": {
                    "invitation_id": mismatch_id,
                    "proof": mismatch_proof,
                    "wrong_subject": f"sub-wrong-{seed['tenant_a'].hex[:6]}",
                    "wrong_email": f"wrong-{seed['tenant_a'].hex[:6]}@example.com",
                    "subject": f"sub-right-{seed['tenant_a'].hex[:6]}",
                    "email": mismatch_email,
                },
                "stale": {
                    "invitation_id": stale_id,
                    "proof": stale_proof,
                    "subject": f"sub-stale-{seed['tenant_a'].hex[:6]}",
                    "email": stale_email,
                    "stale_iat": int(time.time()) - 1200,
                },
                "revoked": {
                    "invitation_id": revoked_id,
                    "proof": revoked_proof,
                    "subject": f"sub-revoked-{seed['tenant_a'].hex[:6]}",
                    "email": revoked_email,
                },
                "expired": {
                    "invitation_id": expired_id,
                    "proof": expired_proof,
                    "subject": f"sub-expired-{seed['tenant_a'].hex[:6]}",
                    "email": expired_email,
                },
                "resent_old": {
                    "invitation_id": resent_id,
                    "proof": resent_old_proof,
                    "subject": f"sub-resent-{seed['tenant_a'].hex[:6]}",
                    "email": resent_email,
                },
                "retry_503": {
                    "invitation_id": retry_503_id,
                    "proof": retry_503_proof,
                    "subject": f"sub-r503-{seed['tenant_a'].hex[:6]}",
                    "email": retry_503_email,
                },
                "lost_resp": {
                    "invitation_id": lost_resp_id,
                    "proof": lost_resp_proof,
                    "subject": f"sub-lost-{seed['tenant_a'].hex[:6]}",
                    "email": lost_resp_email,
                },
            }

            pw_env = os.environ.copy()
            pw_env["NO_PROXY"] = "127.0.0.1,localhost"
            pw_env["no_proxy"] = "127.0.0.1,localhost"
            run_res = subprocess.run(
                [
                    playwright_py,
                    str(ROOT / "tests/e2e/run_playwright_invitation_e2e.py"),
                    "--config",
                    json.dumps(e2e_cfg),
                ],
                cwd=str(ROOT),
                env=pw_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=210,
            )
            api_tail = api_log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            web_tail = web_log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            assert run_res.returncode == 0, (
                f"Playwright driver failed (exit={run_res.returncode}):\n"
                f"STDOUT:\n{run_res.stdout}\nSTDERR:\n{run_res.stderr}\n"
                f"API LOG:\n{api_tail}\nWEB LOG:\n{web_tail}"
            )
            parsed_out = json.loads(run_res.stdout.strip().splitlines()[-1])
            assert parsed_out["happy_replay_logout"] == "PASS"
            assert parsed_out["unverified_no_loop"] == "PASS"
            assert parsed_out["mismatch_switch_account"] == "PASS"
            assert parsed_out["stale_reauth"] == "PASS"
            assert parsed_out["lifecycle_errors"] == "PASS"
            assert parsed_out["handoff_503_retry"] == "PASS"
            assert parsed_out["handoff_lost_response_retry"] == "PASS"
            assert parsed_out["reload_back_no_auto_accept"] == "PASS"
            assert parsed_out["sanitized_diagnostics_count"] >= 9
            diag_serialized = json.dumps(parsed_out["sanitized_diagnostics"], ensure_ascii=False)
            for sensitive in (
                happy_proof,
                mismatch_proof,
                resent_old_proof,
                retry_503_proof,
                lost_resp_proof,
                happy_email,
                mismatch_email,
                resent_email,
            ):
                assert sensitive not in diag_serialized
            diag_out = os.environ.get("STEP_03C_BROWSER_DIAG_OUT", "").strip()
            if diag_out:
                out_path = Path(diag_out)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(parsed_out, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        finally:
            _terminate_process(web_proc)
            _terminate_process(api_proc)
            api_log_f.close()
            web_log_f.close()
            shim_server.shutdown()
            shim_server.server_close()
