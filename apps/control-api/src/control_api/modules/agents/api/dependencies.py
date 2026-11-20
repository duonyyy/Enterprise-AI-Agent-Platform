"""Compose Agent services and persistence adapters at the FastAPI boundary."""

from typing import Annotated

from control_api.modules.agents.repositories.agent_repository import agent_repository
from control_api.modules.agents.services.agent_service import AgentDraftService
from control_api.modules.agents.services.agent_validation_service import AgentValidationService
from control_api.modules.agents.services.promotion_service import AgentPromotionService
from control_api.modules.agents.services.publish_service import AgentPublishService
from fastapi import Depends


def get_agent_service() -> AgentDraftService:
    """Resolve the production PostgreSQL adapter without coupling the service to it."""
    return AgentDraftService(agent_repository)


def get_agent_validation_service() -> AgentValidationService:
    """Resolve validation service with production repository and resolver adapters."""
    return AgentValidationService(repository=agent_repository, resolver=agent_repository)


def get_agent_publish_service() -> AgentPublishService:
    """Resolve publish service with production repository adapter."""
    return AgentPublishService(agent_repository)


def get_agent_promotion_service() -> AgentPromotionService:
    """Resolve promotion service with production repository adapter."""
    return AgentPromotionService(agent_repository)


AgentServiceDep = Annotated[AgentDraftService, Depends(get_agent_service)]
AgentValidationServiceDep = Annotated[AgentValidationService, Depends(get_agent_validation_service)]
AgentPublishServiceDep = Annotated[AgentPublishService, Depends(get_agent_publish_service)]
AgentPromotionServiceDep = Annotated[AgentPromotionService, Depends(get_agent_promotion_service)]

__all__ = [
    "AgentPromotionServiceDep",
    "AgentPublishServiceDep",
    "AgentServiceDep",
    "AgentValidationServiceDep",
    "get_agent_promotion_service",
    "get_agent_publish_service",
    "get_agent_service",
    "get_agent_validation_service",
]
