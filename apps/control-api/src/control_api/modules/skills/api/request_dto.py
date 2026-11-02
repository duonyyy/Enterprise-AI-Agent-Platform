from pydantic import BaseModel, Field
from typing import Optional, Dict

class CreateSkillDraftDTO(BaseModel):
    slug: str
    display_name: str
    draft_skill_md: str
    draft_manifest: Optional[Dict[str, str]] = Field(default_factory=dict)

class UpdateSkillDraftDTO(BaseModel):
    expected_revision: int
    draft_skill_md: str
    draft_manifest: Optional[Dict[str, str]] = None

