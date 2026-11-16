"""Compose Agent services and persistence adapters at the FastAPI boundary."""

from typing import Annotated

from control_api.modules.agents.repositories.agent_repository import agent_repository
from control_api.modules.agents.services.agent_service import AgentDraftService
from fastapi import Depends


def get_agent_service() -> AgentDraftService:
    """Resolve the production PostgreSQL adapter without coupling the service to it."""
    return AgentDraftService(agent_repository)


AgentServiceDep = Annotated[AgentDraftService, Depends(get_agent_service)]

__all__ = ["AgentServiceDep", "get_agent_service"]
