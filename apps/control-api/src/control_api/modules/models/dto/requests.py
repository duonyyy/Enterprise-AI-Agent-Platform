"""Request DTOs for model management."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateModelConfigDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    provider_model_id: str = Field(alias="providerModelId", min_length=1, max_length=128)
    secret_ref: str | None = Field(default=None, alias="secretRef", max_length=128)
    settings: dict[str, Any] = Field(default_factory=dict)


class UpdateModelConfigDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=128)
    settings: dict[str, Any] | None = None
    secret_ref: str | None = Field(default=None, alias="secretRef", max_length=128)
