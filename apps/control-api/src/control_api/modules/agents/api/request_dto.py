from pydantic import BaseModel
from typing import Optional, Dict, Any

class CreateAgentDraftDTO(BaseModel):
    slug: str
    display_name: str
    spec: Dict[str, Any]

class UpdateAgentDraftDTO(BaseModel):
    expected_version: int
    spec: Dict[str, Any]
