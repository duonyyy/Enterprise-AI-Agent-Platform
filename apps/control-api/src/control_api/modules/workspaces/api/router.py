"""FastAPI Router for Workspace Bootstrap, Creation, Discovery, and Invitations (SPEC-WORKSPACE-001)."""

from __future__ import annotations

from typing import Annotated

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.authorization import require_permission
from control_api.core.dependencies import (
    CurrentAuthenticatedUser,
    CurrentSessionWorkspace,
    CurrentUser,
    DBSession,
)
from control_api.core.errors import RepositoryUnavailableError
from control_api.modules.workspaces.api._http_helpers import (
    extract_correlation_ids,
    parse_uuid_path_param,
    translate_domain_error,
)
from control_api.modules.workspaces.dto import (
    CreateInvitationRequestDTO,
    CreateWorkspaceRequestDTO,
    RevokeMembershipRequestDTO,
    UpdateMembershipRoleRequestDTO,
    WorkspaceInvitationListResponseDTO,
    WorkspaceInvitationResponseDTO,
    WorkspaceListResponseDTO,
    WorkspaceMembershipListResponseDTO,
    WorkspaceMembershipRevokeResponseDTO,
    WorkspaceMembershipRoleUpdateResponseDTO,
    WorkspaceResponseDTO,
)
from control_api.modules.workspaces.models.enums import MembershipStatus
from control_api.modules.workspaces.models.errors import (
    InvitationError,
    MembershipError,
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.repositories.invitation_repository import InvitationRepository
from control_api.modules.workspaces.repositories.membership_repository import MembershipRepository
from control_api.modules.workspaces.repositories.workspace_repository import workspace_repository
from control_api.modules.workspaces.services.invitation_service import InvitationService
from control_api.modules.workspaces.services.membership_service import MembershipService
from control_api.modules.workspaces.services.workspace_service import WorkspaceService
from fastapi import APIRouter, Depends, Query, Request, Response, status

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])
invitation_repository = InvitationRepository()
membership_repository = MembershipRepository()


def get_workspace_service(session: DBSession) -> WorkspaceService:
    """Resolve the session-bound workspace service for FastAPI dependency injection."""
    return WorkspaceService(workspace_repository.bind(session))


def get_invitation_service(session: DBSession) -> InvitationService:
    """Resolve the session-bound invitation service for FastAPI dependency injection."""
    return InvitationService(invitation_repository.bind(session))


def get_membership_service(session: DBSession) -> MembershipService:
    """Resolve the session-bound membership service for FastAPI dependency injection."""
    return MembershipService(membership_repository.bind(session))


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
InvitationServiceDep = Annotated[InvitationService, Depends(get_invitation_service)]
MembershipServiceDep = Annotated[MembershipService, Depends(get_membership_service)]


