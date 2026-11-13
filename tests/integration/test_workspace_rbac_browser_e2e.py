"""Real Chromium Playwright E2E integration tests for Step 03-D."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import redis as redis_lib
from control_api.core.settings import settings
from control_api.modules.identity.models.session import VerifiedIdentityEvidence
from control_api.modules.identity.repositories.session_repository import RedisRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from cryptography.fernet import Fernet

from tests.integration.test_workspace_invitations_real_postgres_smtp import (
    _seed_workspace_and_users,
    _to_async_pg_url,
)

ROOT = Path(__file__).resolve().parents[2]
REDIS_E2E_URL = os.environ.get("TEST_REDIS_E2E_URL", "redis://127.0.0.1:16379/1")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _find_playwright_python() -> str:
    import shutil

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


def _wait_http_ready(
    url: str,
    *,
    proc: subprocess.Popen[Any] | None = None,
    log_path: Path | None = None,
    timeout: float = 60.0,
    accepted_statuses: tuple[int, ...] = (200,),
) -> None:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            log_text = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path and log_path.exists()
                else ""
            )
            raise AssertionError(f"Process exited early ({proc.returncode}):\n{log_text}")
        try:
            req = urllib.request.Request(url, method="GET")
            with _NO_PROXY_OPENER.open(req, timeout=2.0) as resp:
                if resp.status in accepted_statuses:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code in accepted_statuses:
                return
            last_err = f"HTTP {exc.code}"
        except (OSError, urllib.error.URLError) as exc:  # pragma: no cover
            last_err = repr(exc)
        time.sleep(0.2)
    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path and log_path.exists()
        else ""
    )
    raise AssertionError(f"Timed out waiting for {url} ({last_err}):\n{log_text}")


def _api_call_json(
    method: str,
    url: str,
    payload: dict[str, Any],
    *,
    cookie_header: str,
    origin: str,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie_header,
            "Origin": origin,
        },
        method=method,
    )
    with _NO_PROXY_OPENER.open(req, timeout=10.0) as resp:
        assert 200 <= resp.status < 300
        return json.loads(resp.read().decode("utf-8"))


def _start_control_shim(api_base: str, web_base: str, owner_cookie_header: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8"))
            if self.path == "/mutate-member-role":
                res = _api_call_json(
                    "PATCH",
                    f"{api_base}/v1/workspaces/{body['workspace_id']}/memberships/{body['membership_id']}/role",
                    {
                        "role": body["role"],
                        "expected_role": body["expected_role"],
                        "expected_status": "active",
                    },
                    cookie_header=owner_cookie_header,
                    origin=web_base,
                )
            elif self.path == "/revoke-member":
                res = _api_call_json(
                    "POST",
                    f"{api_base}/v1/workspaces/{body['workspace_id']}/memberships/{body['membership_id']}/revoke",
                    {
                        "expected_role": body["expected_role"],
                        "expected_status": "active",
                    },
                    cookie_header=owner_cookie_header,
                    origin=web_base,
                )
            else:
                self.send_response(404)
                self.end_headers()
                return
            out = json.dumps(res).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.mark.integration
def test_workspace_rbac_member_management_switcher_and_revocation_browser_e2e(
    migrated_database: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Full Chromium Playwright E2E verifying 03-D Settings UI, Switcher, Focus Revoke & Owner Accept."""
    playwright_py = _find_playwright_python()
    redis_client = redis_lib.Redis.from_url(REDIS_E2E_URL, socket_timeout=2.0)
    try:
        assert redis_client.ping(), f"Cannot ping isolated E2E Redis at {REDIS_E2E_URL}"
        redis_client.flushdb()
    finally:
        redis_client.close()

    owner_url = migrated_database["owner_url"]
    app_url = migrated_database["app_url"]
    seed = _seed_workspace_and_users(owner_url)

    issuer = seed["issuer"]
    tenant_a, ws_a = seed["tenant_a"], seed["ws_a"]
    tenant_b, ws_b = seed["tenant_b"], seed["ws_b"]
    suffix = uuid4().hex[:8]

    op_a = uuid4()
    admin2_a = uuid4()
    prom_owner_b = uuid4()
    op_a_name = "Operator E2E"
    admin2_a_name = "Admin2 E2E"
    prom_owner_b_name = "Promoted Owner B"
    owner_a_name = f"owner-a-{str(tenant_a)[:8]}"
    admin_a_name = f"admin-a-{str(tenant_a)[:8]}"
    dev_a_name = f"dev-a-{str(tenant_a)[:8]}"

    inv_b_id = uuid4()
    raw_invite_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_invite_token.encode()).hexdigest()

    extra_users: list[tuple[UUID, str, str]] = [
        (op_a, f"op-e2e-{suffix}", op_a_name),
        (admin2_a, f"adm2-e2e-{suffix}", admin2_a_name),
        (prom_owner_b, f"prom-b-{suffix}", prom_owner_b_name),
    ]
    filler_ids = [(uuid4(), f"fill-{i:02d}-{suffix}", f"Filler Member {i:02d}") for i in range(25)]

    with psycopg.connect(owner_url, autocommit=True) as conn:
        for uid, subj, disp in [*extra_users, *filler_ids]:
            conn.execute(
                "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) "
                "VALUES (%s, %s, %s, %s, %s)",
                (uid, issuer, subj, disp, f"{subj}@example.com"),
            )
        # Seed extra members in WS A; grant owner_a & dev_a membership in WS B; prom_owner_b is owner in WS B
        conn.execute(
            "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
            "VALUES (%s, %s, %s, 'operator', 'active'), "
            "       (%s, %s, %s, 'admin', 'active'), "
            "       (%s, %s, %s, 'owner', 'active'), "
            "       (%s, %s, %s, 'developer', 'active'), "
            "       (%s, %s, %s, 'owner', 'active')",
            (
                tenant_a,
                ws_a,
                op_a,
                tenant_a,
                ws_a,
                admin2_a,
                tenant_b,
                ws_b,
                seed["owner_a"],
                tenant_b,
                ws_b,
                seed["dev_a"],
                tenant_b,
                ws_b,
                prom_owner_b,
            ),
        )
        for uid, _, _ in filler_ids:
            conn.execute(
                "INSERT INTO iam.memberships (tenant_id, workspace_id, user_id, role, status) "
                "VALUES (%s, %s, %s, 'viewer', 'active')",
                (tenant_a, ws_a, uid),
            )
        # Pre-seed an accepted invitation in WS B for prom_owner_b (already promoted to owner!)
        conn.execute(
            """
            INSERT INTO iam.workspace_invitations (
                id, tenant_id, workspace_id, recipient_email_normalized, recipient_issuer,
                role, status, created_by_user_id, accepted_by_user_id, accepted_at,
                token_hash, proof_ciphertext, proof_key_id, delivery_requested_by_user_id,
                expires_at
            ) VALUES (%s, %s, %s, %s, %s, 'admin', 'accepted', %s, %s, now(), %s, NULL, NULL, %s, now() + interval '1 day')
            """,
            (
                inv_b_id,
                tenant_b,
                ws_b,
                f"prom-b-{suffix}@example.com",
                issuer,
                seed["owner_b"],
                prom_owner_b,
                token_hash,
                seed["owner_b"],
            ),
        )
        # Fetch membership IDs + subjects for E2E config
        mrows = conn.execute(
            "SELECT user_id, id FROM iam.memberships WHERE workspace_id = %s",
            (ws_a,),
        ).fetchall()
        mids_ws_a = {r[0]: str(r[1]) for r in mrows}

    api_port = _find_free_port()
    web_port = _find_free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://localhost:{web_port}"

    proof_key = Fernet.generate_key().decode()
    handoff_key = Fernet.generate_key().decode()
    jwt_secret = settings.jwt_secret_key.get_secret_value()
    runtime_secret = settings.runtime_jwt_secret.get_secret_value()

    monkeypatch.setattr(session_manager, "denylist", RedisRevocationStore(redis_url=REDIS_E2E_URL))

    def _make_cookie_token(user_id: UUID, subj: str, email: str) -> str:
        now_ts = int(time.time())
        ev = VerifiedIdentityEvidence(
            issuer=issuer,
            subject=subj,
            user_id=str(user_id),
            email_normalized=email,
            email_verified=True,
            id_token_issued_at=now_ts,
            observed_at=now_ts,
        )
        _sess, tok = session_manager.create_session(
            user_id=str(user_id),
            issuer=issuer,
            subject=subj,
            identity_evidence=ev,
        )
        return tok

    owner_a_tok = _make_cookie_token(
        seed["owner_a"], owner_a_name, f"{owner_a_name}@example.com"
    )
    admin2_a_tok = _make_cookie_token(
        admin2_a, f"adm2-e2e-{suffix}", f"adm2-e2e-{suffix}@example.com"
    )
    dev_a_tok = _make_cookie_token(seed["dev_a"], dev_a_name, f"{dev_a_name}@example.com")
    prom_owner_b_tok = _make_cookie_token(
        prom_owner_b, f"prom-b-{suffix}", f"prom-b-{suffix}@example.com"
    )

    shim_srv, ctrl_url = _start_control_shim(
        api_base, web_base, f"{settings.session_cookie_name}={owner_a_tok}"
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
            "JWT_ISSUER": issuer,
            "RUNTIME_JWT_SECRET": runtime_secret,
            "OIDC_ISSUER": issuer,
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

    api_log = tmp_path / "api_e2e.log"
    web_log = tmp_path / "web_e2e.log"
    api_log_f = api_log.open("w", encoding="utf-8")
    web_log_f = web_log.open("w", encoding="utf-8")

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
            log_path=api_log,
            timeout=45.0,
            accepted_statuses=(200, 401),
        )
        _wait_http_ready(
            f"{web_base}/login",
            proc=web_proc,
            log_path=web_log,
            timeout=90.0,
            accepted_statuses=(200,),
        )

        e2e_cfg = {
            "web_base": web_base,
            "ctrl_url": ctrl_url,
            "session_cookie_name": settings.session_cookie_name,
            "ws_a": str(ws_a),
            "ws_b": str(ws_b),
            "owner_a_session": owner_a_tok,
            "owner_a_name": owner_a_name,
            "admin_a_name": admin_a_name,
            "admin2_a_session": admin2_a_tok,
            "admin2_a_mid": mids_ws_a[admin2_a],
            "op_a_name": op_a_name,
            "op_a_mid": mids_ws_a[op_a],
            "dev_a_name": dev_a_name,
            "dev_a_mid": mids_ws_a[seed["dev_a"]],
            "dev_a_session": dev_a_tok,
            "promoted_owner_session": prom_owner_b_tok,
            "promoted_invitation_id": str(inv_b_id),
            "promoted_invitation_token": raw_invite_token,
        }

        pw_env = os.environ.copy()
        pw_env["NO_PROXY"] = "127.0.0.1,localhost"
        pw_env["no_proxy"] = "127.0.0.1,localhost"
        res = subprocess.run(
            [
                playwright_py,
                str(ROOT / "tests/e2e/run_playwright_rbac_e2e.py"),
                "--config",
                json.dumps(e2e_cfg),
            ],
            cwd=str(ROOT),
            env=pw_env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert res.returncode == 0, f"Playwright E2E failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        summary = json.loads(res.stdout.strip().splitlines()[-1])
        assert summary["status"] == "ok"
    finally:
        shim_srv.shutdown()
        shim_srv.server_close()
        for proc in (web_proc, api_proc):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        api_log_f.close()
        web_log_f.close()

