"""FastAPI Router for Model Registry CRUD (SPEC-LLM-001, ADR-005, DB-56)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.authorization import require_permission
from control_api.core.dependencies import CurrentUser, DBSession
from control_api.modules.models.api._http_helpers import (
    extract_correlation_ids,
    parse_uuid_param,
    translate_model_error,
)
from control_api.modules.models.api.dependencies import ModelServiceDep
from control_api.modules.models.dto.requests import (
    CreateModelConfigDTO,
    UpdateModelConfigDTO,
)
from control_api.modules.models.dto.responses import (
    ModelConfigDTO,
    ModelConfigPageDTO,
)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/models", tags=["models"])


@router.get("", response_model=ModelConfigPageDTO)
async def list_models(
    workspace_id: str,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: ModelServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    status: Annotated[str | None, Query(pattern="^(active|disabled)$")] = None,
) -> ModelConfigPageDTO:
    """List model configurations in workspace under RLS."""
    response.headers["Cache-Control"] = "no-store"
    require_permission(principal, "model:read")
    ws_uuid = parse_uuid_param(workspace_id, "workspace_id")

    t_uuid = UUID(str(principal.tenant_id))

    try:
        return await service.list_model_configs(
            session,
            tenant_id=t_uuid,
            workspace_id=ws_uuid,
            limit=limit,
            cursor=cursor,
            status_filter=status,
        )
    except Exception as exc:
        raise translate_model_error(exc) from exc


@router.post("", response_model=ModelConfigDTO, status_code=status.HTTP_201_CREATED)
async def create_model(
    workspace_id: str,
    payload: CreateModelConfigDTO,
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: ModelServiceDep,
) -> ModelConfigDTO:
    """Create a new model configuration in workspace with transactional audit log."""
    response.headers["Cache-Control"] = "no-store"
    require_permission(principal, "model:create")
    verify_csrf_or_origin(request)
    ws_uuid = parse_uuid_param(workspace_id, "workspace_id")
    req_id, trace_id = extract_correlation_ids(request)

    t_uuid = UUID(str(principal.tenant_id))

    try:
        return await service.create_model_config(
            session,
            tenant_id=t_uuid,
            workspace_id=ws_uuid,
            actor_subject=str(principal.user_id),
            dto=payload,
            request_id=req_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise translate_model_error(exc) from exc


@router.get("/{model_id}", response_model=ModelConfigDTO)
async def get_model(
    workspace_id: str,
    model_id: str,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: ModelServiceDep,
) -> ModelConfigDTO:
    """Get a single model configuration by ID."""
    response.headers["Cache-Control"] = "no-store"
    require_permission(principal, "model:read")
    ws_uuid = parse_uuid_param(workspace_id, "workspace_id")
    m_uuid = parse_uuid_param(model_id, "model_id")

    t_uuid = UUID(str(principal.tenant_id))

    try:
        return await service.get_model_config(
            session,
            tenant_id=t_uuid,
            workspace_id=ws_uuid,
            model_id=m_uuid,
        )
    except Exception as exc:
        raise translate_model_error(exc) from exc


@router.patch("/{model_id}", response_model=ModelConfigDTO)
async def update_model(
    workspace_id: str,
    model_id: str,
    payload: UpdateModelConfigDTO,
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: ModelServiceDep,
) -> ModelConfigDTO:
    """Update model configuration settings or name within workspace."""
    response.headers["Cache-Control"] = "no-store"
    require_permission(principal, "model:update")
    verify_csrf_or_origin(request)
    ws_uuid = parse_uuid_param(workspace_id, "workspace_id")
    m_uuid = parse_uuid_param(model_id, "model_id")
    req_id, trace_id = extract_correlation_ids(request)
    t_uuid = UUID(str(principal.tenant_id))

    try:
        return await service.update_model_config(
            session,
            tenant_id=t_uuid,
            workspace_id=ws_uuid,
            model_id=m_uuid,
            actor_subject=str(principal.user_id),
            dto=payload,
            request_id=req_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise translate_model_error(exc) from exc


@router.post("/{model_id}/disable", response_model=ModelConfigDTO)
async def disable_model(
    workspace_id: str,
    model_id: str,
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: ModelServiceDep,
) -> ModelConfigDTO:
    """Disable model configuration to prevent new executions."""
    response.headers["Cache-Control"] = "no-store"
    require_permission(principal, "model:disable")
    verify_csrf_or_origin(request)
    ws_uuid = parse_uuid_param(workspace_id, "workspace_id")
    m_uuid = parse_uuid_param(model_id, "model_id")
    req_id, trace_id = extract_correlation_ids(request)
    t_uuid = UUID(str(principal.tenant_id))

    try:
        return await service.disable_model_config(
            session,
            tenant_id=t_uuid,
            workspace_id=ws_uuid,
            model_id=m_uuid,
            actor_subject=str(principal.user_id),
            request_id=req_id,
            trace_id=trace_id,
        )
    except Exception as exc:
        raise translate_model_error(exc) from exc
