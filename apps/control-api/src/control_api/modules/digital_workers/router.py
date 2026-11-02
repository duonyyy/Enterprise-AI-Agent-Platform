from fastapi import APIRouter
router = APIRouter(prefix="/v1/workspaces/{workspace_id}/digital-workers", tags=["digital_workers"])
@router.get("")
def list_items(workspace_id: str): return {"module": "digital_workers", "workspace_id": workspace_id, "items": []}
