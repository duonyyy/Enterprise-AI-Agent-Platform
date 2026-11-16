"""Application service for Agent dependency validation and publish readiness (SPEC-AGENT-001, ADR-022, 04-B)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from agent_contracts.validation import AgentSpecValidationError, validate_agent_spec_raw
from control_api.modules.agents.models.errors import AgentNotFoundError
from control_api.modules.agents.models.ports import (
    AgentDependencyResolverPort,
    AgentRepositoryPort,
)
from control_api.modules.agents.models.records import (
    AgentDiagnosticEntity,
    AgentValidationResultEntity,
)
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SUPPORTED_MODES = frozenset({"fixed_plan"})


class AgentValidationService:
    """Evaluates canonical schema, domain invariants, and live scoped dependencies."""

    def __init__(
        self,
        repository: AgentRepositoryPort[AsyncSession],
        resolver: AgentDependencyResolverPort[AsyncSession],
    ) -> None:
        self._repository = repository
        self._resolver = resolver

    async def validate_draft(
        self,
        session: AsyncSession,
        *,
        principal: AuthenticatedPrincipal,
        agent_id: UUID,
        spec_override: dict[str, Any] | None = None,
        has_spec_override: bool | None = None,
    ) -> AgentValidationResultEntity:
        """Validate persisted draft or pre-validate a submitted spec against scoped dependencies."""
        tenant_id = UUID(principal.tenant_id)
        workspace_id = UUID(principal.workspace_id)
        actor_user_id = UUID(principal.user_id)
        caller_role = principal.roles[0] if principal.roles else "viewer"

        # 1. Fetch persisted draft to verify existence and check slug immutability
        draft = await self._repository.get_draft(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        if draft is None:
            raise AgentNotFoundError(f"Agent '{agent_id}' not found in workspace")

        is_override = (
            has_spec_override if has_spec_override is not None else (spec_override is not None)
        )
        if is_override:
            if spec_override is None:
                return AgentValidationResultEntity(
                    agent_id=draft.agent_id,
                    revision=draft.revision,
                    ready=False,
                    diagnostics=[
                        AgentDiagnosticEntity(
                            field="spec",
                            code="invalid_spec",
                            message="Submitted spec override cannot be null",
                        )
                    ],
                )
            spec = spec_override
        else:
            spec = draft.spec

        diagnostics: list[AgentDiagnosticEntity] = []

        if not isinstance(spec, dict):
            return AgentValidationResultEntity(
                agent_id=draft.agent_id,
                revision=draft.revision,
                ready=False,
                diagnostics=[
                    AgentDiagnosticEntity(
                        field="spec",
                        code="invalid_spec",
                        message="Specification must be a JSON object",
                    )
                ],
            )

        # 2. Canonical JSON Schema validation on raw dict
        try:
            validate_agent_spec_raw(spec)
        except AgentSpecValidationError as exc:
            for err in exc.errors:
                diagnostics.append(
                    AgentDiagnosticEntity(
                        field="spec",
                        code="invalid_spec",
                        message=err,
                    )
                )

        if diagnostics:
            # Raw structural failures are authoritative; never query dependencies for them.
            return AgentValidationResultEntity(
                agent_id=draft.agent_id,
                revision=draft.revision,
                ready=False,
                diagnostics=diagnostics,
            )

        # 3. Slug immutability invariant
        spec_name = (
            spec.get("metadata", {}).get("name") if isinstance(spec.get("metadata"), dict) else None
        )
        if spec_name is not None and spec_name != draft.slug:
            diagnostics.append(
                AgentDiagnosticEntity(
                    field="metadata.name",
                    code="invalid_spec",
                    message=f"spec.metadata.name '{spec_name}' cannot be changed and must match agent slug '{draft.slug}'",
                )
            )

        # 4. Mode capability check (B05)
        mode = spec.get("mode")
        if mode is not None:
            if not isinstance(mode, str):
                diagnostics.append(
                    AgentDiagnosticEntity(
                        field="mode",
                        code="invalid_spec",
                        message="Agent execution mode must be a string",
                    )
                )
            elif mode not in _SUPPORTED_MODES:
                diagnostics.append(
                    AgentDiagnosticEntity(
                        field="mode",
                        code="unsupported_capability",
                        message=f"Agent execution mode '{mode}' is not supported; only 'fixed_plan' is currently available",
                    )
                )

        # 5. Unsupported workflow reference (B05)
        if spec.get("workflowRef"):
            diagnostics.append(
                AgentDiagnosticEntity(
                    field="workflowRef",
                    code="unsupported_capability",
                    message="Workflow execution is not supported in the current capability release",
                )
            )

        # 6. Unsupported approval policy reference (B05)
        if spec.get("approvalPolicyRef"):
            diagnostics.append(
                AgentDiagnosticEntity(
                    field="approvalPolicyRef",
                    code="unsupported_capability",
                    message="Approval policy configuration is not supported in the current capability release",
                )
            )

        # 7. Model reference validation (B01, B02, B04, B06, B07)
        model_ref = spec.get("modelRef")
        if not model_ref or not isinstance(model_ref, str):
            diagnostics.append(
                AgentDiagnosticEntity(
                    field="modelRef",
                    code="invalid_spec",
                    message="Missing or invalid modelRef",
                )
            )
        else:
            try:
                model_uuid = UUID(model_ref)
                model_cfg = await self._resolver.resolve_model_config(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    model_id=model_uuid,
                )
                if model_cfg is None:
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field="modelRef",
                            code="dependency_unavailable",
                            message="Model configuration not found in workspace",
                        )
                    )
                elif model_cfg.status != "active":
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field="modelRef",
                            code="dependency_unavailable",
                            message=f"Model configuration is {model_cfg.status}",
                        )
                    )
            except (ValueError, TypeError, AttributeError):
                diagnostics.append(
                    AgentDiagnosticEntity(
                        field="modelRef",
                        code="dependency_unavailable",
                        message="Model configuration reference must be a valid UUID in workspace",
                    )
                )

        # 8. Tool references validation (B01, B02, B04)
        tool_refs = spec.get("toolRefs")
        if isinstance(tool_refs, list):
            for i, tool_ref in enumerate(tool_refs):
                if not isinstance(tool_ref, str):
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field=f"toolRefs[{i}]",
                            code="invalid_spec",
                            message="Tool reference must be a string UUID",
                        )
                    )
                    continue
                try:
                    tool_uuid = UUID(tool_ref)
                    tool_res = await self._resolver.resolve_tool_version(
                        session,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        tool_version_id=tool_uuid,
                        caller_role=caller_role,
                    )
                    if tool_res is None:
                        diagnostics.append(
                            AgentDiagnosticEntity(
                                field=f"toolRefs[{i}]",
                                code="dependency_unavailable",
                                message="Tool version not found in workspace",
                            )
                        )
                    else:
                        if tool_res.kind == "mcp":
                            diagnostics.append(
                                AgentDiagnosticEntity(
                                    field=f"toolRefs[{i}]",
                                    code="unsupported_capability",
                                    message="MCP readiness requires an approved owner resolver; this capability is unavailable",
                                )
                            )
                        if not tool_res.has_invoke_grant:
                            diagnostics.append(
                                AgentDiagnosticEntity(
                                    field=f"toolRefs[{i}]",
                                    code="dependency_not_permitted",
                                    message=f"Caller role '{caller_role}' lacks invoke grant for tool",
                                )
                            )
                except (ValueError, TypeError, AttributeError):
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field=f"toolRefs[{i}]",
                            code="dependency_unavailable",
                            message="Tool reference must be a valid UUID in workspace",
                        )
                    )

        # 9. Skill references validation (B01, B02, B03, B04)
        skill_refs = spec.get("skillRefs")
        if isinstance(skill_refs, list):
            for i, skill_ref in enumerate(skill_refs):
                if not isinstance(skill_ref, dict):
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field=f"skillRefs[{i}]",
                            code="invalid_spec",
                            message="Skill reference must be an object with skillId and skillVersionId",
                        )
                    )
                    continue
                skill_id_str = skill_ref.get("skillId")
                ver_id_str = skill_ref.get("skillVersionId")
                try:
                    skill_uuid = UUID(str(skill_id_str))
                    ver_uuid = UUID(str(ver_id_str))
                    skill_res = await self._resolver.resolve_skill_version(
                        session,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        skill_id=skill_uuid,
                        skill_version_id=ver_uuid,
                    )
                    if skill_res is None:
                        diagnostics.append(
                            AgentDiagnosticEntity(
                                field=f"skillRefs[{i}]",
                                code="dependency_unavailable",
                                message="Skill version not found in workspace",
                            )
                        )
                    else:
                        # Version-pair integrity (B03)
                        if skill_res.skill_id != skill_uuid:
                            diagnostics.append(
                                AgentDiagnosticEntity(
                                    field=f"skillRefs[{i}]",
                                    code="dependency_unavailable",
                                    message="Skill version does not belong to the specified skill",
                                )
                            )
                        if skill_res.skill_status != "active":
                            diagnostics.append(
                                AgentDiagnosticEntity(
                                    field=f"skillRefs[{i}]",
                                    code="dependency_unavailable",
                                    message=f"Skill is {skill_res.skill_status}",
                                )
                            )
                except (ValueError, TypeError, AttributeError):
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field=f"skillRefs[{i}]",
                            code="invalid_spec",
                            message="Skill reference must contain valid UUIDs for skillId and skillVersionId",
                        )
                    )

        # 10. Knowledge references validation (B01, B02, B08)
        knowledge_refs = spec.get("knowledgeRefs")
        if isinstance(knowledge_refs, list):
            for i, base_ref in enumerate(knowledge_refs):
                if not isinstance(base_ref, str):
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field=f"knowledgeRefs[{i}]",
                            code="invalid_spec",
                            message="Knowledge reference must be a string UUID",
                        )
                    )
                    continue
                try:
                    base_uuid = UUID(base_ref)
                    kb_res = await self._resolver.resolve_knowledge_base(
                        session,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        base_id=base_uuid,
                        user_id=actor_user_id,
                        caller_role=caller_role,
                    )
                    if kb_res is None:
                        diagnostics.append(
                            AgentDiagnosticEntity(
                                field=f"knowledgeRefs[{i}]",
                                code="dependency_unavailable",
                                message="Knowledge base not found in workspace",
                            )
                        )
                    elif not kb_res.has_access:
                        diagnostics.append(
                            AgentDiagnosticEntity(
                                field=f"knowledgeRefs[{i}]",
                                code="dependency_not_permitted",
                                message="Access denied to restricted knowledge base in workspace",
                            )
                        )
                except (ValueError, TypeError, AttributeError):
                    diagnostics.append(
                        AgentDiagnosticEntity(
                            field=f"knowledgeRefs[{i}]",
                            code="dependency_unavailable",
                            message="Knowledge reference must be a valid UUID in workspace",
                        )
                    )

        # 11. Final readiness calculation
        ready = len(diagnostics) == 0

        return AgentValidationResultEntity(
            agent_id=draft.agent_id,
            revision=draft.revision,
            ready=ready,
            diagnostics=diagnostics,
        )


__all__ = ["AgentValidationService"]
