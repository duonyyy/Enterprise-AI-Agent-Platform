from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any, Literal

class WorkflowNode(BaseModel):
    id: str
    type: Literal["knowledge_retrieval", "agent_step", "human_approval", "tool_call", "condition", "end"]
    params: Dict[str, Any] = Field(default_factory=dict)

class WorkflowEdge(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    when: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

class WorkflowLimits(BaseModel):
    maxTransitions: int = 15

class WorkflowSpec(BaseModel):
    schemaVersion: Literal["1.0"] = "1.0"
    kind: Literal["Workflow"] = "Workflow"
    entrypoint: str
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)
