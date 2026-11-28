"""Typed run-admission contracts, canonical intent hashing, errors, and ports (06-B)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from agent_contracts.manifest import CanonicalizationError, canonicalize_json
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.application.run_events import RunEventDTO

MIN_IDEMPOTENCY_KEY_LENGTH = 1
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_ATTACHMENTS_PER_RUN = 100
ADMISSION_OPERATION_NAME = "run:start:v1"
INITIAL_RUN_STATUS = "queued"
DEFAULT_ADMISSION_EVENT_TYPE = "run.queued"
ADMISSION_EVENT_SCHEMA_VERSION = "1.1"
OUTBOX_RUN_EVENTS_TOPIC = "runtime.run.events"


class AdmissionWriteStage(StrEnum):
    """Transaction write stages where fault-injection/crash hooks execute."""

    AFTER_RUN_INSERT = "after_run_insert"
    AFTER_JOB_INSERT = "after_job_insert"
    AFTER_EVENT_INSERT = "after_event_insert"
    AFTER_AUDIT_INSERT = "after_audit_insert"
    AFTER_OUTBOX_INSERT = "after_outbox_insert"


class RunAdmissionError(RuntimeError):
    """Base domain error for runtime run admission."""

    def __init__(self, message: str, *, code: str = "run_admission_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunAdmissionValidationError(RunAdmissionError):
    """Raised on invalid request payload, UUID, key, or unsupported agent spec."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message, code=code)


class RunAdmissionAccessDeniedError(RunAdmissionError):
    """Raised on workspace scope, membership, role, or dependency grant denial."""

    def __init__(self, message: str, *, code: str = "access_denied") -> None:
        super().__init__(message, code=code)


class RunAdmissionNotFoundError(RunAdmissionError):
    """Raised when a run, agent version, or conversation does not exist in scope."""

    def __init__(self, message: str, *, code: str = "not_found") -> None:
        super().__init__(message, code=code)


class RunAdmissionConflictError(RunAdmissionError):
    """Raised on idempotency mismatch, archived agent, unsealed version, or disabled dependency."""

    def __init__(self, message: str, *, code: str = "conflict") -> None:
        super().__init__(message, code=code)


class RunAdmissionQuotaExceededError(RunAdmissionError):
    """Raised when workspace active/queued run admission capacity is reached."""

    def __init__(self, message: str, *, code: str = "quota_exceeded") -> None:
        super().__init__(message, code=code)


class RunAdmissionUnavailableError(RunAdmissionError):
    """Raised when DB, object storage, or commit verification is unavailable (fail-closed)."""

    def __init__(self, message: str, *, code: str = "service_unavailable") -> None:
        super().__init__(message, code=code)


class RunResumeDisabledError(RunAdmissionError):
    """Raised unconditionally until durable approval proof verification (RD-09) is implemented."""

    def __init__(
        self,
        message: str = "Run resume is disabled pending verified durable approval proof (RD-09)",
        *,
        code: str = "resume_disabled",
    ) -> None:
        super().__init__(message, code=code)


class StartRunRequestDTO(BaseModel):
    """Strict request DTO for POST /v1/runs (extra fields forbidden)."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    agent_version_id: UUID
    inputs: dict[str, Any] = Field(default_factory=dict)
    conversation_id: UUID | None = None
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_ATTACHMENTS_PER_RUN,
    )


class StartRunResponseDTO(BaseModel):
    """Strict 202 Accepted response DTO for POST /v1/runs (D6-03 availability aware)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_version_id: str
    conversation_id: str | None
    status: str
    replayed: bool
    execution_available: bool
    availability_reason: str | None = None


