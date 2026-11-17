from typing import Literal

from pydantic import BaseModel, Field


class SkillRef(BaseModel):
    skillId: str
    skillVersionId: str


class SkillManifest(BaseModel):
    schemaVersion: Literal["1.0"] = "1.0"
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    resources: list[str] = Field(default_factory=list)
    allowedTools: list[str] = Field(default_factory=list)
