from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from agent_contracts.skill_spec import SkillRef

class MetadataSpec(BaseModel):
    name: str
    displayName: Optional[str] = None
    description: Optional[str] = None

class PersonaSpec(BaseModel):
    role: Optional[str] = "Assistant"
    tone: Optional[str] = "Professional"
    language: str = "vi"
    instructions: str

class LimitsSpec(BaseModel):
    maxSteps: int = Field(default=10, ge=1, le=50)
    maxWallTimeSeconds: int = 180
    maxTotalTokens: int = 16000

class AgentSpec(BaseModel):
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
