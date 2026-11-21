"""Model configuration domain models and errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

ModelStatus = Literal["active", "disabled"]


@dataclass(frozen=True, slots=True)
class ModelConfig:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    name: str
    provider: str
    provider_model_id: str
    secret_ref: str | None
    settings: dict[str, Any]
    status: str
    created_at: datetime
