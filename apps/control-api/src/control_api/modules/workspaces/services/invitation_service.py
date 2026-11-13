"""Domain service orchestrating workspace invitation lifecycle and policy checks."""

from __future__ import annotations

from uuid import UUID

from control_api.core.settings import settings
from control_api.modules.workspaces.dto import (
    CreateInvitationRequestDTO,
    WorkspaceInvitationListResponseDTO,
    WorkspaceInvitationResponseDTO,
)
from control_api.modules.workspaces.models.errors import (
    InvitationConfigurationError,
    InvitationForbiddenError,
    InvitationValidationError,
)
from control_api.modules.workspaces.models.ports import InvitationRepositoryPort
from control_api.modules.workspaces.models.rbac import (
    can_invite_target_role,
    can_manage_invitations,
)
from control_api.modules.workspaces.models.records import (
    InvitationPolicyConfig,
    InvitationRecord,
)
from platform_common.invitations import (
    InvitationCryptoError,
    InvitationEmailError,
    InvitationProofProtector,
    normalize_invitation_email,
    validate_public_app_origin,
)
from platform_common.principal import AuthenticatedPrincipal


class InvitationService:
    """Orchestrates workspace invitation create, list, resend, and revoke use cases."""

    def __init__(self, repository: InvitationRepositoryPort) -> None:
        self._repository = repository

    @staticmethod
    def _ensure_invitations_enabled() -> None:
        if not settings.invitations_enabled:
            raise InvitationConfigurationError(
                "invitation_service_unavailable",
                "Workspace invitations are currently disabled",
            )

    @staticmethod
    def _build_proof_protector() -> InvitationProofProtector:
        InvitationService._ensure_invitations_enabled()
        try:
            validate_public_app_origin(settings.invitation_public_app_origin)
            return InvitationProofProtector(
                fernet_key=settings.invitation_proof_encryption_key.get_secret_value(),
                key_id=settings.invitation_proof_key_id,
            )
        except InvitationCryptoError as exc:
            raise InvitationConfigurationError(
                "invitation_configuration_invalid",
                "Workspace invitation delivery configuration is incomplete or invalid",
            ) from exc

    @staticmethod
    def _policy() -> InvitationPolicyConfig:
        return InvitationPolicyConfig(
            ttl_hours=settings.invitation_ttl_hours,
            resend_cooldown_seconds=settings.invitation_resend_cooldown_seconds,
            recipient_hourly_limit=settings.invitation_recipient_hourly_limit,
            workspace_hourly_limit=settings.invitation_workspace_hourly_limit,
        )

    @staticmethod
    def _require_owner_or_admin(principal: AuthenticatedPrincipal) -> str:
        role = principal.roles[0] if principal.roles else ""
        if not can_manage_invitations(role):
            raise InvitationForbiddenError(
                "invitation_forbidden",
                "Only workspace owners and admins can manage invitations",
            )
        return role

    @staticmethod
    def _to_response_dto(record: InvitationRecord) -> WorkspaceInvitationResponseDTO:
        return WorkspaceInvitationResponseDTO.model_validate(record)

    async def create_invitation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        actor_issuer: str,
        payload: CreateInvitationRequestDTO,
        request_id: str,
        trace_id: str,
    ) -> WorkspaceInvitationResponseDTO:
        """Validate RBAC policy, encrypt invitation proof, and enqueue durable outbox delivery."""
        protector = self._build_proof_protector()
        actor_role = self._require_owner_or_admin(principal)
        allowed, err_code, err_msg = can_invite_target_role(actor_role, str(payload.role))
        if not allowed:
            raise InvitationForbiddenError(err_code, err_msg)
        try:
            normalized_email = normalize_invitation_email(payload.email)
        except InvitationEmailError as exc:
            raise InvitationValidationError("invalid_email", str(exc)) from exc

        if not actor_issuer or not actor_issuer.strip():
            raise InvitationConfigurationError(
                "invitation_issuer_missing",
                "Authenticated session is missing issuer identity",
            )

        record = await self._repository.create_invitation(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            actor_user_id=UUID(principal.user_id),
            recipient_email_normalized=normalized_email,
            recipient_issuer=actor_issuer.strip(),
            role=payload.role,
            protector=protector,
            policy=self._policy(),
            request_id=request_id,
            trace_id=trace_id,
        )
        return self._to_response_dto(record)

    async def list_invitations(
        self,
        *,
        principal: AuthenticatedPrincipal,
        limit: int,
        cursor: str | None,
    ) -> WorkspaceInvitationListResponseDTO:
        """List workspace invitations ordered newest-first with opaque keyset cursor."""
        self._ensure_invitations_enabled()
        self._require_owner_or_admin(principal)
        if limit < 1 or limit > 100:
            raise InvitationValidationError(
                "invalid_limit",
                "limit must be between 1 and 100",
            )
        items, next_cursor = await self._repository.list_invitations(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            actor_user_id=UUID(principal.user_id),
            limit=limit,
            cursor=cursor,
        )
        return WorkspaceInvitationListResponseDTO(
            items=[self._to_response_dto(item) for item in items],
            next_cursor=next_cursor,
        )

    async def resend_invitation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        invitation_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> WorkspaceInvitationResponseDTO:
        """Rotate proof generation and queue a new outbox delivery for an active invitation."""
        protector = self._build_proof_protector()
        self._require_owner_or_admin(principal)
        record = await self._repository.resend_invitation(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            invitation_id=invitation_id,
            actor_user_id=UUID(principal.user_id),
            protector=protector,
            policy=self._policy(),
            request_id=request_id,
            trace_id=trace_id,
        )
        return self._to_response_dto(record)

    async def revoke_invitation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        invitation_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> WorkspaceInvitationResponseDTO:
        """Revoke a pending workspace invitation idempotently and cancel queued delivery."""
        self._ensure_invitations_enabled()
        self._require_owner_or_admin(principal)
        record = await self._repository.revoke_invitation(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            invitation_id=invitation_id,
            actor_user_id=UUID(principal.user_id),
            request_id=request_id,
            trace_id=trace_id,
        )
        return self._to_response_dto(record)


__all__ = ["InvitationService"]
