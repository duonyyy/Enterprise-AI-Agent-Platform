"""Standard SMTP delivery adapter for workspace invitation emails (ADR-019)."""

from __future__ import annotations

import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate
from typing import Literal, Protocol

from platform_common.invitations import InvitationEmailError, normalize_invitation_email

SmtpTlsMode = Literal["starttls", "smtps", "none"]
SmtpOutcomeKind = Literal[
    "provider_accepted",
    "transient_failure",
    "permanent_failure",
    "ambiguous_unknown",
]

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


class SmtpConfigurationError(ValueError):
    """Raised when SMTP settings fail security or completeness validation."""


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    """Validated configuration for outbound SMTP invitation delivery."""

    host: str
    port: int
    sender: str
    username: str = ""
    password: str = ""
    tls_mode: SmtpTlsMode = "starttls"
    timeout_seconds: float = 10.0
    wall_clock_deadline_seconds: float = 30.0
    allow_plaintext_local_smtp: bool = False
    ca_file: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        clean_host = self.host.strip()
        if not clean_host or any(ch in clean_host for ch in ("\r", "\n", "\x00")):
            raise SmtpConfigurationError("SMTP_HOST must be configured safely")
        if self.port < 1 or self.port > 65535:
            raise SmtpConfigurationError("SMTP_PORT must be between 1 and 65535")
        try:
            normalize_invitation_email(self.sender)
        except InvitationEmailError as exc:
            raise SmtpConfigurationError(f"Invalid SMTP_FROM address: {exc}") from exc
        if any(ch in self.username for ch in ("\r", "\n", "\x00")) or any(
            ch in self.password for ch in ("\r", "\n", "\x00")
        ):
            raise SmtpConfigurationError("SMTP credentials contain forbidden control characters")
        if self.tls_mode not in {"starttls", "smtps", "none"}:
            raise SmtpConfigurationError("SMTP_TLS_MODE must be 'starttls', 'smtps', or 'none'")
        if self.tls_mode == "none" and (
            not self.allow_plaintext_local_smtp or clean_host.lower() not in _LOOPBACK_HOSTS
        ):
            raise SmtpConfigurationError(
                "Plaintext SMTP is allowed only on loopback hosts when ALLOW_PLAINTEXT_LOCAL_SMTP=true"
            )
        if self.timeout_seconds <= 0 or self.timeout_seconds > 10.0:
            raise SmtpConfigurationError("SMTP_TIMEOUT_SECONDS must be > 0 and <= 10.0")
        if (
            self.wall_clock_deadline_seconds <= 0
            or self.wall_clock_deadline_seconds > 30.0
        ):
            raise SmtpConfigurationError("SMTP wall-clock deadline must be > 0 and <= 30.0")


@dataclass(frozen=True, slots=True)
class SmtpSendResult:
    """Typed outcome returned by an SMTP delivery attempt."""

    outcome: SmtpOutcomeKind
    error_code: str | None = None


class SmtpTransport(Protocol):
    """Port interface for sending a single invitation delivery attempt."""

    def send_invitation_email(
        self,
        *,
        recipient_email: str,
        accept_url: str,
        invitation_id: str,
        generation: int,
        outbox_event_id: str,
        workspace_id: str,
        expires_at_iso: str,
        role: str,
    ) -> SmtpSendResult:
        """Deliver one invitation message and classify the provider outcome."""


def _sanitize_header_value(value: str, field_name: str) -> str:
    if any(ch in value for ch in ("\r", "\n", "\x00")):
        raise ValueError(f"{field_name} contains forbidden CR/LF/NUL characters")
    return value


