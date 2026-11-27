"""R05D-01: bounded cleanup preserves the original outcome, including cancellation."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agent_contracts.model_contracts import ModelGenerationRequest, ModelMessage, ModelProfile
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.llm.authority import ModelAuthority, ModelBinding
from agent_runtime.llm.budget import AdmissionEnvelope
from agent_runtime.llm.budget_ledger import BudgetReservation, PostgresModelBudgetLedger
from agent_runtime.llm.errors import ModelTimeoutError
from agent_runtime.llm.gateway import ModelGateway
from agent_runtime.llm.profile import TokenProfiler
from agent_runtime.llm.secrets import ScopedSecretResolver


@pytest.mark.parametrize("rollback_mode", ["error_then_hang", "hang"])
def test_cleanup_never_retries_outside_its_deadline(monkeypatch, rollback_mode):
    monkeypatch.setattr("agent_runtime.llm.gateway.CLEANUP_TIMEOUT_SECONDS", 0.05)

    async def check():
        req = ModelGenerationRequest(
            requestId=uuid4(),
            tenantId=uuid4(),
            workspaceId=uuid4(),
            modelConfigId=uuid4(),
            requestedProvider="test-provider",
            requestedModel="test-model",
            messages=[ModelMessage(role="user", content="test")],
        )
        profile = ModelProfile.model_validate(
            {
                "provider": "test-provider",
                "modelId": "test-model",
                "capabilities": {
                    "supportsTools": False,
                    "supportsJsonSchema": False,
                    "supportsStream": False,
                    "supportsEmbeddings": False,
                },
                "contextLimits": {"contextWindow": 4096, "maxOutputTokens": 64},
            }
        )
        binding = ModelBinding(profile, "https://approved.example/v1")
        envelope = AdmissionEnvelope(
            10, 10, 20, Decimal("0.01"), "test", Decimal(0), Decimal(0), {}
        )
        ledger = AsyncMock(spec=PostgresModelBudgetLedger)
        ledger.reserve.return_value = BudgetReservation(
            uuid4(), uuid4(), "reserved", Decimal("0.01"), False
        )
        ledger.mark_dispatched.return_value = uuid4()
        session = AsyncMock()
        count = 0

        async def rollback():
            nonlocal count
            count += 1
            if rollback_mode == "error_then_hang" and count == 1:
                raise OSError("Fixture connection loss")
            await asyncio.Event().wait()

        session.rollback.side_effect = rollback

        class WaitingAdapter:
            async def generate(self, *_, **__):
                await asyncio.Event().wait()

        gateway = ModelGateway(
            budget_ledger=ledger,
            secret_resolver=ScopedSecretResolver(),
            token_profiler=TokenProfiler(),
            generation_adapter=WaitingAdapter(),
            authority=ModelAuthority({}),
            timeout_seconds=0.05,
        )
        gateway._prepare = AsyncMock(return_value=(req, binding, envelope))
        gateway._authorize = AsyncMock(return_value=(binding, None, {}))
        principal = RuntimePrincipal(
            subject="fixture",
            user_id=str(uuid4()),
            tenant_id=str(req.tenant_id),
            workspace_id=str(req.workspace_id),
            roles=("developer",),
            scopes=frozenset({"run:start"}),
        )
        with pytest.raises(ModelTimeoutError):
            await asyncio.wait_for(
                gateway.generate(session, req, principal=principal, run_id=uuid4()), 1
            )
        assert count == 1
        assert ledger.mark_dispatched.await_count == 1
        ledger.release_before_dispatch.assert_not_awaited()

    asyncio.run(check())
