from pydantic import BaseModel
from typing import Optional, Literal

class DecisionInputDTO(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    decision_reason: Optional[str] = None
    expected_action_hash: str