def build_invitation_message(
    *,
    sender: str,
    recipient_email: str,
    accept_url: str,
    invitation_id: str,
    generation: int,
    outbox_event_id: str,
    workspace_id: str,
    expires_at_iso: str,
    role: str,
) -> EmailMessage:
    """Build a sanitized RFC 5322 email message with a deterministic per-attempt Message-ID."""
    clean_sender = normalize_invitation_email(sender)
    clean_recipient = normalize_invitation_email(recipient_email)
    clean_inv_id = _sanitize_header_value(invitation_id, "invitation_id")
    clean_event_id = _sanitize_header_value(outbox_event_id, "outbox_event_id")
    clean_ws_id = _sanitize_header_value(workspace_id, "workspace_id")
    clean_role = _sanitize_header_value(role, "role")
    clean_exp = _sanitize_header_value(expires_at_iso, "expires_at")
    clean_url = _sanitize_header_value(accept_url, "accept_url")

    msg = EmailMessage()
    msg["From"] = clean_sender
    msg["To"] = clean_recipient
    msg["Subject"] = f"Workspace invitation ({clean_role})"
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = f"<inv.{clean_inv_id}.g{generation}.{clean_event_id}@platform.local>"
    msg["X-Workspace-ID"] = clean_ws_id
    msg["X-Invitation-ID"] = clean_inv_id
    msg["X-Invitation-Generation"] = str(generation)

    body = (
        "You have been invited to join an Enterprise Agent Platform workspace.\n\n"
        f"Workspace ID: {clean_ws_id}\n"
        f"Role: {clean_role}\n"
        f"Expires at: {clean_exp}\n\n"
        "Accept your invitation using the link below:\n"
        f"{clean_url}\n"
    )
    msg.set_content(body, subtype="plain", charset="utf-8", cte="7bit")
    return msg


def _classify_smtp_code(code: int, *, default_transient: str, default_permanent: str) -> SmtpSendResult:
    if 400 <= code < 500:
        return SmtpSendResult(outcome="transient_failure", error_code=default_transient)
    if 500 <= code < 600:
        return SmtpSendResult(outcome="permanent_failure", error_code=default_permanent)
    return SmtpSendResult(outcome="ambiguous_unknown", error_code="SMTP_UNEXPECTED_RESPONSE")


