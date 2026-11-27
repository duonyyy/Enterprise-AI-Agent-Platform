"""Test-only child process: real Gateway/factory/auth/HTTP, deterministic crash barriers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from agent_contracts.model_contracts import ModelGenerationRequest, ModelMessage
from agent_runtime.auth import (
    RedisParentSessionVerifier,
    RuntimePrincipal,
    revalidate_runtime_authorization,
    set_parent_session_verifier,
)
from agent_runtime.llm.budget_ledger import PostgresModelBudgetLedger
from agent_runtime.llm.composition import model_gateway_resources
from agent_runtime.llm.errors import IdempotencyConflictError
from agent_runtime.settings import RuntimeSettings
from platform_common.rls import set_rls_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


async def pause(stage: str) -> None:
    print("BARRIER:" + stage, flush=True)
    await asyncio.Event().wait()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--stop", default="")
    parser.add_argument("--action", choices=["generate", "replay", "claim"], default="generate")
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    scope = packet["scope"]
    principal = RuntimePrincipal(
        **{
            **packet["principal"],
            "scopes": frozenset(packet["principal"]["scopes"]),
            "token_scopes": frozenset(packet["principal"]["token_scopes"]),
            "roles": tuple(packet["principal"]["roles"]),
        }
    )

    class BarrierSession(AsyncSession):
        async def commit(self):
            await super().commit()
            phase = self.info.pop("crash_phase", None)
            if args.action == "generate" and args.stop == phase:
                await pause(phase)

    class BarrierLedger(PostgresModelBudgetLedger):
        async def reserve(self, session, request):
            result = await super().reserve(session, request)
            session.info["crash_phase"] = "reserved"
            return result

        async def mark_dispatched(self, session, **kwargs):
            result = await super().mark_dispatched(session, **kwargs)
            session.info["crash_phase"] = "dispatched"
            return result

        async def settle(self, session, **kwargs):
            if args.action == "generate" and args.stop == "response":
                await pause("response")
            result = await super().settle(session, **kwargs)
            session.info["crash_phase"] = "settled"
            return result

    engine = create_async_engine(os.environ["MODEL_CRASH_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=BarrierSession, expire_on_commit=False)
    settings = RuntimeSettings(
        _env_file=None,
        environment="test",
        redis_url=os.environ["MODEL_CRASH_REDIS_URL"],
        model_gateway_config_path=packet["routes_path"],
        model_gateway_allow_local_endpoints=True,
    )
    verifier = RedisParentSessionVerifier(settings)
    set_parent_session_verifier(verifier)

    @asynccontextmanager
    async def scoped(tenant, workspace, user):
        async with factory() as session, session.begin():
            await set_rls_context(session, tenant_id=tenant, workspace_id=workspace, user_id=user)
            yield session

    async def check(user):
        return await revalidate_runtime_authorization(
            user, required_scope="run:start", session_factory=scoped, settings=settings
        )

    try:
        if args.action == "claim":
            from datetime import timedelta

            async with factory() as session, session.begin():
                leases = await PostgresModelBudgetLedger().claim_unknown_attempts(
                    session,
                    tenant_id=UUID(scope["tenant_id"]),
                    workspace_id=UUID(scope["workspace_id"]),
                    worker_id="restored-process",
                    limit=100,
                    lease_for=timedelta(minutes=1),
                )
            print(
                "RESULT:" + json.dumps({"leases": [str(x.reservation_id) for x in leases]}),
                flush=True,
            )
            return
        async with model_gateway_resources(settings, principal_check=check) as gateway:
            # Fault hooks wrap successful ledger calls; orchestration, commits,
            # scopes, normalization and network transport remain production code.
            gateway._ledger = BarrierLedger()
            request = ModelGenerationRequest(
                requestId=UUID(packet["request_id"]),
                tenantId=UUID(scope["tenant_id"]),
                workspaceId=UUID(scope["workspace_id"]),
                modelConfigId=UUID(scope["model_id"]),
                requestedProvider="test-provider",
                requestedModel="test-model",
                messages=[ModelMessage(role="user", content="Synthetic crash recovery check.")],
            )
            async with factory() as session:
                try:
                    result = await gateway.generate(
                        session, request, principal=principal, run_id=UUID(scope["run_id"])
                    )
                    print(
                        "RESULT:"
                        + json.dumps(
                            {"outcome": "generated", "request_id": str(result.request_id)}
                        ),
                        flush=True,
                    )
                except IdempotencyConflictError:
                    print('RESULT:{"outcome":"replay_conflict"}', flush=True)
    finally:
        set_parent_session_verifier(None)
        verifier.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
