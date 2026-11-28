"""Single state owner for run and job lifecycle transitions under active lease fence (DEC-07 D7-02 / D7-04).

Replaces legacy scattered _set_run_status / _complete / _fail paths for the RUN loop.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Final

from platform_common.event_bus import PostgresOutboxEventBus
from platform_common.execution import (
    ExecutionDisposition,
    ExecutionDispositionType,
    LeaseIdentity,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.execution.grant import (
    ExecutionGrant,
    ExecutionGrantError,
    decrypt_execution_grant,
    normalize_encryption_key,
)
from agent_runtime.application.run_admission import OUTBOX_RUN_EVENTS_TOPIC, OutboxPublisherPort
from agent_runtime.auth import (
    ParentSessionVerifierProtocol,
    RuntimePrincipal,
    get_parent_session_verifier,
)
from agent_runtime.persistence.database import get_session_factory, scoped_session
from agent_runtime.persistence.execution_repository import (
    BeginExecutionResult,
    ClaimedJobRecord,
    ExecutionRepository,
    LostLeaseError,
)
from agent_runtime.settings import RuntimeSettings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRY_BACKOFF_SECONDS: Final[float] = 60.0
DEFAULT_RETRY_BACKOFF_BASE: Final[float] = 2.0
MAX_JITTER_SECONDS: Final[float] = 1.0

SessionFactory = Callable[[str, str, str | None], AbstractAsyncContextManager[AsyncSession]]


class ExecutionStateOwner:
    """Authoritative coordinator for all run/job mutations under active lease fencing."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = scoped_session,
        repository: ExecutionRepository | None = None,
        outbox_publisher: OutboxPublisherPort | None = None,
        parent_session_verifier: ParentSessionVerifierProtocol | None = None,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repo = repository if repository is not None else ExecutionRepository()
        self._outbox = (
            outbox_publisher
            if outbox_publisher is not None
            else PostgresOutboxEventBus(lambda: get_session_factory()())
        )
        self._verifier = parent_session_verifier
        self._settings = settings or get_settings()

    async def claim_next_job(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        worker_id: str,
        lease_token: str,
        lease_duration_seconds: int = 30,
        max_attempts: int = 3,
        workspace_concurrency_cap: int = 2,
        require_grant: bool = False,
    ) -> ClaimedJobRecord | None:
        """Atomically claim one due job in workspace under advisory xact lock and capacity cap."""
        async with self._session_factory(tenant_id, workspace_id, None) as session:
            return await self._repo.claim_next_job(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_duration_seconds=lease_duration_seconds,
                max_attempts=max_attempts,
                workspace_concurrency_cap=workspace_concurrency_cap,
                require_grant=require_grant,
            )

    async def heartbeat(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_duration_seconds: int = 30,
        principal: RuntimePrincipal | None = None,
    ) -> bool:
        """Dedicated connection heartbeat update. Also rechecks live authority if principal given."""
        if principal is not None and principal.session_id is not None:
            verifier = self._verifier or get_parent_session_verifier(self._settings)
            try:
                status_str, _ = verifier.verify_parent_session(
                    session_id=principal.session_id,
                    user_id=principal.user_id,
                )
                if status_str != "OK":
                    logger.warning(
                        "Heartbeat aborted: parent session revoked (%s)",
                        status_str,
                        extra={
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "job_id": job_id,
                            "worker_id": worker_id,
                            "session_status": status_str,
                        },
                    )
                    return False
            except (ConnectionError, TimeoutError, OSError, ValueError, KeyError) as exc:
                logger.warning(
                    "Heartbeat parent session check failed (%s)",
                    type(exc).__name__,
                    extra={
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "job_id": job_id,
                        "worker_id": worker_id,
                        "error_type": type(exc).__name__,
                    },
                )
                return False

        async with self._session_factory(tenant_id, workspace_id, None) as session:
            return await self._repo.extend_lease(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_duration_seconds=lease_duration_seconds,
            )

    async def begin_execution(
        self,
        *,
        lease_identity: LeaseIdentity,
        max_wall_time_seconds: int = 180,
        require_grant: bool = False,
    ) -> tuple[BeginExecutionResult, ExecutionGrant | None, RuntimePrincipal | None]:
        """Verify fence, deadline, decrypt and revalidate execution grant, transition to running."""
        t_id = lease_identity.tenant_id
        w_id = lease_identity.workspace_id
        r_id = lease_identity.run_id
        if r_id is None:
            raise LostLeaseError("Run job requires run_id")

        async with self._session_factory(t_id, w_id, None) as session:
            result = await self._repo.begin_execution(
                session,
                tenant_id=t_id,
                workspace_id=w_id,
                job_id=lease_identity.job_id,
                run_id=r_id,
                worker_id=lease_identity.leased_by,
                lease_token=lease_identity.lease_token,
                attempt=lease_identity.attempt,
                max_wall_time_seconds=max_wall_time_seconds,
            )

        if not result.success:
            return result, None, None

        # Re-verify grant if present or required
        grant: ExecutionGrant | None = None
        principal: RuntimePrincipal | None = None
        if result.authority_ciphertext is not None:
            try:
                key_bytes = normalize_encryption_key(self._settings.run_execution_key)
                grant = decrypt_execution_grant(
                    result.authority_ciphertext,
                    key_bytes,
                    tenant_id=t_id,
                    workspace_id=w_id,
                    run_id=r_id,
                )
            except (ExecutionGrantError, ValueError, KeyError) as exc:
                logger.warning(
                    "Execution grant decryption/integrity failed (%s)",
                    exc,
                    extra={
                        "tenant_id": t_id,
                        "workspace_id": w_id,
                        "run_id": r_id,
                        "job_id": lease_identity.job_id,
                        "error_type": type(exc).__name__,
                    },
                )
                await self.fail_execution(
                    lease_identity=lease_identity,
                    error_code="execution_authority_denied",
                    closed_reason="authority_denied",
                    message="Execution grant ciphertext corrupted or tampered",
                )
                return (
                    BeginExecutionResult(
                        success=False,
                        started=False,
                        error_code="execution_authority_denied",
                    ),
                    None,
                    None,
                )

            # Check grant expiry
            now_dt = datetime.now(UTC)
            grant_exp = datetime.fromisoformat(grant.authority_expires_at)
            if now_dt >= grant_exp:
                logger.warning(
                    "Execution grant expired at %s",
                    grant.authority_expires_at,
                    extra={
                        "tenant_id": t_id,
                        "workspace_id": w_id,
                        "run_id": r_id,
                        "job_id": lease_identity.job_id,
                        "expired_at": grant.authority_expires_at,
                    },
                )
                await self.fail_execution(
                    lease_identity=lease_identity,
                    error_code="execution_authority_denied",
                    closed_reason="authority_denied",
                    message="Execution grant has expired",
                )
                return (
                    BeginExecutionResult(
                        success=False,
                        started=False,
                        error_code="execution_authority_denied",
                    ),
                    None,
                    None,
                )

            # Re-verify parent session in Redis
            verifier = self._verifier or get_parent_session_verifier(self._settings)
            try:
                status_str, sess_data = verifier.verify_parent_session(
                    session_id=grant.parent_session_id,
                    user_id=grant.user_id,
                )
            except (ConnectionError, TimeoutError, OSError, ValueError, KeyError) as exc:
                logger.warning(
                    "Parent session verification failed (%s)",
                    type(exc).__name__,
                    extra={
                        "tenant_id": t_id,
                        "workspace_id": w_id,
                        "run_id": r_id,
                        "error_type": type(exc).__name__,
                    },
                )
                status_str = "ERROR"

            if status_str != "OK" or not isinstance(sess_data, dict):
                logger.warning(
                    "Parent session is revoked or inactive (%s)",
                    status_str,
                    extra={
                        "tenant_id": t_id,
                        "workspace_id": w_id,
                        "run_id": r_id,
                        "session_status": status_str,
                    },
                )
                await self.fail_execution(
                    lease_identity=lease_identity,
                    error_code="execution_authority_denied",
                    closed_reason="authority_denied",
                    message="Parent application session revoked or inactive",
                )
                return (
                    BeginExecutionResult(
                        success=False,
                        started=False,
                        error_code="execution_authority_denied",
                    ),
                    None,
                    None,
                )

            principal = RuntimePrincipal(
                subject=grant.subject,
                user_id=grant.user_id,
                tenant_id=grant.tenant_id,
                workspace_id=grant.workspace_id,
                roles=(),  # Revalidated dynamically
                scopes=frozenset(grant.original_scopes),
                session_id=grant.parent_session_id,
                token_scopes=frozenset(grant.original_scopes),
            )
        elif require_grant:
            logger.warning("Execution grant required but absent on run %s", r_id)
            await self.fail_execution(
                lease_identity=lease_identity,
                error_code="execution_authority_denied",
                closed_reason="authority_denied",
                message="Execution grant missing",
            )
            return (
                BeginExecutionResult(
                    success=False,
                    started=False,
                    error_code="execution_authority_denied",
                ),
                None,
                None,
            )

        return result, grant, principal

    async def complete_execution(
        self,
        *,
        lease_identity: LeaseIdentity,
    ) -> ExecutionDisposition:
        """Mark run succeeded and job done in a single atomic transaction under fence."""
        r_id = lease_identity.run_id
        if r_id is None:
            raise LostLeaseError("Run job requires run_id")

        async with self._session_factory(
            lease_identity.tenant_id, lease_identity.workspace_id, None
        ) as session:
            event = await self._repo.complete_execution(
                session,
                tenant_id=lease_identity.tenant_id,
                workspace_id=lease_identity.workspace_id,
                job_id=lease_identity.job_id,
                run_id=r_id,
                worker_id=lease_identity.leased_by,
                lease_token=lease_identity.lease_token,
                attempt=lease_identity.attempt,
            )
            await self._outbox.publish_in_transaction(
                session,
                OUTBOX_RUN_EVENTS_TOPIC,
                f"{lease_identity.tenant_id}:{lease_identity.workspace_id}",
                event.to_wire_envelope(),
                aggregate_type="run",
                aggregate_id=r_id,
                event_id=event.eventId,
                set_rls=False,
            )

        return ExecutionDisposition(
            disposition=ExecutionDispositionType.COMPLETED,
            lease_identity=lease_identity,
            terminal=True,
        )

    async def fail_execution(
        self,
        *,
        lease_identity: LeaseIdentity,
        error_code: str,
        closed_reason: str = "execution_failed",
        message: str | None = None,
    ) -> ExecutionDisposition:
        """Mark run failed and job dead under fence with sanitized error code."""
        r_id = lease_identity.run_id
        if r_id is None:
            raise LostLeaseError("Run job requires run_id")

        try:
            async with self._session_factory(
                lease_identity.tenant_id, lease_identity.workspace_id, None
            ) as session:
                event = await self._repo.fail_execution(
                    session,
                    tenant_id=lease_identity.tenant_id,
                    workspace_id=lease_identity.workspace_id,
                    job_id=lease_identity.job_id,
                    run_id=r_id,
                    worker_id=lease_identity.leased_by,
                    lease_token=lease_identity.lease_token,
                    attempt=lease_identity.attempt,
                    error_code=error_code,
                    closed_reason=closed_reason,
                )
                await self._outbox.publish_in_transaction(
                    session,
                    OUTBOX_RUN_EVENTS_TOPIC,
                    f"{lease_identity.tenant_id}:{lease_identity.workspace_id}",
                    event.to_wire_envelope(),
                    aggregate_type="run",
                    aggregate_id=r_id,
                    event_id=event.eventId,
                    set_rls=False,
                )
        except LostLeaseError:
            logger.warning("Stale worker failed to update terminal failure due to lost lease fence")
            return ExecutionDisposition(
                disposition=ExecutionDispositionType.LOST_LEASE,
                lease_identity=lease_identity,
                error_code="lost_lease",
                message="Worker lost lease fence",
                terminal=False,
            )

        return ExecutionDisposition(
            disposition=ExecutionDispositionType.DENIED
            if error_code == "execution_authority_denied"
            else ExecutionDispositionType.BLOCKED_UNKNOWN,
            lease_identity=lease_identity,
            error_code=error_code,
            message=message,
            terminal=True,
        )

    async def schedule_retry(
        self,
        *,
        lease_identity: LeaseIdentity,
        error_code: str = "execution_backend_unavailable",
        message: str | None = None,
        max_attempts: int = 3,
    ) -> ExecutionDisposition:
        """Schedule retry with exponential backoff and jitter if attempt < max_attempts."""
        r_id = lease_identity.run_id
        if r_id is None:
            raise LostLeaseError("Run job requires run_id")

        base_delay = min(
            DEFAULT_RETRY_BACKOFF_BASE**lease_identity.attempt,
            DEFAULT_MAX_RETRY_BACKOFF_SECONDS,
        )
        jitter = random.uniform(0.0, MAX_JITTER_SECONDS)
        delay = base_delay + jitter

        async with self._session_factory(
            lease_identity.tenant_id, lease_identity.workspace_id, None
        ) as session:
            scheduled = await self._repo.schedule_retry(
                session,
                tenant_id=lease_identity.tenant_id,
                workspace_id=lease_identity.workspace_id,
                job_id=lease_identity.job_id,
                run_id=r_id,
                worker_id=lease_identity.leased_by,
                lease_token=lease_identity.lease_token,
                attempt=lease_identity.attempt,
                delay_seconds=delay,
                max_attempts=max_attempts,
            )

        if not scheduled:
            return ExecutionDisposition(
                disposition=ExecutionDispositionType.BLOCKED_UNKNOWN,
                lease_identity=lease_identity,
                error_code="attempts_exhausted",
                message="Max claim attempts exhausted",
                terminal=True,
            )

        return ExecutionDisposition(
            disposition=ExecutionDispositionType.RETRY_SCHEDULED,
            lease_identity=lease_identity,
            error_code=error_code,
            message=message,
            retry_delay_seconds=delay,
            terminal=False,
        )

    async def reclaim_expired_leases(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        max_attempts: int = 3,
    ) -> int:
        """Sweep expired leased jobs in the workspace."""
        async with self._session_factory(tenant_id, workspace_id, None) as session:
            return await self._repo.reclaim_expired_leases(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                max_attempts=max_attempts,
            )