class StdlibSmtpTransport:
    """Production SMTP client using Python smtplib with explicit pre-DATA vs DATA outcome mapping."""

    def __init__(self, config: SmtpConfig) -> None:
        config.validate()
        self._config = config

    def _remaining_timeout(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SMTP wall-clock deadline exceeded")
        return min(self._config.timeout_seconds, remaining)

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=self._config.ca_file)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def send_invitation_email(
        self,
        *,
        recipient_email: str,
        accept_url: str,
        invitation_id: str,
        generation: int,
        outbox_event_id: str,
        workspace_id: str,
        expires_at_iso: str,
        role: str,
    ) -> SmtpSendResult:
        clean_recipient = normalize_invitation_email(recipient_email)
        clean_sender = normalize_invitation_email(self._config.sender)
        msg = build_invitation_message(
            sender=clean_sender,
            recipient_email=clean_recipient,
            accept_url=accept_url,
            invitation_id=invitation_id,
            generation=generation,
            outbox_event_id=outbox_event_id,
            workspace_id=workspace_id,
            expires_at_iso=expires_at_iso,
            role=role,
        )
        raw_bytes = msg.as_bytes()
        deadline = time.monotonic() + self._config.wall_clock_deadline_seconds

        server: smtplib.SMTP | None = None
        in_data_phase = False
        try:
            timeout = self._remaining_timeout(deadline)
            if self._config.tls_mode == "smtps":
                server = smtplib.SMTP_SSL(
                    host=self._config.host,
                    port=self._config.port,
                    timeout=timeout,
                    context=self._ssl_context(),
                )
            else:
                server = smtplib.SMTP(
                    host=self._config.host,
                    port=self._config.port,
                    timeout=timeout,
                )

            if server.sock is not None:
                server.sock.settimeout(self._remaining_timeout(deadline))

            ehlo_code, _ = server.ehlo()
            if not (200 <= ehlo_code < 300):
                return _classify_smtp_code(
                    ehlo_code,
                    default_transient="SMTP_EHLO_TRANSIENT",
                    default_permanent="SMTP_EHLO_REJECTED",
                )

            if self._config.tls_mode == "starttls":
                if not server.has_extn("starttls"):
                    return SmtpSendResult(
                        outcome="permanent_failure",
                        error_code="SMTP_STARTTLS_UNSUPPORTED",
                    )
                if server.sock is not None:
                    server.sock.settimeout(self._remaining_timeout(deadline))
                server.starttls(context=self._ssl_context())
                ehlo_tls_code, _ = server.ehlo()
                if not (200 <= ehlo_tls_code < 300):
                    return _classify_smtp_code(
                        ehlo_tls_code,
                        default_transient="SMTP_EHLO_TLS_TRANSIENT",
                        default_permanent="SMTP_EHLO_TLS_REJECTED",
                    )

            if self._config.username or self._config.password:
                if server.sock is not None:
                    server.sock.settimeout(self._remaining_timeout(deadline))
                server.login(self._config.username, self._config.password)

            if server.sock is not None:
                server.sock.settimeout(self._remaining_timeout(deadline))
            mail_code, _ = server.mail(clean_sender)
            if not (200 <= mail_code < 300):
                return _classify_smtp_code(
                    mail_code,
                    default_transient="SMTP_MAIL_FROM_TRANSIENT",
                    default_permanent="SMTP_MAIL_FROM_REJECTED",
                )

            if server.sock is not None:
                server.sock.settimeout(self._remaining_timeout(deadline))
            rcpt_code, _ = server.rcpt(clean_recipient)
            if rcpt_code not in {250, 251}:
                return _classify_smtp_code(
                    rcpt_code,
                    default_transient="SMTP_RCPT_TRANSIENT",
                    default_permanent="SMTP_RCPT_REJECTED",
                )

            if server.sock is not None:
                server.sock.settimeout(self._remaining_timeout(deadline))
            in_data_phase = True
            data_code, _ = server.data(raw_bytes)
            if data_code == 250:
                return SmtpSendResult(outcome="provider_accepted", error_code=None)
            return _classify_smtp_code(
                data_code,
                default_transient="SMTP_DATA_TRANSIENT",
                default_permanent="SMTP_DATA_REJECTED",
            )
        except smtplib.SMTPAuthenticationError:
            return SmtpSendResult(outcome="permanent_failure", error_code="SMTP_AUTH_FAILED")
        except smtplib.SMTPNotSupportedError:
            return SmtpSendResult(outcome="permanent_failure", error_code="SMTP_TLS_NOT_SUPPORTED")
        except (ssl.SSLCertVerificationError, ssl.SSLError):
            return SmtpSendResult(outcome="permanent_failure", error_code="SMTP_TLS_ERROR")
        except smtplib.SMTPRecipientsRefused:
            return SmtpSendResult(outcome="permanent_failure", error_code="SMTP_RCPT_REJECTED")
        except smtplib.SMTPSenderRefused as exc:
            return _classify_smtp_code(
                exc.smtp_code,
                default_transient="SMTP_MAIL_FROM_TRANSIENT",
                default_permanent="SMTP_MAIL_FROM_REJECTED",
            )
        except smtplib.SMTPDataError as exc:
            return _classify_smtp_code(
                exc.smtp_code,
                default_transient="SMTP_DATA_TRANSIENT",
                default_permanent="SMTP_DATA_REJECTED",
            )
        except smtplib.SMTPConnectError:
            return SmtpSendResult(outcome="transient_failure", error_code="SMTP_CONNECT_FAILED")
        except smtplib.SMTPResponseException as exc:
            return _classify_smtp_code(
                exc.smtp_code,
                default_transient="SMTP_TRANSIENT_ERROR",
                default_permanent="SMTP_PERMANENT_ERROR",
            )
        except (TimeoutError, smtplib.SMTPServerDisconnected, OSError):
            if in_data_phase:
                return SmtpSendResult(
                    outcome="ambiguous_unknown",
                    error_code="SMTP_AMBIGUOUS_DATA_OUTCOME",
                )
            return SmtpSendResult(outcome="transient_failure", error_code="SMTP_NETWORK_TRANSIENT")
        finally:
            if server is not None:
                try:
                    server.quit()
                except (smtplib.SMTPException, OSError):
                    try:
                        server.close()
                    except OSError:
                        pass

