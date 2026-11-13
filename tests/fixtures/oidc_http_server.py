"""Live HTTP OIDC IdP Server Fixture (Step 02 / PR-02 / Gate G2 / D4-E).

Runs a real HTTP server on an ephemeral loopback socket (127.0.0.1:<port>)
serving OIDC Discovery, JWKS, and Token Exchange over real HTTP network transport.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode

from tests.fixtures.oidc_test_server import OidcTestServer


class _OidcRequestHandler(BaseHTTPRequestHandler):
    server: _OidcHttpServer

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress console logging in test output
        pass

    def do_GET(self) -> None:
        path, _, raw_query = self.path.partition("?")
        if path == "/protocol/openid-connect/auth":
            profile = getattr(self.server.oidc, "interactive_login_profile", None)
            if not isinstance(profile, dict):
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(raw_query)
            observed = getattr(self.server.oidc, "observed_auth_queries", None)
            if isinstance(observed, list):
                observed.append(dict(qs))
            redirect_uri = qs.get("redirect_uri", [""])[0]
            state = qs.get("state", [""])[0]
            nonce = qs.get("nonce", [""])[0]
            code_challenge = qs.get("code_challenge", [""])[0]
            client_id = qs.get("client_id", [""])[0]
            code = self.server.oidc.create_authorization_code(
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                nonce=nonce,
                code_challenge=code_challenge,
                subject=str(profile["subject"]),
                name=str(profile.get("name", "OIDC User")),
                email=str(profile["email"]),
                email_verified=profile.get("email_verified", True),
                iat_override=profile.get("iat_override"),
            )
            sep = "&" if "?" in redirect_uri else "?"
            target = f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        if path == "/protocol/openid-connect/certs":
            body = json.dumps(self.server.oidc.get_jwks()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/.well-known/openid-configuration":
            body = json.dumps(self.server.oidc.get_openid_configuration()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/protocol/openid-connect/token":
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8")
            parsed = parse_qs(raw_body)
            code = parsed.get("code", [""])[0]
            code_verifier = parsed.get("code_verifier", [""])[0]
            client_id = parsed.get("client_id", [""])[0]
            client_secret = parsed.get("client_secret", [""])[0]
            redirect_uri = parsed.get("redirect_uri", [""])[0]

            try:
                token_resp = self.server.oidc.exchange_code_for_token(
                    code=code,
                    code_verifier=code_verifier,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                )
                body = json.dumps(token_resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except ValueError as err:
                err_body = json.dumps(
                    {"error": "invalid_grant", "error_description": str(err)}
                ).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
        else:
            self.send_response(404)
            self.end_headers()


class _OidcHttpServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], oidc: OidcTestServer) -> None:
        super().__init__(server_address, _OidcRequestHandler)
        self.oidc = oidc


@contextlib.contextmanager
def run_oidc_http_server() -> Generator[tuple[OidcTestServer, str], None, None]:
    """Start an ephemeral HTTP OIDC IdP server on 127.0.0.1 for live network tests."""
    dummy_oidc = OidcTestServer(issuer="http://127.0.0.1:0")
    httpd = _OidcHttpServer(("127.0.0.1", 0), dummy_oidc)
    port = httpd.server_address[1]
    issuer = f"http://127.0.0.1:{port}"
    dummy_oidc.issuer = issuer

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield dummy_oidc, issuer
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)
