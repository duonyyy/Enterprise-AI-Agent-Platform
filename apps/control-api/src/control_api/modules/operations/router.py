from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/operations", tags=["operations"])
@router.get("")
def list_items(workspace_id: str): return {"module": "operations", "workspace_id": workspace_id, "items": []}
