from pydantic import BaseModel, Field
from typing import List

class AuthenticatedPrincipal(BaseModel):
    user_id: str
    workspace_id: str
    tenant_id: str
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    is_service_account: bool = False
