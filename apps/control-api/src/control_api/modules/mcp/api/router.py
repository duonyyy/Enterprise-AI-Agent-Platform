import hashlib

from control_api.core.authorization import require_permission
from control_api.core.dependencies import CurrentUser
from control_api.modules.mcp.api.request_dto import CreateMcpConnectionDTO
from control_api.modules.mcp.api.response_dto import (
    McpConnectionResponseDTO,
    McpDiscoveryResponseDTO,
)
from fastapi import APIRouter, status

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/mcp", tags=["mcp"])


@router.get("/connections", response_model=list[McpConnectionResponseDTO])
async def list_connections(workspace_id: str, principal: CurrentUser):
    require_permission(principal, "mcp:read")
    return [
        {
            "id": "conn_1",
            "name": "crm-sandbox",
            "transport": "streamable_http",
            "endpoint": "https://crm.internal/mcp",
            "status": "active",
        }
    ]


@router.post("/connections", status_code=status.HTTP_201_CREATED, response_model=McpConnectionResponseDTO)
async def create_connection(workspace_id: str, dto: CreateMcpConnectionDTO, principal: CurrentUser):
    require_permission(principal, "mcp:connection:manage")
    return {
        "id": "conn_new",
        "name": dto.name,
        "transport": dto.transport,
        "endpoint": dto.endpoint,
        "status": "active",
    }


@router.post(
    "/connections/{connection_id}/discover",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=McpDiscoveryResponseDTO,
)
async def discover_connection(workspace_id: str, connection_id: str, principal: CurrentUser):
    require_permission(principal, "mcp:connection:manage")
    catalog_hash = hashlib.sha256(f"mcp-catalog-{connection_id}".encode()).hexdigest()
    return {
        "snapshot_id": f"snap_{connection_id}",
        "connection_id": connection_id,
        "protocol_version": "2026-07-28",
        "protocol_era": "modern",
        "status": "complete",
        "catalog_sha256": catalog_hash,
        "counts": {"tools": 2, "resources": 1, "prompts": 0},
    }


