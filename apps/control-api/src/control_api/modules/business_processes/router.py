from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/business-processes", tags=["business_processes"])
@router.get("")
def list_items(workspace_id: str): return {"module": "business_processes", "workspace_id": workspace_id, "items": []}
