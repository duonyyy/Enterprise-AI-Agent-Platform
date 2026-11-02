from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/workflows", tags=["workflows"])
@router.get("")
def list_items(workspace_id: str): return {"module": "workflows", "workspace_id": workspace_id, "items": []}
