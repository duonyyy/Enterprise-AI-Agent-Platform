from pydantic import BaseModel
from typing import Optional, List

class SkillResponseDTO(BaseModel):
    id: str
    slug: str
    display_name: str
    revision: int
    status: str

class SkillVersionDTO(BaseModel):
    id: str
    skill_id: str
    version: int
    bundle_sha256: str
    is_immutable: bool = True

