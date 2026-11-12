"""Domain orchestration for encrypted invitation handoff, preview, and atomic acceptance (ADR-020)."""

from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from control_api.core.settings import settings
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.identity.models.session import SessionData
from control_api.modules.workspaces.dto import (
    InvitationAcceptResponseDTO,
    InvitationHandoffResponseDTO,
    InvitationPreviewResponseDTO,
)
from control_api.modules.workspaces.models.errors import (
    InvitationConfigurationError,
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationNotFoundError,
    InvitationRateLimitError,
)
from control_api.modules.workspaces.repositories.invitation_repository import (
    InvitationRepository,
)
from control_api.modules.workspaces.services import handoff_store as handoff_store_module
from control_api.modules.workspaces.services.handoff_store import (
    BaseInvitationHandoffStore,
    InvitationHandoffProtector,
    generate_browser_binding_token,
    hash_browser_binding_token,
    is_valid_browser_binding_token,
)
from platform_common.invitations import (
    InvitationEmailError,
    hash_invitation_proof,
    normalize_invitation_email,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VerifiedHandoffContext:
    """Resolved proof-hash and lifecycle state extracted from a browser-bound Redis handoff."""

    token_hash: str
    expires_at: int
    is_already_consumed: bool
    ciphertext: str = field(repr=False)


class InvitationAcceptanceService:
    """Orchestrates pre-auth encrypted handoff, verified preview, and atomic acceptance."""

    def __init__(
        self,
        repository: InvitationRepository | None = None,
        store: BaseInvitationHandoffStore | None = None,
    ) -> None:
        self._repository = repository or InvitationRepository()
        self._store_override = store

    @property
    def store(self) -> BaseInvitationHandoffStore:
        if self._store_override is not None:
            return self._store_override
        return handoff_store_module.invitation_handoff_store

    @staticmethod
    def _ensure_enabled() -> None:
        if not settings.invitations_enabled:
            raise InvitationConfigurationError(
                "invitation_service_unavailable",
                "Workspace invitations are currently disabled",
            )

    @staticmethod
    def _build_protector() -> InvitationHandoffProtector:
        InvitationAcceptanceService._ensure_enabled()
        handoff_key = settings.invitation_handoff_encryption_key.get_secret_value().strip()
        proof_key = settings.invitation_proof_encryption_key.get_secret_value().strip()
        if handoff_key and proof_key and hmac.compare_digest(handoff_key, proof_key):
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff encryption key must be distinct from the proof encryption key",
            )
        return InvitationHandoffProtector(
            fernet_key=handoff_key,
            key_id=settings.invitation_handoff_key_id,
        )

    @staticmethod
    def verify_session_identity_evidence(
        *,
        session_data: SessionData | None,
        issuer: str,
        subject: str,
        user_id: str,
        now: int | None = None,
    ) -> str:
        """Require active application session with fresh OIDC email_verified === True evidence."""
        if session_data is None:
            raise InvitationForbiddenError(
                "credential_type_not_allowed",
                "An active application session is required to view or accept invitations",
            )

        current = now if now is not None else int(time.time())
        evidence = session_data.identity_evidence
        if evidence is None or not evidence.matches_session(
            issuer=issuer,
            subject=subject,
            user_id=user_id,
        ):
            raise InvitationForbiddenError(
                "reauthentication_required",
                "Fresh identity verification is required; please sign in again",
            )

        if not evidence.is_fresh(
            current,
            max_age_seconds=settings.invitation_identity_freshness_seconds,
            max_future_skew_seconds=settings.invitation_identity_max_future_skew_seconds,
        ):
            raise InvitationForbiddenError(
                "reauthentication_required",
                "Identity verification window has expired; please sign in again",
            )

        if evidence.email_verified is not True or not evidence.email_normalized:
            raise InvitationForbiddenError(
                "recipient_email_unverified",
                "Your identity provider account does not have a verified email address",
            )

        try:
            return normalize_invitation_email(evidence.email_normalized)
        except InvitationEmailError as exc:
            raise InvitationForbiddenError(
                "recipient_email_unverified",
                "Authenticated account email is invalid or unverified",
            ) from exc

    def _resolve_handoff_context(
        self,
        *,
        protector: InvitationHandoffProtector,
        invitation_id: UUID,
        browser_cookie: str | None,
        user_id: str,
        now: int,
    ) -> VerifiedHandoffContext:
        if not is_valid_browser_binding_token(browser_cookie):
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or handoff session has expired",
            )
        assert browser_cookie is not None
        browser_digest = hash_browser_binding_token(browser_cookie)
        entry = self.store.get_handoff_entry(
            invitation_id=str(invitation_id),
            browser_binding_digest=browser_digest,
            now=now,
        )
        if entry is None:
            raise InvitationNotFoundError(
                "invitation_not_found",
                "Invitation not found or handoff session has expired",
            )

        kind = entry.get("kind")
        inv_id_str = str(invitation_id)
        if kind == "handoff":
            ciphertext = entry.get("ciphertext")
            if not isinstance(ciphertext, str) or not ciphertext:
                raise InvitationNotFoundError(
                    "invitation_not_found",
                    "Invitation not found or handoff session has expired",
                )
            decrypted = protector.decrypt_handoff(
                ciphertext=ciphertext,
                expected_invitation_id=inv_id_str,
                expected_browser_digest=browser_digest,
                now=now,
            )
            if decrypted is None:
                raise InvitationNotFoundError(
                    "invitation_not_found",
                    "Invitation not found or handoff session has expired",
                )
            return VerifiedHandoffContext(
                token_hash=decrypted.token_hash,
                expires_at=decrypted.expires_at,
                is_already_consumed=False,
                ciphertext=ciphertext,
            )

        if kind == "consumed_receipt":
            r_inv = entry.get("invitation_id")
            r_user = entry.get("user_id")
            r_hash = entry.get("token_hash")
            r_digest = entry.get("browser_binding_digest")
            r_exp = entry.get("expires_at")
            if (
                not isinstance(r_inv, str)
                or not isinstance(r_user, str)
                or not isinstance(r_hash, str)
                or not isinstance(r_digest, str)
                or type(r_exp) is not int
                or now >= r_exp
                or not hmac.compare_digest(r_inv, inv_id_str)
                or not hmac.compare_digest(r_digest, browser_digest)
                or not hmac.compare_digest(r_user, user_id)
            ):
                raise InvitationNotFoundError(
                    "invitation_not_found",
                    "Invitation not found or handoff session has expired",
                )
            return VerifiedHandoffContext(
                token_hash=r_hash,
                expires_at=r_exp,
                is_already_consumed=True,
                ciphertext="",
            )

        raise InvitationNotFoundError(
            "invitation_not_found",
            "Invitation not found or handoff session has expired",
        )

    def create_handoff(
        self,
        *,
        invitation_id: UUID,
        raw_token: str,
        existing_browser_cookie: str | None,
        client_ip: str,
        now: int | None = None,
    ) -> tuple[InvitationHandoffResponseDTO, str]:
        """Encrypt and persist pre-auth proof handoff in Redis bound to a 256-bit cookie."""
        protector = self._build_protector()
        current = now if now is not None else int(time.time())

        browser_token = (
            existing_browser_cookie
            if is_valid_browser_binding_token(existing_browser_cookie)
            else generate_browser_binding_token()
        )
        assert browser_token is not None
        browser_digest = hash_browser_binding_token(browser_token)

        retry_after = self.store.enforce_handoff_rate_limit(
            browser_binding_digest=browser_digest,
            client_ip=client_ip,
            browser_limit=settings.invitation_handoff_browser_rate_limit,
            ip_limit=settings.invitation_handoff_ip_rate_limit,
            window_seconds=settings.invitation_handoff_rate_window_seconds,
            now=current,
        )
        if retry_after is not None:
            raise InvitationRateLimitError(
                "handoff_rate_limited",
                "Too many invitation handoff attempts; please try again shortly",
                retry_after_seconds=retry_after,
            )

        ttl = settings.invitation_handoff_ttl_seconds
        expires_at = current + ttl
        token_hash = hash_invitation_proof(raw_token)
        ciphertext = protector.encrypt_handoff(
            invitation_id=str(invitation_id),
            raw_proof=raw_token,
            browser_binding_digest=browser_digest,
            issued_at=current,
            expires_at=expires_at,
        )
        persist_result = self.store.save_or_get_encrypted_handoff(
            invitation_id=str(invitation_id),
            browser_binding_digest=browser_digest,
            token_hash=token_hash,
            key_id=protector.key_id,
            ciphertext=ciphertext,
            expires_at=expires_at,
            ttl_seconds=ttl,
            now=current,
        )
        if persist_result.outcome == "conflict_consumed":
            raise InvitationConflictError(
                "invitation_already_accepted",
                "This invitation handoff has already been consumed",
            )
        expires_iso = datetime.fromtimestamp(persist_result.expires_at, tz=UTC).isoformat()
        return InvitationHandoffResponseDTO(expires_at=expires_iso), browser_token

    async def preview_invitation(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        session_data: SessionData | None,
        issuer: str,
        subject: str,
        user_id: str,
        browser_cookie: str | None,
        now: int | None = None,
    ) -> InvitationPreviewResponseDTO:
        """Verify fresh identity + browser handoff and return non-consuming preview metadata."""
        protector = self._build_protector()
        current = now if now is not None else int(time.time())
        email_normalized = self.verify_session_identity_evidence(
            session_data=session_data,
            issuer=issuer,
            subject=subject,
            user_id=user_id,
            now=current,
        )
        handoff_ctx = self._resolve_handoff_context(
            protector=protector,
            invitation_id=invitation_id,
            browser_cookie=browser_cookie,
            user_id=user_id,
            now=current,
        )
        record = await self._repository.preview_invitation(
            session,
            invitation_id=invitation_id,
            expected_token_hash=handoff_ctx.token_hash,
            recipient_user_id=UUID(user_id),
            recipient_issuer=issuer,
            recipient_subject=subject,
            recipient_email_normalized=email_normalized,
        )
        return InvitationPreviewResponseDTO.model_validate(record)

    async def accept_invitation(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        session_data: SessionData | None,
        issuer: str,
        subject: str,
        user_id: str,
        browser_cookie: str | None,
        request_id: str,
        trace_id: str,
        now: int | None = None,
    ) -> InvitationAcceptResponseDTO:
        """Atomically accept verified invitation, commit DB, then replace handoff ciphertext."""
        protector = self._build_protector()
        current = now if now is not None else int(time.time())
        email_normalized = self.verify_session_identity_evidence(
            session_data=session_data,
            issuer=issuer,
            subject=subject,
            user_id=user_id,
            now=current,
        )
        identity_deadline: int | None = None
        if session_data is not None and session_data.identity_evidence is not None:
            ev = session_data.identity_evidence
            identity_deadline = (
                min(ev.observed_at, ev.id_token_issued_at)
                + settings.invitation_identity_freshness_seconds
            )
        handoff_ctx = self._resolve_handoff_context(
            protector=protector,
            invitation_id=invitation_id,
            browser_cookie=browser_cookie,
            user_id=user_id,
            now=current,
        )
        result = await self._repository.accept_invitation(
            session,
            invitation_id=invitation_id,
            expected_token_hash=handoff_ctx.token_hash,
            recipient_user_id=UUID(user_id),
            recipient_issuer=issuer,
            recipient_subject=subject,
            recipient_email_normalized=email_normalized,
            request_id=request_id,
            trace_id=trace_id,
            identity_freshness_deadline=identity_deadline,
            handoff_expires_at=handoff_ctx.expires_at,
            enforce_wall_clock_deadlines=(now is None),
        )

        # Post-commit cleanup only: never consume handoff before PostgreSQL COMMIT succeeds.
        if not handoff_ctx.is_already_consumed and is_valid_browser_binding_token(browser_cookie):
            assert browser_cookie is not None
            browser_digest = hash_browser_binding_token(browser_cookie)
            try:
                self.store.convert_to_consumed_receipt(
                    invitation_id=str(invitation_id),
                    browser_binding_digest=browser_digest,
                    user_id=user_id,
                    token_hash=handoff_ctx.token_hash,
                    expected_handoff_ciphertext=handoff_ctx.ciphertext,
                    expires_at=handoff_ctx.expires_at,
                    now=int(time.time()),
                )
            except RepositoryUnavailableError as exc:
                logger.warning(
                    "Post-commit invitation handoff receipt conversion degraded for %s (%s)",
                    invitation_id,
                    type(exc).__name__,
                )

        workspace_id = str(result["workspace_id"])
        return InvitationAcceptResponseDTO.model_validate(
            {
                **result,
                "redirect_url": f"/w/{workspace_id}/agents",
            }
        )
