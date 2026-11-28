"""Agent Runtime HTTP endpoints for run admission, redacted metadata, and fail-closed resume."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from platform_common.event_bus import PostgresOutboxEventBus

from agent_runtime.application.run_admission import (
    RunAdmissionAccessDeniedError,
    RunAdmissionConflictError,
    RunAdmissionError,
    RunAdmissionNotFoundError,
    RunAdmissionQuotaExceededError,
    RunAdmissionUnavailableError,
    RunAdmissionValidationError,
    RunMetadataResponseDTO,
    RunResumeDisabledError,
    StartRunRequestDTO,
    StartRunResponseDTO,
)
from agent_runtime.application.run_admission_service import RunAdmissionService
from agent_runtime.application.run_data import ObjectStorePort
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.application.run_lifecycle import (
    CancelRunRequestDTO,
    CancelRunResponseDTO,
    RunLifecycleAccessDeniedError,
    RunLifecycleConflictError,
    RunLifecycleError,
    RunLifecycleNotFoundError,
    RunLifecycleResumeDisabledError,
    RunLifecycleUnavailableError,
    RunLifecycleValidationError,
)
from agent_runtime.application.run_lifecycle_service import RunLifecycleService
from agent_runtime.auth import CurrentRuntimePrincipal
from agent_runtime.persistence.database import get_session_factory, scoped_session
from agent_runtime.persistence.object_store import create_object_store
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_data_repository import RunDataRepository
from agent_runtime.persistence.run_event_repository import RunEventRepository
from agent_runtime.persistence.run_lifecycle_repository import RunLifecycleRepository
from agent_runtime.settings import RuntimeSettings, get_settings

router = APIRouter(tags=["runs"])


def build_run_admission_service(
    *,
    settings: RuntimeSettings | None = None,
    object_store: ObjectStorePort | None = None,
) -> RunAdmissionService:
    """Compose RunAdmissionService from runtime settings and process-owned adapters."""
    active_settings = settings or get_settings()
    store = object_store if object_store is not None else create_object_store(active_settings)
    run_data_repo = RunDataRepository()
    run_data_service = RunDataService(
        object_store=store,
        bucket=active_settings.s3_bucket,
        repository=run_data_repo,
        max_input_bytes=active_settings.run_data_max_input_bytes,
        max_output_bytes=active_settings.run_data_max_output_bytes,
        default_retention_days=active_settings.run_data_default_retention_days,
    )
    outbox = PostgresOutboxEventBus(lambda: get_session_factory()())
    return RunAdmissionService(
        session_factory=scoped_session,
        run_data_service=run_data_service,
        admission_repository=RunAdmissionRepository(),
        outbox_publisher=outbox,
        run_data_repository=run_data_repo,
        max_queued_per_workspace=active_settings.run_admission_max_queued_per_workspace,
        execution_available=active_settings.run_admission_execution_enabled,
        availability_reason=None
        if active_settings.run_admission_execution_enabled
        else "no_executor_configured",
        queue_ttl_seconds=active_settings.run_queue_ttl_seconds,
    )


def get_run_admission_service(request: Request) -> RunAdmissionService:
    """Resolve lifespan-owned RunAdmissionService or compose fail-closed fallback."""
    existing = getattr(request.app.state, "run_admission_service", None)
    if isinstance(existing, RunAdmissionService):
        return existing
    store = getattr(request.app.state, "object_store", None)
    return build_run_admission_service(object_store=store)


_ADMISSION_ERROR_STATUS_MAP: tuple[tuple[type[RunAdmissionError], int], ...] = (
    (RunAdmissionValidationError, status.HTTP_400_BAD_REQUEST),
    (RunAdmissionAccessDeniedError, status.HTTP_403_FORBIDDEN),
    (RunAdmissionNotFoundError, status.HTTP_404_NOT_FOUND),
    (RunAdmissionConflictError, status.HTTP_409_CONFLICT),
    (RunAdmissionQuotaExceededError, status.HTTP_429_TOO_MANY_REQUESTS),
    (RunAdmissionUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (RunResumeDisabledError, status.HTTP_503_SERVICE_UNAVAILABLE),
)


def _translate_admission_error(exc: RunAdmissionError) -> HTTPException:
    detail = {"code": exc.code, "message": exc.message}
    for error_cls, status_code in _ADMISSION_ERROR_STATUS_MAP:
        if isinstance(exc, error_cls):
            return HTTPException(status_code=status_code, detail=detail)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


RunAdmissionServiceDep = Annotated[RunAdmissionService, Depends(get_run_admission_service)]


def build_run_lifecycle_service(
    *,
    settings: RuntimeSettings | None = None,
) -> RunLifecycleService:
    """Compose RunLifecycleService from runtime settings and process-owned adapters."""
    outbox = PostgresOutboxEventBus(lambda: get_session_factory()())
    event_repo = RunEventRepository()
    lifecycle_repo = RunLifecycleRepository(
        event_repository=event_repo,
        outbox_publisher=outbox,
    )
    admission_repo = RunAdmissionRepository()
    return RunLifecycleService(
        session_factory=scoped_session,
        lifecycle_repository=lifecycle_repo,
        admission_repository=admission_repo,
    )


def get_run_lifecycle_service(request: Request) -> RunLifecycleService:
    """Resolve lifespan-owned RunLifecycleService or compose fail-closed fallback."""
    existing = getattr(request.app.state, "run_lifecycle_service", None)
    if isinstance(existing, RunLifecycleService):
        return existing
    return build_run_lifecycle_service()


_LIFECYCLE_ERROR_STATUS_MAP: tuple[tuple[type[RunLifecycleError], int], ...] = (
    (RunLifecycleValidationError, status.HTTP_400_BAD_REQUEST),
    (RunLifecycleAccessDeniedError, status.HTTP_403_FORBIDDEN),
    (RunLifecycleNotFoundError, status.HTTP_404_NOT_FOUND),
    (RunLifecycleConflictError, status.HTTP_409_CONFLICT),
    (RunLifecycleUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (RunLifecycleResumeDisabledError, status.HTTP_503_SERVICE_UNAVAILABLE),
)


def _translate_lifecycle_error(exc: RunLifecycleError) -> HTTPException:
    detail = {"code": exc.code, "message": exc.message}
    for error_cls, status_code in _LIFECYCLE_ERROR_STATUS_MAP:
        if isinstance(exc, error_cls):
            return HTTPException(status_code=status_code, detail=detail)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


RunLifecycleServiceDep = Annotated[RunLifecycleService, Depends(get_run_lifecycle_service)]


@router.post(
    "/v1/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartRunResponseDTO,
)
async def start_run(
    req: StartRunRequestDTO,
    principal: CurrentRuntimePrincipal,
    service: RunAdmissionServiceDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StartRunResponseDTO:
    try:
        admitted = await service.admit_run(
            principal=principal,
            workspace_id=str(req.workspace_id),
            agent_version_id=str(req.agent_version_id),
            inputs=req.inputs,
            conversation_id=str(req.conversation_id) if req.conversation_id is not None else None,
            attachments=req.attachments,
            idempotency_key=idempotency_key,
        )
    except RunAdmissionError as exc:
        raise _translate_admission_error(exc) from exc

    return StartRunResponseDTO(
        run_id=admitted.run_id,
        agent_version_id=admitted.agent_version_id,
        conversation_id=admitted.conversation_id,
        status=admitted.status,
        replayed=admitted.replayed,
        execution_available=admitted.execution_available,
        availability_reason=admitted.availability_reason,
    )


@router.get("/v1/runs/{run_id}", response_model=RunMetadataResponseDTO)
async def get_run(
    run_id: str,
    principal: CurrentRuntimePrincipal,
    service: RunAdmissionServiceDep,
) -> RunMetadataResponseDTO:
    try:
        record = await service.get_run_metadata(principal=principal, run_id=run_id)
    except RunAdmissionError as exc:
        raise _translate_admission_error(exc) from exc

    return RunMetadataResponseDTO(
        run_id=record.run_id,
        agent_version_id=record.agent_version_id,
        conversation_id=record.conversation_id,
        workflow_version_id=record.workflow_version_id,
        status=record.status,
        trace_id=record.trace_id,
        next_event_seq=record.next_event_seq,
        started_at=record.started_at.isoformat() if record.started_at else None,
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        created_at=record.created_at.isoformat() if record.created_at else None,
        queue_expires_at=record.queue_expires_at.isoformat() if record.queue_expires_at else None,
    )


@router.post(
    "/v1/runs/{run_id}/cancel",
    status_code=status.HTTP_200_OK,
    response_model=CancelRunResponseDTO,
)
async def cancel_run(
    run_id: str,
    req: CancelRunRequestDTO,
    principal: CurrentRuntimePrincipal,
    service: RunLifecycleServiceDep,
) -> CancelRunResponseDTO:
    try:
        res = await service.cancel_run(principal=principal, run_id=run_id)
    except RunLifecycleError as exc:
        raise _translate_lifecycle_error(exc) from exc

    return CancelRunResponseDTO(
        run_id=res.run_id,
        status=res.status,
        changed=res.changed,
        disposition=res.disposition,
    )


@router.post("/internal/v1/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    principal: CurrentRuntimePrincipal,
    service: RunAdmissionServiceDep,
) -> dict[str, str]:
    principal.require_scope("run:start")
    try:
        await service.resume_run(principal=principal, run_id=run_id)
    except RunAdmissionError as exc:
        raise _translate_admission_error(exc) from exc
    return {"run_id": run_id, "status": "queued"}
