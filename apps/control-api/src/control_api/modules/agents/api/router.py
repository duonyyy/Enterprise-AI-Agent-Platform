"""FastAPI Router for Agent Draft CRUD and Optimistic Concurrency Control (SPEC-AGENT-001, ADR-022)."""

from __future__ import annotations

from typing import Annotated

from control_api.core.auth import verify_csrf_or_origin
from control_api.core.authorization import require_permission
from control_api.core.concurrency import IfMatchRevision
from control_api.core.dependencies import CurrentUser, DBSession
from control_api.modules.agents.api._http_helpers import (
    extract_correlation_ids,
    parse_uuid_param,
    translate_agent_error,
)
from control_api.modules.agents.api.dependencies import (
    AgentServiceDep,
    AgentValidationServiceDep,
)
from control_api.modules.agents.dto.requests import (
    CreateAgentDraftDTO,
    ReplaceAgentDraftSpecDTO,
    ValidateAgentDraftDTO,
)
from control_api.modules.agents.dto.responses import (
    AgentDiagnosticDTO,
    AgentDraftDTO,
    AgentDraftMetadataDTO,
    AgentDraftPageDTO,
    AgentValidationResultDTO,
    PublishResponseDTO,
)
from control_api.modules.agents.services.publish_service import publish_agent_revision
from fastapi import APIRouter, HTTPException, Query, Request, Response, status

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/agents", tags=["agents"])


