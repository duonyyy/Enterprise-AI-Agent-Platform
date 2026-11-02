from pydantic import BaseModel
from typing import Optional, Dict, Any

class McpConnectionResponseDTO(BaseModel):
    id: str
    name: str
    transport: str
    endpoint: str
    status: str

class McpDiscoveryResponseDTO(BaseModel):
    snapshot_id: str
    connection_id: str
    protocol_version: str
    protocol_era: str
    status: str
    catalog_sha256: str
    counts: Dict[str, int]

