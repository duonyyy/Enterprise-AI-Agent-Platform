"""Dependency resolution for models API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from control_api.modules.models.repositories.model_repository import model_repository
from control_api.modules.models.services.model_service import ModelService


def get_model_service() -> ModelService:
    return ModelService(model_repository)


ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]

__all__ = ["ModelServiceDep", "get_model_service"]
