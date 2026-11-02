from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/policies", tags=["policies"])
@router.get("")
def list_items(workspace_id: str): return {"module": "policies", "workspace_id": workspace_id, "items": []}
