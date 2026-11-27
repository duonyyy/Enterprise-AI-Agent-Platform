"""Trusted runtime identity and server-provisioned model routing; no public inference API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agent_contracts.model_contracts import ModelProfile
from platform_common.rls import set_rls_context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import (
    RuntimePrincipal,
    RuntimeStreamAuthError,
    revalidate_runtime_authorization,
)
from agent_runtime.llm.errors import ModelPolicyDeniedError, ModelUnavailableError


@dataclass(frozen=True)
class ModelBinding:
    """Provisioned by the maintainer, never constructed from a model request."""

    profile: ModelProfile
    endpoint_url: str | None = None


PrincipalCheck = Callable[[RuntimePrincipal], Awaitable[RuntimePrincipal]]


async def check_runtime_principal(principal: RuntimePrincipal) -> RuntimePrincipal:
    return await revalidate_runtime_authorization(principal, required_scope="run:start")


class ModelAuthority:
    def __init__(
        self,
        bindings: Mapping[UUID, ModelBinding],
        *,
        principal_check: PrincipalCheck = check_runtime_principal,
    ) -> None:
        self._bindings = {
            key: ModelBinding(value.profile.model_copy(deep=True), value.endpoint_url)
            for key, value in bindings.items()
        }
        self._principal_check = principal_check

    async def authorize(
        self,
        session: AsyncSession,
        *,
        principal: RuntimePrincipal | None,
        tenant_id: UUID,
        workspace_id: UUID,
        caller_id: UUID,
        model_config_id: UUID,
        run_id: UUID | None,
        job_id: UUID | None,
        embedding: bool = False,
    ) -> tuple[ModelBinding, str | None, dict[str, Any]]:
        try:
            async with asyncio.timeout(3):
                return await self._authorize(
                    session,
                    principal=principal,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    caller_id=caller_id,
                    model_config_id=model_config_id,
                    run_id=run_id,
                    job_id=job_id,
                    embedding=embedding,
                )
        except TimeoutError:
            raise ModelUnavailableError("Model authorization timed out") from None

    async def _authorize(
        self,
        session: AsyncSession,
        *,
        principal: RuntimePrincipal | None,
        tenant_id: UUID,
        workspace_id: UUID,
        caller_id: UUID,
        model_config_id: UUID,
        run_id: UUID | None,
        job_id: UUID | None,
        embedding: bool = False,
    ) -> tuple[ModelBinding, str | None, dict[str, Any]]:
        if principal is None or (
            principal.tenant_id != str(tenant_id)
            or principal.workspace_id != str(workspace_id)
            or principal.user_id != str(caller_id)
        ):
            raise ModelPolicyDeniedError("Trusted runtime principal and scope are required")
        try:
            live = await self._principal_check(principal)
        except RuntimeStreamAuthError as exc:
            if exc.code == "authorization_unavailable":
                raise ModelUnavailableError("Authorization unavailable") from None
            raise ModelPolicyDeniedError("Authorization revoked") from None
        if (live.user_id, live.tenant_id, live.workspace_id) != (
            principal.user_id,
            principal.tenant_id,
            principal.workspace_id,
        ):
            raise ModelPolicyDeniedError("Principal changed scope")
        binding = self._bindings.get(model_config_id)
        if binding is None:
            raise ModelPolicyDeniedError("Model profile is not provisioned")
        await set_rls_context(
            session,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            user_id=str(caller_id),
        )
        params = {"tenant": tenant_id, "workspace": workspace_id, "model": model_config_id}
        row = (
            (
                await session.execute(
                    text(
                        "SELECT provider, provider_model_id, secret_ref FROM catalog.model_configs "
                        "WHERE tenant_id=:tenant AND workspace_id=:workspace AND id=:model "
                        "AND status='active' FOR SHARE"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or (row["provider"], row["provider_model_id"]) != (
            binding.profile.provider,
            binding.profile.model_id,
        ):
            raise ModelPolicyDeniedError("Model unavailable or identity changed")
        if run_id is None:
            # User sessions may only act on an owned run. A worker identity/lease
            # is a separate boundary; a caller cannot impersonate it with job_id.
            raise ModelPolicyDeniedError("An owned run association is required")
        run = (
            (
                await session.execute(
                    text(
                        "SELECT r.initiated_by, r.status, v.bundle_manifest, v.bindings_sealed_at "
                        "FROM runtime.runs r JOIN catalog.agent_versions v "
                        "ON (v.tenant_id,v.workspace_id,v.id)=(r.tenant_id,r.workspace_id,r.agent_version_id) "
                        "WHERE r.tenant_id=:tenant AND r.workspace_id=:workspace AND r.id=:run FOR SHARE OF r,v"
                    ),
                    {**params, "run": run_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            run is None
            or run["initiated_by"] != caller_id
            or run["status"] not in {"queued", "running"}
            or run["bindings_sealed_at"] is None
        ):
            raise ModelPolicyDeniedError("Run is unavailable")
        if job_id is not None:
            job = (
                await session.execute(
                    text(
                        "SELECT id FROM runtime.jobs WHERE tenant_id=:tenant AND workspace_id=:workspace "
                        "AND id=:job AND run_id=:run AND status IN ('queued','leased') FOR SHARE"
                    ),
                    {**params, "job": job_id, "run": run_id},
                )
            ).scalar_one_or_none()
            if job is None:
                raise ModelPolicyDeniedError("Job association is unavailable")
        settings: dict[str, Any] = {}
        if not embedding:
            model = run["bundle_manifest"].get("dependencies", {}).get("model")
            if not isinstance(model, dict) or (
                model.get("modelConfigId"),
                model.get("provider"),
                model.get("providerModelId"),
            ) != (str(model_config_id), binding.profile.provider, binding.profile.model_id):
                raise ModelPolicyDeniedError("Published model snapshot does not match")
            settings = model.get("effectiveSettings", {})
            if not isinstance(settings, dict):
                raise ModelPolicyDeniedError("Invalid published model settings")
        return (
            ModelBinding(binding.profile.model_copy(deep=True), binding.endpoint_url),
            row["secret_ref"],
            dict(settings),
        )
