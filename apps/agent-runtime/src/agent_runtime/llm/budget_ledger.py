"""PostgreSQL-backed model budget admission, settlement, and reconciliation.

All public methods receive trusted tenant/workspace data from the runtime.  They
bind it transaction-locally before issuing SQL, so a pooled connection cannot
reuse another request's RLS context.  Callers own the surrounding short
transaction and must never hold it while talking to a model provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from platform_common.rls import set_rls_context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.llm.budget import AdmissionEnvelope
from agent_runtime.llm.errors import (
    IdempotencyConflictError,
    ModelBudgetExceededError,
    ModelBudgetNotConfiguredError,
    ModelReservationNotFoundError,
    ReservationExpiredError,
    SettlementConflictError,
)

CallerKind = Literal["user", "service_account", "run", "job", "eval", "standalone"]
UsageSource = Literal["provider", "estimated", "unknown"]


@dataclass(frozen=True, slots=True)
class BudgetReservationRequest:
    tenant_id: UUID
    workspace_id: UUID
    caller_kind: CallerKind
    caller_id: UUID
    model_config_id: UUID
    operation_key: str
    request_hash: str
    envelope: AdmissionEnvelope
    run_id: UUID | None = None
    job_id: UUID | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    id: UUID
    attempt_id: UUID
    status: str
    reserved_cost_usd: Decimal
    replayed: bool


@dataclass(frozen=True, slots=True)
class UsageRecord:
    id: UUID
    reservation_id: UUID
    attempt_id: UUID
    usage_source: UsageSource
    cost_usd: Decimal | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationLease:
    """A short-lived, fenced lease for resolving one ambiguous provider call."""

    reservation_id: UUID
    attempt_id: UUID
    lease_id: UUID
    fence: int


class PostgresModelBudgetLedger:
    """Owns atomic budget state transitions using a workspace-row lock."""

    async def reserve(
        self,
        session: AsyncSession,
        request: BudgetReservationRequest,
    ) -> BudgetReservation:
        """Create a reservation and attempt, or return a verified idempotent replay.

        Lock order is workspace budget -> reservation.  The transaction commits
        before the caller may dispatch a provider request.
        """
        self._validate_request(request)
        await self._scope(session, request.tenant_id, request.workspace_id)
        now = datetime.now(UTC)
        expires_at = request.expires_at or now + timedelta(minutes=10)
        if expires_at <= now:
            raise ReservationExpiredError("Reservation expiry must be in the future")

        existing_budget_id = await self._operation_budget_id(
            session,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            operation_key=request.operation_key,
        )
        budget = (
            await self._locked_budget(
                session,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                budget_id=existing_budget_id,
            )
            if existing_budget_id is not None
            else await self._locked_active_budget(
                session, tenant_id=request.tenant_id, workspace_id=request.workspace_id
            )
        )

        existing = (
            await session.execute(
                text(
                    """
                    SELECT id, status, request_hash, caller_kind, caller_id, model_config_id,
                           reserved_cost_usd
                    FROM ops.model_reservations
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                      AND operation_key = :operation_key
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": request.tenant_id,
                    "workspace_id": request.workspace_id,
                    "operation_key": request.operation_key,
                },
            )
        ).mappings().one_or_none()
        if existing is not None:
            if (
                existing["request_hash"] != request.request_hash
                or existing["caller_kind"] != request.caller_kind
                or existing["caller_id"] != request.caller_id
                or existing["model_config_id"] != request.model_config_id
            ):
                raise IdempotencyConflictError()
            attempt_id = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM ops.model_attempts
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                          AND reservation_id = :reservation_id AND attempt_number = 1
                        """
                    ),
                    {
                        "tenant_id": request.tenant_id,
                        "workspace_id": request.workspace_id,
                        "reservation_id": existing["id"],
                    },
                )
            ).scalar_one()
            return BudgetReservation(
                id=existing["id"],
                attempt_id=attempt_id,
                status=str(existing["status"]),
                reserved_cost_usd=existing["reserved_cost_usd"],
                replayed=True,
            )

        committed = budget["spent_usd"] + budget["reserved_usd"]
        if committed + request.envelope.worst_case_cost_usd > budget["budget_limit_usd"]:
            raise ModelBudgetExceededError()

        reservation_id = uuid4()
        attempt_id = uuid4()
        await session.execute(
            text(
                """
                UPDATE ops.workspace_budgets
                SET reserved_usd = reserved_usd + :reserved_cost_usd, updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :budget_id
                """
            ),
            {
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "budget_id": budget["id"],
                "reserved_cost_usd": request.envelope.worst_case_cost_usd,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO ops.model_reservations (
                    tenant_id, workspace_id, id, operation_key, request_hash,
                    caller_kind, caller_id, model_config_id, budget_id, run_id, job_id,
                    reserved_tokens, reserved_cost_usd, pricing_snapshot,
                    pricing_version, expires_at
                ) VALUES (
                    :tenant_id, :workspace_id, :reservation_id, :operation_key, :request_hash,
                    :caller_kind, :caller_id, :model_config_id, :budget_id, :run_id, :job_id,
                    :reserved_tokens, :reserved_cost_usd, CAST(:pricing_snapshot AS jsonb),
                    :pricing_version, :expires_at
                )
                """
            ),
            {
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "reservation_id": reservation_id,
                "operation_key": request.operation_key,
                "request_hash": request.request_hash,
                "caller_kind": request.caller_kind,
                "caller_id": request.caller_id,
                "model_config_id": request.model_config_id,
                "budget_id": budget["id"],
                "run_id": request.run_id,
                "job_id": request.job_id,
                "reserved_tokens": request.envelope.worst_case_tokens,
                "reserved_cost_usd": request.envelope.worst_case_cost_usd,
                "pricing_snapshot": self._json(request.envelope.pricing_snapshot),
                "pricing_version": request.envelope.pricing_version,
                "expires_at": expires_at,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO ops.model_attempts
                    (tenant_id, workspace_id, id, reservation_id, attempt_number)
                VALUES (:tenant_id, :workspace_id, :attempt_id, :reservation_id, 1)
                """
            ),
            {
                "tenant_id": request.tenant_id,
                "workspace_id": request.workspace_id,
                "attempt_id": attempt_id,
                "reservation_id": reservation_id,
            },
        )
        return BudgetReservation(
            id=reservation_id,
            attempt_id=attempt_id,
            status="reserved",
            reserved_cost_usd=request.envelope.worst_case_cost_usd,
            replayed=False,
        )

    async def mark_dispatched(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        reservation_id: UUID,
        attempt_id: UUID,
        provider_request_id: str | None = None,
    ) -> UUID:
        """Record dispatch immediately before the provider boundary is crossed."""
        await self._scope(session, tenant_id, workspace_id)
        reservation = await self._locked_reservation(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        if reservation["status"] != "reserved" or reservation["expires_at"] <= datetime.now(UTC):
            raise ReservationExpiredError("Reservation is missing, expired, or no longer dispatchable")
        result = await session.execute(
            text(
                """
                UPDATE ops.model_attempts AS a
                SET status = 'dispatched', dispatched_at = now(),
                    provider_request_id = :provider_request_id, updated_at = now()
                FROM ops.model_reservations AS r
                WHERE a.tenant_id = :tenant_id AND a.workspace_id = :workspace_id
                  AND a.id = :attempt_id AND a.reservation_id = :reservation_id
                  AND a.status = 'reserved'
                  AND r.tenant_id = a.tenant_id AND r.workspace_id = a.workspace_id
                  AND r.id = a.reservation_id AND r.status = 'reserved'
                RETURNING a.dispatch_token
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "reservation_id": reservation_id,
                "attempt_id": attempt_id,
                "provider_request_id": provider_request_id,
            },
        )
        dispatch_token = result.scalar_one_or_none()
        if dispatch_token is None:
            raise ReservationExpiredError("Reservation is missing, expired, or no longer dispatchable")
        updated = await session.execute(
            text(
                """
                UPDATE ops.model_reservations
                SET status = 'dispatched', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :reservation_id AND status = 'reserved'
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "reservation_id": reservation_id,
            },
        )
        if updated.rowcount != 1:
            raise SettlementConflictError("Reservation dispatch state was lost")
        return dispatch_token

    async def settle(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        reservation_id: UUID,
        attempt_id: UUID,
        dispatch_token: UUID,
        provider: str,
        model_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        usage_source: UsageSource,
        cost_usd: Decimal | None,
    ) -> UsageRecord:
        """Settle once after a known result; unknown outcomes use ``mark_unknown``."""
        if usage_source == "unknown" or cost_usd is None:
            raise SettlementConflictError("Unknown cost must remain unknown and be reconciled")
        if input_tokens is None or output_tokens is None or input_tokens < 0 or output_tokens < 0:
            raise SettlementConflictError("Settlement requires non-negative token counts")
        if cost_usd < 0:
            raise SettlementConflictError("Settlement cost cannot be negative")
        await self._scope(session, tenant_id, workspace_id)
        budget_id = await self._reservation_budget_id(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        await self._locked_budget(
            session, tenant_id=tenant_id, workspace_id=workspace_id, budget_id=budget_id
        )
        reservation = await self._locked_reservation(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        if reservation["status"] == "settled":
            usage = await self._usage_for_reservation(
                session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
            )
            if usage is None or usage["attempt_id"] != attempt_id:
                raise SettlementConflictError("Reservation was settled by a different attempt")
            return self._usage_record(usage)
        if reservation["status"] != "dispatched" or cost_usd > reservation["reserved_cost_usd"]:
            raise SettlementConflictError("Reservation cannot be settled with this amount or state")
        attempt = (
            await session.execute(
                text(
                    """
                    SELECT id FROM ops.model_attempts
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                      AND id = :attempt_id AND reservation_id = :reservation_id
                      AND dispatch_token = :dispatch_token AND status = 'dispatched'
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "attempt_id": attempt_id,
                    "reservation_id": reservation_id,
                    "dispatch_token": dispatch_token,
                },
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise SettlementConflictError("Attempt is not the active dispatched attempt")

        usage_id = uuid4()
        balance = await session.execute(
            text(
                """
                UPDATE ops.workspace_budgets
                SET reserved_usd = reserved_usd - :reserved_cost_usd,
                    spent_usd = spent_usd + :cost_usd,
                    updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :budget_id
                  AND reserved_usd >= :reserved_cost_usd
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "budget_id": budget_id,
                "reserved_cost_usd": reservation["reserved_cost_usd"],
                "cost_usd": cost_usd,
            },
        )
        if balance.rowcount != 1:
            raise SettlementConflictError("Budget balance cannot settle reservation")
        await session.execute(
            text(
                """
                INSERT INTO ops.model_usage (
                    tenant_id, workspace_id, id, run_id, job_id, reservation_id, attempt_id,
                    provider, model_id, input_tokens, output_tokens, usage_source, cost_usd,
                    pricing_version
                ) VALUES (
                    :tenant_id, :workspace_id, :usage_id, :run_id, :job_id, :reservation_id,
                    :attempt_id, :provider, :model_id, :input_tokens, :output_tokens,
                    :usage_source, :cost_usd, :pricing_version
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "usage_id": usage_id,
                "run_id": reservation["run_id"],
                "job_id": reservation["job_id"],
                "reservation_id": reservation_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "model_id": model_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "usage_source": usage_source,
                "cost_usd": cost_usd,
                "pricing_version": reservation["pricing_version"],
            },
        )
        await session.execute(
            text(
                """
                UPDATE ops.model_reservations
                SET status = 'settled', settled_at = now(), updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :reservation_id
                """
            ),
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "reservation_id": reservation_id},
        )
        await session.execute(
            text(
                """
                UPDATE ops.model_attempts SET status = 'settled', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :attempt_id
                """
            ),
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "attempt_id": attempt_id},
        )
        usage = await self._usage_for_reservation(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        if usage is None:  # pragma: no cover - transactional invariant
            raise SettlementConflictError("Usage receipt disappeared after settlement")
        return self._usage_record(usage)

    async def mark_unknown(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        reservation_id: UUID,
        attempt_id: UUID,
        dispatch_token: UUID,
    ) -> None:
        """Preserve ambiguous external outcomes without releasing budget."""
        await self._scope(session, tenant_id, workspace_id)
        reservation = await self._locked_reservation(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        if reservation["status"] != "dispatched":
            raise SettlementConflictError("Only a dispatched reservation may become unknown")
        changed = await session.execute(
            text(
                """
                UPDATE ops.model_attempts
                SET status = 'unknown', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :attempt_id AND reservation_id = :reservation_id
                  AND dispatch_token = :dispatch_token AND status = 'dispatched'
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "attempt_id": attempt_id,
                "reservation_id": reservation_id,
                "dispatch_token": dispatch_token,
            },
        )
        if changed.rowcount != 1:
            raise SettlementConflictError("Attempt cannot become unknown")
        reservation_changed = await session.execute(
            text(
                """
                UPDATE ops.model_reservations
                SET status = 'unknown', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :reservation_id AND status = 'dispatched'
                """
            ),
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "reservation_id": reservation_id},
        )
        if reservation_changed.rowcount != 1:
            raise SettlementConflictError("Reservation cannot become unknown")

    async def release_before_dispatch(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        reservation_id: UUID,
    ) -> None:
        """Release only a reservation proved never to have crossed dispatch."""
        await self._scope(session, tenant_id, workspace_id)
        budget_id = await self._reservation_budget_id(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        await self._locked_budget(
            session, tenant_id=tenant_id, workspace_id=workspace_id, budget_id=budget_id
        )
        reservation = await self._locked_reservation(
            session, tenant_id=tenant_id, workspace_id=workspace_id, reservation_id=reservation_id
        )
        if reservation["status"] != "reserved":
            raise SettlementConflictError("Only a never-dispatched reservation may be released")
        changed = await session.execute(
            text(
                """
                UPDATE ops.workspace_budgets
                SET reserved_usd = reserved_usd - :reserved_cost_usd, updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :budget_id
                  AND reserved_usd >= :reserved_cost_usd
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "budget_id": budget_id,
                "reserved_cost_usd": reservation["reserved_cost_usd"],
            },
        )
        if changed.rowcount != 1:
            raise SettlementConflictError("Budget balance cannot release reservation")
        await session.execute(
            text(
                """
                UPDATE ops.model_reservations SET status = 'released', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :reservation_id
                """
            ),
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "reservation_id": reservation_id},
        )

    async def claim_unknown_attempts(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        limit: int,
        lease_for: timedelta,
    ) -> list[ReconciliationLease]:
        """Lease unknown attempts with a fencing token for an approved reconciler."""
        if limit < 1 or limit > 100 or not worker_id or lease_for <= timedelta(0):
            raise ValueError("Invalid reconciliation lease request")
        await self._scope(session, tenant_id, workspace_id)
        lease_id = uuid4()
        result = await session.execute(
            text(
                """
                WITH candidates AS (
                    SELECT a.id
                    FROM ops.model_attempts AS a
                    WHERE a.tenant_id = :tenant_id AND a.workspace_id = :workspace_id
                      AND a.status = 'unknown'
                      AND (a.reconciliation_lease_until IS NULL OR a.reconciliation_lease_until < now())
                    ORDER BY a.created_at, a.id
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE ops.model_attempts AS a
                SET reconciliation_lease_id = :lease_id,
                    reconciliation_lease_until = now() + CAST(:lease_for AS interval),
                    reconciliation_fence = reconciliation_fence + 1,
                    reconciliation_worker_id = :worker_id,
                    updated_at = now()
                FROM candidates
                WHERE a.id = candidates.id
                RETURNING a.reservation_id, a.id, a.reconciliation_lease_id, a.reconciliation_fence
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "lease_id": lease_id,
                "worker_id": worker_id,
                "lease_for": lease_for,
                "limit": limit,
            },
        )
        return [
            ReconciliationLease(
                reservation_id=row[0], attempt_id=row[1], lease_id=row[2], fence=row[3]
            )
            for row in result.all()
        ]

    async def release_unknown_after_reconciliation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        lease: ReconciliationLease,
        worker_id: str,
        evidence_ref: str,
    ) -> None:
        """Release an ambiguous reservation only after an authoritative no-charge result.

        The reconciler must present its unexpired lease, fencing number, identity,
        and a non-secret evidence reference.  A late worker can therefore never
        release a reservation claimed by a newer worker.
        """
        if not worker_id or not evidence_ref or len(evidence_ref) > 512:
            raise ValueError("Invalid reconciliation identity or evidence reference")
        await self._scope(session, tenant_id, workspace_id)
        budget_id = await self._reservation_budget_id(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=lease.reservation_id,
        )
        await self._locked_budget(
            session, tenant_id=tenant_id, workspace_id=workspace_id, budget_id=budget_id
        )
        reservation = await self._locked_reservation(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=lease.reservation_id,
        )
        if reservation["status"] != "unknown":
            raise SettlementConflictError("Only an unknown reservation may be reconciled")
        attempt = (
            await session.execute(
                text(
                    """
                    SELECT id FROM ops.model_attempts
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                      AND id = :attempt_id AND reservation_id = :reservation_id
                      AND status = 'unknown' AND reconciliation_lease_id = :lease_id
                      AND reconciliation_fence = :fence
                      AND reconciliation_worker_id = :worker_id
                      AND reconciliation_lease_until > now()
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "attempt_id": lease.attempt_id,
                    "reservation_id": lease.reservation_id,
                    "lease_id": lease.lease_id,
                    "fence": lease.fence,
                    "worker_id": worker_id,
                },
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise SettlementConflictError("Reconciliation lease is stale, expired, or not owned")
        balance = await session.execute(
            text(
                """
                UPDATE ops.workspace_budgets
                SET reserved_usd = reserved_usd - :reserved_cost_usd, updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :budget_id
                  AND reserved_usd >= :reserved_cost_usd
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "budget_id": budget_id,
                "reserved_cost_usd": reservation["reserved_cost_usd"],
            },
        )
        if balance.rowcount != 1:
            raise SettlementConflictError("Budget balance cannot release reconciled reservation")
        attempt_changed = await session.execute(
            text(
                """
                UPDATE ops.model_attempts
                SET status = 'reconciled_released', reconciliation_evidence_ref = :evidence_ref,
                    updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :attempt_id AND reconciliation_lease_id = :lease_id
                  AND reconciliation_fence = :fence
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "attempt_id": lease.attempt_id,
                "lease_id": lease.lease_id,
                "fence": lease.fence,
                "evidence_ref": evidence_ref,
            },
        )
        if attempt_changed.rowcount != 1:
            raise SettlementConflictError("Reconciliation lease changed during release")
        reservation_changed = await session.execute(
            text(
                """
                UPDATE ops.model_reservations
                SET status = 'released', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :reservation_id AND status = 'unknown'
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "reservation_id": lease.reservation_id,
            },
        )
        if reservation_changed.rowcount != 1:
            raise SettlementConflictError("Reservation changed during reconciliation")

    async def settle_unknown_after_reconciliation(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        lease: ReconciliationLease,
        worker_id: str,
        evidence_ref: str,
        provider: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        usage_source: UsageSource,
        cost_usd: Decimal,
    ) -> UsageRecord:
        """Settle a reconciled unknown outcome under the same fenced lease.

        The provider result must already have been independently verified by the
        reconciler; this method only atomically records that result in the
        internal ledger.  It intentionally cannot make an unknown outcome look
        successful without a valid, current reconciliation lease.
        """
        if not worker_id or not evidence_ref or len(evidence_ref) > 512:
            raise ValueError("Invalid reconciliation identity or evidence reference")
        if usage_source == "unknown":
            raise SettlementConflictError("Reconciliation settlement requires known usage")
        await self._scope(session, tenant_id, workspace_id)
        budget_id = await self._reservation_budget_id(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=lease.reservation_id,
        )
        await self._locked_budget(
            session, tenant_id=tenant_id, workspace_id=workspace_id, budget_id=budget_id
        )
        reservation = await self._locked_reservation(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=lease.reservation_id,
        )
        if reservation["status"] != "unknown":
            raise SettlementConflictError("Only an unknown reservation may be reconciled")
        activated = await session.execute(
            text(
                """
                UPDATE ops.model_attempts
                SET status = 'dispatched', reconciliation_evidence_ref = :evidence_ref,
                    updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :attempt_id AND reservation_id = :reservation_id
                  AND status = 'unknown' AND reconciliation_lease_id = :lease_id
                  AND reconciliation_fence = :fence
                  AND reconciliation_worker_id = :worker_id
                  AND reconciliation_lease_until > now()
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "attempt_id": lease.attempt_id,
                "reservation_id": lease.reservation_id,
                "lease_id": lease.lease_id,
                "fence": lease.fence,
                "worker_id": worker_id,
                "evidence_ref": evidence_ref,
            },
        )
        if activated.rowcount != 1:
            raise SettlementConflictError("Reconciliation lease is stale, expired, or not owned")
        reservation_changed = await session.execute(
            text(
                """
                UPDATE ops.model_reservations
                SET status = 'dispatched', updated_at = now()
                WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                  AND id = :reservation_id AND status = 'unknown'
                """
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "reservation_id": lease.reservation_id,
            },
        )
        if reservation_changed.rowcount != 1:
            raise SettlementConflictError("Reservation changed during reconciliation")
        return await self.settle(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=lease.reservation_id,
            attempt_id=lease.attempt_id,
            dispatch_token=(
                await session.execute(
                    text(
                        """
                        SELECT dispatch_token FROM ops.model_attempts
                        WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :attempt_id
                        """
                    ),
                    {"tenant_id": tenant_id, "workspace_id": workspace_id, "attempt_id": lease.attempt_id},
                )
            ).scalar_one(),
            provider=provider,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_source=usage_source,
            cost_usd=cost_usd,
        )

    async def list_usage(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        limit: int = 50,
    ) -> list[UsageRecord]:
        """Return a bounded scope-only ledger view without prompt or credential data."""
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        await self._scope(session, tenant_id, workspace_id)
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, reservation_id, attempt_id, usage_source, cost_usd, observed_at
                    FROM ops.model_usage
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                    ORDER BY observed_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "limit": limit},
            )
        ).mappings().all()
        return [self._usage_record(row) for row in rows]

    @staticmethod
    async def _scope(session: AsyncSession, tenant_id: UUID, workspace_id: UUID) -> None:
        await set_rls_context(
            session, tenant_id=str(tenant_id), workspace_id=str(workspace_id), user_id=None
        )

    @staticmethod
    def _validate_request(request: BudgetReservationRequest) -> None:
        valid_kinds = {"user", "service_account", "run", "job", "eval", "standalone"}
        if request.caller_kind not in valid_kinds:
            raise ValueError(f"Invalid caller_kind: {request.caller_kind}")
        if not request.operation_key or len(request.operation_key) > 128:
            raise ValueError("operation_key must be between 1 and 128 characters")
        if len(request.request_hash) != 64 or any(c not in "0123456789abcdef" for c in request.request_hash):
            raise ValueError("request_hash must be a lowercase SHA-256 hex digest")
        if request.run_id is None and request.job_id is None:
            raise ValueError("A model reservation must be anchored to a real run or job")

    async def _locked_reservation(
        self, session: AsyncSession, *, tenant_id: UUID, workspace_id: UUID, reservation_id: UUID
    ) -> Mapping[str, object]:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, budget_id, status, reserved_cost_usd, pricing_version, run_id, job_id, expires_at
                    FROM ops.model_reservations
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :reservation_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "reservation_id": reservation_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            raise ModelReservationNotFoundError()
        return row

    @staticmethod
    async def _locked_budget(
        session: AsyncSession, *, tenant_id: UUID, workspace_id: UUID, budget_id: UUID
    ) -> Mapping[str, object]:
        budget = (
            await session.execute(
                text(
                    """
                    SELECT id, budget_limit_usd, spent_usd, reserved_usd, period_start, period_end
                    FROM ops.workspace_budgets
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :budget_id
                    FOR UPDATE
                    """
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "budget_id": budget_id},
            )
        ).mappings().one_or_none()
        if budget is None:
            raise ModelBudgetNotConfiguredError("Reservation budget period was not found")
        return budget

    @staticmethod
    async def _locked_active_budget(
        session: AsyncSession, *, tenant_id: UUID, workspace_id: UUID
    ) -> Mapping[str, object]:
        budget = (
            await session.execute(
                text(
                    """
                    SELECT id, budget_limit_usd, spent_usd, reserved_usd, period_start, period_end
                    FROM ops.workspace_budgets
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                      AND period_start <= now() AND period_end > now()
                    FOR UPDATE
                    """
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id},
            )
        ).mappings().one_or_none()
        if budget is None:
            raise ModelBudgetNotConfiguredError()
        return budget

    @staticmethod
    async def _reservation_budget_id(
        session: AsyncSession, *, tenant_id: UUID, workspace_id: UUID, reservation_id: UUID
    ) -> UUID:
        budget_id = (
            await session.execute(
                text(
                    """
                    SELECT budget_id FROM ops.model_reservations
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id AND id = :reservation_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "reservation_id": reservation_id,
                },
            )
        ).scalar_one_or_none()
        if budget_id is None:
            raise ModelReservationNotFoundError()
        return budget_id

    @staticmethod
    async def _operation_budget_id(
        session: AsyncSession, *, tenant_id: UUID, workspace_id: UUID, operation_key: str
    ) -> UUID | None:
        return (
            await session.execute(
                text(
                    """
                    SELECT budget_id FROM ops.model_reservations
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                      AND operation_key = :operation_key
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "operation_key": operation_key,
                },
            )
        ).scalar_one_or_none()

    async def _usage_for_reservation(
        self, session: AsyncSession, *, tenant_id: UUID, workspace_id: UUID, reservation_id: UUID
    ) -> Mapping[str, object] | None:
        return (
            await session.execute(
                text(
                    """
                    SELECT id, reservation_id, attempt_id, usage_source, cost_usd, observed_at
                    FROM ops.model_usage
                    WHERE tenant_id = :tenant_id AND workspace_id = :workspace_id
                      AND reservation_id = :reservation_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "reservation_id": reservation_id,
                },
            )
        ).mappings().one_or_none()

    @staticmethod
    def _usage_record(row: Mapping[str, object]) -> UsageRecord:
        return UsageRecord(
            id=row["id"],
            reservation_id=row["reservation_id"],
            attempt_id=row["attempt_id"],
            usage_source=row["usage_source"],
            cost_usd=row["cost_usd"],
            observed_at=row["observed_at"],
        )

    @staticmethod
    def _json(value: dict[str, str]) -> str:
        import json

        return json.dumps(value, sort_keys=True, separators=(",", ":"))
