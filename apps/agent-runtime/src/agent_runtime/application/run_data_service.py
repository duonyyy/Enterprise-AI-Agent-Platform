"""Application service for durable run data, ownership (D6-01), and retention (D6-02)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_data import (
    DEFAULT_CONTENT_TYPE,
    ObjectStorePort,
    ParsedObjectRef,
    RunDataAccessDeniedError,
    RunDataNotFoundError,
    RunDataRetentionError,
    RunDataValidationError,
    StoredObjectReceipt,
    build_input_manifest,
    build_result_manifest,
    build_scoped_object_ref,
    deserialize_and_verify_manifest,
    parse_scoped_object_ref,
    serialize_manifest,
)
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.persistence.run_data_repository import (
    TERMINAL_RUN_STATUSES,
    RetentionPolicyRecord,
    RunDataRecord,
    RunDataRepository,
)


class RunDataRepositoryPort(Protocol):
    """Explicit repository port for durable run-data persistence (AR-008)."""

    async def get_conversation(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> Any: ...

    async def activate_run_input(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        agent_version_id: str,
        initiated_by: str,
        external_request_key: str,
        input_ref: str,
        conversation_id: str | None = None,
        job_id: str | None = None,
    ) -> str: ...

    async def get_run_record(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> RunDataRecord | None: ...

    async def get_run_input_ref(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> str | None: ...

    async def get_retention_policy(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        data_class: str = ...,
        default_retention_days: int = 30,
    ) -> RetentionPolicyRecord: ...

    async def has_active_runs_for_conversation(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class StagedRunObject:
    """Object written and verified in object store prior to DB activation."""

    receipt: StoredObjectReceipt
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuthorizedRunRef:
    """DB-verified, creator-authorized, retention-checked object reference."""

    run: RunDataRecord
    parsed_ref: ParsedObjectRef
    policy: RetentionPolicyRecord
    expected_sha256: str
    expected_size_bytes: int


@dataclass(frozen=True, slots=True)
class RetentionEvaluation:
    """Read-only D6-02 retention and legal-hold evaluation result."""

    run_id: str
    status: str
    retention_days: int
    legal_hold: bool
    finished_at: datetime | None
    expires_at: datetime | None
    has_active_conversation_runs: bool
    eligible_for_cleanup: bool
    reason: str


def _utc_now(now: datetime | None) -> datetime:
    current = now if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


class RunDataService:
    """Coordinates object-before-DB staging, creator-only reads, and retention evaluation."""

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        bucket: str,
        repository: RunDataRepositoryPort | None = None,
        max_input_bytes: int = 1_048_576,
        max_output_bytes: int = 4_194_304,
        default_retention_days: int = 30,
    ) -> None:
        self._object_store = object_store
        self._bucket = bucket
        self._repo = repository or RunDataRepository()
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._default_retention_days = default_retention_days

    async def validate_conversation_ownership(
        self,
        session: AsyncSession,
        *,
        principal: RuntimePrincipal,
        conversation_id: str,
    ) -> None:
        """Verify conversation exists in caller's scope, is active, and belongs to principal."""
        conv = await self._repo.get_conversation(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            conversation_id=conversation_id,
        )
        if conv is None:
            raise RunDataNotFoundError("Conversation not found in workspace")
        if conv.external_subject != str(UUID(principal.user_id)):
            raise RunDataAccessDeniedError("Conversation belongs to a different creator (D6-01)")
        if conv.status != "active":
            raise RunDataValidationError("Cannot attach a closed conversation to a new run")

    async def stage_run_input(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
        agent_version_id: str,
        inputs: dict[str, Any],
        conversation_id: str | None = None,
        attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        trace_id: str | None = None,
    ) -> StagedRunObject:
        """Canonicalize, size-check, upload, and read-back verify run input outside DB tx."""
        manifest = build_input_manifest(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
            initiated_by=principal.user_id,
            agent_version_id=agent_version_id,
            inputs=inputs,
            conversation_id=conversation_id,
            attachments=attachments,
            trace_id=trace_id,
        )
        raw_bytes, sha256, size_bytes = serialize_manifest(
            manifest, max_bytes=self._max_input_bytes
        )
        _, key = build_scoped_object_ref(
            bucket=self._bucket,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
            kind="input",
            sha256=sha256,
            size_bytes=size_bytes,
        )
        receipt = await self._object_store.put_immutable_object(
            bucket=self._bucket,
            key=key,
            data=raw_bytes,
            expected_sha256=sha256,
            content_type=DEFAULT_CONTENT_TYPE,
        )
        return StagedRunObject(receipt=receipt, manifest=manifest)

    async def activate_staged_input(
        self,
        session: AsyncSession,
        *,
        principal: RuntimePrincipal,
        run_id: str,
        agent_version_id: str,
        external_request_key: str,
        staged: StagedRunObject,
        conversation_id: str | None = None,
    ) -> str:
        """Activate a pre-verified staged input object inside caller-owned DB transaction."""
        if conversation_id is not None:
            await self.validate_conversation_ownership(
                session,
                principal=principal,
                conversation_id=conversation_id,
            )
        return await self._repo.activate_run_input(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
            agent_version_id=agent_version_id,
            initiated_by=principal.user_id,
            external_request_key=external_request_key,
            input_ref=staged.receipt.ref,
            conversation_id=conversation_id,
        )

    async def stage_run_result(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        initiated_by: str,
        attempt: int,
        outcome: str,
        output: dict[str, Any],
        provenance: dict[str, Any] | None = None,
    ) -> StagedRunObject:
        """Stage an immutable run result object (port for future executor 07 / fixture)."""
        manifest = build_result_manifest(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            initiated_by=initiated_by,
            attempt=attempt,
            outcome=outcome,
            output=output,
            provenance=provenance,
        )
        raw_bytes, sha256, size_bytes = serialize_manifest(
            manifest, max_bytes=self._max_output_bytes
        )
        _, key = build_scoped_object_ref(
            bucket=self._bucket,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            kind="output",
            sha256=sha256,
            size_bytes=size_bytes,
        )
        receipt = await self._object_store.put_immutable_object(
            bucket=self._bucket,
            key=key,
            data=raw_bytes,
            expected_sha256=sha256,
            content_type=DEFAULT_CONTENT_TYPE,
        )
        return StagedRunObject(receipt=receipt, manifest=manifest)

    async def authorize_run_input_read(
        self,
        session: AsyncSession,
        *,
        principal: RuntimePrincipal,
        run_id: str,
        now: datetime | None = None,
    ) -> AuthorizedRunRef:
        """Verify creator-only access (D6-01), retention (D6-02), and scoped ref inside short DB tx."""
        run = await self._repo.get_run_record(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
        )
        if run is None:
            raise RunDataNotFoundError("Run not found in workspace")

        # D6-01: Human creator only. Operational roles (Owner/Admin/Operator) do NOT override.
        if run.initiated_by is None or run.initiated_by != str(UUID(principal.user_id)):
            raise RunDataAccessDeniedError("Run content is restricted to the human creator (D6-01)")

        policy = await self._repo.get_retention_policy(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            default_retention_days=self._default_retention_days,
        )

        if (
            run.status in TERMINAL_RUN_STATUSES
            and run.finished_at is not None
            and not policy.legal_hold
        ):
            expires_at = _utc_now(run.finished_at) + timedelta(days=policy.retention_days)
            if _utc_now(now) >= expires_at:
                raise RunDataRetentionError("Run content has expired past retention window")

        input_ref = await self._repo.get_run_input_ref(
            session,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            run_id=run_id,
        )
        if input_ref is None:
            raise RunDataNotFoundError("Run input reference not found")

        parsed_ref = parse_scoped_object_ref(
            input_ref,
            expected_bucket=self._bucket,
            expected_tenant_id=principal.tenant_id,
            expected_workspace_id=principal.workspace_id,
            expected_run_id=run.id,
        )
        return AuthorizedRunRef(
            run=run,
            parsed_ref=parsed_ref,
            policy=policy,
            expected_sha256=parsed_ref.expected_sha256,
            expected_size_bytes=parsed_ref.expected_size_bytes,
        )

    async def load_authorized_manifest(
        self,
        authorized: AuthorizedRunRef,
    ) -> dict[str, Any]:
        """Fetch and verify object bytes against durable PG metadata outside any DB transaction."""
        raw_bytes = await self._object_store.get_verified_object(
            bucket=authorized.parsed_ref.bucket,
            key=authorized.parsed_ref.key,
            expected_sha256=authorized.expected_sha256,
            expected_size_bytes=authorized.expected_size_bytes,
            max_bytes=self._max_input_bytes,
        )
        return deserialize_and_verify_manifest(
            raw_bytes,
            expected_sha256=authorized.expected_sha256,
            expected_size_bytes=authorized.expected_size_bytes,
            expected_tenant_id=authorized.run.tenant_id,
            expected_workspace_id=authorized.run.workspace_id,
            expected_run_id=authorized.run.id,
            expected_initiated_by=authorized.run.initiated_by,
        )

    async def evaluate_run_content_retention(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> RetentionEvaluation:
        """Evaluate D6-02 cleanup eligibility without mutating or deleting data."""
        run = await self._repo.get_run_record(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
        )
        if run is None:
            raise RunDataNotFoundError("Run not found in workspace")

        policy = await self._repo.get_retention_policy(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            default_retention_days=self._default_retention_days,
        )
        has_active_conv = False
        if run.conversation_id is not None:
            has_active_conv = await self._repo.has_active_runs_for_conversation(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                conversation_id=run.conversation_id,
            )

        if run.status not in TERMINAL_RUN_STATUSES or run.finished_at is None:
            return RetentionEvaluation(
                run_id=run.id,
                status=run.status,
                retention_days=policy.retention_days,
                legal_hold=policy.legal_hold,
                finished_at=run.finished_at,
                expires_at=None,
                has_active_conversation_runs=has_active_conv,
                eligible_for_cleanup=False,
                reason="run_not_terminal",
            )

        finished_utc = _utc_now(run.finished_at)
        expires_at = finished_utc + timedelta(days=policy.retention_days)
        current_utc = _utc_now(now)

        if policy.legal_hold:
            return RetentionEvaluation(
                run_id=run.id,
                status=run.status,
                retention_days=policy.retention_days,
                legal_hold=True,
                finished_at=finished_utc,
                expires_at=expires_at,
                has_active_conversation_runs=has_active_conv,
                eligible_for_cleanup=False,
                reason="legal_hold_active",
            )

        if has_active_conv:
            return RetentionEvaluation(
                run_id=run.id,
                status=run.status,
                retention_days=policy.retention_days,
                legal_hold=False,
                finished_at=finished_utc,
                expires_at=expires_at,
                has_active_conversation_runs=True,
                eligible_for_cleanup=False,
                reason="conversation_has_active_runs",
            )

        if current_utc < expires_at:
            return RetentionEvaluation(
                run_id=run.id,
                status=run.status,
                retention_days=policy.retention_days,
                legal_hold=False,
                finished_at=finished_utc,
                expires_at=expires_at,
                has_active_conversation_runs=False,
                eligible_for_cleanup=False,
                reason="within_retention_window",
            )

        return RetentionEvaluation(
            run_id=run.id,
            status=run.status,
            retention_days=policy.retention_days,
            legal_hold=False,
            finished_at=finished_utc,
            expires_at=expires_at,
            has_active_conversation_runs=False,
            eligible_for_cleanup=True,
            reason="expired_and_unheld",
        )
