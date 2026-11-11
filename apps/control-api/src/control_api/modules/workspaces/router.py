from control_api.core.dependencies import CurrentUser
from fastapi import APIRouter

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/workspaces", tags=["workspaces"])


@router.get("")
def list_items(workspace_id: str, principal: CurrentUser):
    return {"module": "workspaces", "workspace_id": principal.workspace_id, "items": []}

