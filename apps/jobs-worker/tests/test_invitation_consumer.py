"""Unit and real-TCP-socket tests for SMTP adapter and scoped invitation consumer."""

from __future__ import annotations

import socket
import threading
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from jobs_worker.invitation_consumer import InvitationOutboxConsumer
from jobs_worker.main import main as worker_main
from jobs_worker.smtp_adapter import (
    SmtpConfig,
    SmtpConfigurationError,
    StdlibSmtpTransport,
    build_invitation_message,
)
from platform_common.invitations import InvitationProofProtector


class _FakeLoopbackSmtpServer:
    """Minimal RFC 5321 loopback TCP SMTP server to exercise real StdlibSmtpTransport sockets."""

    def __init__(self, behavior: str = "accept") -> None:
        self.behavior = behavior
        self.received_data: bytes = b""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
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
            with conn:
                conn.settimeout(2.0)
                conn.sendall(b"220 loopback.smtp ESMTP\r\n")
                buffer = b""
                reading_data = False
                while not self._stop.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    buffer += chunk
                    if reading_data:
                        if b"\r\n.\r\n" in buffer:
                            msg_part, _rest = buffer.split(b"\r\n.\r\n", 1)
                            self.received_data = msg_part
                            if self.behavior == "drop_after_data":
                                conn.close()
                                break
                            if self.behavior == "reject_data_550":
                                conn.sendall(b"550 5.7.1 Message rejected\r\n")
                            else:
                                conn.sendall(b"250 2.0.0 OK queued\r\n")
                            reading_data = False
                            buffer = b""
                        continue

                    while b"\r\n" in buffer:
                        line, buffer = buffer.split(b"\r\n", 1)
                        cmd = line.decode("ascii", errors="replace")
                        upper = cmd.upper()
                        if upper.startswith("EHLO"):
                            if self.behavior == "no_starttls":
                                conn.sendall(b"250 loopback.smtp\r\n")
                            else:
                                conn.sendall(b"250-loopback.smtp\r\n250 PIPELINING\r\n")
                        elif upper.startswith("MAIL FROM:"):
                            conn.sendall(b"250 2.1.0 OK\r\n")
                        elif upper.startswith("RCPT TO:"):
                            if self.behavior == "transient_421":
                                conn.sendall(b"421 4.3.2 Temporary rate limit\r\n")
                            elif self.behavior == "reject_rcpt_550":
                                conn.sendall(b"550 5.1.1 Mailbox unavailable\r\n")
                            else:
                                conn.sendall(b"250 2.1.5 OK\r\n")
                        elif upper == "DATA":
                            conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                            reading_data = True
                            break
                        elif upper == "QUIT":
                            conn.sendall(b"221 Bye\r\n")
                            return
                        else:
                            conn.sendall(b"502 Command not implemented\r\n")

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=1.0)


def test_smtp_config_rejects_unsafe_plaintext_or_oversized_timeouts():
    with pytest.raises(SmtpConfigurationError):
        SmtpConfig(
            host="smtp.example.com",
            port=25,
            sender="no-reply@example.com",
            tls_mode="none",
            allow_plaintext_local_smtp=True,
        )

    with pytest.raises(SmtpConfigurationError):
        SmtpConfig(
            host="127.0.0.1",
            port=1025,
            sender="no-reply@example.com",
            tls_mode="none",
            allow_plaintext_local_smtp=False,
        )

    with pytest.raises(SmtpConfigurationError):
        SmtpConfig(
            host="smtp.example.com",
            port=587,
            sender="no-reply@example.com",
            timeout_seconds=15.0,
        )


