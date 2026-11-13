"""Transactional PostgreSQL repository facade for workspace invitations (ADR-019 / ADR-020)."""

from __future__ import annotations

from uuid import UUID

from control_api.core.database import AsyncSessionLocal
from control_api.modules.workspaces.models.acceptance_policy import evaluate_invitation_acceptance
from control_api.modules.workspaces.models.enums import InvitableRole
from control_api.modules.workspaces.models.ports import InvitationRepositoryPort
from control_api.modules.workspaces.models.records import (
    InvitationAcceptRecord,
    InvitationPolicyConfig,
    InvitationPreviewRecord,
    InvitationRecord,
)
from control_api.modules.workspaces.repositories._invitation_acceptance import (
    accept_invitation_op,
    preview_invitation_op,
)
from control_api.modules.workspaces.repositories._invitation_admin import (
    create_invitation_op,
    list_invitations_op,
    resend_invitation_op,
    revoke_invitation_op,
)
from control_api.modules.workspaces.repositories._invitation_shared import (
    decode_invitation_cursor,
    encode_invitation_cursor,
)
from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.invitations import InvitationProofProtector
from sqlalchemy.ext.asyncio import AsyncSession


class InvitationRepository:
    """Atomic transactional repository for workspace invitations under strict RLS and lock ordering."""

    _evaluate_invitation_acceptance = staticmethod(evaluate_invitation_acceptance)

    def __init__(self, outbox_bus: PostgresOutboxEventBus | None = None) -> None:
        self._outbox_bus = outbox_bus or PostgresOutboxEventBus(AsyncSessionLocal)

    async def create_invitation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        recipient_email_normalized: str,
        recipient_issuer: str,
        role: str,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord:
        """Create a pending invitation inside the caller's active database transaction."""
        return await create_invitation_op(
            session,
            outbox_bus=self._outbox_bus,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            recipient_email_normalized=recipient_email_normalized,
            recipient_issuer=recipient_issuer,
            role=role,
            protector=protector,
            policy=policy,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def list_invitations(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[InvitationRecord], str | None]:
        """Return paginated workspace invitations within the actor's active transaction."""
        return await list_invitations_op(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            limit=limit,
            cursor=cursor,
        )

    async def resend_invitation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord:
        """Rotate proof generation and enqueue fresh outbox delivery."""
        return await resend_invitation_op(
            session,
            outbox_bus=self._outbox_bus,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            actor_user_id=actor_user_id,
            protector=protector,
            policy=policy,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def revoke_invitation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord:
        """Idempotently revoke a pending invitation and dead-letter pending outbox events."""
        return await revoke_invitation_op(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def preview_invitation(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
    ) -> InvitationPreviewRecord:
        """Preview verified invitation details strictly within RLS."""
        return await preview_invitation_op(
            session,
            invitation_id=invitation_id,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_subject=recipient_subject,
            recipient_email_normalized=recipient_email_normalized,
        )

    async def accept_invitation(
        self,
        session: AsyncSession,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
        request_id: str,
        trace_id: str,
        identity_freshness_deadline: int | None = None,
        handoff_expires_at: int | None = None,
        enforce_wall_clock_deadlines: bool = True,
    ) -> InvitationAcceptRecord:
        """Atomically activate workspace membership, consume invitation, dead-letter outbox, and audit."""
        return await accept_invitation_op(
            session,
            invitation_id=invitation_id,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_subject=recipient_subject,
            recipient_email_normalized=recipient_email_normalized,
            request_id=request_id,
            trace_id=trace_id,
            identity_freshness_deadline=identity_freshness_deadline,
            handoff_expires_at=handoff_expires_at,
            enforce_wall_clock_deadlines=enforce_wall_clock_deadlines,
        )

    def bind(self, session: AsyncSession) -> InvitationRepositoryPort:
        """Bind an AsyncSession into a session-free InvitationRepositoryPort adapter."""
        return SessionBoundInvitationRepository(repository=self, session=session)


class SessionBoundInvitationRepository:
    """Session-scoped adapter implementing InvitationRepositoryPort for pure services."""

    def __init__(self, repository: InvitationRepository, session: AsyncSession) -> None:
        self._repository = repository
        self._session = session

    async def create_invitation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        recipient_email_normalized: str,
        recipient_issuer: str,
        role: InvitableRole | str,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord:
        return await self._repository.create_invitation(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            recipient_email_normalized=recipient_email_normalized,
            recipient_issuer=recipient_issuer,
            role=str(role),
            protector=protector,
            policy=policy,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def list_invitations(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[InvitationRecord], str | None]:
        return await self._repository.list_invitations(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            limit=limit,
            cursor=cursor,
        )

    async def resend_invitation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        protector: InvitationProofProtector,
        policy: InvitationPolicyConfig,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord:
        return await self._repository.resend_invitation(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            actor_user_id=actor_user_id,
            protector=protector,
            policy=policy,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def revoke_invitation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        invitation_id: UUID,
        actor_user_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> InvitationRecord:
        return await self._repository.revoke_invitation(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def preview_invitation(
        self,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_email_normalized: str,
        recipient_subject: str | None = None,
    ) -> InvitationPreviewRecord:
        return await self._repository.preview_invitation(
            self._session,
            invitation_id=invitation_id,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_subject=recipient_subject or "",
            recipient_email_normalized=recipient_email_normalized,
        )

    async def accept_invitation(
        self,
        *,
        invitation_id: UUID,
        expected_token_hash: str,
        recipient_user_id: UUID,
        recipient_issuer: str,
        recipient_subject: str,
        recipient_email_normalized: str,
        request_id: str,
        trace_id: str,
        identity_freshness_deadline: int | None = None,
        handoff_expires_at: int | None = None,
        enforce_wall_clock_deadlines: bool = True,
    ) -> InvitationAcceptRecord:
        return await self._repository.accept_invitation(
            self._session,
            invitation_id=invitation_id,
            expected_token_hash=expected_token_hash,
            recipient_user_id=recipient_user_id,
            recipient_issuer=recipient_issuer,
            recipient_subject=recipient_subject,
            recipient_email_normalized=recipient_email_normalized,
            request_id=request_id,
            trace_id=trace_id,
            identity_freshness_deadline=identity_freshness_deadline,
            handoff_expires_at=handoff_expires_at,
            enforce_wall_clock_deadlines=enforce_wall_clock_deadlines,
        )

    async def commit(self) -> None:
        """Commit the underlying SQLAlchemy transaction when active."""
        import inspect

        in_tx = self._session.in_transaction()
        if inspect.isawaitable(in_tx):
            in_tx = await in_tx
        if in_tx:
            await self._session.commit()


__all__ = [
    "InvitationPolicyConfig",
    "InvitationRepository",
    "SessionBoundInvitationRepository",
    "decode_invitation_cursor",
    "encode_invitation_cursor",
]
