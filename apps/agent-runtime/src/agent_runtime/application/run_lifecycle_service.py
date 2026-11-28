"""Application service for run lifecycle, cancellation, and queue deadline expiry (06-D)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_admission import RunAdmissionAccessDeniedError
from agent_runtime.application.run_lifecycle import (
    CancelRunResult,
    RunLifecycleAccessDeniedError,
    RunLifecycleConflictError,
    RunLifecycleNotFoundError,
    RunLifecycleResumeDisabledError,
    RunLifecycleUnavailableError,
    RunLifecycleValidationError,
    validate_run_uuid,
)
from agent_runtime.auth import RuntimePrincipal, has_effective_runtime_scope
from agent_runtime.persistence.database import RuntimeDatabaseUnavailable
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_lifecycle_repository import RunLifecycleRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[str, str, str | None], AbstractAsyncContextManager[AsyncSession]]


class RunLifecycleService:
    """Orchestrates run cancellation, authority checks, and expiry transitions."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        lifecycle_repository: RunLifecycleRepository | None = None,
        admission_repository: RunAdmissionRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repo = (
            lifecycle_repository if lifecycle_repository is not None else RunLifecycleRepository()
        )
        self._admission_repo = (
            admission_repository if admission_repository is not None else RunAdmissionRepository()
        )

    async def cancel_run(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
    ) -> CancelRunResult:
        """Cancel queued run with strict authority checks (Creator or Owner/Admin/Operator)."""
        try:
            principal.require_scope("run:start")
        except Exception as exc:
            raise RunLifecycleAccessDeniedError(
                "Caller lacks run:start scope in workspace",
                code="insufficient_scope",
            ) from exc

        valid_run_id = validate_run_uuid(run_id)

        try:
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                caller_role = await self._admission_repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=False,
                )
                if not has_effective_runtime_scope(caller_role, principal, "run:start"):
                    raise RunLifecycleAccessDeniedError(
                        "Caller lacks run:start scope in workspace",
                        code="insufficient_scope",
                    )

                result = await self._repo.cancel_or_expire_run(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=valid_run_id,
                    actor_subject=principal.user_id,
                    caller_role=caller_role,
                    caller_user_id=principal.user_id,
                    is_system_maintenance=False,
                )
                return result
        except (
            RunLifecycleValidationError,
            RunLifecycleAccessDeniedError,
            RunLifecycleNotFoundError,
            RunLifecycleConflictError,
            RunLifecycleUnavailableError,
            RunLifecycleResumeDisabledError,
        ):
            raise
        except RunAdmissionAccessDeniedError as exc:
            raise RunLifecycleAccessDeniedError(
                "Active workspace membership required", code="access_denied"
            ) from exc
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning(
                "Run cancellation database error (%s)",
                type(exc).__name__,
                extra={"tenant_id": principal.tenant_id, "run_id": valid_run_id},
            )
            raise RunLifecycleUnavailableError(
                "Run cancellation backend temporarily unavailable",
                code="service_unavailable",
            ) from exc
