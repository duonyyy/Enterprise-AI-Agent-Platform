from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/tools", tags=["tools"])
@router.get("")
def list_items(workspace_id: str): return {"module": "tools", "workspace_id": workspace_id, "items": []}
