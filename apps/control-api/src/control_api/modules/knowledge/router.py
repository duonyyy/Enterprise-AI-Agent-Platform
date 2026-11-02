from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/knowledge", tags=["knowledge"])
@router.get("")
def list_items(workspace_id: str): return {"module": "knowledge", "workspace_id": workspace_id, "items": []}
