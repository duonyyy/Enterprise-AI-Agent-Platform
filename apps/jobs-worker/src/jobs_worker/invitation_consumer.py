"""Scoped transactional outbox consumer for workspace invitation email delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from platform_common.invitations import (
    INVITATION_AGGREGATE_TYPE,
    INVITATION_CONSUMER_KEY,
    INVITATION_DELIVERY_EVENT_TYPE,
    InvitationCryptoError,
    InvitationDeliveryRequestedV1,
    InvitationProofBinding,
    InvitationProofProtector,
    build_invitation_accept_url,
    validate_public_app_origin,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from jobs_worker.smtp_adapter import (
    SmtpConfig,
    SmtpSendResult,
    SmtpTlsMode,
    SmtpTransport,
    StdlibSmtpTransport,
)

logger = logging.getLogger("jobs_worker.invitations")


def _normalize_sync_pg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql+asyncpg://")
    if url.startswith(("postgresql://", "postgresql+psycopg://")):
        return url
    raise ValueError("InvitationOutboxConsumer requires a PostgreSQL DATABASE_URL")


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ClaimedInvitationDelivery:
    """In-memory snapshot of one claimed outbox invitation delivery lease."""

    outbox_event_id: str
    attempt: int
    invitation_id: str
    tenant_id: str
    workspace_id: str
    generation: int
    recipient_email_normalized: str
    recipient_issuer: str
    role: str
    token_hash: str
    proof_ciphertext: str
    proof_key_id: str
    lease_token: str
    expires_at: datetime


class InvitationOutboxConsumer:
    """Polls and processes scoped ``workspace.invitation.delivery_requested.v1`` outbox events."""

    def __init__(
        self,
        database_url: str,
        tenant_id: str,
        workspace_id: str,
        *,
        protector: InvitationProofProtector,
        public_app_origin: str,
        smtp_transport: SmtpTransport,
        worker_id: str | None = None,
        lease_duration_seconds: int = 60,
        retry_delays_seconds: tuple[int, ...] = (30, 120),
        max_attempts: int = 3,
        engine: Engine | None = None,
    ) -> None:
        sync_url = _normalize_sync_pg_url(database_url)
        self.tenant_id = str(UUID(tenant_id))
        self.workspace_id = str(UUID(workspace_id))
        self.worker_id = worker_id or f"inv-worker-{uuid.uuid4().hex[:12]}"
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.lease_duration_seconds = lease_duration_seconds
        self.retry_delays_seconds = retry_delays_seconds
        self.max_attempts = max_attempts
        self._protector = protector
        self._public_app_origin = validate_public_app_origin(public_app_origin)
        self._smtp_transport = smtp_transport
        self._engine = engine or create_engine(sync_url, pool_pre_ping=True)
        self._running = False

    @classmethod
    def from_environment(cls) -> InvitationOutboxConsumer:
        """Build a configured scoped invitation consumer from environment variables."""
        encryption_key = os.environ.get("INVITATION_PROOF_ENCRYPTION_KEY", "")
        key_id = os.environ.get("INVITATION_PROOF_KEY_ID", "v1")
        protector = InvitationProofProtector(fernet_key=encryption_key, key_id=key_id)
        origin = os.environ.get("INVITATION_PUBLIC_APP_ORIGIN", "http://localhost:3000")
        tls_raw = os.environ.get("SMTP_TLS_MODE", "starttls").strip().lower()
        tls_mode: SmtpTlsMode = tls_raw if tls_raw in {"starttls", "smtps", "none"} else "starttls"  # type: ignore[assignment]
        if tls_raw not in {"starttls", "smtps", "none"}:
            raise ValueError("SMTP_TLS_MODE must be 'starttls', 'smtps', or 'none'")
        smtp_config = SmtpConfig(
            host=os.environ.get("SMTP_HOST", ""),
            port=int(os.environ.get("SMTP_PORT", "587")),
            sender=os.environ.get("SMTP_FROM", ""),
            username=os.environ.get("SMTP_USERNAME", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            tls_mode=tls_mode,
            timeout_seconds=float(os.environ.get("SMTP_TIMEOUT_SECONDS", "10.0")),
            wall_clock_deadline_seconds=float(
                os.environ.get("SMTP_WALL_CLOCK_DEADLINE_SECONDS", "30.0")
            ),
            allow_plaintext_local_smtp=_parse_bool_env("ALLOW_PLAINTEXT_LOCAL_SMTP", default=False),
            ca_file=os.environ.get("SMTP_CA_FILE") or None,
        )
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            tenant_id=os.environ.get("WORKER_TENANT_ID", ""),
            workspace_id=os.environ.get("WORKER_WORKSPACE_ID", ""),
            protector=protector,
            public_app_origin=origin,
            smtp_transport=StdlibSmtpTransport(smtp_config),
        )

    def _set_scope(self, connection: Connection) -> None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": self.tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
            {"workspace_id": self.workspace_id},
        )

    @staticmethod
    def _mark_outbox_dead(connection: Connection, outbox_event_id: str) -> None:
        connection.execute(
            text(
                "UPDATE integration.outbox_events "
                "SET state = 'dead' "
                "WHERE id = CAST(:event_id AS uuid) AND state IN ('pending', 'delivering')"
            ),
            {"event_id": outbox_event_id},
        )

    def reclaim_expired_leases(self) -> int:
        """Fail-closed cleanup for expired in-flight sending leases and expired pending rows."""
        reclaimed = 0
        with self._engine.begin() as connection:
            self._set_scope(connection)
            expired_leases = connection.execute(
                text(
                    "UPDATE iam.workspace_invitations "
                    "SET delivery_state = 'unknown', "
                    "    delivery_error_code = 'LEASE_EXPIRED_UNKNOWN', "
                    "    lease_token = NULL, "
                    "    leased_until = NULL, "
                    "    proof_ciphertext = NULL, "
                    "    proof_key_id = NULL, "
                    "    updated_at = now() "
                    "WHERE id IN ("
                    "    SELECT id FROM iam.workspace_invitations "
                    "    WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "      AND workspace_id = CAST(:workspace_id AS uuid) "
                    "      AND delivery_state = 'sending' "
                    "      AND leased_until < now() "
                    "    FOR UPDATE SKIP LOCKED"
                    ") "
                    "RETURNING id"
                ),
                {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id},
            ).fetchall()

            for row in expired_leases:
                inv_id = str(row._mapping["id"])
                connection.execute(
                    text(
                        "UPDATE integration.outbox_events "
                        "SET state = 'dead' "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "  AND workspace_id = CAST(:workspace_id AS uuid) "
                        "  AND aggregate_type = :aggregate_type "
                        "  AND aggregate_id = CAST(:invitation_id AS uuid) "
                        "  AND state IN ('pending', 'delivering')"
                    ),
                    {
                        "tenant_id": self.tenant_id,
                        "workspace_id": self.workspace_id,
                        "aggregate_type": INVITATION_AGGREGATE_TYPE,
                        "invitation_id": inv_id,
                    },
                )
                reclaimed += 1

            expired_pending = connection.execute(
                text(
                    "UPDATE iam.workspace_invitations "
                    "SET status = 'expired', "
                    "    proof_ciphertext = NULL, "
                    "    proof_key_id = NULL, "
                    "    lease_token = NULL, "
                    "    leased_until = NULL, "
                    "    delivery_state = CASE "
                    "        WHEN delivery_state = 'queued' THEN 'cancelled' "
                    "        ELSE delivery_state "
                    "    END, "
                    "    updated_at = now() "
                    "WHERE id IN ("
                    "    SELECT id FROM iam.workspace_invitations "
                    "    WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "      AND workspace_id = CAST(:workspace_id AS uuid) "
                    "      AND status = 'pending' "
                    "      AND expires_at <= now() "
                    "      AND delivery_state <> 'sending' "
                    "    FOR UPDATE SKIP LOCKED"
                    ") "
                    "RETURNING id"
                ),
                {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id},
            ).fetchall()

            for row in expired_pending:
                inv_id = str(row._mapping["id"])
                connection.execute(
                    text(
                        "UPDATE integration.outbox_events "
                        "SET state = 'dead' "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "  AND workspace_id = CAST(:workspace_id AS uuid) "
                        "  AND aggregate_type = :aggregate_type "
                        "  AND aggregate_id = CAST(:invitation_id AS uuid) "
                        "  AND state = 'pending'"
                    ),
                    {
                        "tenant_id": self.tenant_id,
                        "workspace_id": self.workspace_id,
                        "aggregate_type": INVITATION_AGGREGATE_TYPE,
                        "invitation_id": inv_id,
                    },
                )
        return reclaimed

    def _claim_next_outbox_delivery(self) -> tuple[bool, ClaimedInvitationDelivery | None]:
        """Short DB transaction claiming one pending outbox invitation event via SKIP LOCKED.

        Returns ``(had_candidate, claimed_delivery)``.
        """
        with self._engine.begin() as connection:
            self._set_scope(connection)
            outbox_row = connection.execute(
                text(
                    "SELECT id, aggregate_id, payload, attempt "
                    "FROM integration.outbox_events "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  AND event_type = :event_type "
                    "  AND state = 'pending' "
                    "  AND available_at <= now() "
                    "ORDER BY available_at, created_at, id "
                    "FOR UPDATE SKIP LOCKED "
                    "LIMIT 1"
                ),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "event_type": INVITATION_DELIVERY_EVENT_TYPE,
                },
            ).mappings().one_or_none()

            if outbox_row is None:
                return False, None

            event_id = str(outbox_row["id"])
            existing_receipt = connection.execute(
                text(
                    "SELECT 1 FROM integration.inbox_receipts "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  AND consumer_key = :consumer_key "
                    "  AND event_id = CAST(:event_id AS uuid) "
                    "LIMIT 1"
                ),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "consumer_key": INVITATION_CONSUMER_KEY,
                    "event_id": event_id,
                },
            ).first()
            if existing_receipt is not None:
                connection.execute(
                    text(
                        "UPDATE integration.outbox_events "
                        "SET state = 'delivered', delivered_at = COALESCE(delivered_at, now()) "
                        "WHERE id = CAST(:event_id AS uuid)"
                    ),
                    {"event_id": event_id},
                )
                return True, None

            envelope = outbox_row["payload"]
            raw_data = envelope.get("data") if isinstance(envelope, dict) else None
            try:
                evt = InvitationDeliveryRequestedV1.model_validate(raw_data)
            except ValidationError:
                self._mark_outbox_dead(connection, event_id)
                return True, None

            if (
                str(evt.tenant_id) != self.tenant_id
                or str(evt.workspace_id) != self.workspace_id
                or str(evt.invitation_id) != str(outbox_row["aggregate_id"])
            ):
                self._mark_outbox_dead(connection, event_id)
                return True, None

            ws_row = connection.execute(
                text(
                    "SELECT w.status AS workspace_status, t.status AS tenant_status "
                    "FROM iam.workspaces AS w "
                    "JOIN iam.tenants AS t ON t.id = w.tenant_id "
                    "WHERE w.tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND w.id = CAST(:workspace_id AS uuid) "
                    "FOR SHARE"
                ),
                {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id},
            ).mappings().one_or_none()

            inv_row = connection.execute(
                text(
                    "SELECT id, tenant_id, workspace_id, recipient_email_normalized, "
                    "       recipient_issuer, role, status, (expires_at <= now()) AS is_expired, "
                    "       expires_at, generation, token_hash, proof_ciphertext, proof_key_id, "
                    "       delivery_state, delivery_requested_by_user_id "
                    "FROM iam.workspace_invitations "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  AND id = CAST(:invitation_id AS uuid) "
                    "FOR UPDATE"
                ),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "invitation_id": str(evt.invitation_id),
                },
            ).mappings().one_or_none()

            if inv_row is None:
                self._mark_outbox_dead(connection, event_id)
                return True, None

            requester_row = connection.execute(
                text(
                    "SELECT role, status "
                    "FROM iam.memberships "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  AND user_id = CAST(:requester_id AS uuid) "
                    "FOR SHARE"
                ),
                {
                    "tenant_id": self.tenant_id,
                    "workspace_id": self.workspace_id,
                    "requester_id": str(inv_row["delivery_requested_by_user_id"]),
                },
            ).mappings().one_or_none()

            scope_active = (
                ws_row is not None
                and str(ws_row["workspace_status"]) == "active"
                and str(ws_row["tenant_status"]) == "active"
            )
            requester_authorized = (
                requester_row is not None
                and str(requester_row["status"]) == "active"
                and (
                    str(requester_row["role"]) == "owner"
                    or (
                        str(requester_row["role"]) == "admin"
                        and str(inv_row["role"]) != "admin"
                    )
                )
            )
            if not scope_active or not requester_authorized:
                self._mark_outbox_dead(connection, event_id)
                if (
                    str(inv_row["status"]) == "pending"
                    and int(inv_row["generation"]) == evt.generation
                    and str(inv_row["delivery_state"]) == "queued"
                ):
                    connection.execute(
                        text(
                            "UPDATE iam.workspace_invitations "
                            "SET delivery_state = 'cancelled', "
                            "    delivery_error_code = 'REQUESTER_UNAUTHORIZED', "
                            "    proof_ciphertext = NULL, "
                            "    proof_key_id = NULL, "
                            "    updated_at = now() "
                            "WHERE id = CAST(:invitation_id AS uuid)"
                        ),
                        {"invitation_id": str(evt.invitation_id)},
                    )
                return True, None

            if (
                str(inv_row["status"]) != "pending"
                or int(inv_row["generation"]) != evt.generation
                or str(inv_row["delivery_state"]) != "queued"
                or inv_row["proof_ciphertext"] is None
                or inv_row["proof_key_id"] is None
            ):
                self._mark_outbox_dead(connection, event_id)
                if str(inv_row["status"]) != "pending" and inv_row["proof_ciphertext"] is not None:
                    connection.execute(
                        text(
                            "UPDATE iam.workspace_invitations "
                            "SET proof_ciphertext = NULL, "
                            "    proof_key_id = NULL, "
                            "    lease_token = NULL, "
                            "    leased_until = NULL, "
                            "    updated_at = now() "
                            "WHERE id = CAST(:invitation_id AS uuid)"
                        ),
                        {"invitation_id": str(evt.invitation_id)},
                    )
                return True, None

            if bool(inv_row["is_expired"]):
                connection.execute(
                    text(
                        "UPDATE iam.workspace_invitations "
                        "SET status = 'expired', "
                        "    delivery_state = 'cancelled', "
                        "    proof_ciphertext = NULL, "
                        "    proof_key_id = NULL, "
                        "    lease_token = NULL, "
                        "    leased_until = NULL, "
                        "    updated_at = now() "
                        "WHERE id = CAST(:invitation_id AS uuid)"
                    ),
                    {"invitation_id": str(evt.invitation_id)},
                )
                self._mark_outbox_dead(connection, event_id)
                return True, None

            lease_token = str(uuid.uuid4())
            connection.execute(
                text(
                    "UPDATE iam.workspace_invitations "
                    "SET delivery_state = 'sending', "
                    "    lease_token = CAST(:lease_token AS uuid), "
                    "    leased_until = now() + make_interval(secs => :lease_seconds), "
                    "    updated_at = now() "
                    "WHERE id = CAST(:invitation_id AS uuid)"
                ),
                {
                    "invitation_id": str(evt.invitation_id),
                    "lease_token": lease_token,
                    "lease_seconds": self.lease_duration_seconds,
                },
            )
            attempt_row = connection.execute(
                text(
                    "UPDATE integration.outbox_events "
                    "SET state = 'delivering', attempt = attempt + 1 "
                    "WHERE id = CAST(:event_id AS uuid) "
                    "RETURNING attempt"
                ),
                {"event_id": event_id},
            ).mappings().one()

            return True, ClaimedInvitationDelivery(
                outbox_event_id=event_id,
                attempt=int(attempt_row["attempt"]),
                invitation_id=str(inv_row["id"]),
                tenant_id=str(inv_row["tenant_id"]),
                workspace_id=str(inv_row["workspace_id"]),
                generation=int(inv_row["generation"]),
                recipient_email_normalized=str(inv_row["recipient_email_normalized"]),
                recipient_issuer=str(inv_row["recipient_issuer"]),
                role=str(inv_row["role"]),
                token_hash=str(inv_row["token_hash"]),
                proof_ciphertext=str(inv_row["proof_ciphertext"]),
                proof_key_id=str(inv_row["proof_key_id"]),
                lease_token=lease_token,
                expires_at=inv_row["expires_at"],
            )

    def _is_lease_still_current_before_smtp(self, claim: ClaimedInvitationDelivery) -> bool:
        """Brief pre-dispatch read to avoid sending SMTP if revoked, resent, or expired before connect."""
        with self._engine.begin() as connection:
            self._set_scope(connection)
            row = connection.execute(
                text(
                    "SELECT 1 "
                    "FROM iam.workspace_invitations "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  AND id = CAST(:invitation_id AS uuid) "
                    "  AND status = 'pending' "
                    "  AND expires_at > now() "
                    "  AND generation = :generation "
                    "  AND delivery_state = 'sending' "
                    "  AND lease_token = CAST(:lease_token AS uuid) "
                    "  AND leased_until >= now()"
                ),
                {
                    "tenant_id": claim.tenant_id,
                    "workspace_id": claim.workspace_id,
                    "invitation_id": claim.invitation_id,
                    "generation": claim.generation,
                    "lease_token": claim.lease_token,
                },
            ).first()
            if row is not None:
                return True
            connection.execute(
                text(
                    "UPDATE integration.outbox_events "
                    "SET state = 'dead' "
                    "WHERE id = CAST(:event_id AS uuid) AND state = 'delivering'"
                ),
                {"event_id": claim.outbox_event_id},
            )
            return False

    def _retry_delay_for_attempt(self, attempt: int) -> int | None:
        if attempt >= self.max_attempts:
            return None
        idx = min(attempt - 1, len(self.retry_delays_seconds) - 1)
        return max(1, int(self.retry_delays_seconds[idx]))

    def _complete_delivery_cas(
        self,
        claim: ClaimedInvitationDelivery,
        result: SmtpSendResult,
    ) -> None:
        """CAS-fenced completion updating invitation delivery state, outbox, and inbox_receipts."""
        with self._engine.begin() as connection:
            self._set_scope(connection)
            base_params = {
                "tenant_id": claim.tenant_id,
                "workspace_id": claim.workspace_id,
                "invitation_id": claim.invitation_id,
                "generation": claim.generation,
                "lease_token": claim.lease_token,
                "error_code": result.error_code,
            }

            if result.outcome == "provider_accepted":
                cas = connection.execute(
                    text(
                        "UPDATE iam.workspace_invitations "
                        "SET delivery_state = 'provider_accepted', "
                        "    provider_accepted_at = now(), "
                        "    delivery_error_code = NULL, "
                        "    lease_token = NULL, "
                        "    leased_until = NULL, "
                        "    proof_ciphertext = NULL, "
                        "    proof_key_id = NULL, "
                        "    updated_at = now() "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "  AND workspace_id = CAST(:workspace_id AS uuid) "
                        "  AND id = CAST(:invitation_id AS uuid) "
                        "  AND status = 'pending' "
                        "  AND generation = :generation "
                        "  AND delivery_state = 'sending' "
                        "  AND lease_token = CAST(:lease_token AS uuid) "
                        "  AND leased_until >= now()"
                    ),
                    base_params,
                )
                if cas.rowcount == 1:
                    connection.execute(
                        text(
                            "UPDATE integration.outbox_events "
                            "SET state = 'delivered', delivered_at = now() "
                            "WHERE id = CAST(:event_id AS uuid) AND state = 'delivering'"
                        ),
                        {"event_id": claim.outbox_event_id},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO integration.inbox_receipts "
                            "    (tenant_id, workspace_id, consumer_key, event_id) "
                            "VALUES ("
                            "    CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), "
                            "    :consumer_key, CAST(:event_id AS uuid)"
                            ") ON CONFLICT DO NOTHING"
                        ),
                        {
                            "tenant_id": claim.tenant_id,
                            "workspace_id": claim.workspace_id,
                            "consumer_key": INVITATION_CONSUMER_KEY,
                            "event_id": claim.outbox_event_id,
                        },
                    )
                else:
                    self._mark_outbox_dead(connection, claim.outbox_event_id)
                return

            if result.outcome == "transient_failure":
                delay_seconds = self._retry_delay_for_attempt(claim.attempt)
                if delay_seconds is not None:
                    retry_cas = connection.execute(
                        text(
                            "UPDATE iam.workspace_invitations "
                            "SET delivery_state = 'queued', "
                            "    delivery_error_code = :error_code, "
                            "    lease_token = NULL, "
                            "    leased_until = NULL, "
                            "    updated_at = now() "
                            "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                            "  AND workspace_id = CAST(:workspace_id AS uuid) "
                            "  AND id = CAST(:invitation_id AS uuid) "
                            "  AND status = 'pending' "
                            "  AND generation = :generation "
                            "  AND delivery_state = 'sending' "
                            "  AND lease_token = CAST(:lease_token AS uuid) "
                            "  AND leased_until >= now() "
                            "  AND now() + make_interval(secs => :delay_seconds) < expires_at"
                        ),
                        {**base_params, "delay_seconds": delay_seconds},
                    )
                    if retry_cas.rowcount == 1:
                        connection.execute(
                            text(
                                "UPDATE integration.outbox_events "
                                "SET state = 'pending', "
                                "    available_at = now() + make_interval(secs => :delay_seconds) "
                                "WHERE id = CAST(:event_id AS uuid) AND state = 'delivering'"
                            ),
                            {"event_id": claim.outbox_event_id, "delay_seconds": delay_seconds},
                        )
                        return

                terminal_cas = connection.execute(
                    text(
                        "UPDATE iam.workspace_invitations "
                        "SET delivery_state = 'failed', "
                        "    delivery_error_code = :error_code, "
                        "    lease_token = NULL, "
                        "    leased_until = NULL, "
                        "    proof_ciphertext = NULL, "
                        "    proof_key_id = NULL, "
                        "    updated_at = now() "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "  AND workspace_id = CAST(:workspace_id AS uuid) "
                        "  AND id = CAST(:invitation_id AS uuid) "
                        "  AND status = 'pending' "
                        "  AND generation = :generation "
                        "  AND delivery_state = 'sending' "
                        "  AND lease_token = CAST(:lease_token AS uuid) "
                        "  AND leased_until >= now()"
                    ),
                    base_params,
                )
                if terminal_cas.rowcount == 1 or terminal_cas.rowcount == 0:
                    self._mark_outbox_dead(connection, claim.outbox_event_id)
                return

            final_delivery_state = (
                "unknown" if result.outcome == "ambiguous_unknown" else "failed"
            )
            connection.execute(
                text(
                    "UPDATE iam.workspace_invitations "
                    "SET delivery_state = :delivery_state, "
                    "    delivery_error_code = :error_code, "
                    "    lease_token = NULL, "
                    "    leased_until = NULL, "
                    "    proof_ciphertext = NULL, "
                    "    proof_key_id = NULL, "
                    "    updated_at = now() "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "  AND workspace_id = CAST(:workspace_id AS uuid) "
                    "  AND id = CAST(:invitation_id AS uuid) "
                    "  AND status = 'pending' "
                    "  AND generation = :generation "
                    "  AND delivery_state = 'sending' "
                    "  AND lease_token = CAST(:lease_token AS uuid) "
                    "  AND leased_until >= now()"
                ),
                {**base_params, "delivery_state": final_delivery_state},
            )
            self._mark_outbox_dead(connection, claim.outbox_event_id)

    def _dispatch_claimed_delivery(self, claim: ClaimedInvitationDelivery) -> None:
        binding = InvitationProofBinding(
            invitation_id=claim.invitation_id,
            tenant_id=claim.tenant_id,
            workspace_id=claim.workspace_id,
            generation=claim.generation,
            recipient_issuer=claim.recipient_issuer,
            recipient_email_normalized=claim.recipient_email_normalized,
            role=claim.role,
        )
        try:
            proof = self._protector.decrypt_and_verify_proof(
                claim.proof_ciphertext,
                claim.proof_key_id,
                binding,
                expected_token_hash=claim.token_hash,
            )
            accept_url = build_invitation_accept_url(
                self._public_app_origin,
                claim.invitation_id,
                proof,
            )
        except InvitationCryptoError:
            logger.warning(
                "Invitation proof integrity verification failed",
                extra={"invitation_id": claim.invitation_id, "generation": claim.generation},
            )
            self._complete_delivery_cas(
                claim,
                SmtpSendResult(outcome="permanent_failure", error_code="PROOF_INTEGRITY_ERROR"),
            )
            return

        if not self._is_lease_still_current_before_smtp(claim):
            return

        expires_at_iso = (
            claim.expires_at.astimezone(UTC).isoformat()
            if isinstance(claim.expires_at, datetime)
            else str(claim.expires_at)
        )
        try:
            send_result = self._smtp_transport.send_invitation_email(
                recipient_email=claim.recipient_email_normalized,
                accept_url=accept_url,
                invitation_id=claim.invitation_id,
                generation=claim.generation,
                outbox_event_id=claim.outbox_event_id,
                workspace_id=claim.workspace_id,
                expires_at_iso=expires_at_iso,
                role=claim.role,
            )
        except Exception as exc:  # noqa: BLE001 - treat unexpected transport exceptions as ambiguous outcome
            logger.warning(
                "Unexpected SMTP adapter error during invitation dispatch (%s)",
                type(exc).__name__,
                extra={"invitation_id": claim.invitation_id, "generation": claim.generation},
            )
            send_result = SmtpSendResult(
                outcome="ambiguous_unknown",
                error_code="SMTP_UNEXPECTED_ADAPTER_ERROR",
            )

        self._complete_delivery_cas(claim, send_result)

    def process_next_event(self) -> bool:
        """Reclaim expired state if needed, claim one due outbox event, and dispatch SMTP."""
        had_candidate, claim = self._claim_next_outbox_delivery()
        if claim is not None:
            self._dispatch_claimed_delivery(claim)
        return had_candidate

    def run_once(self) -> int:
        """Process all currently due events once and return processed count."""
        self.reclaim_expired_leases()
        processed = 0
        while self.process_next_event():
            processed += 1
        return processed

    async def run_forever(self, poll_interval_seconds: float = 1.0) -> None:
        """Poll for invitation outbox deliveries until stopped."""
        self._running = True
        while self._running:
            await asyncio.to_thread(self.reclaim_expired_leases)
            while self._running:
                handled = await asyncio.to_thread(self.process_next_event)
                if not handled:
                    break
            if self._running:
                await asyncio.sleep(poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

