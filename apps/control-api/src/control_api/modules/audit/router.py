from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/audit", tags=["audit"])
@router.get("")
def list_items(workspace_id: str): return {"module": "audit", "workspace_id": workspace_id, "items": []}
