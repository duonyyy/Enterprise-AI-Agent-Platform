from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field
from agent_contracts.skill_spec import SkillRef

class MetadataSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., pattern=r"^[a-z0-9_-]+$")
    displayName: Optional[str] = None
    description: Optional[str] = None

class PersonaSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Optional[str] = "Assistant"
    tone: Optional[str] = "Professional"
    language: str = "vi"
    instructions: str

class LimitsSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    maxSteps: int = Field(default=10, ge=1, le=50)
    maxWallTimeSeconds: int = 180
    maxTotalTokens: int = 16000

class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"] = "1.0"
    kind: Literal["Agent"] = "Agent"
    metadata: MetadataSpec
    mode: Literal["fixed_plan", "planner_based", "react"] = "fixed_plan"
    modelRef: str
    persona: PersonaSpec
    toolRefs: List[str] = Field(default_factory=list)
    skillRefs: List[SkillRef] = Field(default_factory=list)
    knowledgeRefs: List[str] = Field(default_factory=list)
    workflowRef: Optional[str] = None
    approvalPolicyRef: Optional[str] = None
    limits: LimitsSpec = Field(default_factory=LimitsSpec)
