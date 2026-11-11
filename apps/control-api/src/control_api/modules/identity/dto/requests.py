"""Request DTOs for Identity endpoints (SPEC-AUTH-001)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TokenRequestDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
