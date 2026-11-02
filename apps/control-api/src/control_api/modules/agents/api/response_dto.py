from pydantic import BaseModel
from typing import Dict, Any

class AgentResponseDTO(BaseModel):
    agent_id: str
    slug: str
    display_name: str
    lifecycle: str

class PublishResponseDTO(BaseModel):
    agent_id: str
    version: int
    spec_hash: str
    is_immutable: bool = True
