"""FastAPI Router for Workspace Bootstrap, Creation, and Discovery (SPEC-WORKSPACE-001 / Step 03-A)."""

from __future__ import annotations

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.authorization import require_permission
from control_api.core.dependencies import CurrentAuthenticatedUser, CurrentUser, DBSession
from control_api.modules.workspaces.dto import (
    CreateWorkspaceRequestDTO,
    WorkspaceListResponseDTO,
    WorkspaceResponseDTO,
)
from control_api.modules.workspaces.models.errors import (
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.services.workspace_service import workspace_service
from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


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

