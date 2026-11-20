from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowNode(BaseModel):
    id: str
    type: Literal[
        "knowledge_retrieval", "agent_step", "human_approval", "tool_call", "condition", "end"
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    when: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class WorkflowLimits(BaseModel):
    maxTransitions: int = 15


class WorkflowSpec(BaseModel):
    schemaVersion: Literal["1.0"] = "1.0"
    kind: Literal["Workflow"] = "Workflow"
    entrypoint: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)
