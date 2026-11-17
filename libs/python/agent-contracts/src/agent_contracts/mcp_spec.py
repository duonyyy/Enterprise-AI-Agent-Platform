from typing import Literal

from pydantic import BaseModel, Field


class McpConnectionConfig(BaseModel):
    schemaVersion: Literal["1.0"] = "1.0"
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    endpoint: str
    authMode: Literal["service_token", "oauth2", "none"] = "service_token"
    secretRef: str | None = None
    connectTimeoutMs: int = 5000
    toolTimeoutMs: int = 30000
    maxResponseBytes: int = 1048576
    allowlistedHost: str | None = None
    redirectPolicy: Literal["deny", "allow_same_host"] = "deny"


class McpCapabilities(BaseModel):
    tools: bool = True
    resources: bool = False
    prompts: bool = False


class McpDiscoverySnapshot(BaseModel):
    snapshotId: str
    connectionId: str
    status: Literal["complete", "partial", "failed"]
    protocolVersion: str | None = "2026-07-28"
    protocolEra: Literal["modern", "legacy", "unknown"] = "modern"
    capabilities: McpCapabilities = Field(default_factory=McpCapabilities)
    catalogSha256: str
    capturedAt: str | None = None
