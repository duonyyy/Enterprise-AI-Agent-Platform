from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/workspaces", tags=["workspaces"])
@router.get("")
def list_items(workspace_id: str): return {"module": "workspaces", "workspace_id": workspace_id, "items": []}
