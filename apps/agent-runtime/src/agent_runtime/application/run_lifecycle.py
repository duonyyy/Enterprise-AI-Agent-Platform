"""Typed contracts, errors, and DTOs for run lifecycle and queued cancellation (06-D)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunLifecycleError(RuntimeError):
    """Base domain error for run lifecycle and cancellation."""

    def __init__(self, message: str, *, code: str = "run_lifecycle_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunLifecycleValidationError(RunLifecycleError):
    """Raised on invalid request payload, UUID, or parameters."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message, code=code)


class RunLifecycleAccessDeniedError(RunLifecycleError):
    """Raised on missing token scope, inactive membership, or insufficient role."""

    def __init__(self, message: str, *, code: str = "access_denied") -> None:
        super().__init__(message, code=code)


class RunLifecycleNotFoundError(RunLifecycleError):
    """Raised when run does not exist, cross-scope, or peer run is hidden from developer/viewer."""

    def __init__(self, message: str, *, code: str = "not_found") -> None:
        super().__init__(message, code=code)


class RunLifecycleConflictError(RunLifecycleError):
    """Raised when run is in flight (leased/running/waiting_approval) or legacy unsupported."""

    def __init__(self, message: str, *, code: str = "conflict") -> None:
        super().__init__(message, code=code)


class RunLifecycleUnavailableError(RunLifecycleError):
    """Raised when DB, job state inconsistency, or commit outcome is unknown (fail closed)."""

    def __init__(self, message: str, *, code: str = "service_unavailable") -> None:
        super().__init__(message, code=code)


class RunLifecycleResumeDisabledError(RunLifecycleError):
    """Raised unconditionally until durable approval proof verification (RD-09) is implemented."""

    def __init__(
        self,
        message: str = "Run resume is disabled pending verified durable approval proof (RD-09)",
        *,
        code: str = "resume_disabled",
    ) -> None:
        super().__init__(message, code=code)


class CancelRunRequestDTO(BaseModel):
    """Strict empty request body for POST /v1/runs/{run_id}/cancel (extra fields forbidden)."""

    model_config = ConfigDict(extra="forbid")


RunDisposition = Literal["cancelled", "queue_expired", "already_terminal"]


class CancelRunResponseDTO(BaseModel):
    """Strict response DTO for POST /v1/runs/{run_id}/cancel (DEC-06-CD)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    changed: bool
    disposition: RunDisposition


@dataclass(frozen=True, slots=True)
class CancelRunResult:
    """Internal authoritative outcome of run cancellation or expiry CAS."""

    run_id: str
    status: str
    changed: bool
    disposition: RunDisposition


def validate_run_uuid(raw_id: str) -> str:
    """Validate that raw_id is a valid UUID string and return its canonical form."""
    try:
        return str(UUID(raw_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RunLifecycleValidationError(
            f"Invalid run_id UUID format: '{raw_id}'",
            code="invalid_uuid",
        ) from exc