@router.post(
    "",
    response_model=WorkspaceResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    dto: CreateWorkspaceRequestDTO,
    request: Request,
    auth_user: CurrentAuthenticatedUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponseDTO:
    """Provision a new workspace and its initial active Owner membership atomically (WS-D01, WS-D03)."""
    verify_csrf_or_origin(request)
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.create_workspace(
            auth_user=auth_user,
            dto=dto,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (
        WorkspaceAccessDeniedError,
        WorkspaceConflictError,
        WorkspaceDatabaseUnavailableError,
        RepositoryUnavailableError,
    ) as exc:
        raise translate_domain_error(
            exc, scope="workspace", request_id=request_id, trace_id=trace_id
        ) from exc


@router.get(
    "",
    response_model=WorkspaceListResponseDTO,
)
async def list_workspaces(
    request: Request,
    auth_user: CurrentAuthenticatedUser,
    service: WorkspaceServiceDep,
) -> WorkspaceListResponseDTO:
    """List all active workspaces where the authenticated user holds an active membership."""
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.list_user_workspaces(auth_user=auth_user)
    except (
        WorkspaceAccessDeniedError,
        WorkspaceConflictError,
        WorkspaceDatabaseUnavailableError,
        RepositoryUnavailableError,
    ) as exc:
        raise translate_domain_error(
            exc, scope="workspace", request_id=request_id, trace_id=trace_id
        ) from exc


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponseDTO,
)
async def get_workspace(
    workspace_id: str,
    request: Request,
    principal: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponseDTO:
    """Get details of an active workspace within the caller's authorized RLS scope."""
    _ = workspace_id
    require_permission(principal, "workspace:read")
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.get_workspace(principal=principal)
    except (
        WorkspaceAccessDeniedError,
        WorkspaceConflictError,
        WorkspaceDatabaseUnavailableError,
        RepositoryUnavailableError,
    ) as exc:
        raise translate_domain_error(
            exc, scope="workspace", request_id=request_id, trace_id=trace_id
        ) from exc


@router.post(
    "/{workspace_id}/invitations",
    response_model=WorkspaceInvitationResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_invitation(
    workspace_id: str,
    dto: CreateInvitationRequestDTO,
    request: Request,
    ctx: CurrentSessionWorkspace,
    service: InvitationServiceDep,
) -> WorkspaceInvitationResponseDTO:
    """Create a pending workspace invitation and enqueue durable outbox delivery (ADR-019)."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.create_invitation(
            principal=ctx.principal,
            actor_issuer=ctx.session_user.issuer,
            payload=dto,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="invitation", request_id=request_id, trace_id=trace_id
        ) from exc


@router.get(
    "/{workspace_id}/invitations",
    response_model=WorkspaceInvitationListResponseDTO,
)
async def list_workspace_invitations(
    workspace_id: str,
    request: Request,
    ctx: CurrentSessionWorkspace,
    service: InvitationServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> WorkspaceInvitationListResponseDTO:
    """List workspace invitations ordered newest-first with opaque keyset cursor."""
    _ = workspace_id
    require_permission(ctx.principal, "workspace:read")
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.list_invitations(
            principal=ctx.principal,
            limit=limit,
            cursor=cursor,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="invitation", request_id=request_id, trace_id=trace_id
        ) from exc


@router.post(
    "/{workspace_id}/invitations/{invitation_id}/resend",
    response_model=WorkspaceInvitationResponseDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_workspace_invitation(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    ctx: CurrentSessionWorkspace,
    service: InvitationServiceDep,
) -> WorkspaceInvitationResponseDTO:
    """Rotate proof generation and queue a new outbox delivery for an active invitation."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    parsed_id = parse_uuid_path_param(
        invitation_id,
        code="invalid_invitation_id",
        message="invitation_id must be a valid UUID",
    )
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.resend_invitation(
            principal=ctx.principal,
            invitation_id=parsed_id,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="invitation", request_id=request_id, trace_id=trace_id
        ) from exc


@router.post(
    "/{workspace_id}/invitations/{invitation_id}/revoke",
    response_model=WorkspaceInvitationResponseDTO,
)
async def revoke_workspace_invitation(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    ctx: CurrentSessionWorkspace,
    service: InvitationServiceDep,
) -> WorkspaceInvitationResponseDTO:
    """Revoke a pending workspace invitation idempotently and purge temporary proof material."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    parsed_id = parse_uuid_path_param(
        invitation_id,
        code="invalid_invitation_id",
        message="invitation_id must be a valid UUID",
    )
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.revoke_invitation(
            principal=ctx.principal,
            invitation_id=parsed_id,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (InvitationError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="invitation", request_id=request_id, trace_id=trace_id
        ) from exc


@router.get(
    "/{workspace_id}/memberships",
    response_model=WorkspaceMembershipListResponseDTO,
)
async def list_workspace_memberships(
    workspace_id: str,
    request: Request,
    response: Response,
    ctx: CurrentSessionWorkspace,
    service: MembershipServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    status_filter: Annotated[MembershipStatus | None, Query(alias="status")] = None,
) -> WorkspaceMembershipListResponseDTO:
    """List scoped workspace memberships with cursor pagination (ADR-021)."""
    _ = workspace_id
    response.headers["Cache-Control"] = "no-store"
    require_permission(ctx.principal, "workspace:read")
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.list_memberships(
            principal=ctx.principal,
            limit=limit,
            cursor=cursor,
            status_filter=status_filter,
        )
    except (MembershipError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="membership", request_id=request_id, trace_id=trace_id
        ) from exc


@router.patch(
    "/{workspace_id}/memberships/{membership_id}/role",
    response_model=WorkspaceMembershipRoleUpdateResponseDTO,
)
async def update_workspace_membership_role(
    workspace_id: str,
    membership_id: str,
    dto: UpdateMembershipRoleRequestDTO,
    request: Request,
    response: Response,
    ctx: CurrentSessionWorkspace,
    service: MembershipServiceDep,
) -> WorkspaceMembershipRoleUpdateResponseDTO:
    """Atomically change an active workspace member's role with last-owner protection (ADR-021)."""
    _ = workspace_id
    response.headers["Cache-Control"] = "no-store"
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    parsed_id = parse_uuid_path_param(
        membership_id,
        code="invalid_membership_id",
        message="membership_id must be a valid UUID",
    )
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.update_member_role(
            principal=ctx.principal,
            membership_id=parsed_id,
            payload=dto,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (MembershipError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="membership", request_id=request_id, trace_id=trace_id
        ) from exc


@router.post(
    "/{workspace_id}/memberships/{membership_id}/revoke",
    response_model=WorkspaceMembershipRevokeResponseDTO,
)
async def revoke_workspace_membership(
    workspace_id: str,
    membership_id: str,
    dto: RevokeMembershipRequestDTO,
    request: Request,
    response: Response,
    ctx: CurrentSessionWorkspace,
    service: MembershipServiceDep,
) -> WorkspaceMembershipRevokeResponseDTO:
    """Atomically revoke an active workspace member's access with last-owner protection (ADR-021)."""
    _ = workspace_id
    response.headers["Cache-Control"] = "no-store"
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    parsed_id = parse_uuid_path_param(
        membership_id,
        code="invalid_membership_id",
        message="membership_id must be a valid UUID",
    )
    request_id, trace_id = extract_correlation_ids(request)
    try:
        return await service.revoke_member(
            principal=ctx.principal,
            membership_id=parsed_id,
            payload=dto,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (MembershipError, WorkspaceAccessDeniedError, RepositoryUnavailableError) as exc:
        raise translate_domain_error(
            exc, scope="membership", request_id=request_id, trace_id=trace_id
        ) from exc
