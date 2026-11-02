from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/identity", tags=["identity"])
@router.get("")
def list_items(workspace_id: str): return {"module": "identity", "workspace_id": workspace_id, "items": []}