@router.get("", response_model=AgentDraftPageDTO)
async def list_agents(
    workspace_id: str,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: AgentServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> AgentDraftPageDTO:
    """List agent draft metadata with keyset pagination (ordered created_at DESC, id DESC)."""
    _ = workspace_id
    response.headers["Cache-Control"] = "no-store"
    require_permission(principal, "agent:read")
    try:
        items, next_cursor = await service.list_drafts(
            session,
            principal=principal,
            limit=limit,
            cursor=cursor,
        )
        return AgentDraftPageDTO(
            items=[
                AgentDraftMetadataDTO(
                    agent_id=str(it.agent_id),
                    slug=it.slug,
                    display_name=it.display_name,
                    revision=it.revision,
                    archived_at=it.archived_at.isoformat() if it.archived_at else None,
                    created_at=it.created_at.isoformat(),
                )
                for it in items
            ],
            next_cursor=next_cursor,
        )
    except Exception as exc:
        raise translate_agent_error(exc) from exc


@router.post("", response_model=AgentDraftDTO, status_code=status.HTTP_201_CREATED)
async def create_draft(
    workspace_id: str,
    dto: CreateAgentDraftDTO,
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: AgentServiceDep,
) -> AgentDraftDTO:
    """Create a new agent draft with revision 1. Validates canonical AgentSpec raw."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(principal, "agent:create")
    request_id, trace_id = extract_correlation_ids(request)

    try:
        draft = await service.create_draft(
            session,
            principal=principal,
            slug=dto.slug,
            display_name=dto.display_name,
            spec=dto.spec,
            request_id=request_id,
            trace_id=trace_id,
        )
        response.headers["ETag"] = f'"{draft.revision}"'
        response.headers["Cache-Control"] = "no-store"
        result = AgentDraftDTO(
            agent_id=str(draft.agent_id),
            slug=draft.slug,
            display_name=draft.display_name,
            revision=draft.revision,
            archived_at=draft.archived_at.isoformat() if draft.archived_at else None,
            created_at=draft.created_at.isoformat(),
            spec=draft.spec,
        )
        await session.commit()
        return result
    except Exception as exc:
        raise translate_agent_error(exc) from exc


@router.get("/{agent_id}", response_model=AgentDraftDTO)
async def get_draft(
    workspace_id: str,
    agent_id: str,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: AgentServiceDep,
) -> AgentDraftDTO:
    """Fetch current committed agent draft by ID; sets strong ETag and Cache-Control: no-store."""
    _ = workspace_id
    require_permission(principal, "agent:read")
    parsed_agent_id = parse_uuid_param(agent_id, "agent_id")

    try:
        draft = await service.get_draft(
            session,
            principal=principal,
            agent_id=parsed_agent_id,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["ETag"] = f'"{draft.revision}"'
        return AgentDraftDTO(
            agent_id=str(draft.agent_id),
            slug=draft.slug,
            display_name=draft.display_name,
            revision=draft.revision,
            archived_at=draft.archived_at.isoformat() if draft.archived_at else None,
            created_at=draft.created_at.isoformat(),
            spec=draft.spec,
        )
    except Exception as exc:
        raise translate_agent_error(exc) from exc


@router.patch("/{agent_id}", response_model=AgentDraftDTO)
async def replace_draft_spec(
    workspace_id: str,
    agent_id: str,
    dto: ReplaceAgentDraftSpecDTO,
    if_match: IfMatchRevision,
    request: Request,
    response: Response,
    principal: CurrentUser,
    session: DBSession,
    service: AgentServiceDep,
) -> AgentDraftDTO:
    """Replace complete spec using optimistic CAS with strong numeric ETag; increments revision."""
    _ = workspace_id
    verify_csrf_or_origin(request)
    require_permission(principal, "agent:edit")
    parsed_agent_id = parse_uuid_param(agent_id, "agent_id")
    request_id, trace_id = extract_correlation_ids(request)

    try:
        draft = await service.replace_draft_spec(
            session,
            principal=principal,
            agent_id=parsed_agent_id,
            spec=dto.spec,
            expected_revision=if_match,
            request_id=request_id,
            trace_id=trace_id,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["ETag"] = f'"{draft.revision}"'
        result = AgentDraftDTO(
            agent_id=str(draft.agent_id),
            slug=draft.slug,
            display_name=draft.display_name,
            revision=draft.revision,
            archived_at=draft.archived_at.isoformat() if draft.archived_at else None,
            created_at=draft.created_at.isoformat(),
            spec=draft.spec,
        )
        await session.commit()
        return result
    except Exception as exc:
        raise translate_agent_error(exc) from exc


@router.post("/{agent_id}/validate", response_model=AgentValidationResultDTO)
async def validate_draft(
    workspace_id: str,
    agent_id: str,
    response: Response,
    request: Request,
    principal: CurrentUser,
    session: DBSession,
    validation_service: AgentValidationServiceDep,
    dto: ValidateAgentDraftDTO | None = None,
) -> AgentValidationResultDTO:
    """Validate persisted draft or pre-validate a submitted spec against scoped dependencies."""
    _ = workspace_id
    require_permission(principal, "agent:edit")
    parsed_agent_id = parse_uuid_param(agent_id, "agent_id")
    if dto is None and (await request.body()).strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_spec",
                "message": "Request body must be an object when provided",
            },
        )

    try:
        has_spec_override = dto is not None and "spec" in dto.model_fields_set
        spec_override = dto.spec if has_spec_override else None
        res = await validation_service.validate_draft(
            session,
            principal=principal,
            agent_id=parsed_agent_id,
            spec_override=spec_override,
            has_spec_override=has_spec_override,
        )
        response.headers["Cache-Control"] = "no-store"
        return AgentValidationResultDTO(
            agent_id=str(res.agent_id),
            revision=res.revision,
            ready=res.ready,
            diagnostics=[
                AgentDiagnosticDTO(
                    field=d.field,
                    code=d.code,
                    message=d.message,
                )
                for d in res.diagnostics
            ],
        )
    except Exception as exc:
        raise translate_agent_error(exc) from exc


@router.post("/{agent_id}/publish", response_model=PublishResponseDTO)
async def publish(
    workspace_id: str,
    agent_id: str,
    request: Request,
    principal: CurrentUser,
) -> PublishResponseDTO:
    """Publish endpoint fails closed in 04-A (ADR-022, D04-02)."""
    verify_csrf_or_origin(request)
    require_permission(principal, "agent:publish")
    try:
        res = publish_agent_revision(workspace_id, agent_id, principal.user_id)
        return PublishResponseDTO(**res)
    except Exception as exc:
        raise translate_agent_error(exc) from exc
