"""Response DTOs for model management."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelConfigDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    tenant_id: UUID = Field(alias="tenantId")
    workspace_id: UUID = Field(alias="workspaceId")
    name: str
    provider: str
    provider_model_id: str = Field(alias="providerModelId")
    status: str
    settings: dict[str, Any]
    secret_ref: str | None = Field(default=None, alias="secretRef")
    created_at: datetime = Field(alias="createdAt")


class ModelConfigPageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[ModelConfigDTO]
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    total_count: int = Field(alias="totalCount")
