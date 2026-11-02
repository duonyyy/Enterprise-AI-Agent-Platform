from pydantic import BaseModel, Field
from typing import Optional, Literal

class CreateMcpConnectionDTO(BaseModel):
    name: str
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    endpoint: str
    auth_mode: Literal["service_token", "oauth2", "none"] = "service_token"
    secret_ref: Optional[str] = None
    connect_timeout_ms: int = 5000
    tool_timeout_ms: int = 30000

class ReviewMcpToolDTO(BaseModel):
    action: Literal["approve", "reject"]
    risk_level: Literal["read", "write", "high_impact"] = "read"
    approval_required: bool = False

