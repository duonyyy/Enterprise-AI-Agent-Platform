"""HTTP helpers and exception translation for the Agents API."""

from __future__ import annotations

import logging
from uuid import UUID

from control_api.core.errors import RepositoryUnavailableError
from control_api.modules.agents.models.errors import (
    AgentAccessDeniedError,
    AgentArchivedError,
    AgentCapabilityUnavailableError,
    AgentDatabaseUnavailableError,
    AgentDomainError,
    AgentIdempotencyConflictError,
    AgentNotFoundError,
    AgentSlugConflictError,
    AgentStaleRevisionError,
    AgentValidationError,
)
from fastapi import HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def extract_correlation_ids(request: Request) -> tuple[str | None, str | None]:
    """Extract x-request-id and x-trace-id correlation headers."""
    request_id = request.headers.get("x-request-id")
    trace_id = request.headers.get("x-trace-id") or request.headers.get("traceparent")
    return request_id, trace_id


def parse_uuid_param(param_value: str, param_name: str) -> UUID:
    """Validate and parse a UUID string parameter, raising 422 if malformed."""
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


def translate_agent_error(exc: Exception) -> HTTPException:
    """Translate domain exceptions to standard HTTP error envelopes."""
    if isinstance(exc, AgentNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        )

    if isinstance(exc, (AgentSlugConflictError, AgentArchivedError, AgentIdempotencyConflictError)):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        )

    if isinstance(exc, AgentStaleRevisionError):
        return HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": exc.code, "message": exc.message},
        )

    if isinstance(exc, AgentValidationError):
        status_code = (
            status.HTTP_400_BAD_REQUEST
            if exc.code
            in (
                "invalid_cursor",
                "invalid_if_match",
                "missing_idempotency_key",
                "invalid_idempotency_key",
            )
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        return HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message},
        )

    if isinstance(exc, AgentAccessDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": exc.message},
        )

    if isinstance(exc, AgentCapabilityUnavailableError):
        return HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "Agent publishing is not available"},
        )

    if isinstance(
        exc, (AgentDatabaseUnavailableError, RepositoryUnavailableError, SQLAlchemyError)
    ):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "service_unavailable", "message": "Service temporarily unavailable"},
        )

    if isinstance(exc, AgentDomainError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        )

    logger.error("Unexpected exception in agent operations (%s)", type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "internal_error", "message": "An unexpected server error occurred"},
    )
