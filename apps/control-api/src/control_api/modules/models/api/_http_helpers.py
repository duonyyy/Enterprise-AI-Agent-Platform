"""HTTP helpers and exception translation for the Models API."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from control_api.core.errors import RepositoryUnavailableError
from control_api.modules.models.models.errors import (
    ModelAccessDeniedError,
    ModelConfigConflictError,
    ModelConfigDatabaseUnavailableError,
    ModelConfigNotFoundError,
    ModelConfigValidationError,
)

logger = logging.getLogger(__name__)


def extract_correlation_ids(request: Request) -> tuple[str | None, str | None]:
    request_id = request.headers.get("x-request-id")
    trace_id = request.headers.get("x-trace-id") or request.headers.get("traceparent")
    return request_id, trace_id


def parse_uuid_param(param_value: str, param_name: str) -> UUID:
    try:
        return UUID(param_value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_identifier",
                "message": f"Parameter '{param_name}' must be a valid UUID",
            },
        ) from exc


def translate_model_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ModelConfigNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, ModelConfigConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, ModelConfigValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, ModelAccessDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, (ModelConfigDatabaseUnavailableError, RepositoryUnavailableError, SQLAlchemyError)):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_unavailable", "message": "Model database service temporarily unavailable"},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "internal_error", "message": "An unexpected error occurred"},
    )
