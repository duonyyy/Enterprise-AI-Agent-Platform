from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal

class SkillRef(BaseModel):
    skillId: str
    skillVersionId: str

class SkillManifest(BaseModel):
    schemaVersion: Literal["1.0"] = "1.0"
    name: str
    description: str
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
    resources: List[str] = Field(default_factory=list)
    allowedTools: List[str] = Field(default_factory=list)

