"""Domain entities for Agent draft lifecycle (SPEC-AGENT-001, ADR-022, D04-01)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentDraftMetadataEntity:
    """Read projection for an Agent draft within a scoped workspace."""

    agent_id: UUID
    slug: str
    display_name: str
    revision: int
    archived_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentDraftEntity:
    """Full committed state of a mutable Agent draft and its current spec."""

    agent_id: UUID
    slug: str
    display_name: str
    revision: int
    archived_at: datetime | None
    created_at: datetime
    spec: dict[str, Any]
