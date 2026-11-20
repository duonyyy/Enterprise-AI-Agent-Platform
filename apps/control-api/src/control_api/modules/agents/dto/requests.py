"""Request DTOs for Agent draft endpoints matching candidate OpenAPI specification."""

from __future__ import annotations

import math
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# catalog.agents.revision and catalog.agent_publications.revision are PostgreSQL integer (int32).
MAX_REVISION_INT32 = 2_147_483_647


def _validate_json_integer_revision(value: Any) -> int:
    """Validate revision precondition as a canonical JSON integer without bool/string coercion.

    Matches OpenAPI 3.1 / JSON Schema Draft 2020-12 `type: integer`:
    - Rejects booleans (`True`/`False`), strings (`"0"`), null, and non-numeric values.
    - Rejects non-finite floats and floats with non-zero fractional parts (`1.5`).
    - Accepts whole-number floats (`1.0`) as exact integers per JSON Schema & RFC 8785 semantics.
    """
    if isinstance(value, bool):
        raise ValueError("Revision must be an integer, not a boolean")  # noqa: TRY004
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise ValueError("Revision must be an integer without fractional part")
    raise ValueError("Revision must be an integer")


class CreateAgentDraftDTO(BaseModel):
    """Payload for creating a new mutable agent draft."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(..., min_length=1, max_length=256)
    spec: dict[str, Any]


class ReplaceAgentDraftSpecDTO(BaseModel):
    """Payload for replacing the full agent draft specification."""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]


class ValidateAgentDraftDTO(BaseModel):
    """Payload for validating an agent draft. If spec is omitted, persisted draft is validated."""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] = Field(default_factory=dict)


class PublishAgentRequestDTO(BaseModel):
    """Payload for atomically publishing an immutable agent version."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(
        ...,
        ge=1,
        le=MAX_REVISION_INT32,
        description="Expected current draft revision",
    )

    @field_validator("expected_revision", mode="before")
    @classmethod
    def _validate_expected_revision(cls, value: Any) -> int:
        return _validate_json_integer_revision(value)


class PromoteAgentVersionRequestDTO(BaseModel):
    """Payload for promoting or rolling back an agent version to an environment (Step 04-D)."""

    model_config = ConfigDict(extra="forbid")

    agent_version_id: UUID
    environment: Literal["development", "staging", "production"]
    expected_publication_revision: int = Field(
        ...,
        ge=0,
        le=MAX_REVISION_INT32,
        description="Expected publication pointer revision (0 for initial publication to an environment)",
    )

    @field_validator("expected_publication_revision", mode="before")
    @classmethod
    def _validate_expected_publication_revision(cls, value: Any) -> int:
        return _validate_json_integer_revision(value)


# Backward compatibility alias
UpdateAgentDraftDTO = ReplaceAgentDraftSpecDTO

__all__ = [
    "CreateAgentDraftDTO",
    "PromoteAgentVersionRequestDTO",
    "PublishAgentRequestDTO",
    "ReplaceAgentDraftSpecDTO",
    "UpdateAgentDraftDTO",
    "ValidateAgentDraftDTO",
]