class RunMetadataResponseDTO(BaseModel):
    """Strict redacted metadata DTO for GET /v1/runs/{run_id} (D6-01)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_version_id: str
    conversation_id: str | None
    workflow_version_id: str | None = None
    status: str
    trace_id: str | None
    next_event_seq: int
    started_at: str | None
    finished_at: str | None
    created_at: str | None
    queue_expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class ExistingRunAdmission:
    """Authoritative persisted admission state used for idempotent replay/conflict checks."""

    run_id: str
    agent_version_id: str
    conversation_id: str | None
    initiated_by: str | None
    status: str
    request_sha256: str | None
    receipt_expired: bool = False


@dataclass(frozen=True, slots=True)
class PinnedAgentVersionRecord:
    """Verified, sealed, non-archived AgentVersion snapshot pinned at run admission."""

    agent_id: str
    agent_version_id: str
    version: int
    archived_at: datetime | None
    bindings_sealed_at: datetime | None
    model_config_id: str | None
    frozen_spec: dict[str, Any]
    bundle_manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AdmittedRunResult:
    """Result of a durable run admission or authorized idempotent replay."""

    run_id: str
    agent_version_id: str
    conversation_id: str | None
    status: str
    replayed: bool
    execution_available: bool
    availability_reason: str | None


@dataclass(frozen=True, slots=True)
class AdmittedRunMetadata:
    """Redacted run metadata record (excludes raw inputs, outputs, and storage refs)."""

    run_id: str
    agent_version_id: str
    conversation_id: str | None
    workflow_version_id: str | None
    initiated_by: str | None
    status: str
    trace_id: str | None
    next_event_seq: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    queue_expires_at: datetime | None = None


def validate_idempotency_key(raw_key: str | None) -> str:
    """Validate bounded Idempotency-Key header (1..200 chars, no control newlines)."""
    if raw_key is None:
        raise RunAdmissionValidationError(
            "Idempotency-Key header is required",
            code="missing_idempotency_key",
        )
    if "\r" in raw_key or "\n" in raw_key or "\x00" in raw_key:
        raise RunAdmissionValidationError(
            "Idempotency-Key contains forbidden control characters",
            code="invalid_idempotency_key",
        )
    stripped = raw_key.strip()
    if (
        not stripped
        or len(raw_key) < MIN_IDEMPOTENCY_KEY_LENGTH
        or len(raw_key) > MAX_IDEMPOTENCY_KEY_LENGTH
        or len(stripped) > MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise RunAdmissionValidationError(
            (
                f"Idempotency-Key must be between {MIN_IDEMPOTENCY_KEY_LENGTH} "
                f"and {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
            ),
            code="invalid_idempotency_key",
        )
    return stripped


def compute_run_admission_sha256(
    *,
    operation: str = ADMISSION_OPERATION_NAME,
    tenant_id: str | UUID,
    workspace_id: str | UUID,
    actor_user_id: str | UUID,
    agent_version_id: str | UUID,
    conversation_id: str | UUID | None,
    inputs: dict[str, Any],
    attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> str:
    """Compute RFC 8785 deterministic SHA-256 digest of the canonical admission intent.

    Binds operation, trusted tenant/workspace/actor identity, pinned agent_version_id,
    optional conversation_id, and canonical input/attachment payloads. Never relies on
    publish-specific compute_request_sha256 or mutable state.
    """
    envelope: dict[str, Any] = {
        "operation": operation,
        "tenant_id": str(UUID(str(tenant_id))),
        "workspace_id": str(UUID(str(workspace_id))),
        "actor_user_id": str(UUID(str(actor_user_id))),
        "agent_version_id": str(UUID(str(agent_version_id))),
        "conversation_id": str(UUID(str(conversation_id))) if conversation_id is not None else None,
        "inputs": inputs,
        "attachments": list(attachments),
    }
    try:
        canonical_bytes = canonicalize_json(envelope)
    except CanonicalizationError as exc:
        raise RunAdmissionValidationError(
            f"Run request contains values that cannot be canonically serialized: {exc}",
            code="invalid_canonical_json",
        ) from exc
    return hashlib.sha256(canonical_bytes).hexdigest()


def validate_attachments_no_external_urls(
    attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> None:
    """Reject bounded excess, untrusted external URLs, or path traversal in attachment metadata."""
    if len(attachments) > MAX_ATTACHMENTS_PER_RUN:
        raise RunAdmissionValidationError(
            f"attachments exceeds maximum allowed count ({MAX_ATTACHMENTS_PER_RUN})",
            code="too_many_attachments",
        )
    for idx, item in enumerate(attachments):
        if not isinstance(item, dict):
            raise RunAdmissionValidationError(f"attachments[{idx}] must be an object")
        for key, value in item.items():
            if not isinstance(key, str):
                raise RunAdmissionValidationError("Attachment keys must be strings")
            if isinstance(value, str):
                lower = value.strip().lower()
                if lower.startswith(("http://", "https://", "file://", "ftp://")):
                    raise RunAdmissionValidationError(
                        f"attachments[{idx}].{key} must not contain external or file URLs",
                        code="unsafe_attachment_ref",
                    )
                if ".." in value or "\\" in value or "\x00" in value:
                    raise RunAdmissionValidationError(
                        f"attachments[{idx}].{key} contains forbidden path characters",
                        code="unsafe_attachment_ref",
                    )


class RunAdmissionRepositoryPort(Protocol):
    """Persistence port for atomic run admission and metadata queries."""

    async def verify_active_workspace_membership(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        for_share: bool = False,
    ) -> str: ...

    async def acquire_workspace_admission_lock(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None: ...

    async def lookup_existing_admission(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        external_request_key: str,
        for_update: bool = False,
    ) -> ExistingRunAdmission | None: ...

    async def validate_pinned_agent_version(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        agent_version_id: str,
        actor_user_id: str,
        caller_role: str,
        for_share: bool = False,
    ) -> PinnedAgentVersionRecord: ...

    async def count_active_workspace_runs(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> int: ...

    async def insert_run_if_absent(
        self,
        session: Any,
        *,
        run_id: str,
        tenant_id: str,
        workspace_id: str,
        agent_version_id: str,
        conversation_id: str | None,
        initiated_by: str,
        external_request_key: str,
        trace_id: str | None,
        request_sha256: str,
        input_ref: str,
        input_sha256: str,
        input_size_bytes: int,
        queue_ttl_seconds: int = 86400,
    ) -> bool: ...

    async def insert_queued_run_job(
        self,
        session: Any,
        *,
        job_id: str,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        payload_ref: str,
    ) -> str: ...

    async def insert_initial_run_event(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        event_type: str,
        redacted_payload: dict[str, Any],
    ) -> RunEventDTO: ...

    async def insert_admission_audit_event(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_subject: str,
        run_id: str,
        operation_key: str,
        request_sha256: str,
        agent_version_id: str,
        input_ref: str,
        input_sha256: str,
        input_size_bytes: int,
        execution_available: bool,
        trace_id: str | None,
    ) -> str: ...

    async def get_run_metadata(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> AdmittedRunMetadata | None: ...


class RunMessageRepositoryPort(Protocol):
    """Port for appending user message references to an owned conversation."""

    async def append_message(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
        role: str,
        body_ref: str | None = None,
        body_ciphertext: bytes | None = None,
        token_count: int | None = None,
        message_id: str | None = None,
    ) -> str: ...


class OutboxPublisherPort(Protocol):
    """Port matching PostgresOutboxEventBus.publish_in_transaction."""

    async def publish_in_transaction(
        self,
        session: Any,
        topic: str,
        key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | UUID | None = None,
        event_id: str | UUID | None = None,
        set_rls: bool = False,
    ) -> str: ...
