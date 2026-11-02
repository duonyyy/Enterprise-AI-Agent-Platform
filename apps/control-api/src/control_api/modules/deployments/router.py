from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/deployments", tags=["deployments"])
@router.get("")
def list_items(workspace_id: str): return {"module": "deployments", "workspace_id": workspace_id, "items": []}
