"""Real PostgreSQL evidence for 05-B durable model budget accounting."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from agent_contracts.model_contracts import (
    ContextLimits,
    ModelCapabilities,
    ModelPricing,
    ModelProfile,
)
from agent_runtime.llm.budget import calculate_admission_envelope, compute_request_hash
from agent_runtime.llm.budget_ledger import BudgetReservationRequest, PostgresModelBudgetLedger
from agent_runtime.llm.errors import (
    IdempotencyConflictError,
    ModelBudgetExceededError,
    SettlementConflictError,
)
from platform_common.rls import set_rls_context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def _async_url(url: str) -> str:
    parts = psycopg.conninfo.conninfo_to_dict(url)
    return (
        f"postgresql+asyncpg://{parts['user']}:{parts.get('password', '')}"
        f"@{parts['host']}:{parts['port']}/{parts['dbname']}"
    )


def _profile() -> ModelProfile:
    return ModelProfile(
        provider="test-provider",
        model_id="test-model",
        capabilities=ModelCapabilities(
            supports_tools=False,
            supports_json_schema=False,
            supports_stream=False,
            supports_embeddings=False,
        ),
        context_limits=ContextLimits(
            context_window=16_384,
            max_input_tokens=15_000,
            max_output_tokens=1_000,
        ),
        pricing=ModelPricing(
            pricing_version="test-v1",
            cost_per_prompt_token_usd=Decimal("0.00001000"),
            cost_per_completion_token_usd=Decimal("0.00001000"),
        ),
    )


def _seed_budget_scope(
    owner_url: str, budget_limit_usd: Decimal = Decimal("0.03000000")
) -> dict[str, UUID]:
    tenant_id, workspace_id, model_id, agent_id, version_id, run_id, caller_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    with psycopg.connect(owner_url) as conn:
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, 'Budget Tenant')",
            (tenant_id, f"budget-{tenant_id.hex[:12]}"),
        )
        conn.execute(
            "INSERT INTO iam.workspaces (tenant_id, id, slug, name) VALUES (%s, %s, %s, 'Budget Workspace')",
            (tenant_id, workspace_id, f"budget-{workspace_id.hex[:12]}"),
        )
        conn.execute(
            "INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized) VALUES (%s, %s, %s, %s, %s)",
            (
                caller_id,
                "https://budget.test",
                f"caller-{caller_id.hex[:12]}",
                "Budget Caller",
                f"{caller_id.hex}@example.test",
            ),
        )
        conn.execute(
            "INSERT INTO catalog.model_configs (tenant_id, workspace_id, id, name, provider, provider_model_id) VALUES (%s, %s, %s, 'budget-model', 'test-provider', 'test-model')",
            (tenant_id, workspace_id, model_id),
        )
        conn.execute(
            "INSERT INTO catalog.agents (tenant_id, workspace_id, id, slug, display_name) VALUES (%s, %s, %s, 'budget-agent', 'Budget Agent')",
            (tenant_id, workspace_id, agent_id),
        )
        conn.execute(
            'INSERT INTO catalog.agent_versions (tenant_id, workspace_id, id, agent_id, version, schema_version, frozen_spec, bundle_sha256, bundle_manifest) VALUES (%s, %s, %s, %s, 1, \'1.0\', \'{"name":"budget"}\', %s, \'{"fixture":"budget"}\')',
            (tenant_id, workspace_id, version_id, agent_id, "b" * 64),
        )
        conn.execute(
            "UPDATE catalog.agent_versions SET bindings_sealed_at = now() WHERE id = %s",
            (version_id,),
        )
        conn.execute(
            "INSERT INTO runtime.runs (tenant_id, workspace_id, id, agent_version_id, initiated_by, external_request_key) VALUES (%s, %s, %s, %s, %s, %s)",
            (tenant_id, workspace_id, run_id, version_id, caller_id, f"budget-{run_id.hex}"),
        )
        conn.execute(
            "INSERT INTO ops.workspace_budgets (tenant_id, workspace_id, budget_limit_usd, period_start, period_end) VALUES (%s, %s, %s, now() - interval '1 minute', now() + interval '1 day')",
            (tenant_id, workspace_id, budget_limit_usd),
        )
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "model_id": model_id,
        "run_id": run_id,
        "caller_id": caller_id,
    }


def _request(scope: dict[str, UUID], operation_key: str) -> BudgetReservationRequest:
    profile = _profile()
    envelope = calculate_admission_envelope(
        profile, prompt_tokens=1_000, requested_max_tokens=1_000
    )
    return BudgetReservationRequest(
        tenant_id=scope["tenant_id"],
        workspace_id=scope["workspace_id"],
        caller_kind="user",
        caller_id=scope["caller_id"],
        model_config_id=scope["model_id"],
        operation_key=operation_key,
        request_hash=compute_request_hash(
            scope["model_id"], [{"role": "user", "content": operation_key}], {"max_tokens": 1_000}
        ),
        envelope=envelope,
        run_id=scope["run_id"],
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


@pytest.mark.integration
def test_budget_admission_is_atomic_and_idempotent_on_real_postgres(migrated_database) -> None:
    """Two independent app connections cannot reserve the final workspace budget twice."""
    scope = _seed_budget_scope(migrated_database["owner_url"])

    async def scenario() -> None:
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        ledger = PostgresModelBudgetLedger()

        async def reserve(operation_key: str):
            async with factory() as session, session.begin():
                return await ledger.reserve(session, _request(scope, operation_key))

        try:
            first, second = await asyncio.gather(
                reserve("budget-race-a"), reserve("budget-race-b"), return_exceptions=True
            )
            outcomes = [first, second]
            assert sum(not isinstance(item, Exception) for item in outcomes) == 1
            assert sum(isinstance(item, ModelBudgetExceededError) for item in outcomes) == 1

            winner = next(item for item in outcomes if not isinstance(item, Exception))
            assert winner.replayed is False
            winning_key = "budget-race-a" if winner is first else "budget-race-b"
            replay = await reserve(winning_key)
            assert replay.id == winner.id
            assert replay.replayed is True

            with pytest.raises(IdempotencyConflictError):
                conflicting = _request(scope, winning_key)
                conflicting = replace(conflicting, request_hash="c" * 64)
                async with factory() as session, session.begin():
                    await ledger.reserve(session, conflicting)

            async with factory() as session, session.begin():
                await ledger._scope(session, scope["tenant_id"], scope["workspace_id"])
                balance = (
                    await session.execute(
                        text("SELECT spent_usd, reserved_usd FROM ops.workspace_budgets")
                    )
                ).one()
                assert balance.spent_usd == Decimal(0)
                assert balance.reserved_usd == winner.reserved_cost_usd
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_budget_settlement_and_unknown_outcome_preserve_durable_state(migrated_database) -> None:
    """Settlement consumes once; a dispatched timeout remains reserved and unknown."""
    scope = _seed_budget_scope(
        migrated_database["owner_url"], budget_limit_usd=Decimal("0.05000000")
    )

    async def scenario() -> None:
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        ledger = PostgresModelBudgetLedger()
        try:
            async with factory() as session, session.begin():
                settled = await ledger.reserve(session, _request(scope, "settle-once"))
            async with factory() as session, session.begin():
                token = await ledger.mark_dispatched(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=settled.id,
                    attempt_id=settled.attempt_id,
                    provider_request_id="provider-fixture-1",
                )
            async with factory() as session, session.begin():
                usage = await ledger.settle(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=settled.id,
                    attempt_id=settled.attempt_id,
                    dispatch_token=token,
                    provider="test-provider",
                    model_id="test-model",
                    input_tokens=1_000,
                    output_tokens=500,
                    usage_source="provider",
                    cost_usd=Decimal("0.01500000"),
                )
            assert usage.cost_usd == Decimal("0.01500000")

            async with factory() as session, session.begin():
                unknown = await ledger.reserve(session, _request(scope, "unknown-outcome"))
            async with factory() as session, session.begin():
                unknown_token = await ledger.mark_dispatched(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=unknown.id,
                    attempt_id=unknown.attempt_id,
                )
            async with factory() as session, session.begin():
                await ledger.mark_unknown(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=unknown.id,
                    attempt_id=unknown.attempt_id,
                    dispatch_token=unknown_token,
                )

            async with factory() as session, session.begin():
                first_lease = (
                    await ledger.claim_unknown_attempts(
                        session,
                        tenant_id=scope["tenant_id"],
                        workspace_id=scope["workspace_id"],
                        worker_id="reconciler-a",
                        limit=1,
                        lease_for=timedelta(minutes=1),
                    )
                )[0]

            # Failure injection on the isolated PostgreSQL fixture: prove an
            # expired worker cannot release budget after a new fence is issued.
            async with factory() as session, session.begin():
                await ledger._scope(session, scope["tenant_id"], scope["workspace_id"])
                await session.execute(
                    text(
                        """
                        UPDATE ops.model_attempts
                        SET reconciliation_lease_until = now() - interval '1 second'
                        WHERE id = :attempt_id
                        """
                    ),
                    {"attempt_id": first_lease.attempt_id},
                )
            async with factory() as session, session.begin():
                second_lease = (
                    await ledger.claim_unknown_attempts(
                        session,
                        tenant_id=scope["tenant_id"],
                        workspace_id=scope["workspace_id"],
                        worker_id="reconciler-b",
                        limit=1,
                        lease_for=timedelta(minutes=1),
                    )
                )[0]
            async with factory() as session, session.begin():
                with pytest.raises(SettlementConflictError, match="stale, expired, or not owned"):
                    await ledger.release_unknown_after_reconciliation(
                        session,
                        tenant_id=scope["tenant_id"],
                        workspace_id=scope["workspace_id"],
                        lease=first_lease,
                        worker_id="reconciler-a",
                        evidence_ref="provider-no-charge:fixture",
                    )
            async with factory() as session, session.begin():
                await ledger.release_unknown_after_reconciliation(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    lease=second_lease,
                    worker_id="reconciler-b",
                    evidence_ref="provider-no-charge:fixture",
                )

            # A reconciler may also commit independently verified provider usage;
            # this follows the same lease/fence path and cannot create a second receipt.
            async with factory() as session, session.begin():
                reconciled = await ledger.reserve(session, _request(scope, "reconcile-settlement"))
            async with factory() as session, session.begin():
                reconciled_token = await ledger.mark_dispatched(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=reconciled.id,
                    attempt_id=reconciled.attempt_id,
                )
            async with factory() as session, session.begin():
                await ledger.mark_unknown(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=reconciled.id,
                    attempt_id=reconciled.attempt_id,
                    dispatch_token=reconciled_token,
                )
            async with factory() as session, session.begin():
                reconciliation_lease = (
                    await ledger.claim_unknown_attempts(
                        session,
                        tenant_id=scope["tenant_id"],
                        workspace_id=scope["workspace_id"],
                        worker_id="reconciler-c",
                        limit=1,
                        lease_for=timedelta(minutes=1),
                    )
                )[0]
            async with factory() as session, session.begin():
                reconciled_usage = await ledger.settle_unknown_after_reconciliation(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    lease=reconciliation_lease,
                    worker_id="reconciler-c",
                    evidence_ref="provider-receipt:fixture",
                    provider="test-provider",
                    model_id="test-model",
                    input_tokens=1_000,
                    output_tokens=100,
                    usage_source="provider",
                    cost_usd=Decimal("0.01100000"),
                )
                assert reconciled_usage.reservation_id == reconciled.id

            async with factory() as session, session.begin():
                await ledger._scope(session, scope["tenant_id"], scope["workspace_id"])
                balance = (
                    await session.execute(
                        text("SELECT spent_usd, reserved_usd FROM ops.workspace_budgets")
                    )
                ).one()
                statuses = (
                    (
                        await session.execute(
                            text("SELECT status FROM ops.model_reservations ORDER BY created_at")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert balance.spent_usd == Decimal("0.02600000")
                assert balance.reserved_usd == Decimal("0.00000000")
                assert statuses == ["settled", "released", "settled"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_budget_ledger_rows_are_forced_rls_scoped(migrated_database) -> None:
    """The application role cannot read or mutate another workspace's budget row."""
    scope_a = _seed_budget_scope(migrated_database["owner_url"])
    scope_b = _seed_budget_scope(migrated_database["owner_url"])

    async def scenario() -> None:
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                without_context = await session.execute(
                    text(
                        "SELECT count(*) FROM ops.workspace_budgets "
                        "WHERE workspace_id = :workspace_id"
                    ),
                    {"workspace_id": scope_a["workspace_id"]},
                )
                assert without_context.scalar_one() == 0

                await set_rls_context(
                    session,
                    tenant_id=str(scope_a["tenant_id"]),
                    workspace_id=str(scope_a["workspace_id"]),
                    user_id=None,
                )
                own_row = await session.execute(
                    text(
                        "SELECT count(*) FROM ops.workspace_budgets "
                        "WHERE workspace_id = :workspace_id"
                    ),
                    {"workspace_id": scope_a["workspace_id"]},
                )
                assert own_row.scalar_one() == 1

                await set_rls_context(
                    session,
                    tenant_id=str(scope_b["tenant_id"]),
                    workspace_id=str(scope_b["workspace_id"]),
                    user_id=None,
                )
                foreign_row = await session.execute(
                    text(
                        "SELECT count(*) FROM ops.workspace_budgets "
                        "WHERE workspace_id = :workspace_id"
                    ),
                    {"workspace_id": scope_a["workspace_id"]},
                )
                assert foreign_row.scalar_one() == 0
                denied_update = await session.execute(
                    text(
                        "UPDATE ops.workspace_budgets SET budget_limit_usd = 999 "
                        "WHERE workspace_id = :workspace_id"
                    ),
                    {"workspace_id": scope_a["workspace_id"]},
                )
                assert denied_update.rowcount == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_late_settlement_uses_the_original_budget_period(migrated_database) -> None:
    """A reservation settled after rollover must update its original locked budget period."""
    scope = _seed_budget_scope(migrated_database["owner_url"])

    async def scenario() -> None:
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        ledger = PostgresModelBudgetLedger()
        try:
            async with factory() as session, session.begin():
                reserved = await ledger.reserve(session, _request(scope, "period-boundary"))
            async with factory() as session, session.begin():
                token = await ledger.mark_dispatched(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=reserved.id,
                    attempt_id=reserved.attempt_id,
                )

            with psycopg.connect(migrated_database["owner_url"]) as conn:
                old_budget_id = conn.execute(
                    "SELECT budget_id FROM ops.model_reservations WHERE id = %s", (reserved.id,)
                ).fetchone()[0]
                conn.execute(
                    "UPDATE ops.workspace_budgets SET period_end = now() - interval '1 second' WHERE id = %s",
                    (old_budget_id,),
                )
                conn.execute(
                    """
                    INSERT INTO ops.workspace_budgets (
                        tenant_id, workspace_id, budget_limit_usd, period_start, period_end
                    ) VALUES (%s, %s, %s, now(), now() + interval '1 day')
                    """,
                    (scope["tenant_id"], scope["workspace_id"], Decimal("0.05000000")),
                )

            async with factory() as session, session.begin():
                usage = await ledger.settle(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=reserved.id,
                    attempt_id=reserved.attempt_id,
                    dispatch_token=token,
                    provider="test-provider",
                    model_id="test-model",
                    input_tokens=1_000,
                    output_tokens=100,
                    usage_source="provider",
                    cost_usd=Decimal("0.01100000"),
                )
                assert usage.reservation_id == reserved.id

            with psycopg.connect(migrated_database["owner_url"]) as conn:
                balances = conn.execute(
                    """
                        SELECT id, spent_usd, reserved_usd FROM ops.workspace_budgets
                        WHERE tenant_id = %s AND workspace_id = %s
                        ORDER BY period_start
                        """,
                    (scope["tenant_id"], scope["workspace_id"]),
                ).fetchall()
                old_budget = next(row for row in balances if row[0] == old_budget_id)
                new_budget = next(row for row in balances if row[0] != old_budget_id)
                assert old_budget[1:] == (Decimal("0.01100000"), Decimal("0.00000000"))
                assert new_budget[1:] == (Decimal("0.00000000"), Decimal("0.00000000"))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_expired_dispatch_after_process_loss_is_reconcilable_without_refund(migrated_database):
    scope = _seed_budget_scope(migrated_database["owner_url"])

    async def scenario():
        engine = create_async_engine(_async_url(migrated_database["app_url"]), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ledger = PostgresModelBudgetLedger()
        try:
            async with factory() as session, session.begin():
                reservation = await ledger.reserve(session, _request(scope, "crashed-dispatch"))
            async with factory() as session, session.begin():
                await ledger.mark_dispatched(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    reservation_id=reservation.id,
                    attempt_id=reservation.attempt_id,
                )
            with psycopg.connect(migrated_database["owner_url"]) as conn:
                conn.execute(
                    "UPDATE ops.model_reservations SET expires_at=now()-interval '1 second' WHERE id=%s",
                    (reservation.id,),
                )
            async with factory() as session, session.begin():
                leases = await ledger.claim_unknown_attempts(
                    session,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    worker_id="recovery",
                    limit=10,
                    lease_for=timedelta(minutes=1),
                )
                assert [lease.reservation_id for lease in leases] == [reservation.id]
            with psycopg.connect(migrated_database["owner_url"]) as conn:
                row = conn.execute(
                    "SELECT r.status,b.reserved_usd FROM ops.model_reservations r JOIN ops.workspace_budgets b ON b.id=r.budget_id WHERE r.id=%s",
                    (reservation.id,),
                ).fetchone()
                assert row[0] == "unknown" and row[1] == reservation.reserved_cost_usd
        finally:
            await engine.dispose()

    asyncio.run(scenario())
