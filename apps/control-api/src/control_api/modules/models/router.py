from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/models", tags=["models"])
@router.get("")
def list_items(workspace_id: str): return {"module": "models", "workspace_id": workspace_id, "items": []}
