"""Domain service orchestrating scoped workspace member management (ADR-021 / Step 03-D)."""

from __future__ import annotations

from uuid import UUID

from control_api.modules.workspaces.dto import (
    RevokeMembershipRequestDTO,
    UpdateMembershipRoleRequestDTO,
    WorkspaceMembershipListResponseDTO,
    WorkspaceMembershipRecordDTO,
    WorkspaceMembershipRevokeResponseDTO,
    WorkspaceMembershipRoleUpdateResponseDTO,
)
from control_api.modules.workspaces.models.enums import MembershipStatus
from control_api.modules.workspaces.models.errors import MembershipForbiddenError
from control_api.modules.workspaces.models.ports import MembershipRepositoryPort
from control_api.modules.workspaces.models.rbac import (
    can_assign_target_role,
    can_list_workspace_members,
    can_revoke_target_role,
)
from platform_common.principal import AuthenticatedPrincipal


class MembershipService:
    """Enforces authoritative member-management RBAC policy backed by MembershipRepositoryPort."""

    def __init__(self, repository: MembershipRepositoryPort) -> None:
        self._repository = repository

    @staticmethod
    def _actor_role(principal: AuthenticatedPrincipal) -> str:
        return str(principal.roles[0]) if principal.roles else ""

    async def list_memberships(
        self,
        *,
        principal: AuthenticatedPrincipal,
        limit: int,
        cursor: str | None,
        status_filter: MembershipStatus | None,
    ) -> WorkspaceMembershipListResponseDTO:
        """Return paginated workspace memberships when principal holds active Owner/Admin role."""
        if not can_list_workspace_members(self._actor_role(principal)):
            raise MembershipForbiddenError(
                "membership_management_forbidden",
                "Only active workspace owners and admins can list members",
            )
        items, next_cursor = await self._repository.list_memberships(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            actor_user_id=UUID(principal.user_id),
            limit=limit,
            cursor=cursor,
            status_filter=status_filter,
        )
        return WorkspaceMembershipListResponseDTO(
            items=[WorkspaceMembershipRecordDTO.model_validate(item) for item in items],
            next_cursor=next_cursor,
        )

    async def update_member_role(
        self,
        *,
        principal: AuthenticatedPrincipal,
        membership_id: UUID,
        payload: UpdateMembershipRoleRequestDTO,
        request_id: str,
        trace_id: str,
    ) -> WorkspaceMembershipRoleUpdateResponseDTO:
        """Validate pre-check role matrix and atomically update member role."""
        actor_role = self._actor_role(principal)
        if not can_list_workspace_members(actor_role) or not can_assign_target_role(
            actor_role,
            payload.expected_role,
            payload.role,
        ):
            raise MembershipForbiddenError(
                "role_management_forbidden",
                "Actor role is not permitted to perform this role assignment",
            )
        record = await self._repository.update_member_role(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            actor_user_id=UUID(principal.user_id),
            membership_id=membership_id,
            new_role=payload.role,
            expected_role=payload.expected_role,
            expected_status=payload.expected_status,
            request_id=request_id,
            trace_id=trace_id,
        )
        return WorkspaceMembershipRoleUpdateResponseDTO.model_validate(record)

    async def revoke_member(
        self,
        *,
        principal: AuthenticatedPrincipal,
        membership_id: UUID,
        payload: RevokeMembershipRequestDTO,
        request_id: str,
        trace_id: str,
    ) -> WorkspaceMembershipRevokeResponseDTO:
        """Validate pre-check role matrix and atomically revoke workspace membership."""
        actor_role = self._actor_role(principal)
        if not can_list_workspace_members(actor_role) or not can_revoke_target_role(
            actor_role,
            payload.expected_role,
        ):
            raise MembershipForbiddenError(
                "role_management_forbidden",
                "Actor role is not permitted to revoke this member",
            )
        record = await self._repository.revoke_member(
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            actor_user_id=UUID(principal.user_id),
            membership_id=membership_id,
            expected_role=payload.expected_role,
            expected_status=payload.expected_status,
            request_id=request_id,
            trace_id=trace_id,
        )
        return WorkspaceMembershipRevokeResponseDTO.model_validate(record)


__all__ = ["MembershipService"]
