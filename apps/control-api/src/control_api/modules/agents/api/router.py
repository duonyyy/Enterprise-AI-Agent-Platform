from fastapi import APIRouter, Depends, status
from control_api.core.dependencies import CurrentUser
from control_api.modules.agents.api.request_dto import CreateAgentDraftDTO
from control_api.modules.agents.api.response_dto import AgentResponseDTO, PublishResponseDTO
from control_api.modules.agents.application.publish import publish_agent_revision
from platform_common.principal import AuthenticatedPrincipal

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/agents", tags=["agents"])

@router.get("", response_model=list[AgentResponseDTO])
async def list_agents(workspace_id: str, principal: CurrentUser):
    return [{"agent_id": "ag_1", "slug": "quotation-assistant", "display_name": "Quotation Assistant", "lifecycle": "PUBLISHED"}]

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_draft(workspace_id: str, dto: CreateAgentDraftDTO, principal: CurrentUser):
    return {"agent_id": "ag_1", "status": "DRAFT", "slug": dto.slug}

@router.post("/{agent_id}/publish", response_model=PublishResponseDTO)
async def publish(workspace_id: str, agent_id: str, principal: CurrentUser):
    res = publish_agent_revision(workspace_id, agent_id, principal.user_id)
    return res