def test_stdlib_smtp_transport_accept_transient_permanent_and_ambiguous_outcomes():
    inv_id = str(uuid4())
    event_id = str(uuid4())
    ws_id = str(uuid4())

    # 1. Accepted (250 after DATA)
    srv_ok = _FakeLoopbackSmtpServer("accept")
    try:
        transport = StdlibSmtpTransport(
            SmtpConfig(
                host="127.0.0.1",
                port=srv_ok.port,
                sender="no-reply@example.com",
                tls_mode="none",
                allow_plaintext_local_smtp=True,
            )
        )
        res = transport.send_invitation_email(
            recipient_email="invitee@example.com",
            accept_url=f"http://localhost:3000/invitations/{inv_id}/accept#token=proof-xyz",
            invitation_id=inv_id,
            generation=1,
            outbox_event_id=event_id,
            workspace_id=ws_id,
            expires_at_iso="2026-11-15T00:00:00Z",
            role="developer",
        )
        assert res.outcome == "provider_accepted"
        assert res.error_code is None
        assert f"<inv.{inv_id}.g1.{event_id}@platform.local>".encode("ascii") in srv_ok.received_data
    finally:
        srv_ok.close()

    # 2. Pre-DATA 421 -> transient_failure (retryable)
    srv_421 = _FakeLoopbackSmtpServer("transient_421")
    try:
        transport = StdlibSmtpTransport(
            SmtpConfig(
                host="127.0.0.1",
                port=srv_421.port,
                sender="no-reply@example.com",
                tls_mode="none",
                allow_plaintext_local_smtp=True,
            )
        )
        res = transport.send_invitation_email(
            recipient_email="invitee@example.com",
            accept_url=f"http://localhost:3000/invitations/{inv_id}/accept#token=proof-xyz",
            invitation_id=inv_id,
            generation=1,
            outbox_event_id=event_id,
            workspace_id=ws_id,
            expires_at_iso="2026-11-15T00:00:00Z",
            role="developer",
        )
        assert res.outcome == "transient_failure"
    finally:
        srv_421.close()

    # 3. 550 recipient rejected -> permanent_failure
    srv_550 = _FakeLoopbackSmtpServer("reject_rcpt_550")
    try:
        transport = StdlibSmtpTransport(
            SmtpConfig(
                host="127.0.0.1",
                port=srv_550.port,
                sender="no-reply@example.com",
                tls_mode="none",
                allow_plaintext_local_smtp=True,
            )
        )
        res = transport.send_invitation_email(
            recipient_email="invitee@example.com",
            accept_url=f"http://localhost:3000/invitations/{inv_id}/accept#token=proof-xyz",
            invitation_id=inv_id,
            generation=1,
            outbox_event_id=event_id,
            workspace_id=ws_id,
            expires_at_iso="2026-11-15T00:00:00Z",
            role="developer",
        )
        assert res.outcome == "permanent_failure"
        assert res.error_code == "SMTP_RCPT_REJECTED"
    finally:
        srv_550.close()

    # 4. Drop after DATA dot -> ambiguous_unknown (no blind retry)
    srv_drop = _FakeLoopbackSmtpServer("drop_after_data")
    try:
        transport = StdlibSmtpTransport(
            SmtpConfig(
                host="127.0.0.1",
                port=srv_drop.port,
                sender="no-reply@example.com",
                tls_mode="none",
                allow_plaintext_local_smtp=True,
            )
        )
        res = transport.send_invitation_email(
            recipient_email="invitee@example.com",
            accept_url=f"http://localhost:3000/invitations/{inv_id}/accept#token=proof-xyz",
            invitation_id=inv_id,
            generation=1,
            outbox_event_id=event_id,
            workspace_id=ws_id,
            expires_at_iso="2026-11-15T00:00:00Z",
            role="developer",
        )
        assert res.outcome == "ambiguous_unknown"
        assert res.error_code == "SMTP_AMBIGUOUS_DATA_OUTCOME"
    finally:
        srv_drop.close()

    # 5. STARTTLS unsupported -> fail closed (no downgrade)
    srv_no_tls = _FakeLoopbackSmtpServer("no_starttls")
    try:
        transport = StdlibSmtpTransport(
            SmtpConfig(
                host="127.0.0.1",
                port=srv_no_tls.port,
                sender="no-reply@example.com",
                tls_mode="starttls",
            )
        )
        res = transport.send_invitation_email(
            recipient_email="invitee@example.com",
            accept_url=f"http://localhost:3000/invitations/{inv_id}/accept#token=proof-xyz",
            invitation_id=inv_id,
            generation=1,
            outbox_event_id=event_id,
            workspace_id=ws_id,
            expires_at_iso="2026-11-15T00:00:00Z",
            role="developer",
        )
        assert res.outcome == "permanent_failure"
        assert res.error_code == "SMTP_STARTTLS_UNSUPPORTED"
    finally:
        srv_no_tls.close()


def test_build_invitation_message_rejects_header_injection():
    with pytest.raises(ValueError):
        build_invitation_message(
            sender="no-reply@example.com",
            recipient_email="invitee@example.com",
            accept_url="http://localhost:3000/invitations/x/accept#token=abc\r\nBcc: evil@example.com",
            invitation_id=str(uuid4()),
            generation=1,
            outbox_event_id=str(uuid4()),
            workspace_id=str(uuid4()),
            expires_at_iso="2026-11-15T00:00:00Z",
            role="developer",
        )


def test_consumer_and_cli_fail_closed_without_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INVITATIONS_ENABLED", "false")
    with pytest.raises(RuntimeError, match="INVITATIONS_ENABLED"):
        worker_main(["--mode", "invitations", "--once"])

    with pytest.raises(ValueError, match="PostgreSQL"):
        InvitationOutboxConsumer(
            database_url="sqlite:///:memory:",
            tenant_id=str(uuid4()),
            workspace_id=str(uuid4()),
            protector=InvitationProofProtector(Fernet.generate_key().decode("ascii")),
            public_app_origin="http://localhost:3000",
            smtp_transport=StdlibSmtpTransport(
                SmtpConfig(
                    host="127.0.0.1",
                    port=1025,
                    sender="no-reply@example.com",
                    tls_mode="none",
                    allow_plaintext_local_smtp=True,
                )
            ),
        )

