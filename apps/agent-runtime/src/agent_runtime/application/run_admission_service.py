"""Application service for atomic, idempotent run admission (06-B)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from agent_runtime.application.execution.grant import (
    DEFAULT_KEY_ID,
    ExecutionGrant,
    ExecutionKeyUnavailableError,
    encrypt_execution_grant,
    normalize_encryption_key,
)
from agent_runtime.application.run_admission import (
    DEFAULT_ADMISSION_EVENT_TYPE,
    INITIAL_RUN_STATUS,
    OUTBOX_RUN_EVENTS_TOPIC,
    AdmissionWriteStage,
    AdmittedRunMetadata,
    AdmittedRunResult,
    ExistingRunAdmission,
    OutboxPublisherPort,
    RunAdmissionAccessDeniedError,
    RunAdmissionConflictError,
    RunAdmissionError,
    RunAdmissionNotFoundError,
    RunAdmissionQuotaExceededError,
    RunAdmissionRepositoryPort,
    RunAdmissionUnavailableError,
    RunAdmissionValidationError,
    RunMessageRepositoryPort,
    RunResumeDisabledError,
    compute_run_admission_sha256,
    validate_attachments_no_external_urls,
    validate_idempotency_key,
)
from agent_runtime.application.run_data import (
    RunDataAccessDeniedError,
    RunDataConflictError,
    RunDataIntegrityError,
    RunDataNotFoundError,
    RunDataStorageUnavailableError,
    RunDataValidationError,
)
from agent_runtime.application.run_data_service import RunDataService, StagedRunObject
from agent_runtime.auth import (
    ParentSessionVerifierProtocol,
    RuntimePrincipal,
    get_parent_session_verifier,
    has_effective_runtime_scope,
)
from agent_runtime.persistence.database import RuntimeDatabaseUnavailable
from agent_runtime.persistence.run_data_repository import RunDataRepository
from agent_runtime.settings import RuntimeSettings

logger = logging.getLogger(__name__)

SessionFactory = Callable[[str, str, str | None], AbstractAsyncContextManager[Any]]
AfterWriteHook = Callable[[str, Any], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _ParsedAdmissionRequest:
    agent_version_id: str
    conversation_id: str | None
    idempotency_key: str
    request_sha256: str


class RunAdmissionService:
    """Orchestrates preflight checks, object staging, and atomic DB admission."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        run_data_service: RunDataService,
        admission_repository: RunAdmissionRepositoryPort,
        outbox_publisher: OutboxPublisherPort,
        run_data_repository: RunMessageRepositoryPort | None = None,
        max_queued_per_workspace: int = 1000,
        execution_available: bool = False,
        availability_reason: str | None = "no_executor_configured",
        initial_event_type: str = DEFAULT_ADMISSION_EVENT_TYPE,
        queue_ttl_seconds: int = 86400,
        execution_grants_enabled: bool = False,
        execution_key: str = "",
        execution_key_id: str = DEFAULT_KEY_ID,
        jwt_issuer: str = "",
        parent_session_verifier: ParentSessionVerifierProtocol | None = None,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_data_service = run_data_service
        self._repo = admission_repository
        self._outbox = outbox_publisher
        self._run_data_repo: RunMessageRepositoryPort = (
            run_data_repository if run_data_repository is not None else RunDataRepository()
        )
        self._max_queued_per_workspace = max_queued_per_workspace
        self._execution_available = execution_available
        self._availability_reason = availability_reason if not execution_available else None
        self._initial_event_type = initial_event_type
        self._queue_ttl_seconds = queue_ttl_seconds
        self._execution_grants_enabled = execution_grants_enabled
        self._execution_key = execution_key
        self._execution_key_id = execution_key_id
        self._jwt_issuer = jwt_issuer
        self._parent_session_verifier = parent_session_verifier
        self._settings = settings

    def _resolve_replay_or_conflict(
        self,
        *,
        existing: ExistingRunAdmission,
        principal: RuntimePrincipal,
        request_sha256: str,
    ) -> AdmittedRunResult:
        # Never disclose run identity/status to a different actor in the same workspace.
        if existing.initiated_by != str(UUID(principal.user_id)):
            raise RunAdmissionConflictError(
                "Idempotency-Key conflicts with an existing request",
                code="idempotency_conflict",
            )
        if existing.request_sha256 is None or existing.request_sha256 != request_sha256:
            raise RunAdmissionConflictError(
                "Idempotency-Key conflicts with an existing request",
                code="idempotency_conflict",
            )
        if existing.receipt_expired:
            raise RunAdmissionConflictError(
                "Idempotency-Key replay window has expired; this key cannot create a new run",
                code="idempotency_key_expired",
            )
        return AdmittedRunResult(
            run_id=existing.run_id,
            agent_version_id=existing.agent_version_id,
            conversation_id=existing.conversation_id,
            status=existing.status,
            replayed=True,
            execution_available=self._execution_available,
            availability_reason=self._availability_reason,
        )

    @staticmethod
    def _map_run_data_error(exc: Exception) -> RunAdmissionError:
        if isinstance(exc, RunDataNotFoundError):
            return RunAdmissionNotFoundError(str(exc), code="conversation_not_found")
        if isinstance(exc, RunDataAccessDeniedError):
            return RunAdmissionAccessDeniedError(str(exc), code="conversation_access_denied")
        if isinstance(exc, RunDataConflictError):
            return RunAdmissionConflictError(str(exc), code="object_conflict")
        if isinstance(exc, (RunDataValidationError, RunDataIntegrityError)):
            return RunAdmissionValidationError(str(exc), code="invalid_input_data")
        if isinstance(exc, RunDataStorageUnavailableError):
            return RunAdmissionUnavailableError(
                "Run input storage is temporarily unavailable",
                code="storage_unavailable",
            )
        return RunAdmissionUnavailableError("Run input processing failed", code="storage_error")

    @staticmethod
    def _parse_admission_request(
        *,
        principal: RuntimePrincipal,
        workspace_id: str,
        agent_version_id: str,
        inputs: dict[str, Any],
        conversation_id: str | None,
        attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        idempotency_key: str | None,
    ) -> _ParsedAdmissionRequest:
        try:
            req_workspace_uuid = str(UUID(workspace_id))
            av_uuid = str(UUID(agent_version_id))
            conv_uuid = str(UUID(conversation_id)) if conversation_id is not None else None
        except (ValueError, TypeError) as exc:
            raise RunAdmissionValidationError(
                "Invalid UUID format in run request",
                code="invalid_uuid",
            ) from exc

        if req_workspace_uuid != principal.workspace_id:
            raise RunAdmissionAccessDeniedError(
                "Workspace scope denied: principal does not belong to requested workspace",
                code="workspace_scope_mismatch",
            )

        principal.require_scope("run:start")
        bounded_key = validate_idempotency_key(idempotency_key)
        validate_attachments_no_external_urls(attachments)
        request_sha256 = compute_run_admission_sha256(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            agent_version_id=av_uuid,
            conversation_id=conv_uuid,
            inputs=inputs,
            attachments=attachments,
        )
        return _ParsedAdmissionRequest(
            agent_version_id=av_uuid,
            conversation_id=conv_uuid,
            idempotency_key=bounded_key,
            request_sha256=request_sha256,
        )

    async def _run_preflight(
        self,
        *,
        principal: RuntimePrincipal,
        parsed: _ParsedAdmissionRequest,
        trace_id: str,
    ) -> AdmittedRunResult | None:
        """Phase 1: Short read-only transaction before any object storage I/O."""
        try:
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                caller_role = await self._repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=False,
                )
                if not has_effective_runtime_scope(caller_role, principal, "run:start"):
                    raise RunAdmissionAccessDeniedError(
                        "Active workspace role lacks run:start permission",
                        code="insufficient_scope",
                    )
                existing = await self._repo.lookup_existing_admission(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    external_request_key=parsed.idempotency_key,
                    for_update=False,
                )
                if existing is not None:
                    return self._resolve_replay_or_conflict(
                        existing=existing,
                        principal=principal,
                        request_sha256=parsed.request_sha256,
                    )

                await self._repo.validate_pinned_agent_version(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    agent_version_id=parsed.agent_version_id,
                    actor_user_id=principal.user_id,
                    caller_role=caller_role,
                    for_share=False,
                )
                if parsed.conversation_id is not None:
                    try:
                        await self._run_data_service.validate_conversation_ownership(
                            session,
                            principal=principal,
                            conversation_id=parsed.conversation_id,
                        )
                    except (
                        RunDataNotFoundError,
                        RunDataAccessDeniedError,
                        RunDataValidationError,
                    ) as exc:
                        raise self._map_run_data_error(exc) from exc

                active_count = await self._repo.count_active_workspace_runs(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                )
                if active_count >= self._max_queued_per_workspace:
                    raise RunAdmissionQuotaExceededError(
                        "Workspace active run capacity reached",
                        code="workspace_run_quota_exceeded",
                    )
        except RunAdmissionError:
            raise
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning(
                "Run admission preflight failed (%s)",
                type(exc).__name__,
                extra={
                    "tenant_id": principal.tenant_id,
                    "workspace_id": principal.workspace_id,
                    "trace_id": trace_id,
                },
            )
            raise RunAdmissionUnavailableError(
                "Run admission database temporarily unavailable",
                code="database_unavailable",
            ) from exc
        return None

    async def _persist_new_admission_records(
        self,
        session: Any,
        *,
        principal: RuntimePrincipal,
        caller_role: str,
        parsed: _ParsedAdmissionRequest,
        run_id: str,
        resolved_trace_id: str,
        staged: StagedRunObject,
        after_write_hook: AfterWriteHook | None,
    ) -> None:
        await self._repo.validate_pinned_agent_version(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            agent_version_id=parsed.agent_version_id,
            actor_user_id=principal.user_id,
            caller_role=caller_role,
            for_share=True,
        )
        if parsed.conversation_id is not None:
            try:
                await self._run_data_service.validate_conversation_ownership(
                    session,
                    principal=principal,
                    conversation_id=parsed.conversation_id,
                )
            except (
                RunDataNotFoundError,
                RunDataAccessDeniedError,
                RunDataValidationError,
            ) as exc:
                raise self._map_run_data_error(exc) from exc
            await self._run_data_repo.append_message(
                session,
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                conversation_id=parsed.conversation_id,
                role="user",
                body_ref=staged.receipt.ref,
            )

        # Enforce bounded capacity post-insert (count includes newly inserted queued run).
        active_count = await self._repo.count_active_workspace_runs(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
        )
        if active_count > self._max_queued_per_workspace:
            raise RunAdmissionQuotaExceededError(
                "Workspace active run capacity reached",
                code="workspace_run_quota_exceeded",
            )

        job_id = str(uuid4())
        await self._repo.insert_queued_run_job(
            session,
            job_id=job_id,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
            payload_ref=staged.receipt.ref,
        )
        if after_write_hook is not None:
            await after_write_hook(AdmissionWriteStage.AFTER_JOB_INSERT, session)

        initial_payload = {
            "status": INITIAL_RUN_STATUS,
            "agentVersionId": parsed.agent_version_id,
            "agent_version_id": parsed.agent_version_id,
            "executionAvailable": self._execution_available,
            "execution_available": self._execution_available,
        }
        if not self._execution_available:
            initial_payload["availabilityReason"] = "no_executor_configured"
            initial_payload["availability_reason"] = "no_executor_configured"

        initial_event = await self._repo.insert_initial_run_event(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
            event_type=self._initial_event_type,
            redacted_payload=initial_payload,
        )
        if after_write_hook is not None:
            await after_write_hook(AdmissionWriteStage.AFTER_EVENT_INSERT, session)

        await self._repo.insert_admission_audit_event(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            actor_subject=principal.user_id,
            run_id=run_id,
            operation_key=parsed.idempotency_key,
            request_sha256=parsed.request_sha256,
            agent_version_id=parsed.agent_version_id,
            input_ref=staged.receipt.ref,
            input_sha256=staged.receipt.sha256,
            input_size_bytes=staged.receipt.size_bytes,
            execution_available=self._execution_available,
            trace_id=resolved_trace_id,
        )
        if after_write_hook is not None:
            await after_write_hook(AdmissionWriteStage.AFTER_AUDIT_INSERT, session)

        outbox_event_id = initial_event.eventId
        await self._outbox.publish_in_transaction(
            session,
            OUTBOX_RUN_EVENTS_TOPIC,
            f"{principal.tenant_id}:{principal.workspace_id}",
            initial_event.to_wire_envelope(),
            aggregate_type="run",
            aggregate_id=run_id,
            event_id=outbox_event_id,
            set_rls=False,
        )
        if after_write_hook is not None:
            await after_write_hook(AdmissionWriteStage.AFTER_OUTBOX_INSERT, session)

    async def _run_atomic_admission(
        self,
        *,
        principal: RuntimePrincipal,
        parsed: _ParsedAdmissionRequest,
        run_id: str,
        resolved_trace_id: str,
        staged: StagedRunObject,
        after_write_hook: AfterWriteHook | None,
    ) -> AdmittedRunResult:
        """Phase 3: Atomic DB tx enforcing lock order, live authority, quota, and durability."""
        try:
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                caller_role = await self._repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=True,
                )
                if not has_effective_runtime_scope(caller_role, principal, "run:start"):
                    raise RunAdmissionAccessDeniedError(
                        "Active workspace role lacks run:start permission",
                        code="insufficient_scope",
                    )
                await self._repo.acquire_workspace_admission_lock(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                )

                authority_ciphertext: bytes | None = None
                authority_key_id: str | None = None
                authority_expires_at_dt: datetime | None = None

                if self._execution_grants_enabled:
                    if principal.session_id is None:
                        raise RunAdmissionAccessDeniedError(
                            "Parent application session is required",
                            code="parent_session_missing",
                        )
                    verifier = self._parent_session_verifier or get_parent_session_verifier(
                        self._settings
                    )
                    try:
                        sess_status, sess_data = verifier.verify_parent_session(
                            session_id=principal.session_id,
                            user_id=principal.user_id,
                        )
                    except HTTPException as exc:
                        raise RunAdmissionUnavailableError(
                            "Session verification service temporarily unavailable",
                            code="service_unavailable",
                        ) from exc
                    except Exception as exc:
                        raise RunAdmissionUnavailableError(
                            "Session verification service temporarily unavailable",
                            code="service_unavailable",
                        ) from exc

                    if sess_status != "OK" or not isinstance(sess_data, dict):
                        raise RunAdmissionAccessDeniedError(
                            "Parent session is inactive or revoked",
                            code="parent_session_inactive",
                        )

                    now_dt = datetime.now(UTC)
                    parent_abs_exp_ts = sess_data.get("absolute_expires_at")
                    max_grant_dt = now_dt + timedelta(hours=24)
                    if isinstance(parent_abs_exp_ts, (int, float)) and parent_abs_exp_ts > 0:
                        parent_abs_dt = datetime.fromtimestamp(parent_abs_exp_ts, tz=UTC)
                        authority_expires_at_dt = min(parent_abs_dt, max_grant_dt)
                    else:
                        authority_expires_at_dt = max_grant_dt

                    grant = ExecutionGrant(
                        issuer=self._jwt_issuer or "agent-runtime",
                        subject=principal.subject,
                        user_id=principal.user_id,
                        tenant_id=principal.tenant_id,
                        workspace_id=principal.workspace_id,
                        run_id=run_id,
                        parent_session_id=principal.session_id,
                        parent_rotation_version=int(sess_data.get("rotation_version", 1)),
                        original_scopes=tuple(sorted(principal.token_scopes or principal.scopes))[
                            :64
                        ],
                        granted_at=now_dt.isoformat(),
                        authority_expires_at=authority_expires_at_dt.isoformat(),
                    )

                    try:
                        key_bytes = normalize_encryption_key(self._execution_key)
                        authority_ciphertext, authority_key_id, _ = encrypt_execution_grant(
                            grant=grant,
                            key=key_bytes,
                            key_id=self._execution_key_id,
                        )
                    except ExecutionKeyUnavailableError as exc:
                        raise RunAdmissionUnavailableError(
                            "Run execution encryption key is unavailable",
                            code="service_unavailable",
                        ) from exc

                insert_kwargs: dict[str, Any] = {
                    "session": session,
                    "run_id": run_id,
                    "tenant_id": principal.tenant_id,
                    "workspace_id": principal.workspace_id,
                    "agent_version_id": parsed.agent_version_id,
                    "conversation_id": parsed.conversation_id,
                    "initiated_by": principal.user_id,
                    "external_request_key": parsed.idempotency_key,
                    "trace_id": resolved_trace_id,
                    "request_sha256": parsed.request_sha256,
                    "input_ref": staged.receipt.ref,
                    "input_sha256": staged.receipt.sha256,
                    "input_size_bytes": staged.receipt.size_bytes,
                    "queue_ttl_seconds": self._queue_ttl_seconds,
                }
                if self._execution_grants_enabled:
                    insert_kwargs["execution_authority_ciphertext"] = authority_ciphertext
                    insert_kwargs["execution_authority_key_id"] = authority_key_id
                    insert_kwargs["authority_expires_at"] = authority_expires_at_dt

                inserted = await self._repo.insert_run_if_absent(**insert_kwargs)
                if not inserted:
                    existing = await self._repo.lookup_existing_admission(
                        session,
                        tenant_id=principal.tenant_id,
                        workspace_id=principal.workspace_id,
                        external_request_key=parsed.idempotency_key,
                        for_update=True,
                    )
                    if existing is None:
                        raise RunAdmissionUnavailableError(
                            "Concurrent run admission state could not be verified",
                            code="admission_state_unavailable",
                        )
                    return self._resolve_replay_or_conflict(
                        existing=existing,
                        principal=principal,
                        request_sha256=parsed.request_sha256,
                    )

                if after_write_hook is not None:
                    await after_write_hook(AdmissionWriteStage.AFTER_RUN_INSERT, session)

                await self._persist_new_admission_records(
                    session,
                    principal=principal,
                    caller_role=caller_role,
                    parsed=parsed,
                    run_id=run_id,
                    resolved_trace_id=resolved_trace_id,
                    staged=staged,
                    after_write_hook=after_write_hook,
                )
        except RunAdmissionError:
            raise
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning(
                "Run admission transaction outcome unavailable (%s)",
                type(exc).__name__,
                extra={
                    "tenant_id": principal.tenant_id,
                    "workspace_id": principal.workspace_id,
                    "run_id": run_id,
                    "trace_id": resolved_trace_id,
                },
            )
            raise RunAdmissionUnavailableError(
                (
                    "Run admission commit outcome could not be verified; "
                    "retry with the same Idempotency-Key"
                ),
                code="admission_outcome_unknown",
            ) from exc

        return AdmittedRunResult(
            run_id=run_id,
            agent_version_id=parsed.agent_version_id,
            conversation_id=parsed.conversation_id,
            status=INITIAL_RUN_STATUS,
            replayed=False,
            execution_available=self._execution_available,
            availability_reason=self._availability_reason,
        )

    async def admit_run(
        self,
        *,
        principal: RuntimePrincipal,
        workspace_id: str,
        agent_version_id: str,
        inputs: dict[str, Any],
        conversation_id: str | None = None,
        attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        after_write_hook: AfterWriteHook | None = None,
    ) -> AdmittedRunResult:
        """Admit a run durably and idempotently without holding DB locks across object I/O."""
        parsed = self._parse_admission_request(
            principal=principal,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            inputs=inputs,
            conversation_id=conversation_id,
            attachments=attachments,
            idempotency_key=idempotency_key,
        )
        resolved_trace_id = trace_id or uuid4().hex

        replay = await self._run_preflight(
            principal=principal,
            parsed=parsed,
            trace_id=resolved_trace_id,
        )
        if replay is not None:
            return replay

        run_id = str(uuid4())
        try:
            staged = await self._run_data_service.stage_run_input(
                principal=principal,
                run_id=run_id,
                agent_version_id=parsed.agent_version_id,
                inputs=inputs,
                conversation_id=parsed.conversation_id,
                attachments=attachments,
                trace_id=resolved_trace_id,
            )
        except (
            RunDataValidationError,
            RunDataIntegrityError,
            RunDataConflictError,
            RunDataStorageUnavailableError,
        ) as exc:
            raise self._map_run_data_error(exc) from exc

        return await self._run_atomic_admission(
            principal=principal,
            parsed=parsed,
            run_id=run_id,
            resolved_trace_id=resolved_trace_id,
            staged=staged,
            after_write_hook=after_write_hook,
        )

    async def get_run_metadata(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
    ) -> AdmittedRunMetadata:
        """Fetch redacted run metadata inside trusted workspace scope."""
        principal.require_scope("run:read")
        try:
            run_uuid = str(UUID(run_id))
        except (ValueError, TypeError) as exc:
            raise RunAdmissionValidationError(
                "Invalid run_id UUID format",
                code="invalid_uuid",
            ) from exc

        try:
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                caller_role = await self._repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=False,
                )
                if not has_effective_runtime_scope(caller_role, principal, "run:read"):
                    raise RunAdmissionAccessDeniedError(
                        "Active workspace role lacks run:read permission",
                        code="insufficient_scope",
                    )
                record = await self._repo.get_run_metadata(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=run_uuid,
                )
        except RunAdmissionError:
            raise
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning(
                "Get run metadata failed (%s)",
                type(exc).__name__,
                extra={
                    "tenant_id": principal.tenant_id,
                    "workspace_id": principal.workspace_id,
                    "run_id": run_uuid,
                },
            )
            raise RunAdmissionUnavailableError(
                "Run metadata store temporarily unavailable",
                code="database_unavailable",
            ) from exc

        if record is None:
            raise RunAdmissionNotFoundError(
                f"Run '{run_id}' not found",
                code="run_not_found",
            )

        is_creator = record.initiated_by is not None and str(UUID(record.initiated_by)) == str(
            UUID(principal.user_id)
        )
        if not is_creator:
            role = caller_role.lower()
            if role not in ("owner", "admin", "operator"):
                raise RunAdmissionNotFoundError(
                    f"Run '{run_id}' not found",
                    code="run_not_found",
                )
        return record

    async def resume_run(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
    ) -> None:
        """Fail closed unconditionally until durable approval proof verification (RD-09) exists."""
        _ = (principal, run_id)
        raise RunResumeDisabledError()
