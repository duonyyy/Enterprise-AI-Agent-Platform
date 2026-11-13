"""Shared HTTP parsing, correlation extraction, and error translation helpers."""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID, uuid4

from control_api.core.errors import RepositoryUnavailableError
from control_api.modules.workspaces.models.errors import (
    InvitationConfigurationError,
    InvitationConflictError,
    InvitationForbiddenError,
    InvitationGoneError,
    InvitationNotFoundError,
    InvitationRateLimitError,
    InvitationValidationError,
    MembershipConflictError,
    MembershipForbiddenError,
    MembershipNotFoundError,
    MembershipValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceConflictError,
    WorkspaceDatabaseUnavailableError,
    WorkspaceError,
)
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

ErrorScope = Literal["workspace", "invitation", "membership", "accept"]

_WORKSPACE_DEFAULT_MESSAGES: dict[type[WorkspaceError], tuple[int, str]] = {
    WorkspaceAccessDeniedError: (
        status.HTTP_403_FORBIDDEN,
        "Access denied for requested workspace operation",
    ),
    WorkspaceConflictError: (
        status.HTTP_409_CONFLICT,
        "Workspace conflict: resource already exists",
    ),
    WorkspaceDatabaseUnavailableError: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Workspace persistence service temporarily unavailable",
    ),
}

_STATUS_CODE_BY_ERROR: tuple[tuple[type[WorkspaceError], int], ...] = (
    (InvitationNotFoundError, status.HTTP_404_NOT_FOUND),
    (MembershipNotFoundError, status.HTTP_404_NOT_FOUND),
    (InvitationForbiddenError, status.HTTP_403_FORBIDDEN),
    (MembershipForbiddenError, status.HTTP_403_FORBIDDEN),
    (InvitationGoneError, status.HTTP_410_GONE),
    (InvitationConflictError, status.HTTP_409_CONFLICT),
    (MembershipConflictError, status.HTTP_409_CONFLICT),
    (InvitationValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (MembershipValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
)


def parse_uuid_path_param(raw_value: str, *, code: str, message: str) -> UUID:
    """Parse a UUID path segment or raise an OpenAPI-compliant 422 HTTPException."""
    try:
        return UUID(raw_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": code, "message": message},
        ) from exc


def extract_correlation_ids(request: Request) -> tuple[str, str]:
    """Extract normalized request and trace IDs, generating defaults when absent."""
    raw_request_id = (request.headers.get("x-request-id") or "").strip()
    request_id = raw_request_id or str(uuid4())
    raw_trace_id = (request.headers.get("x-trace-id") or "").strip()
    trace_id = raw_trace_id or request_id
    return request_id, trace_id


def _structured_detail(
    exc: WorkspaceError | RepositoryUnavailableError,
    *,
    fallback_code: str,
    fallback_message: str,
) -> tuple[str, str]:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    safe_code = str(code) if isinstance(code, str) and code else fallback_code
    safe_msg = (
        str(message) if isinstance(message, str) and message else str(exc) or fallback_message
    )
    return safe_code, safe_msg


def translate_domain_error(
    exc: WorkspaceError | RepositoryUnavailableError,
    *,
    scope: ErrorScope,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> HTTPException:
    """Translate domain and persistence errors into consistent contract-safe HTTP responses."""
    extra: dict[str, Any] = {
        "error_scope": scope,
        "error_type": type(exc).__name__,
    }
    if request_id:
        extra["request_id"] = request_id
    if trace_id:
        extra["trace_id"] = trace_id
    logger.warning(
        "Workspace API domain error (%s: %s)",
        scope,
        type(exc).__name__,
        extra=extra,
    )

    # Workspace collection/detail routes preserve legacy string `detail` per existing contract tests.
    if scope == "workspace":
        for err_cls, status_and_msg in _WORKSPACE_DEFAULT_MESSAGES.items():
            if isinstance(exc, err_cls):
                code_num, fallback = status_and_msg
                message = str(exc).strip() or fallback
                return HTTPException(status_code=code_num, detail=message)
        logger.exception("Unexpected workspace domain error (%s)", type(exc).__name__, extra=extra)
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected workspace operation error",
        )

    for err_cls, status_code in _STATUS_CODE_BY_ERROR:
        if isinstance(exc, err_cls):
            code, message = _structured_detail(
                exc,
                fallback_code=f"{scope}_error",
                fallback_message="Workspace operation failed",
            )
            return HTTPException(
                status_code=status_code,
                detail={"code": code, "message": message},
            )

    if isinstance(exc, WorkspaceAccessDeniedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "workspace_access_denied",
                "message": str(exc).strip() or "Access denied for workspace resource",
            },
        )

    if isinstance(exc, InvitationRateLimitError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    if isinstance(
        exc,
        (
            InvitationConfigurationError,
            WorkspaceDatabaseUnavailableError,
            RepositoryUnavailableError,
        ),
    ):
        fallback_code = {
            "invitation": "invitation_store_unavailable",
            "membership": "membership_store_unavailable",
            "accept": "invitation_service_unavailable",
        }[scope]
        fallback_message = "Workspace persistence service temporarily unavailable"
        code, message = _structured_detail(
            exc,
            fallback_code=fallback_code,
            fallback_message=fallback_message,
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": code, "message": message},
        )

    logger.exception(
        "Unexpected %s domain error (%s)",
        scope,
        type(exc).__name__,
        extra=extra,
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": f"{scope}_unexpected_error",
            "message": "Unexpected workspace operation error",
        },
    )
