"""FastAPI Router for Workspace Bootstrap, Creation, Discovery, and Invitations (SPEC-WORKSPACE-001)."""

from __future__ import annotations

from uuid import UUID, uuid4

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.authorization import require_permission
from control_api.core.dependencies import (
    CurrentAuthenticatedUser,
    CurrentSessionWorkspace,
    CurrentUser,
    DBSession,
)
from control_api.modules.workspaces.dto import (
    CreateInvitationRequestDTO,
    CreateWorkspaceRequestDTO,
    WorkspaceInvitationListResponseDTO,
    WorkspaceInvitationResponseDTO,
    WorkspaceListResponseDTO,
    WorkspaceResponseDTO,
)
from control_api.modules.workspaces.models.errors import (
    InvitationConfigurationError,
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationNotFoundError,
    InvitationRateLimitError,
    InvitationValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.services.invitation_service import InvitationService
from control_api.modules.workspaces.services.workspace_service import workspace_service
from fastapi import APIRouter, HTTPException, Query, Request, status

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])
invitation_service = InvitationService()


def _translate_workspace_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkspaceAccessDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    if isinstance(exc, WorkspaceConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, WorkspaceDatabaseUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected workspace operation error",
    )


def _parse_invitation_uuid(invitation_id: str) -> UUID:
    try:
        return UUID(invitation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_invitation_id",
                "message": "invitation_id must be a valid UUID",
            },
        ) from exc


def _translate_invitation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvitationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationForbiddenError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, WorkspaceAccessDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "workspace_access_denied", "message": str(exc)},
        )
    if isinstance(exc, InvitationConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationRateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    if isinstance(exc, InvitationValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, InvitationConfigurationError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, WorkspaceDatabaseUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "invitation_store_unavailable", "message": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "invitation_unexpected_error",
            "message": "Unexpected workspace invitation error",
        },
    )


@router.post(
    "",
    response_model=WorkspaceResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    dto: CreateWorkspaceRequestDTO,
    request: Request,
    auth_user: CurrentAuthenticatedUser,
    session: DBSession,
) -> WorkspaceResponseDTO:
    """Provision a new workspace and its initial active Owner membership atomically (WS-D01, WS-D03)."""
    verify_csrf_or_origin(request)
    request_id = request.headers.get("x-request-id")
    trace_id = request.headers.get("x-trace-id")
    try:
        return await workspace_service.create_workspace(
            session=session,
            auth_user=auth_user,
            dto=dto,
            request_id=request_id,
            trace_id=trace_id,
        )
    except (
        WorkspaceAccessDeniedError,
        WorkspaceConflictError,
        WorkspaceDatabaseUnavailableError,
    ) as exc:
        raise _translate_workspace_error(exc) from exc


@router.get(
    "",
    response_model=WorkspaceListResponseDTO,
)
async def list_workspaces(
    auth_user: CurrentAuthenticatedUser,
    session: DBSession,
) -> WorkspaceListResponseDTO:
    """List active workspaces where the authenticated user holds an active membership."""
    try:
        return await workspace_service.list_user_workspaces(
            session=session,
            auth_user=auth_user,
        )
    except (
        WorkspaceAccessDeniedError,
        WorkspaceConflictError,
        WorkspaceDatabaseUnavailableError,
    ) as exc:
        raise _translate_workspace_error(exc) from exc


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponseDTO,
)
async def get_workspace(
    workspace_id: str,
    principal: CurrentUser,
    session: DBSession,
) -> WorkspaceResponseDTO:
    """Get details of an active workspace within the caller's authorized RLS scope."""
    _ = workspace_id
    require_permission(principal, "workspace:read")
    try:
        return await workspace_service.get_workspace(
            session=session,
            principal=principal,
        )
    except (
        WorkspaceAccessDeniedError,
        WorkspaceConflictError,
        WorkspaceDatabaseUnavailableError,
    ) as exc:
        raise _translate_workspace_error(exc) from exc


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
    session: DBSession,
) -> WorkspaceInvitationResponseDTO:
    """Create a pending workspace invitation and enqueue durable outbox delivery (ADR-019)."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    request_id = request.headers.get("x-request-id") or str(uuid4())
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        return await invitation_service.create_invitation(
            session=session,
            principal=ctx.principal,
            actor_issuer=ctx.session_user.issuer,
            payload=dto,
            request_id=request_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise _translate_invitation_error(exc) from exc


@router.get(
    "/{workspace_id}/invitations",
    response_model=WorkspaceInvitationListResponseDTO,
)
async def list_workspace_invitations(
    workspace_id: str,
    ctx: CurrentSessionWorkspace,
    session: DBSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> WorkspaceInvitationListResponseDTO:
    """List workspace invitations ordered newest-first with opaque keyset cursor."""
    _ = workspace_id
    require_permission(ctx.principal, "workspace:read")
    try:
        return await invitation_service.list_invitations(
            session=session,
            principal=ctx.principal,
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:
        raise _translate_invitation_error(exc) from exc


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
    session: DBSession,
) -> WorkspaceInvitationResponseDTO:
    """Rotate proof generation and queue a new outbox delivery for an active invitation."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    parsed_id = _parse_invitation_uuid(invitation_id)
    request_id = request.headers.get("x-request-id") or str(uuid4())
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        return await invitation_service.resend_invitation(
            session=session,
            principal=ctx.principal,
            invitation_id=parsed_id,
            request_id=request_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise _translate_invitation_error(exc) from exc


@router.post(
    "/{workspace_id}/invitations/{invitation_id}/revoke",
    response_model=WorkspaceInvitationResponseDTO,
)
async def revoke_workspace_invitation(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    ctx: CurrentSessionWorkspace,
    session: DBSession,
) -> WorkspaceInvitationResponseDTO:
    """Revoke a pending workspace invitation idempotently and purge temporary proof material."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(ctx.principal, "workspace:write")
    parsed_id = _parse_invitation_uuid(invitation_id)
    request_id = request.headers.get("x-request-id") or str(uuid4())
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        return await invitation_service.revoke_invitation(
            session=session,
            principal=ctx.principal,
            invitation_id=parsed_id,
            request_id=request_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise _translate_invitation_error(exc) from exc
