"""Internal user-session model boundary; short transactions, durable unknown outcomes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from agent_contracts.manifest import canonicalize_json
from agent_contracts.model_contracts import (
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelGenerationRequest,
    ModelGenerationResponse,
    ModelPricing,
    ModelStreamingDelta,
    ModelUsage,
)
from platform_common.rls import set_rls_context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import RuntimePrincipal
from agent_runtime.llm.authority import ModelAuthority, ModelBinding
from agent_runtime.llm.budget import (
    AdmissionEnvelope,
    calculate_admission_envelope,
    compute_settlement_cost,
)
from agent_runtime.llm.budget_ledger import (
    BudgetReservation,
    BudgetReservationRequest,
    PostgresModelBudgetLedger,
)
from agent_runtime.llm.errors import (
    IdempotencyConflictError,
    ModelContextTooLargeError,
    ModelPolicyDeniedError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from agent_runtime.llm.ports import EmbeddingPort, GenerationPort, StreamingPort, TokenProfilePort
from agent_runtime.llm.secrets import ScopedSecretResolver

logger = logging.getLogger(__name__)
Request = ModelGenerationRequest | ModelEmbeddingRequest
CLEANUP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _Call:
    reservation: BudgetReservation
    dispatch_token: UUID
    binding: ModelBinding
    auth_header: str | None


class ModelGateway:
    def __init__(
        self,
        *,
        budget_ledger: PostgresModelBudgetLedger,
        secret_resolver: ScopedSecretResolver,
        token_profiler: TokenProfilePort,
        generation_adapter: GenerationPort | None,
        authority: ModelAuthority,
        streaming_adapter: StreamingPort | None = None,
        embedding_adapter: EmbeddingPort | None = None,
        timeout_seconds: float = 60,
        ready_capabilities: frozenset[str] = frozenset(),
    ) -> None:
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Model timeout must be in (0, 300]")
        self._ledger = budget_ledger
        self._secrets = secret_resolver
        self._profiler = token_profiler
        self._generation = generation_adapter
        self._streaming = streaming_adapter
        self._embedding = embedding_adapter
        self._authority = authority
        self._timeout = timeout_seconds
        self._ready_capabilities = ready_capabilities

    @property
    def ready_capabilities(self) -> frozenset[str]:
        """Local startup readiness, not provider/billing verification."""
        return self._ready_capabilities

    async def _authorize(
        self,
        session: AsyncSession,
        request: Request,
        principal: RuntimePrincipal,
        run_id: UUID | None,
        job_id: UUID | None,
    ) -> tuple[ModelBinding, str | None, dict[str, Any]]:
        if request.model_config_id is None:
            raise ModelPolicyDeniedError("Model configuration is required")
        return await self._authority.authorize(
            session,
            principal=principal,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            caller_id=UUID(principal.user_id),
            model_config_id=request.model_config_id,
            run_id=run_id,
            job_id=job_id,
            embedding=isinstance(request, ModelEmbeddingRequest),
        )

    @asynccontextmanager
    async def _attempt(
        self,
        session: AsyncSession,
        request: Request,
        binding: ModelBinding,
        envelope: AdmissionEnvelope,
        principal: RuntimePrincipal,
        run_id: UUID | None,
        job_id: UUID | None,
    ) -> AsyncIterator[_Call]:
        assert request.model_config_id is not None
        payload = request.model_dump(mode="json", exclude={"deadline"})
        payload.update(actor_id=principal.user_id, run_id=str(run_id), job_id=str(job_id))
        reservation = await self._ledger.reserve(
            session,
            BudgetReservationRequest(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                caller_kind="user",
                caller_id=UUID(principal.user_id),
                model_config_id=request.model_config_id,
                operation_key=str(request.request_id),
                request_hash=hashlib.sha256(canonicalize_json(payload)).hexdigest(),
                envelope=envelope,
                run_id=run_id,
                job_id=job_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=self._timeout + 30),
            ),
        )
        await session.commit()
        if reservation.replayed:
            # Ledger retains receipts, not text/vectors. Never re-dispatch merely
            # to reconstruct a response or interfere with another request.
            raise IdempotencyConflictError(
                "Operation already admitted; inspect its durable receipt"
            )
        token: UUID | None = None
        finished = False
        try:
            current, secret_ref, _ = await self._authorize(
                session, request, principal, run_id, job_id
            )
            if current != binding:
                raise ModelPolicyDeniedError("Model routing changed")
            header = None
            if secret_ref:
                secret = self._secrets.resolve(
                    "model-gateway", request.tenant_id, request.workspace_id, secret_ref
                )
                header = f"Bearer {secret.expose_secret()}"
            token = await self._ledger.mark_dispatched(
                session,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                reservation_id=reservation.id,
                attempt_id=reservation.attempt_id,
            )
            await session.commit()
            yield _Call(reservation, token, binding, header)
            finished = True
        finally:
            if not finished:
                try:
                    async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                        await session.rollback()
                        if token is None:
                            await self._ledger.release_before_dispatch(
                                session,
                                tenant_id=request.tenant_id,
                                workspace_id=request.workspace_id,
                                reservation_id=reservation.id,
                            )
                        else:
                            await set_rls_context(
                                session,
                                tenant_id=str(request.tenant_id),
                                workspace_id=str(request.workspace_id),
                            )
                            state = (
                                await session.execute(
                                    text(
                                        "SELECT status FROM ops.model_reservations WHERE tenant_id=:tenant "
                                        "AND workspace_id=:workspace AND id=:id"
                                    ),
                                    {
                                        "tenant": request.tenant_id,
                                        "workspace": request.workspace_id,
                                        "id": reservation.id,
                                    },
                                )
                            ).scalar_one()
                            if state == "dispatched":
                                await self._ledger.mark_unknown(
                                    session,
                                    tenant_id=request.tenant_id,
                                    workspace_id=request.workspace_id,
                                    reservation_id=reservation.id,
                                    attempt_id=reservation.attempt_id,
                                    dispatch_token=token,
                                )
                        await session.commit()
                except (Exception, asyncio.CancelledError):  # noqa: BLE001
                    logger.error("Model cleanup unavailable; durable reservation retained")
                    # Do not retry rollback outside the bounded cleanup context.
                    # Keep the durable reservation for recovery; caller owns disposal.

    async def _settle(
        self, session: AsyncSession, request: Request, call: _Call, usage: ModelUsage
    ) -> None:
        if (
            usage.usage_source == "unknown"
            or usage.input_tokens is None
            or usage.output_tokens is None
        ):
            raise ModelUnavailableError("Usage unavailable; reconciliation required")
        await set_rls_context(
            session, tenant_id=str(request.tenant_id), workspace_id=str(request.workspace_id)
        )
        snapshot = (
            await session.execute(
                text(
                    "SELECT pricing_snapshot FROM ops.model_reservations "
                    "WHERE tenant_id=:tenant AND workspace_id=:workspace AND id=:id"
                ),
                {
                    "tenant": request.tenant_id,
                    "workspace": request.workspace_id,
                    "id": call.reservation.id,
                },
            )
        ).scalar_one()
        try:
            pricing = ModelPricing(
                pricingVersion=snapshot["pricing_version"],
                costPerPromptTokenUsd=Decimal(snapshot["cost_per_prompt_token_usd"]),
                costPerCompletionTokenUsd=Decimal(snapshot["cost_per_completion_token_usd"]),
            )
            cost = compute_settlement_cost(
                pricing, usage.input_tokens, usage.output_tokens, usage.usage_source
            )
        except (ArithmeticError, KeyError, TypeError, ValueError):
            raise ModelUnavailableError("Invalid reservation pricing snapshot") from None
        if cost is None:
            raise ModelUnavailableError("Settlement price unavailable")
        await self._ledger.settle(
            session,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            reservation_id=call.reservation.id,
            attempt_id=call.reservation.attempt_id,
            dispatch_token=call.dispatch_token,
            provider=call.binding.profile.provider,
            model_id=call.binding.profile.model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            usage_source=usage.usage_source,
            cost_usd=cost,
        )
        await session.commit()

    def _duration(self, request: Request) -> float:
        deadline = request.deadline if isinstance(request, ModelGenerationRequest) else None
        if deadline is None:
            return self._timeout
        if deadline.tzinfo is None:
            raise ModelPolicyDeniedError("Deadline must include timezone")
        remaining = min(self._timeout, (deadline - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise ModelTimeoutError("Deadline expired before dispatch")
        return remaining

    async def _prepare(
        self,
        session: AsyncSession,
        request: ModelGenerationRequest,
        principal: RuntimePrincipal,
        run_id: UUID | None,
        job_id: UUID | None,
        *,
        stream: bool = False,
    ) -> tuple[ModelGenerationRequest, ModelBinding, AdmissionEnvelope]:
        binding, _, settings = await self._authorize(session, request, principal, run_id, job_id)
        if binding.endpoint_url is None:
            raise ModelPolicyDeniedError("Generation requires a provisioned HTTP route")
        profile = binding.profile
        if (request.requested_provider, request.requested_model) != (
            profile.provider,
            profile.model_id,
        ):
            raise ModelPolicyDeniedError("Requested model identity does not match")
        if request.settings and request.settings != settings:
            raise ModelPolicyDeniedError("Settings must match the published snapshot")
        fmt = settings.get("response_format")
        if request.response_format is not None and request.response_format != fmt:
            raise ModelPolicyDeniedError("Response format must match the published snapshot")
        if (
            request.tools
            and not profile.capabilities.supports_tools
            or fmt
            and not profile.capabilities.supports_json_schema
        ):
            raise ModelPolicyDeniedError("Unsupported model capability")
        if stream and (not profile.capabilities.supports_stream or self._streaming is None):
            raise ModelPolicyDeniedError("Streaming is unavailable")
        # Include roles, names, tool IDs and schema, not just message content.
        measurement = self._profiler.measure(
            profile,
            canonicalize_json(
                {
                    "messages": [m.model_dump(mode="json") for m in request.messages],
                    "response_format": fmt,
                }
            ).decode(),
            tools=[t.model_dump() for t in request.tools] if request.tools else None,
        )
        if measurement.basis == "unknown":
            raise ModelPolicyDeniedError("Verified counting profile is required")
        envelope = calculate_admission_envelope(
            profile, measurement.input_tokens, settings.get("max_output_tokens")
        )
        if measurement.input_tokens > profile.calculate_allowance(
            output_reserve=envelope.max_output_tokens
        ):
            raise ModelContextTooLargeError()
        request = request.model_copy(
            deep=True,
            update={
                "settings": {**settings, "max_output_tokens": envelope.max_output_tokens},
                "response_format": fmt,
            },
        )
        await session.commit()
        return request, binding, envelope

    async def generate(
        self,
        session: AsyncSession,
        request: ModelGenerationRequest,
        *,
        principal: RuntimePrincipal,
        run_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> ModelGenerationResponse:
        try:
            async with asyncio.timeout(self._duration(request)):
                request, binding, envelope = await self._prepare(
                    session, request, principal, run_id, job_id
                )
                if self._generation is None:
                    raise ModelUnavailableError("Generation is not configured")
                async with self._attempt(
                    session, request, binding, envelope, principal, run_id, job_id
                ) as call:
                    response = await self._generation.generate(
                        request, auth_header=call.auth_header, endpoint_url=binding.endpoint_url
                    )
                    await self._settle(session, request, call, response.usage)
                    await self._authorize(session, request, principal, run_id, job_id)
                    await session.commit()
                    return response
        except TimeoutError:
            raise ModelTimeoutError() from None

    async def stream(
        self,
        session: AsyncSession,
        request: ModelGenerationRequest,
        *,
        principal: RuntimePrincipal,
        run_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> AsyncIterator[ModelStreamingDelta]:
        # Buffer until completion/validation. Knowledge ACL policy is a separate
        # owner; this boundary never exposes raw partial provider output.
        try:
            async with asyncio.timeout(self._duration(request)):
                request, binding, envelope = await self._prepare(
                    session, request, principal, run_id, job_id, stream=True
                )
                assert self._streaming is not None
                async with self._attempt(
                    session, request, binding, envelope, principal, run_id, job_id
                ) as call:
                    buffered: list[ModelStreamingDelta] = []
                    size = 0
                    final: ModelStreamingDelta | None = None
                    async with aclosing(
                        self._streaming.stream(
                            request, auth_header=call.auth_header, endpoint_url=binding.endpoint_url
                        )
                    ) as source:
                        while True:
                            next_delta = asyncio.create_task(anext(source))
                            try:
                                while not next_delta.done():
                                    done, _ = await asyncio.wait({next_delta}, timeout=2.0)
                                    if not done:
                                        await self._authorize(
                                            session, request, principal, run_id, job_id
                                        )
                                        await session.commit()
                                try:
                                    delta = next_delta.result()
                                except StopAsyncIteration:
                                    break
                            finally:
                                if not next_delta.done():
                                    next_delta.cancel()
                                await asyncio.gather(next_delta, return_exceptions=True)
                            if (
                                delta.sequence != len(buffered)
                                or delta.request_id != request.request_id
                                or final is not None
                                or delta.delta_type == "error"
                            ):
                                raise ModelUnavailableError("Invalid stream ordering or outcome")
                            size += len(delta.model_dump_json().encode())
                            if size > 1_048_576 or len(buffered) >= 8192:
                                raise ModelUnavailableError("Stream buffer limit exceeded")
                            buffered.append(delta)
                            if delta.delta_type == "finish":
                                final = delta
                    if final is None or final.usage is None:
                        raise ModelUnavailableError("Stream completion or usage unavailable")
                    await self._settle(session, request, call, final.usage)
            # Never leave an asyncio timeout attached to the consumer task while
            # this async generator is suspended at yield.
            for delta in buffered:
                await self._authorize(session, request, principal, run_id, job_id)
                await session.commit()
                yield delta
        except TimeoutError:
            raise ModelTimeoutError() from None

    async def embed(
        self,
        session: AsyncSession,
        request: ModelEmbeddingRequest,
        *,
        principal: RuntimePrincipal,
        run_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> ModelEmbeddingResponse:
        try:
            async with asyncio.timeout(self._timeout):
                binding, _, _ = await self._authorize(session, request, principal, run_id, job_id)
                profile = binding.profile
                if (
                    not profile.capabilities.supports_embeddings
                    or self._embedding is None
                    or request.model_id != profile.model_id
                ):
                    raise ModelPolicyDeniedError("Embedding model is unavailable")
                # Explicit local tariff: zero provider bill does not mean CPU/RAM
                # cost is zero. Token usage comes from the actual E5 tokenizer.
                if (
                    profile.pricing is None
                    or profile.pricing.cost_per_prompt_token_usd != 0
                    or profile.pricing.cost_per_completion_token_usd != 0
                ):
                    raise ModelPolicyDeniedError(
                        "Local embedding requires explicit zero provider tariff"
                    )
                await session.commit()
                tokens = await self._embedding.count_tokens(request)
                envelope = AdmissionEnvelope(
                    tokens,
                    0,
                    tokens,
                    Decimal(0),
                    profile.pricing.pricing_version,
                    Decimal(0),
                    Decimal(0),
                    {
                        "pricing_version": profile.pricing.pricing_version,
                        "cost_per_prompt_token_usd": "0",
                        "cost_per_completion_token_usd": "0",
                    },
                )
                await session.commit()
                async with self._attempt(
                    session, request, binding, envelope, principal, run_id, job_id
                ) as call:
                    response = await self._embedding.embed(request)
                    if response.usage.input_tokens != tokens:
                        raise ModelUnavailableError("Embedding tokenizer usage mismatch")
                    await self._settle(session, request, call, response.usage)
                    await self._authorize(session, request, principal, run_id, job_id)
                    await session.commit()
                    return response
        except TimeoutError:
            raise ModelTimeoutError() from None
