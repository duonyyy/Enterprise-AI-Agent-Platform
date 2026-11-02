from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal

class McpConnectionConfig(BaseModel):
    schemaVersion: Literal["1.0"] = "1.0"
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    endpoint: str
    authMode: Literal["service_token", "oauth2", "none"] = "service_token"
    secretRef: Optional[str] = None
    connectTimeoutMs: int = 5000
    toolTimeoutMs: int = 30000
    maxResponseBytes: int = 1048576
    allowlistedHost: Optional[str] = None
    redirectPolicy: Literal["deny", "allow_same_host"] = "deny"

class McpCapabilities(BaseModel):
    tools: bool = True
    resources: bool = False
    prompts: bool = False

class McpDiscoverySnapshot(BaseModel):
    snapshotId: str
    connectionId: str
    status: Literal["complete", "partial", "failed"]
    protocolVersion: Optional[str] = "2026-07-28"
    protocolEra: Literal["modern", "legacy", "unknown"] = "modern"
    capabilities: McpCapabilities = Field(default_factory=McpCapabilities)
    catalogSha256: str
    capturedAt: Optional[str] = None

